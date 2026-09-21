"""Execute exported modules against the real SDK and frameworks without network access."""

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx2
import pytest
from conftest import RESPONSE, MockEvaluator
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score, TypeSafeClient

from jev.core.client import verify_response
from jev.core.errors import JevError
from jev.core.exporting import ExportLanguage, export_template, write_export
from jev.core.models import ConfidenceGate, NoulGate, Template
from jev.core.service import Workbench
from jev.core.thresholds import route


def load_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: Template,
    lang: ExportLanguage = "python",
) -> ModuleType:
    path = write_export(template, tmp_path / f"{lang}.py", lang=lang)
    name = f"jev_test_export_{lang.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def sdk_transport(body: dict[str, Any], requests: list[dict[str, Any]]) -> httpx2.MockTransport:
    def respond(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["authorization"] == "Bearer offline-export-secret"
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=body)

    return httpx2.MockTransport(respond)


def test_export_preserves_structured_rubrics_without_code_execution(
    design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = "\"\"\"\n__import__('pathlib').Path('must-not-exist').touch()\n# __TEMPLATE_JSON__"
    design.notes = payload
    design.description = "Unicode: español \u2028" + payload
    design.questions["route"] = Choice(
        instructions={"ask": payload, "evidence": ["ticket", {"path": "customer"}]},
        criteria={"billing": {"includes": [payload]}, "technical": None, "other": "Neither"},
    )
    design.questions["impact"] = Score(
        instructions={"dimension": "Disruption", "ignore": ["sentiment"]},
        criteria=[{"example": "None"}, ["Workaround", "Partial"], "Blocked"],
    )
    design.questions["refund_requested"] = Noul(instructions=["Explicit request?", payload])
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-export-secret")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://must-not-use.example")
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "must-not-use")
    module = load_export(tmp_path, monkeypatch, design)
    assert json.loads(module.TEMPLATE_JSON) == design.model_dump(mode="json")
    requests: list[dict[str, Any]] = []
    state = {"ticket": "Refund requested", "nested": [1, True, None]}
    result = module.evaluate(state, transport=sdk_transport(RESPONSE, requests), max_retries=0)
    assert requests == [
        {
            "state": state,
            "model": design.model,
            "questions": {k: q.model_dump(mode="json") for k, q in design.questions.items()},
        }
    ]
    verify_response(design, result.response)
    assert {key: value.model_dump() for key, value in result.routing.items()} == route(
        design, result.response
    )
    assert result.response.scores["impact"].score == 0.3
    assert not (Path.cwd() / "must-not-exist").exists()
    assert "from jev" not in export_template(design)
    assert "offline-export-secret" not in export_template(design)


