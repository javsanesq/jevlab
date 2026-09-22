import json
from typing import Any, Literal

import httpx2
import pytest

from jevlab.coach.service import Coach, Completion, ProviderAdvisor
from jevlab.core.errors import JevError
from jevlab.core.models import Settings, Template
from jevlab.core.service import Workbench

ADVICE = {
    "summary": "Clarify the evidence boundary.",
    "observations": ["Keep this a single judgment."],
    "next_experiment": "Name ticket.message explicitly in the instructions.",
    "template": None,
}


class FakeAdvisor:
    def __init__(self, data: object = ADVICE) -> None:
        self.data = data
        self.prompts: list[str] = []

    async def complete(self, system: str, prompt: str) -> Completion:
        self.prompts.append(system + prompt)
        return Completion(json.dumps(self.data), "configured-test-model", 100, 50)


def coach_settings(provider: Literal["openai", "anthropic"] = "openai") -> Settings:
    return Settings(
        coach_provider=provider, coach_model="configured-test-model", credential_mode="environment"
    )


def provider_response(provider: str, model: str, data: object) -> dict[str, object]:
    if provider == "openai":
        return {
            "id": "resp_test",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": model,
            "output": [
                {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": json.dumps(data), "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": json.dumps(data)}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


async def test_coach_proposal_is_validated_but_not_saved_or_run(
    wb: Workbench, design: Template
) -> None:
    data = {**ADVICE, "template": design.model_dump(mode="json")}
    advisor = FakeAdvisor(data)
    result = await Coach(coach_settings(), advisor=advisor).design(
        "Route a support ticket.", "proposal"
    )
    assert result.advice.template and result.advice.template.name == "proposal"
    assert result.advice.template.model == "jev-1.13.0"
    assert "proposal" not in wb.templates.names()
    assert wb.storage.history() == []
    assert result.cost_nanousd is None and result.sources
    assert "not the decision maker" in advisor.prompts[0]


@pytest.mark.parametrize(
    "body", [ADVICE, {**ADVICE, "template": {"name": "../escape"}}, {"summary": "incomplete"}]
)
async def test_invalid_design_never_persists(body: object, wb: Workbench) -> None:
    with pytest.raises(JevError, match="invalid proposal"):
        await Coach(coach_settings(), advisor=FakeAdvisor(body)).design("Test", "test-design")
    assert not wb.storage.history()


async def test_disabled_coach_never_calls_provider(design: Template) -> None:
    advisor = FakeAdvisor()
    with pytest.raises(JevError) as error:
        await Coach(Settings(), advisor=advisor).critique(design)
    assert error.value.code == "coach_disabled" and advisor.prompts == []


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_real_provider_sdks_with_mock_transport(
    provider: Literal["openai", "anthropic"], design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-not-be-used.invalid")
    requests: list[dict[str, Any]] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "configured-test-model"
        assert "reasoning" not in body
        if provider == "openai":
            assert str(request.url) == "https://api.openai.com/v1/responses"
            assert body["store"] is False and "tools" not in body
        else:
            assert str(request.url) == "https://api.anthropic.com/v1/messages"
            assert body["max_tokens"] == 4096 and "tools" not in body
        return httpx2.Response(200, json=provider_response(provider, body["model"], ADVICE))

    settings = coach_settings(provider)
    advisor = ProviderAdvisor("synthetic-key", settings, transport=httpx2.MockTransport(respond))
    result = await Coach(settings, advisor=advisor).critique(design)
    assert result.advice.next_experiment == ADVICE["next_experiment"]
    assert result.input_tokens == 100 and len(requests) == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_provider_auth_error_is_redacted(
    provider: Literal["openai", "anthropic"], design: Template
) -> None:
    settings = coach_settings(provider)
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(
            401,
            json={
                "error": {
                    "message": "synthetic-key must never leak",
                    "type": "authentication_error",
                }
            },
        )
    )
    advisor = ProviderAdvisor("synthetic-key", settings, transport=transport)
    with pytest.raises(JevError) as caught:
        await Coach(settings, advisor=advisor).critique(design)
    assert caught.value.code == "coach_authentication"
    assert "synthetic-key" not in str(caught.value)
