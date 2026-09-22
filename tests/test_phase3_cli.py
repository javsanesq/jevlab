"""Phase 3 CLI contracts against actual services and a mocked official SDK."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import MockEvaluator
from typer.testing import CliRunner
from typesafe_sdk import JSONContent

from jevlab.cli.app import app
from jevlab.core.client import Evaluator
from jevlab.core.models import Run, Template
from jevlab.core.service import Workbench

runner = CliRunner()


@pytest.fixture
def cli_jobs(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> tuple[Workbench, MockEvaluator, Path]:
    import jevlab.cli.evaluation as commands

    monkeypatch.setattr(commands, "workbench", lambda: wb)
    original = wb.run
    evaluator = MockEvaluator()

    async def mock_run(
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        return await original(
            template,
            state,
            evaluator=fake,
            parent_run_id=parent_run_id,
            run_id=run_id,
        )

    fake = evaluator
    monkeypatch.setattr(wb, "run", mock_run)
    path = wb.root.parent / "labeled.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "id": str(index),
                    "state": f"Ticket {index}: Please refund the duplicate charge.",
                    "expected": {
                        "route": "billing" if index == 0 else "technical",
                        "impact": 0,
                        "refund_requested": True,
                    },
                }
            )
            for index in range(2)
        )
        + "\n"
    )
    return wb, evaluator, path


def result_json(arguments: list[str], exit_code: int = 0) -> dict[str, object]:
    result = runner.invoke(app, [*arguments, "--json"])
    assert result.exit_code == exit_code, f"{result.stdout}\n{result.stderr}\n{result.exception}"
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


def test_dataset_registration_eval_reporting_and_threshold_safety(
    cli_jobs: tuple[Workbench, MockEvaluator, Path],
) -> None:
    wb, fake, path = cli_jobs
    imported = result_json(["datasets", "import", str(path), "--template", "support-triage"])
    assert imported["ok"]
    assert result_json(["datasets", "list"])["data"]
    assert result_json(["eval", "plan", "support-triage", str(path)])["ok"]
    assert not fake.requests
    result = runner.invoke(
        app,
        ["eval", "run", "support-triage", str(path), "--rate", "100", "--json"],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["data"]
    assert "estimated" in result.stderr
    assert len(fake.requests) == 2
    assert report["evaluation"]["per_question"]["route"]["accuracy"] == 0.5
    job_id = report["id"]
    assert result_json(["eval", "show", job_id])["ok"]
    assert result_json(["eval"])["data"]
    preview = result_json(["eval", "tune", job_id, "route", "--threshold", "0.8"])
    assert preview["data"]["stats"]["coverage"] == 1.0  # type: ignore[index]
    saved = result_json(["eval", "tune", job_id, "route", "--threshold", "0.9", "--save"])
    assert saved["data"]["saved"]  # type: ignore[index]
    assert wb.templates.load("support-triage").thresholds["route"].model_dump() == {
        "kind": "confidence",
        "automate_at_or_above": 0.9,
    }
    assert result_json(
        [
            "eval",
            "tune",
            job_id,
            "refund_requested",
            "--no-below",
            "0.1",
            "--yes-above",
            "0.9",
            "--save",
        ]
    )["ok"]
    wrong_gate = result_json(["eval", "tune", job_id, "refund_requested", "--threshold", "0.9"], 2)
    assert wrong_gate["error"]["code"] == "invalid_gate"  # type: ignore[index]
    design = wb.templates.load("support-triage")
    design.questions["route"].instructions = "A changed judgment."
    wb.templates.save(design, overwrite=True)
    stale = result_json(["eval", "tune", job_id, "route", "--threshold", "0.7", "--save"], 2)
    assert not stale["ok"]
    assert (
        wb.templates.load("support-triage").questions["route"].instructions == "A changed judgment."
    )
    human = runner.invoke(app, ["eval", "show", job_id])
    assert human.exit_code == 0, human.output
    assert "worst misses" in human.stdout and "actual" in human.stdout


def test_budget_gate_json_and_batch_resume_do_not_duplicate_success(
    cli_jobs: tuple[Workbench, MockEvaluator, Path],
) -> None:
    wb, fake, path = cli_jobs
    wb.update_settings(wb.settings.model_copy(update={"confirm_cost_usd": 0.0}))
    denied = runner.invoke(app, ["eval", "run", "support-triage", str(path), "--json"])
    assert denied.exit_code == 2
    assert json.loads(denied.stdout)["error"]["code"] == "cost_confirmation"
    assert not fake.requests and "estimated" in denied.stderr
    output = path.parent / "results.jsonl"
    report = result_json(
        [
            "batch",
            "support-triage",
            "--input",
            str(path),
            "--output",
            str(output),
            "--yes",
            "--rate",
            "100",
        ]
    )["data"]
    assert output.exists() and len(output.read_text().splitlines()) == 2
    assert len(fake.requests) == 2
    result_json(["batch", "--resume", report["id"], "--rate", "100"])  # type: ignore[index]
    assert len(fake.requests) == 2
    assert len(output.read_text().splitlines()) == 2
    assert result_json(["batch"])["data"]


def test_job_failure_retains_report_and_nonzero_exit(
    cli_jobs: tuple[Workbench, MockEvaluator, Path],
) -> None:
    _, fake, path = cli_jobs
    fake.statuses = [401]
    result = runner.invoke(
        app, ["eval", "run", "support-triage", str(path), "--rate", "100", "--json"]
    )
    assert result.exit_code == 4, result.output
    response = json.loads(result.stdout)
    assert response["ok"] is False and response["data"]["failed"] > 0
    assert response["error"]["code"] == "job_incomplete"
    assert result_json(["eval", "show", response["data"]["id"]])["ok"]
    fake.statuses = [200]
    resumed = result_json(
        [
            "eval",
            "run",
            "support-triage",
            str(path),
            "--resume",
            response["data"]["id"],
            "--retry-failed",
            "--rate",
            "100",
        ]
    )
    assert resumed["data"]["status"] == "completed"  # type: ignore[index]
    assert resumed["data"]["unknown_cost_runs"] >= 1  # type: ignore[index,operator]


def test_comparison_stdin_and_model_override_are_real_service_calls(
    cli_jobs: tuple[Workbench, MockEvaluator, Path],
) -> None:
    wb, fake, _ = cli_jobs
    result = runner.invoke(
        app,
        ["compare", "support-triage", "support-triage", "--state", "-", "--format", "text"],
        input="Please refund the duplicate payment.",
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["data"]
    assert report["status"] == "completed" and len(fake.requests) == 2
    assert report["differences"][0]["probability_deltas"]["billing"] == 0
    denied = result_json(
        [
            "compare",
            "support-triage",
            "support-triage",
            "--text",
            "Test state",
            "--format",
            "text",
            "--right-model",
            "jev-latest",
        ],
        2,
    )
    assert denied["error"]["code"] == "cost_confirmation"  # type: ignore[index]
    assert len(fake.requests) == 2
    assert len(wb.storage.history()) == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["eval", "run", "--json"],
        ["eval", "unknown", "--json"],
        ["batch", "support-triage", "--rate", "0", "--json"],
        ["batch", "--retry-failed", "--json"],
        ["compare", "support-triage", "support-triage", "--state", "-", "--format", "xml"],
    ],
)
def test_phase3_entrypoint_usage_errors_are_json(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "jevlab", *arguments],
        capture_output=True,
        text=True,
        input="State",
        cwd=tmp_path,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["ok"] is False
    assert "Traceback" not in result.stderr
