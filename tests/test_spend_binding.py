"""Cost authorization applies only to the exact inputs reviewed by a person."""

import importlib
import json
from pathlib import Path

import pytest
from conftest import MockEvaluator
from typer.testing import CliRunner

from jevlab.cli.app import app
from jevlab.core.errors import JevError
from jevlab.core.jobs import BatchService, JobPlan
from jevlab.core.models import Template
from jevlab.core.service import Workbench


def write_cases(path: Path, count: int = 1, state: str = "Refund ticket") -> None:
    path.write_text(
        "".join(
            json.dumps(
                {
                    "id": str(index),
                    "state": state,
                    "expected": {"route": "billing", "impact": 0, "refund_requested": True},
                }
            )
            + "\n"
            for index in range(count)
        )
    )


@pytest.mark.parametrize(
    "change", ["more_rows", "same_size_state", "design", "settings", "invalid"]
)
async def test_changed_inputs_cannot_reuse_approval(
    change: str, wb: Workbench, design: Template, tmp_path: Path
) -> None:
    path, output = tmp_path / "cases.jsonl", tmp_path / "results.jsonl"
    write_cases(path)
    service = BatchService(wb)
    approved = service.plan(design, path)
    fake = MockEvaluator()
    if change == "more_rows":
        write_cases(path, 3)
    elif change == "same_size_state":
        write_cases(path, state="Repair ticket")
    elif change == "design":
        design.questions["route"].instructions = "Changed judgment."
    elif change == "settings":
        wb.update_settings(wb.settings.with_updates({"max_retries": 5}))
    else:
        path.write_text("not a JSON line\n")
    with pytest.raises(JevError) as caught:
        await service.run(
            design, path, output=output, authorize_cost=True, expected_plan=approved, evaluator=fake
        )
    if change != "invalid":
        assert caught.value.code == "cost_plan_changed"
    assert not fake.requests and not wb.storage.history() and not service.list()
    assert not output.exists()


async def test_fresh_approval_works_and_stays_out_of_public_schema(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    path = tmp_path / "cases.jsonl"
    write_cases(path)
    service, fake = BatchService(wb), MockEvaluator()
    plan = service.plan(design, path)
    assert set(plan.model_dump()) == {
        "calls",
        "remaining_calls",
        "estimated_input_tokens",
        "estimated_cost_nanousd",
        "requires_confirmation",
        "estimate_note",
    }
    assert "fingerprint" not in plan.model_dump_json()
    report = await service.run(
        design, path, authorize_cost=True, expected_plan=plan, evaluator=fake
    )
    assert report.status == "completed" and len(fake.requests) == 1
    assert service.get(report.id) == report
    resume_plan = service.plan(design, path, resume_id=report.id)
    assert resume_plan.remaining_calls == 0
    resumed = await service.run(
        design, path, resume_id=report.id, expected_plan=resume_plan, evaluator=fake
    )
    assert resumed.status == "completed" and len(fake.requests) == 1


async def test_old_serialized_report_is_not_a_new_spending_authorization(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    path = tmp_path / "cases.jsonl"
    write_cases(path)
    service, fake = BatchService(wb), MockEvaluator()
    serialized = JobPlan.model_validate(service.plan(design, path).model_dump())
    with pytest.raises(JevError, match="fresh estimate"):
        await service.run(
            design, path, authorize_cost=True, expected_plan=serialized, evaluator=fake
        )
    assert not fake.requests


async def test_resume_approval_binds_case_status_not_only_count_and_cost(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    path = tmp_path / "cases.jsonl"
    write_cases(path, 2)
    service = BatchService(wb)
    report = await service.run(
        design,
        path,
        evaluator=MockEvaluator(statuses=[401]),
        concurrency=1,
        requests_per_second=1000,
    )
    plan = service.plan(design, path, resume_id=report.id, retry_failed=True, retry_unknown=True)
    item = next(service.rows(report.id))
    item.status, item.run_id = "unknown", None
    wb.storage.save_job_item(report.id, item.model_dump())
    newer = service.plan(design, path, resume_id=report.id, retry_failed=True, retry_unknown=True)
    assert newer.model_dump() == plan.model_dump()  # Same advertised count and price.
    fake = MockEvaluator()
    with pytest.raises(JevError, match="changed"):
        await service.run(
            design,
            path,
            resume_id=report.id,
            retry_failed=True,
            retry_unknown=True,
            authorize_cost=True,
            expected_plan=plan,
            evaluator=fake,
        )
    assert not fake.requests


@pytest.mark.parametrize("command", ["batch", "eval"])
def test_cli_binds_confirmation_before_dispatch(
    command: str, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = importlib.import_module("jevlab.cli.evaluation")
    spending = importlib.import_module("jevlab.cli.spending")
    path, output = tmp_path / "cases.jsonl", tmp_path / "result.jsonl"
    write_cases(path)
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    for module in (commands, spending):
        monkeypatch.setattr(module, "interactive", lambda machine=False: not machine)

    def accept_after_file_changes(*args: object, **kwargs: object) -> bool:
        write_cases(path, 3)
        return True

    monkeypatch.setattr(spending.typer, "confirm", accept_after_file_changes)
    arguments = (
        ["batch", "support-triage", "--input", str(path), "--output", str(output)]
        if command == "batch"
        else ["eval", "run", "support-triage", str(path)]
    )
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 2, result.output
    assert "Run 1 live Jev decisions" in result.stderr
    assert "changed" in result.stderr and "No new request was sent" in result.stderr
    assert not wb.storage.history() and not BatchService(wb).list() and not output.exists()