async def test_export_async_model_override_and_threshold_boundaries(
    design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    design.thresholds = {
        "route": ConfidenceGate(automate_at_or_above=0.85),
        "impact": ConfidenceGate(automate_at_or_above=0.81),
        "refund_requested": NoulGate(no_at_or_below=0.1, yes_at_or_above=0.9),
    }
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-export-secret")
    module = load_export(tmp_path, monkeypatch, design)
    for probability, disposition, value in [
        (0.1, "automate", False),
        (0.9, "automate", True),
        (0.5, "review", None),
    ]:
        body = copy.deepcopy(RESPONSE)
        body["answers"]["refund_requested"]["noul"] = probability
        requests: list[dict[str, Any]] = []
        result = await module.aevaluate(
            "Please refund this invoice.",
            model="jev-latest",
            max_retries=0,
            transport=sdk_transport(body, requests),
        )
        assert requests[0]["model"] == "jev-latest"
        assert result.routing["route"].disposition == "automate"
        assert result.routing["impact"].disposition == "review"
        assert result.routing["refund_requested"].disposition == disposition
        assert result.routing["refund_requested"].value is value
        assert {key: gate.model_dump() for key, gate in result.routing.items()} == route(
            design, result.response
        )


def test_missing_thresholds_always_review(
    design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    design.thresholds.clear()
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-export-secret")
    module = load_export(tmp_path, monkeypatch, design)
    result = module.evaluate("ticket", transport=sdk_transport(RESPONSE, []), max_retries=0)
    assert all(item.disposition == "review" for item in result.routing.values())
    assert result.routing["refund_requested"].value is None


@pytest.mark.parametrize("state", ["", "  ", 1, None, True, {"bad": float("nan")}])
def test_invalid_state_fails_before_credentials_or_network(
    design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: Any
) -> None:
    module = load_export(tmp_path, monkeypatch, design)
    with pytest.raises(ValueError):
        module.evaluate(state)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "wrong_type", "options", "winner", "sum", "score_levels", "legend", "usage"],
)
def test_bad_response_never_produces_automation(
    design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-export-secret")
    module = load_export(tmp_path, monkeypatch, design)
    body = copy.deepcopy(RESPONSE)
    answers = body["answers"]
    if mutation == "missing":
        del answers["route"]
    elif mutation == "wrong_type":
        answers["route"] = {"type": "noul", "noul": 0.99}
    elif mutation == "options":
        answers["route"]["probabilities"]["surprise"] = 0.0
    elif mutation == "winner":
        answers["route"]["choice"] = "technical"
    elif mutation == "sum":
        answers["route"]["probabilities"]["billing"] = 0.5
    elif mutation == "score_levels":
        answers["impact"]["probabilities"] = {"0": 0.9, "1": 0.1}
    elif mutation == "legend":
        del answers["impact"]["legend"]["2"]
    elif mutation == "usage":
        body["usage"]["input_tokens"] = -1
    with pytest.raises(ValueError, match="inconsistent"):
        module.evaluate("ticket", transport=sdk_transport(body, []), max_retries=0)


@pytest.mark.parametrize("levels", [2, 3, 10])
@pytest.mark.parametrize("difference,accepted", [(0.014, True), (0.016, False), (0.5, False)])
async def test_score_consistency_matches_core_and_both_export_entrypoints(
    wb: Workbench,
    design: Template,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    levels: int,
    difference: float,
    accepted: bool,
) -> None:
    """Accept small rounding differences; contradictory Scores never reach routing."""
    design.questions["impact"] = Score(
        instructions="Rate impact.", criteria=[f"Level {index}" for index in range(levels)]
    )
    design.thresholds["impact"] = ConfidenceGate(automate_at_or_above=0.5)
    body = copy.deepcopy(RESPONSE)
    score = (0.5 + difference) * (levels - 1)
    body["answers"]["impact"].update(
        score=score,
        confidence=1.0,
        probabilities={
            str(index): 0.5 if index in (0, levels - 1) else 0.0 for index in range(levels)
        },
        legend={str(index): f"Level {index}" for index in range(levels)},
    )
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-export-secret")
    module = load_export(tmp_path, monkeypatch, design)
    if accepted:
        saved = await wb.run(design, "ticket", evaluator=MockEvaluator(body))
        sync = module.evaluate("ticket", transport=sdk_transport(body, []), max_retries=0)
        asynchronous = await module.aevaluate(
            "ticket", transport=sdk_transport(body, []), max_retries=0
        )
        assert saved.routing is not None
        saved_gate = saved.routing["impact"]
        assert isinstance(saved_gate, dict) and saved_gate["value"] == score
        for result in (sync, asynchronous):
            assert result.routing["impact"].disposition == "automate"
            assert result.routing["impact"].value == score  # Never replace the provider's Score.
    else:
        with pytest.raises(JevError, match="contradicts the probability-weighted mean") as caught:
            await wb.run(design, "ticket", evaluator=MockEvaluator(body))
        saved = wb.storage.get(caught.value.run_id or "")
        assert saved.status == "failed" and not saved.routing
        with pytest.raises(ValueError, match="inconsistent score for impact"):
            module.evaluate("ticket", transport=sdk_transport(body, []), max_retries=0)
        with pytest.raises(ValueError, match="inconsistent score for impact"):
            await module.aevaluate("ticket", transport=sdk_transport(body, []), max_retries=0)


def _inject_offline_transport(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, requests: list[dict[str, Any]]
) -> None:
    def sync_client(**kwargs: Any) -> TypeSafeClient:
        kwargs["transport"] = sdk_transport(RESPONSE, requests)
        return TypeSafeClient(**kwargs)

    def async_client(**kwargs: Any) -> AsyncTypeSafeClient:
        kwargs["transport"] = sdk_transport(RESPONSE, requests)
        return AsyncTypeSafeClient(**kwargs)

    monkeypatch.setattr(module, "TypeSafeClient", sync_client)
    monkeypatch.setattr(module, "AsyncTypeSafeClient", async_client)
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-export-secret")


async def test_real_langchain_runnable_calls_the_original_sdk_rubric(
    design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_export(tmp_path, monkeypatch, design, "langchain")
    requests: list[dict[str, Any]] = []
    _inject_offline_transport(module, monkeypatch, requests)
    first = module.decision_runnable.invoke({"ticket": "Refund please"})
    second = await module.decision_runnable.ainvoke({"ticket": "Refund please"})
    assert first.model_dump() == second.model_dump()
    assert requests[0] == requests[1]
    assert requests[0]["questions"] == {
        key: value.model_dump(mode="json") for key, value in design.questions.items()
    }


async def test_real_pydantic_agent_executes_exported_tool(
    design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    module = load_export(tmp_path, monkeypatch, design, "pydantic-ai")
    requests: list[dict[str, Any]] = []
    _inject_offline_transport(module, monkeypatch, requests)
    calls = 0

    def agent_model(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(module.decision_tool.name, {"state": {"ticket": "Refund please"}})
                ]
            )
        assert "billing" in str(messages[-1])
        assert "routing" in str(messages[-1])
        return ModelResponse(parts=[TextPart("Tool result received.")])

    agent = Agent(FunctionModel(agent_model), tools=[module.decision_tool])
    result = await agent.run("Check this ticket")
    assert result.output == "Tool result received."
    assert calls == 2 and len(requests) == 1
    assert requests[0]["questions"] == {
        key: value.model_dump(mode="json") for key, value in design.questions.items()
    }


@pytest.mark.parametrize("existing", ["file", "symlink", "dangling-symlink"])
def test_private_export_is_atomic_and_never_clobbers(
    design: Template, tmp_path: Path, existing: str
) -> None:
    path = tmp_path / "decision.py"
    target = tmp_path / "keep.py"
    if existing == "file":
        path.write_text("keep")
    else:
        if existing == "symlink":
            target.write_text("keep")
        path.symlink_to(target)
    with pytest.raises(JevError, match="already exists"):
        write_export(design, path)
    if existing == "file":
        assert path.read_text() == "keep"
    else:
        assert path.is_symlink()
    if existing == "symlink":
        assert target.read_text() == "keep"
    new_path = write_export(design, tmp_path / "fresh.py")
    assert new_path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".*"))


def test_export_rejects_invalid_target_and_revalidates_template(
    design: Template, tmp_path: Path
) -> None:
    with pytest.raises(JevError, match=".py"):
        write_export(design, tmp_path / "decision.txt")
    invalid = design.model_copy(update={"questions": {}})
    with pytest.raises(ValueError):
        export_template(invalid)
