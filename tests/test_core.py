import asyncio
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import RESPONSE, MockEvaluator
from typesafe_sdk import JSONContent

from jevlab.core.client import Evaluation
from jevlab.core.credentials import Credentials
from jevlab.core.errors import JevError
from jevlab.core.models import Template
from jevlab.core.pricing import price
from jevlab.core.service import Workbench, parse_state
from jevlab.core.templates import dump_template, parse_template


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(name="../escape"),
        lambda d: d["questions"]["impact"].update(criteria=["Only one"]),
        lambda d: d["questions"]["route"].update(criteria={str(i): None for i in range(256)}),
        lambda d: d["questions"]["route"].update(instructions="  "),
        lambda d: d["thresholds"]["refund_requested"].update(no_at_or_below=0.95),
        lambda d: d["thresholds"].update(
            missing={"kind": "confidence", "automate_at_or_above": 0.5}
        ),
        lambda d: d["thresholds"].update(
            refund_requested={"kind": "confidence", "automate_at_or_above": 0.5}
        ),
        lambda d: d.update(unknown_field="not accepted"),
    ],
)
def test_invalid_designs(design: Template, mutation: Callable[[dict[str, Any]], None]) -> None:
    import yaml

    data = design.model_dump(mode="json")
    mutation(data)
    with pytest.raises(JevError):
        parse_template(yaml.safe_dump(data))


@pytest.mark.parametrize(
    "text",
    ["name: a\nname: b", "a: &a [*a]", "!!python/object/apply:os.system ['true']", "true: value"],
)
def test_unsafe_yaml_rejected(text: str) -> None:
    with pytest.raises(JevError):
        parse_template(text)


def test_roundtrip_and_failed_save_preserves_file(wb: Workbench, design: Template) -> None:
    assert parse_template(dump_template(design)) == design
    original = wb.templates.path(design.name).read_text()
    design.questions["impact"].instructions = None
    with pytest.raises(JevError):
        wb.templates.save(design, overwrite=True)
    assert wb.templates.path(design.name).read_text() == original


@pytest.mark.parametrize("text", ["null", "12", "true", '{"x": NaN}', "{invalid"])
def test_state_rejects_invalid_json(text: str) -> None:
    with pytest.raises(JevError):
        parse_state(text, "json")


async def test_real_sdk_mock_roundtrip(
    wb: Workbench, design: Template, evaluator: MockEvaluator
) -> None:
    result = await wb.run(design, {"ticket": {"message": "Refund me"}}, evaluator=evaluator)
    assert result.status == "succeeded"
    assert result.input_tokens == 1000
    assert result.cost_nanousd == 42000
    assert result.response and result.response["future_metadata"] == {"preserve": True}
    assert result.request_id == "test-request"
    routing = result.model_dump()["routing"]
    assert routing["refund_requested"]["value"] is True
    assert routing["route"]["disposition"] == "automate"
    assert routing["impact"]["disposition"] == "review"
    assert evaluator.requests[0]["model"] == "jev-1.13.0"
    assert "thresholds" not in evaluator.requests[0]
    assert wb.storage.get(result.id[:8]) == result
    assert b"offline-secret" not in wb.storage.path.read_bytes()


async def test_unknown_usage_is_not_free(wb: Workbench, design: Template) -> None:
    body = copy.deepcopy(RESPONSE)
    body["usage"] = {}
    run = await wb.run(design, "state", evaluator=MockEvaluator(body))
    assert run.cost_nanousd is None and run.input_tokens is None
    assert wb.storage.totals()[0]["unknown_cost_runs"] == 1
    assert price("future-model", 1000)[0] is None


async def test_alias_records_resolved_version(
    wb: Workbench, design: Template, evaluator: MockEvaluator
) -> None:
    design.model = "jev-latest"
    result = await wb.run(design, "state", evaluator=evaluator)
    assert result.requested_model == "jev-latest" and result.resolved_model == "jev-1.13.0"
    assert result.cost_nanousd == 42000


