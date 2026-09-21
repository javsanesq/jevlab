"""The doctor's provider probes use real SDKs with offline HTTP responses."""

import json
import time
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from jev.coach.diagnostics import (
    PROBE_OUTPUT_TOKENS,
    PROVIDERS,
    CoachProvider,
    check_coaches,
    probe_estimates,
    safe_value,
)
from jev.coach.service import DIAGNOSTIC_INSTRUCTIONS, SYSTEM
from jev.core.credentials import ENV_KEYS, SERVICE, Credentials
from jev.core.models import Settings

ADVICE = {
    "summary": "The topic question has a clear boundary.",
    "observations": ["The other option covers remaining topics."],
    "next_experiment": "Test messages that mention both a payment and another issue.",
    "template": None,
}


class MemoryStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}
        self.reads: list[tuple[str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.reads.append((service, username))
        assert service == SERVICE
        return self.values.get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[username] = password


def response_body(provider: CoachProvider, model: str) -> dict[str, Any]:
    if provider == "anthropic":
        return {
            "id": "msg_probe",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": json.dumps(ADVICE)}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 200, "output_tokens": 55},
        }
    return {
        "id": "resp_probe",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": model,
        "output": [
            {
                "id": "msg_probe",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(ADVICE), "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 200, "output_tokens": 55, "total_tokens": 255},
    }


def responder(
    provider: CoachProvider, requests: list[dict[str, Any]]
) -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        output_field = "max_tokens" if provider == "anthropic" else "max_output_tokens"
        assert body[output_field] == PROBE_OUTPUT_TOKENS
        assert "tools" not in body
        if provider == "anthropic":
            assert str(request.url) == "https://api.anthropic.com/v1/messages"
            prompt = body["messages"][0]["content"]
            system = body["system"]
        else:
            assert str(request.url) == "https://api.openai.com/v1/responses"
            assert body["store"] is False
            prompt, system = body["input"], body["instructions"]
        assert "synthetic" in prompt
        assert system == SYSTEM + DIAGNOSTIC_INSTRUCTIONS
        assert "45 words" in system
        assert "diagnostic_instructions" not in prompt
        assert set(json.loads(prompt)["data"]) == {"template"}
        return httpx2.Response(200, json=response_body(provider, body["model"]))

    return respond


@pytest.mark.parametrize("source", ["keychain", "environment"])
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_coach_probe_resolves_each_key_once_and_returns_validated_advice(
    provider: CoachProvider, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = f"synthetic-{provider}-secret"
    store = MemoryStore({provider: key} if source == "keychain" else {})
    if source == "environment":
        monkeypatch.setenv(ENV_KEYS[provider], key)
    settings = Settings(credential_mode="keychain")
    requests: list[dict[str, Any]] = []
    reports = await check_coaches(
        settings,
        live=True,
        credentials=Credentials("keychain", store),
        transports={provider: httpx2.MockTransport(responder(provider, requests))},
    )
    row = next(row for row in reports if row["provider"] == provider)
    assert row["status"] == "succeeded" and row["network_checked"] is True
    assert row["key_found"] is True and row["key_source"] == source
    assert row["sdk_installed"] is True and row["sdk_version"]
    result = row["result"]
    assert isinstance(result, dict)
    assert result["advice"] == ADVICE
    assert result["input_tokens"] == 200 and result["output_tokens"] == 55
    assert result["cost_nanousd"] > 0
    assert len(requests) == 1
    prompt = (
        requests[0]["messages"][0]["content"] if provider == "anthropic" else requests[0]["input"]
    )
    estimate = row["estimate"]
    assert isinstance(estimate, dict)
    actual_input_bytes = len((SYSTEM + DIAGNOSTIC_INSTRUCTIONS + prompt).encode())
    assert estimate["estimated_input_tokens"] == (actual_input_bytes + 2) // 3 + 100
    assert all(count == 1 for count in [store.reads.count((SERVICE, p)) for p in PROVIDERS])
    assert key not in json.dumps(reports)
    assert settings.coach_provider == "disabled"  # Checking does not enable a coach.


async def test_keychain_unavailable_falls_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LockedStore(MemoryStore):
        def get_password(self, service: str, username: str) -> str | None:
            raise RuntimeError("secret Keychain backend detail must not be shown")

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    reports = await check_coaches(
        Settings(), live=False, credentials=Credentials("keychain", LockedStore())
    )
    row = reports[1]
    assert row["key_found"] is True
    assert row["key_source"] == "environment (Keychain unavailable)"
    assert "secret Keychain backend detail" not in json.dumps(reports)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_missing_key_is_actionable_and_never_calls_provider(provider: CoachProvider) -> None:
    reports = await check_coaches(Settings(credential_mode="environment"), live=True)
    row = next(row for row in reports if row["provider"] == provider)
    assert row["key_found"] is False and row["key_source"] == "missing"
    assert row["status"] == "blocked" and row["live_attempted"] is False
    blockers = row["blockers"]
    assert isinstance(blockers, list)
    assert blockers[0]["code"] == "missing_key"
    assert ENV_KEYS[provider] in blockers[0]["fix"]


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_missing_sdk_still_reports_key_and_model(
    provider: CoachProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jev.coach.diagnostics.sdk_version", lambda _: None)
    monkeypatch.setenv(ENV_KEYS[provider], "synthetic-key")
    settings = Settings(credential_mode="environment")
    reports = await check_coaches(settings, live=True)
    row = next(row for row in reports if row["provider"] == provider)
    assert row["sdk_installed"] is False and row["sdk_version"] is None
    assert row["key_found"] is True
    assert row["model"] == settings.coach_model_for(provider)
    assert row["live_attempted"] is False
    blockers = row["blockers"]
    assert isinstance(blockers, list)
    assert blockers[0]["code"] == "coach_dependency"
    assert f"make install COACH={provider}" in blockers[0]["fix"]
    other = next(row for row in reports if row["provider"] != provider)
    assert isinstance(other["blockers"], list)
    assert {item["code"] for item in other["blockers"]} == {"coach_dependency", "missing_key"}


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    ("status", "error_type", "message", "expected_code"),
    [
        (401, "authentication_error", "Invalid API key", "coach_authentication"),
        (404, "not_found_error", "The model does not exist", "coach_model"),
        (429, "rate_limit_error", "Requests per minute exceeded", "coach_rate_limit"),
        (429, "insufficient_quota", "You exceeded your current quota", "coach_billing"),
    ],
)
async def test_live_provider_error_is_specific_redacted_and_not_retried(
    provider: CoachProvider,
    status: int,
    error_type: str,
    message: str,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = f"synthetic-{provider}-secret"
    monkeypatch.setenv(ENV_KEYS[provider], secret)
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            status,
            json={
                "error": {
                    "type": error_type,
                    "code": error_type,
                    "message": f"{message}: {secret}",
                }
            },
        )

    reports = await check_coaches(
        Settings(credential_mode="environment"),
        live=True,
        transports={provider: httpx2.MockTransport(respond)},
    )
    row = next(row for row in reports if row["provider"] == provider)
    assert row["status"] == "failed" and row["live_attempted"] is True
    error = row["error"]
    assert isinstance(error, dict)
    assert error["code"] == expected_code
    assert message in error["message"]
    assert error["fix"] and secret not in json.dumps(reports)
    assert len(requests) == 1


async def test_one_failed_provider_does_not_hide_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for provider in PROVIDERS:
        monkeypatch.setenv(ENV_KEYS[provider], "synthetic-key")
    successful_requests: list[dict[str, Any]] = []
    reports = await check_coaches(
        Settings(credential_mode="environment"),
        live=True,
        transports={
            "anthropic": httpx2.MockTransport(
                lambda request: httpx2.Response(
                    401, json={"error": {"type": "authentication_error", "message": "Bad key"}}
                )
            ),
            "openai": httpx2.MockTransport(responder("openai", successful_requests)),
        },
    )
    assert [row["status"] for row in reports] == ["failed", "succeeded"]
    assert len(successful_requests) == 1


async def test_offline_probe_never_calls_with_all_preconditions_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for provider in PROVIDERS:
        monkeypatch.setenv(ENV_KEYS[provider], "synthetic-key")
    reports = await check_coaches(Settings(credential_mode="environment"), live=False)
    assert all(row["ready"] is True and row["status"] == "not_checked" for row in reports)
    assert all(row["live_attempted"] is False for row in reports)


def test_estimates_do_not_read_keys_and_unknown_prices_remain_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Cost preview must not read credentials.")

    monkeypatch.setattr(Credentials, "resolve", forbidden)
    estimates = probe_estimates(Settings(anthropic_model="claude-opus-5"))
    assert all(isinstance(row["estimated_cost_nanousd"], int) for row in estimates)
    assert all(row["max_output_tokens"] == 256 for row in estimates)
    assert all(row["price_verified"] == "2026-09-20" for row in estimates)
    unknown = probe_estimates(Settings(openai_model="my-private-model"))[1]
    assert unknown["estimated_cost_nanousd"] is None
    assert "unknown" in str(unknown["note"])


def test_redaction_preserves_large_nested_result_shape() -> None:
    value = {
        "advice": {
            "summary": "An intentionally detailed explanation. " * 50 + "synthetic-secret",
            "observations": ["first", "second synthetic-secret"],
            "template": None,
        },
        "sources": ["https://example.test/" + "a" * 200] * 8,
        "input_tokens": 456,
    }
    assert len(json.dumps(value)) > 1200
    result = safe_value(value, "synthetic-secret")
    assert isinstance(result, dict)
    assert result["input_tokens"] == 456 and result["sources"] == value["sources"]
    assert result["advice"]["template"] is None
    assert len(result["advice"]["observations"]) == 2
    assert "synthetic-secret" not in json.dumps(result)


async def test_credential_lookup_timeout_is_bounded_and_distinct() -> None:
    class SlowStore(MemoryStore):
        def get_password(self, service: str, username: str) -> str | None:
            time.sleep(0.08)
            return "late-secret"

    started = time.perf_counter()
    reports = await check_coaches(
        Settings(coach_timeout_seconds=0.005),
        live=True,
        credentials=Credentials("keychain", SlowStore()),
    )
    assert time.perf_counter() - started < 0.5
    for row in reports:
        assert row["live_attempted"] is False and row["status"] == "blocked"
        assert isinstance(row["blockers"], list)
        assert row["blockers"][0]["code"] == "coach_keychain_timeout"


async def test_connection_failure_does_not_claim_provider_was_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("synthetic-key raw network details", request=request)

    reports = await check_coaches(
        Settings(credential_mode="environment"),
        live=True,
        transports={"openai": httpx2.MockTransport(refuse)},
    )
    assert reports[1]["live_attempted"] is True and reports[1]["network_checked"] is False
    error = reports[1]["error"]
    assert isinstance(error, dict) and error["code"] == "coach_network"
    assert "synthetic-key" not in json.dumps(reports)


async def test_invalid_model_is_blocked_before_provider_call() -> None:
    settings = Settings().model_copy(update={"anthropic_model": ""})
    reports = await check_coaches(
        settings,
        live=True,
        credentials=Credentials("keychain", MemoryStore({"anthropic": "synthetic-key"})),
    )
    row = reports[0]
    assert row["live_attempted"] is False
    blockers = row["blockers"]
    assert isinstance(blockers, list) and blockers[0]["code"] == "coach_model_invalid"


async def test_large_successful_advice_is_redacted_without_corrupting_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "synthetic-secret"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = response_body("anthropic", json.loads(request.content)["model"])
        body["content"][0]["text"] = json.dumps(
            {
                **ADVICE,
                "observations": ["A long observation. " * 100, "Never reveal " + secret],
            }
        )
        return httpx2.Response(200, json=body)

    reports = await check_coaches(
        Settings(credential_mode="environment"),
        live=True,
        transports={"anthropic": httpx2.MockTransport(respond)},
    )
    row = reports[0]
    assert row["status"] == "succeeded"
    result = row["result"]
    assert isinstance(result, dict)
    assert result["input_tokens"] == 200
    assert result["advice"]["observations"][1] == "Never reveal [redacted]"
    assert result["sources"]
    assert secret not in json.dumps(reports)
