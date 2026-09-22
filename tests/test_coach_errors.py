"""Provider failures remain distinct, actionable, and safe without using real keys."""

import asyncio
import builtins
import json
import logging
from collections.abc import Mapping
from threading import Event
from typing import Any, Literal

import httpx2
import pytest
from test_coach import ADVICE, FakeAdvisor, coach_settings, provider_response

from jevlab.coach.errors import coach_error, sanitize_text
from jevlab.coach.service import Coach, Completion, ProviderAdvisor, resolve_credentials
from jevlab.core.credentials import LEGACY_SERVICE, SERVICE, Credentials, Provider
from jevlab.core.errors import JevError
from jevlab.core.models import Settings, Template
from jevlab.core.service import Workbench
from jevlab.presentation import human_error


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    ("status", "kind", "message", "code", "retryable", "action"),
    [
        (401, "authentication_error", "API key is invalid", "coach_authentication", False, "key"),
        (
            403,
            "permission_error",
            "Project cannot use this model",
            "coach_permission",
            False,
            "permissions",
        ),
        (404, "not_found_error", "Model does not exist", "coach_model", False, "identifier"),
        (429, "insufficient_quota", "Credits exhausted", "coach_billing", False, "billing"),
        (429, "rate_limit_error", "Too many requests", "coach_rate_limit", True, "Wait"),
        (
            400,
            "invalid_request_error",
            "Credit balance is too low",
            "coach_billing",
            False,
            "billing",
        ),
        (400, "invalid_request_error", "Unsupported parameter", "coach_request", False, "model"),
        (529, "overloaded_error", "Service overloaded", "coach_provider_error", True, "status"),
    ],
)
async def test_real_sdk_failures_are_specific_and_preserve_safe_provider_message(
    provider: Literal["openai", "anthropic"],
    status: int,
    kind: str,
    message: str,
    code: str,
    retryable: bool,
    action: str,
) -> None:
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(status, json={"error": {"type": kind, "message": message}})

    advisor = ProviderAdvisor(
        "offline-synthetic-key",
        coach_settings(provider),
        transport=httpx2.MockTransport(respond),
        max_retries=0,
    )
    with pytest.raises(JevError) as caught:
        await advisor.complete("Only give advice.", "A synthetic diagnostic.")
    error = caught.value
    assert error.code == code and error.retryable is retryable and error.exit_code == 4
    assert message in error.message and action in error.fix and calls == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_http_status_and_redacted_request_id_survive_every_coach_boundary(
    provider: Literal["openai", "anthropic"],
    design: Template,
) -> None:
    secret = "offline-synthetic-audit-key"
    request_id = f"req-{secret}"
    advisor = ProviderAdvisor(
        secret,
        coach_settings(provider),
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                401,
                headers={"x-request-id": request_id, "request-id": request_id},
                json={"error": {"type": "authentication_error", "message": f"Revoked {secret}."}},
            )
        ),
        max_retries=0,
    )
    with pytest.raises(JevError) as caught:
        await Coach(coach_settings(provider), advisor=advisor).critique(design)
    error = caught.value
    assert error.http_status == 401
    assert error.request_id == "req-[redacted]"
    assert error.provider_code == "authentication_error"
    rendered = human_error(error, verbose=True)
    assert "401" in rendered and "req-[redacted]" in rendered
    assert secret not in rendered + json.dumps(error.as_dict())


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_provider_key_echo_and_json_escaped_keys_never_reach_errors_or_logs(
    provider: Literal["openai", "anthropic"],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = 'offline-key-"quoted"-\\slash'
    escaped = json.dumps(secret)[1:-1]
    monkeypatch.setenv("OPENAI_LOG", "debug")
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")
    caplog.set_level(logging.DEBUG)
    response_message = f"Rejected {secret} and {escaped}; other sk-proj-fake-secret.\x1b[31m"
    advisor = ProviderAdvisor(
        secret,
        coach_settings(provider),
        max_retries=0,
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                401, json={"error": {"message": response_message, "type": "authentication_error"}}
            )
        ),
    )
    with pytest.raises(JevError) as caught:
        await advisor.complete("Only give advice.", "Synthetic test.")
    rendered = str(caught.value) + json.dumps(caught.value.as_dict()) + caplog.text
    for forbidden in (secret, escaped, "sk-proj-fake-secret", "\x1b"):
        assert forbidden not in rendered
    assert "Provider message: Rejected [redacted]" in caught.value.message
    assert not caplog.records


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("failure", ["network", "timeout"])
async def test_sdk_connection_and_deadline_failures_are_distinct(
    provider: Literal["openai", "anthropic"],
    failure: str,
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        error_type = httpx2.ConnectError if failure == "network" else httpx2.ReadTimeout
        raise error_type("Never render raw secret contents", request=request)

    advisor = ProviderAdvisor(
        "offline-key",
        coach_settings(provider),
        transport=httpx2.MockTransport(respond),
        max_retries=0,
    )
    with pytest.raises(JevError) as caught:
        await advisor.complete("System", "Test")
    assert caught.value.code == f"coach_{failure}"
    assert caught.value.retryable and "secret contents" not in str(caught.value)


class MemoryKeyStore:
    def __init__(self, values: Mapping[str, str]) -> None:
        self.values = dict(values)
        self.reads: list[tuple[str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.reads.append((service, username))
        return self.values.get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[username] = password


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("source", ["keychain", "environment"])
async def test_coach_reads_saved_key_or_environment_fallback(
    provider: Literal["openai", "anthropic"],
    source: str,
    monkeypatch: pytest.MonkeyPatch,
    design: Template,
) -> None:
    key = f"offline-{source}-key"
    store = MemoryKeyStore({provider: key} if source == "keychain" else {})
    monkeypatch.setattr("jevlab.core.credentials.native_store", lambda: store)
    monkeypatch.setenv(f"{provider.upper()}_API_KEY", key if source == "environment" else "unused")
    captured: list[str] = []

    def advisor(key: str, settings: Settings) -> FakeAdvisor:
        captured.append(key)
        return FakeAdvisor()

    monkeypatch.setattr("jevlab.coach.service.ProviderAdvisor", advisor)
    settings = Settings(coach_provider=provider, coach_model="configured-test-model")
    result = await Coach(settings).critique(design)
    assert result.advice.summary == ADVICE["summary"]
    assert captured == [key]
    assert store.reads == [(SERVICE, provider)] + (
        [(LEGACY_SERVICE, provider)] if source == "environment" else []
    )
    assert key not in result.model_dump_json()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_missing_key_is_actionable_without_provider_call(
    provider: Literal["openai", "anthropic"],
    design: Template,
) -> None:
    with pytest.raises(JevError) as caught:
        await Coach(coach_settings(provider)).critique(design)
    assert caught.value.code == "missing_key" and caught.value.exit_code == 3
    assert f"{provider.upper()}_API_KEY" in caught.value.fix


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_missing_sdk_reports_global_install_command(
    provider: Literal["openai", "anthropic"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_import = builtins.__import__

    def intercept(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == provider:
            raise ImportError("Do not echo this import exception")
        return actual_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", intercept)
    with pytest.raises(JevError) as caught:
        await ProviderAdvisor("offline-key", coach_settings(provider)).complete("System", "Test")
    assert caught.value.code == "coach_dependency" and caught.value.exit_code == 3
    assert f"make install COACH={provider}" in caught.value.fix
    assert "import exception" not in str(caught.value)


async def test_keychain_wait_is_inside_coach_deadline(
    monkeypatch: pytest.MonkeyPatch,
    design: Template,
) -> None:
    release = Event()

    def blocked(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        release.wait(2)
        return "offline-key", "keychain"

    monkeypatch.setattr(Credentials, "resolve", blocked)
    settings = coach_settings().model_copy(update={"coach_timeout_seconds": 0.02})
    try:
        async with asyncio.timeout(0.5):
            with pytest.raises(JevError) as caught:
                await Coach(settings).critique(design)
        assert caught.value.code == "coach_timeout" and "Keychain" in caught.value.fix
    finally:
        release.set()


async def test_native_worker_completion_after_cancellation_is_discarded() -> None:
    release = Event()

    class BlockedStore(MemoryKeyStore):
        def get_password(self, service: str, username: str) -> str | None:
            release.wait(2)
            return "offline-key"

    task = asyncio.create_task(resolve_credentials(Credentials(store=BlockedStore({})), "openai"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.sleep(0.01)


async def test_cancellation_is_not_converted_to_provider_failure(design: Template) -> None:
    class CancelledAdvisor:
        async def complete(self, system: str, prompt: str) -> Completion:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await Coach(coach_settings(), advisor=CancelledAdvisor()).critique(design)


def test_unexpected_internal_error_is_safe_and_nonretryable() -> None:
    error = coach_error(RuntimeError("Arbitrary secret, request or traceback must stay private"))
    assert error.code == "coach_internal" and error.exit_code == 4 and not error.retryable
    assert "secret" not in str(error) and "jevlab doctor --coach" in error.fix


def test_message_redaction_handles_control_characters_unicode_and_bound() -> None:
    key = 'offline-"key"-café'
    message = f"{key} {json.dumps(key)[1:-1]} Bearer another-secret\x00\n\u202e " + "x" * 2000
    safe = sanitize_text(message, (key,))
    assert len(safe) == 1200 and key not in safe and "another-secret" not in safe
    assert "\u202e" not in safe and "\\u00e9" not in safe and "\n" not in safe


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_successful_coach_advice_cannot_echo_credentials_and_preserves_long_design(
    provider: Literal["openai", "anthropic"],
    design: Template,
    wb: Workbench,
) -> None:
    key = 'offline-"quoted"-key-\\suffix'
    long_note = "Evidence should identify the source explicitly. " * 80
    template = design.model_copy(deep=True)
    template.notes = long_note + key
    data = {
        "summary": f"Assess the supplied evidence {key}.",
        "observations": [f"Quoted key {json.dumps(key)[1:-1]}", "Bearer sk-proj-fake-token"],
        "next_experiment": f"Never request {key} from a user.",
        "template": template.model_dump(mode="json"),
    }
    settings = coach_settings(provider)
    advisor = ProviderAdvisor(
        key,
        settings,
        max_retries=0,
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, json=provider_response(provider, "configured-model-" + key, data)
            )
        ),
    )
    result = await Coach(settings, advisor=advisor).design("Make a support design", "safe-proposal")
    rendered = result.model_dump_json()
    assert key not in rendered and json.dumps(key)[1:-1] not in rendered
    assert "sk-proj-fake-token" not in rendered
    assert result.advice.template and result.advice.template.notes == long_note + "[redacted]"
    assert len(result.advice.template.notes) > 1200
    assert result.model == "configured-model-[redacted]"
    assert "safe-proposal" not in wb.templates.names() and not wb.storage.history()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_luna_reasoning_is_disabled_without_overriding_custom_models(
    provider: Literal["openai", "anthropic"],
    design: Template,
) -> None:
    settings = Settings(coach_provider=provider)
    requests: list[dict[str, Any]] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx2.Response(200, json=provider_response(provider, body["model"], ADVICE))

    advisor = ProviderAdvisor("offline-key", settings, transport=httpx2.MockTransport(respond))
    await Coach(settings, advisor=advisor).critique(design)
    if provider == "openai":
        assert requests[0]["reasoning"] == {"effort": "none"}
    else:
        assert "reasoning" not in requests[0]


@pytest.mark.parametrize(
    ("reason", "code", "action"),
    [
        ("max_tokens", "coach_output_limit", "coach_max_output_tokens"),
        ("refusal", "coach_refused", "revise"),
        ("pause_turn", "coach_paused", "shorter"),
        ("model_context_window_exceeded", "coach_context_limit", "Reduce"),
        ("tool_use", "coach_incomplete", "compatibility"),
    ],
)
async def test_anthropic_finish_reason_is_preserved_and_partial_advice_rejected(
    reason: str,
    code: str,
    action: str,
    design: Template,
) -> None:
    key = "offline-anthropic-key"
    response = provider_response("anthropic", "configured-test-model", ADVICE)
    response["stop_reason"] = reason
    if reason == "refusal":
        response["content"] = [{"type": "text", "text": f"Cannot fulfill request {key}."}]
    settings = coach_settings("anthropic")
    advisor = ProviderAdvisor(
        key,
        settings,
        max_retries=0,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=response)),
    )
    with pytest.raises(JevError) as caught:
        await Coach(settings, advisor=advisor).critique(design)
    assert caught.value.code == code and caught.value.exit_code == 4
    assert f"Provider reason: {reason}" in caught.value.message and action in caught.value.fix
    assert key not in str(caught.value)
    if reason == "refusal":
        assert "Cannot fulfill request [redacted]" in caught.value.message


@pytest.mark.parametrize(
    ("status", "reason", "code", "action"),
    [
        ("incomplete", "max_output_tokens", "coach_output_limit", "coach_max_output_tokens"),
        ("incomplete", "content_filter", "coach_refused", "revise"),
        ("incomplete", "max_messages", "coach_incomplete", "compatibility"),
        ("failed", "server_error", "coach_provider_error", "service status"),
        ("failed", "rate_limit_exceeded", "coach_rate_limit", "Wait"),
        ("failed", "invalid_prompt", "coach_request", "revise"),
    ],
)
async def test_openai_finish_details_distinguish_output_limit_from_provider_failure(
    status: str,
    reason: str,
    code: str,
    action: str,
    design: Template,
) -> None:
    key = "offline-openai-key"
    response = provider_response("openai", "configured-test-model", ADVICE)
    response["status"] = status
    if status == "incomplete":
        response["incomplete_details"] = {"reason": reason}
    else:
        response["error"] = {"code": reason, "message": f"Provider explanation {key}."}
    settings = coach_settings("openai")
    advisor = ProviderAdvisor(
        key,
        settings,
        max_retries=0,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=response)),
    )
    with pytest.raises(JevError) as caught:
        await Coach(settings, advisor=advisor).critique(design)
    assert caught.value.code == code and caught.value.exit_code == 4
    assert f"Provider reason: {reason}" in caught.value.message and action in caught.value.fix
    assert key not in str(caught.value)
    if status == "failed":
        assert "Provider explanation [redacted]" in caught.value.message


async def test_openai_completed_refusal_is_not_reported_as_invalid_json(design: Template) -> None:
    response = provider_response("openai", "configured-test-model", ADVICE)
    response["output"] = [
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "refusal", "refusal": "Cannot fulfill request offline-key."}],
        }
    ]
    settings = coach_settings("openai")
    advisor = ProviderAdvisor(
        "offline-key",
        settings,
        max_retries=0,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=response)),
    )
    with pytest.raises(JevError) as caught:
        await Coach(settings, advisor=advisor).critique(design)
    assert caught.value.code == "coach_refused"
    assert "Cannot fulfill request [redacted]" in caught.value.message


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_empty_completed_response_is_explicit(
    provider: Literal["openai", "anthropic"],
    design: Template,
) -> None:
    response = provider_response(provider, "configured-test-model", ADVICE)
    response["output" if provider == "openai" else "content"] = []
    settings = coach_settings(provider)
    advisor = ProviderAdvisor(
        "offline-key",
        settings,
        max_retries=0,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=response)),
    )
    with pytest.raises(JevError) as caught:
        await Coach(settings, advisor=advisor).critique(design)
    assert caught.value.code == "coach_empty_output"
    assert "without returning any coach text" in caught.value.message