async def test_rerun_uses_immutable_revision(wb: Workbench, design: Template) -> None:
    first = await wb.run(design, "original state", evaluator=MockEvaluator())
    design.questions["route"].instructions = "Different question"
    wb.templates.save(design, overwrite=True)
    evaluator = MockEvaluator()
    replay = await wb.rerun(first.id, evaluator=evaluator)
    assert replay.parent_run_id == first.id
    assert replay.template_hash == first.template_hash
    assert evaluator.requests[0] == first.request
    assert len(wb.storage.history(search="original state", status="succeeded")) == 2
    assert wb.storage.totals("template")[0]["cost_nanousd"] == 84000


async def test_sdk_retries_rate_limit(wb: Workbench, design: Template) -> None:
    evaluator = MockEvaluator(statuses=[429, 200])
    await wb.run(design, "state", evaluator=evaluator)
    assert len(evaluator.requests) == 2


async def test_auth_error_is_safe_and_persisted(wb: Workbench, design: Template) -> None:
    evaluator = MockEvaluator({"error": "Rejected offline-secret"}, [401])
    with pytest.raises(JevError) as raised:
        await wb.run(design, "state", evaluator=evaluator)
    assert raised.value.code == "authentication"
    assert len(evaluator.requests) == 1
    saved = wb.storage.get(raised.value.run_id or "")
    assert saved.status == "failed" and saved.cost_nanousd is None
    assert "offline-secret" not in json.dumps(saved.model_dump())
    assert saved.error and saved.error["message"] == "Rejected [redacted]"


@pytest.mark.parametrize("broken", ["missing", "wrong_type", "bad_probability", "extra"])
async def test_semantic_validation_preserves_raw(
    wb: Workbench, design: Template, broken: str
) -> None:
    body = copy.deepcopy(RESPONSE)
    if broken == "missing":
        body["answers"].pop("impact")
    elif broken == "wrong_type":
        body["answers"]["route"] = {"type": "noul", "noul": 0.5}
    elif broken == "bad_probability":
        body["answers"]["route"]["probabilities"]["billing"] = 0.01
    else:
        body["answers"]["unexpected"] = {"type": "noul", "noul": 0.5}
    with pytest.raises(JevError) as error:
        await wb.run(design, "state", evaluator=MockEvaluator(body))
    saved = wb.storage.get(error.value.run_id or "")
    assert saved.status == "failed" and saved.response == body and saved.routing is None


async def test_sdk_response_validation_is_recorded(wb: Workbench, design: Template) -> None:
    body = copy.deepcopy(RESPONSE)
    body["answers"]["route"].pop("confidence")
    with pytest.raises(JevError) as error:
        await wb.run(design, "state", evaluator=MockEvaluator(body))
    assert wb.storage.get(error.value.run_id or "").response == body


async def test_cancel_records_uncertain_request(wb: Workbench, design: Template) -> None:
    entered = asyncio.Event()

    class HangingEvaluator:
        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    task = asyncio.create_task(wb.run(design, "state", evaluator=HangingEvaluator()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    record = wb.storage.history()[0]
    assert record.status == "interrupted" and record.cost_nanousd is None


class FakeKeyStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[username] = password


def test_keychain_precedence_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeKeyStore()
    credentials = Credentials(store=store)
    monkeypatch.setenv("TYPESAFE_API_KEY", "environment-secret")
    assert credentials.resolve() == ("environment-secret", "environment")
    credentials.save("typesafe", "keychain-secret")
    assert credentials.resolve() == ("keychain-secret", "keychain")
    assert "secret" not in json.dumps(credentials.status())
    assert Credentials("environment", store).require() == "environment-secret"


def test_core_has_no_ui_imports() -> None:
    import ast
    from importlib.util import resolve_name

    from jevlab import core

    root = Path(core.__file__).parent
    forbidden = ("textual", "rich", "typer", "jevlab.tui", "jevlab.cli", "jevlab.coach")
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        package = ".".join(("jevlab", "core", *path.relative_to(root).parts[:-1]))
        for node in ast.walk(tree):
            imports: list[str] = []
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            if isinstance(node, ast.ImportFrom):
                module = resolve_name("." * node.level + (node.module or ""), package)
                imports = [module, *(f"{module}.{item.name}" for item in node.names)]
            for imported in imports:
                assert not any(
                    imported == name or imported.startswith(name + ".") for name in forbidden
                ), f"{path.name}:{getattr(node, 'lineno', '?')} imports UI dependency {imported}"
