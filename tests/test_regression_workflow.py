"""Run the public evaluation-evidence workflow with the official SDK mocked offline."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import MockEvaluator
from typer.testing import CliRunner
from typesafe_sdk import JSONContent

from jevlab.cli.app import app
from jevlab.core.client import Evaluator
from jevlab.core.models import ConfidenceGate, Run, Template
from jevlab.core.service import Workbench
from jevlab.core.templates import dump_template

runner = CliRunner()


@pytest.fixture
def workflow(wb: Workbench, monkeypatch: pytest.MonkeyPatch) -> tuple[Workbench, MockEvaluator]:
    design = wb.templates.load("support-triage")
    design.questions = {"route": design.questions["route"]}
    design.thresholds = {"route": ConfidenceGate(automate_at_or_above=0.8)}
    wb.templates.save(design, overwrite=True)
    fake = MockEvaluator()
    fake.body["answers"] = {"route": fake.body["answers"]["route"]}
    original = wb.run

    async def mocked_run(
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        return await original(
            template, state, evaluator=fake, parent_run_id=parent_run_id, run_id=run_id
        )

    monkeypatch.setattr(wb, "run", mocked_run)
    monkeypatch.setattr("jevlab.cli.evaluation.workbench", lambda: wb)
    monkeypatch.setattr("jevlab.cli.regression.workbench", lambda: wb)
    return wb, fake


def _dataset(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    path.write_text(
        "".join(
            json.dumps({"id": identifier, "state": state, "expected": {"route": label}}) + "\n"
            for identifier, state, label in rows
        )
    )
    return path


def _json(arguments: list[str], exit_code: int = 0) -> dict[str, Any]:
    result = runner.invoke(app, [*arguments, "--json"])
    assert result.exit_code == exit_code, (result.stdout, result.stderr, result.exception)
    envelope = json.loads(result.stdout)
    assert envelope["schema_version"] == 1 and envelope["ok"] is (exit_code == 0)
    assert "Traceback" not in result.stderr
    if arguments[:2] != ["eval", "run"]:
        assert result.stderr == ""
    return envelope


def _eval(path: Path, *, exit_code: int = 0) -> str:
    return _json(["eval", "run", "support-triage", str(path), "--yes", "--rate", "100"], exit_code)[
        "data"
    ]["id"]


def _save_baseline(job_id: str, path: Path) -> None:
    result = _json(["eval", "baseline", job_id, "--output", str(path)])
    assert result["data"]["job_id"] == job_id and path.is_file()


def _freeze(job_id: str, path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _json(
        ["eval", "freeze", job_id, "--template", "support-triage", "--output", str(path)]
    )
    assert result["data"]["purpose"] == "tuning"
    # Keep ordering deterministic even when the CLI calls fall in one clock tick.
    policy = json.loads(path.read_text())
    after = datetime.fromisoformat(policy["frozen_at"]) + timedelta(milliseconds=1)
    monkeypatch.setattr("jevlab.core.jobs.now", lambda: after.isoformat())


def test_portable_baselines_reveal_regressed_cases_despite_equal_aggregate_accuracy(
    workflow: tuple[Workbench, MockEvaluator],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fake = workflow
    source = _dataset(
        tmp_path / "cases.jsonl",
        [
            ("invoice", "Synthetic duplicate charge", "billing"),
            ("login", "Synthetic login", "technical"),
        ],
    )
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    _save_baseline(_eval(source), before)
    fake.body["answers"]["route"].update(
        choice="technical", probabilities={"billing": 0.07, "technical": 0.9, "other": 0.03}
    )
    _save_baseline(_eval(source), after)
    assert len(fake.requests) == 4
    assert "Synthetic duplicate charge" not in before.read_text()
    assert str(source) not in before.read_text()
    source.unlink()

    def no_profile() -> Workbench:
        pytest.fail("Comparing portable evidence must not open a profile or its original data.")

    monkeypatch.setattr("jevlab.cli.regression.workbench", no_profile)
    monkeypatch.setattr("jevlab.cli.evaluation.workbench", no_profile)
    output = tmp_path / "comparison.json"
    envelope = _json(["eval", "compare", str(before), str(after), "--output", str(output)], 5)
    assert envelope["error"]["code"] == "quality_gate_failed"
    report = envelope["data"]
    question = report["per_question"]["route"]
    assert question["baseline_accuracy"] == question["candidate_accuracy"] == 0.5
    assert question["regressed"] == question["improved"] == 1
    assert {(row["case_id"], row["change"]) for row in report["changes"]} == {
        ("invoice", "regressed"),
        ("login", "improved"),
    }
    assert json.loads(output.read_text()) == report
    assert _json(["eval", "compare", str(before), str(after), "--max-regressions", "1"])["ok"]
    minimum = _json(["eval", "compare", str(before), str(before), "--min-accuracy", "0.75"], 5)
    assert minimum["data"]["per_question"]["route"]["regressed"] == 0
    assert any("accuracy 50.00%" in reason for reason in minimum["data"]["failures"])
    human = runner.invoke(app, ["eval", "compare", str(before), str(after)])
    assert human.exit_code == 5 and "FAIL" in human.stdout and "invoice" in human.stdout
    assert human.stderr == "" and len(fake.requests) == 4


def test_candidate_execution_failure_cannot_pass_permissive_quality_limits(
    workflow: tuple[Workbench, MockEvaluator], tmp_path: Path
) -> None:
    _, fake = workflow
    source = _dataset(tmp_path / "cases.jsonl", [("first", "Synthetic refund", "billing")])
    before = tmp_path / "baseline.json"
    _save_baseline(_eval(source), before)
    fake.statuses = [401]
    failed_job = _eval(source, exit_code=4)
    requests = len(fake.requests)
    result = _json(
        [
            "eval",
            "compare",
            str(before),
            failed_job,
            "--max-regressions",
            "100",
            "--min-accuracy",
            "0",
        ],
        5,
    )
    assert result["error"]["code"] == "quality_gate_failed"
    assert any(
        "Candidate evaluation has incomplete or failed calls" in value
        for value in result["data"]["failures"]
    )
    assert len(fake.requests) == requests


def test_tune_freeze_then_verify_on_separate_cases(
    workflow: tuple[Workbench, MockEvaluator], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb, fake = workflow
    tuning = _dataset(
        tmp_path / "tuning.jsonl", [("tune", "Synthetic duplicate charge", "billing")]
    )
    tuning_job = _eval(tuning)
    tuned = _json(["eval", "tune", tuning_job, "route", "--threshold", "0.75", "--save"])
    assert tuned["data"]["saved"] and tuned["data"]["stats"]["accuracy"] == 1
    policy = tmp_path / "policy.json"
    _freeze(tuning_job, policy, monkeypatch)
    assert (
        wb.templates.load("support-triage").thresholds["route"].model_dump()["automate_at_or_above"]
        == 0.75
    )
    holdout = _dataset(
        tmp_path / "holdout.jsonl", [("holdout", "Synthetic invoice cancellation", "billing")]
    )
    holdout_job = _eval(holdout)
    requests = len(fake.requests)
    output = tmp_path / "verified.json"
    result = _json(
        [
            "eval",
            "verify",
            str(policy),
            holdout_job,
            "--min-accuracy",
            "1",
            "--min-coverage",
            "1",
            "--output",
            str(output),
        ]
    )
    report = result["data"]
    assert report["purpose"] == "held_out_verification" and report["passed"]
    assert report["tuning_job"] == tuning_job
    assert report["verification"]["job_id"] == holdout_job
    assert report["per_question"]["route"]["accuracy"] == 1
    assert report["per_question"]["route"]["coverage"] == 1
    assert json.loads(output.read_text()) == report and len(fake.requests) == requests


def test_holdout_overlap_is_rejected_even_with_different_id_and_label(
    workflow: tuple[Workbench, MockEvaluator], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, fake = workflow
    state = "Synthetic state reused under another identity"
    tuning = _dataset(tmp_path / "tuning.jsonl", [("original-id", state, "billing")])
    policy = tmp_path / "policy.json"
    _freeze(_eval(tuning), policy, monkeypatch)
    holdout = _dataset(tmp_path / "holdout.jsonl", [("renamed-id", state, "technical")])
    holdout_job = _eval(holdout)
    requests = len(fake.requests)
    result = _json(["eval", "verify", str(policy), holdout_job, "--min-accuracy", "0"], 2)
    assert result["error"]["code"] == "holdout_overlap"
    assert "changing case IDs or labels" in result["error"]["fix"]
    assert len(fake.requests) == requests


def test_project_threshold_save_stays_in_project_and_no_automation_is_not_a_pass(
    workflow: tuple[Workbench, MockEvaluator], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb, fake = workflow
    design = wb.templates.load("support-triage")
    project = tmp_path / "decision.yaml"
    project.write_text(dump_template(design))
    tuning = _dataset(tmp_path / "tuning.jsonl", [("tune", "Synthetic tuning", "billing")])
    job = _eval(tuning)
    _json(
        ["eval", "tune", job, "route", "--threshold", "0.99", "--save", "--template", str(project)]
    )
    assert wb.templates.load("support-triage").thresholds["route"] == design.thresholds["route"]
    assert wb.templates.load_reference(project).thresholds["route"] == ConfidenceGate(
        automate_at_or_above=0.99
    )
    # Freeze the separately updated project file, then evaluate its unchanged policy.
    policy = tmp_path / "policy.json"
    _json(["eval", "freeze", job, "--template", str(project), "--output", str(policy)])
    parsed = json.loads(policy.read_text())
    after = datetime.fromisoformat(parsed["frozen_at"]) + timedelta(milliseconds=1)
    monkeypatch.setattr("jevlab.core.jobs.now", lambda: after.isoformat())
    holdout = _dataset(tmp_path / "holdout.jsonl", [("new", "Synthetic holdout", "billing")])
    result = _json(["eval", "run", str(project), str(holdout), "--yes", "--rate", "100"])
    checked = _json(["eval", "verify", str(policy), result["data"]["id"], "--min-accuracy", "0"], 5)
    assert checked["data"]["per_question"]["route"]["accuracy"] is None
    assert checked["data"]["per_question"]["route"]["automated"] == 0
    assert len(fake.requests) == 2


def test_baseline_rejects_changed_source_and_preserves_existing_files(
    workflow: tuple[Workbench, MockEvaluator], tmp_path: Path
) -> None:
    _, fake = workflow
    source = _dataset(tmp_path / "cases.jsonl", [("first", "Synthetic state", "billing")])
    job = _eval(source)
    artifact = tmp_path / "baseline.json"
    _save_baseline(job, artifact)
    original = artifact.read_bytes()
    assert (
        _json(["eval", "baseline", job, "--output", str(artifact)], 2)["error"]["code"]
        == "artifact_exists"
    )
    assert artifact.read_bytes() == original
    _dataset(source, [("first", "Edited state", "billing")])
    rejected = _json(["eval", "baseline", job, "--output", str(tmp_path / "new.json")], 2)
    assert rejected["error"]["code"] == "dataset_changed" and len(fake.requests) == 1


@pytest.mark.parametrize("changed", ["threshold", "instructions", "model", "resolved_model"])
def test_verification_rejects_design_policy_and_model_drift(
    workflow: tuple[Workbench, MockEvaluator],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    wb, fake = workflow
    tuning = _dataset(tmp_path / "tuning.jsonl", [("tune", "Synthetic tuning state", "billing")])
    policy = tmp_path / "policy.json"
    _freeze(_eval(tuning), policy, monkeypatch)
    design = wb.templates.load("support-triage")
    if changed == "threshold":
        design.thresholds["route"] = ConfidenceGate(automate_at_or_above=0.7)
    elif changed == "instructions":
        design.questions[
            "route"
        ].instructions = "Choose the team for this changed routing question."
    elif changed == "model":
        design.model = "jev-latest"
    else:
        fake.body["model"] = "jev-1.14.0"
    wb.templates.save(design, overwrite=True)
    holdout = _dataset(
        tmp_path / "holdout.jsonl", [("holdout", "Synthetic unseen state", "billing")]
    )
    holdout_job = _eval(holdout)
    requests = len(fake.requests)
    result = _json(["eval", "verify", str(policy), holdout_job], 2)
    assert result["error"]["code"] == (
        "model_drift" if changed == "resolved_model" else "policy_changed"
    )
    assert len(fake.requests) == requests
