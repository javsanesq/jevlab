"""Offline user workflows across jobs, reports, sliders, and comparison."""

import json
from pathlib import Path

import pytest
from conftest import MockEvaluator
from textual.pilot import Pilot
from textual.widgets import DataTable, Input, Select, Static, TextArea
from textual.worker import Worker
from typesafe_sdk import JSONContent

from jevlab.core.client import Evaluator
from jevlab.core.jobs import BatchService
from jevlab.core.models import ConfidenceGate, Run, Template
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Confirm
from jevlab.tui.evaluation import (
    CompareResultScreen,
    CompareScreen,
    EvalScreen,
    JobScreen,
    ThresholdScreen,
    ThresholdSlider,
)


def dataset(tmp_path: Path) -> Path:
    path = tmp_path / "data.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "ticket-1",
                "state": {"ticket": "refund please"},
                "expected": {"route": "technical", "impact": 0, "refund_requested": True},
            }
        )
        + "\n"
    )
    return path


def mock_runs(wb: Workbench, monkeypatch: pytest.MonkeyPatch) -> MockEvaluator:
    evaluate = MockEvaluator()
    real = wb.run

    async def run(
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        return await real(
            template, state, evaluator=evaluate, parent_run_id=parent_run_id, run_id=run_id
        )

    monkeypatch.setattr(wb, "run", run)
    return evaluate


async def finish_job(
    app: JevApp, pilot: Pilot[None], preparation: Worker[None], *, approve: bool = True
) -> None:
    # prepare() starts a second worker. Wait for its registration before snapshotting
    # the worker manager; inference completion itself only schedules the report mount.
    await preparation.wait()
    await pilot.pause()
    if isinstance(app.screen, Confirm):
        if not approve:
            return
        await pilot.click("#discard")
    await app.workers.wait_for_complete()
    await pilot.pause()  # Drain screen/child events and wait for idle, with no timed sleep.


async def test_eval_preview_grade_inspect_and_slider(
    wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluator = mock_runs(wb, monkeypatch)
    path = dataset(tmp_path)
    app = JevApp(wb, start="eval")
    async with app.run_test(size=(120, 52)) as pilot:
        assert isinstance(app.screen, JobScreen)
        app.screen.query_one("#dataset-path", Input).value = str(path)
        await finish_job(app, pilot, app.screen.prepare())
        assert not evaluator.requests
        assert BatchService(wb).datasets()[0].rows == 1
        await finish_job(app, pilot, app.screen.prepare(run=True))
        assert isinstance(app.screen, EvalScreen)
        report = app.screen.report
        assert report is not None
        assert report.status == "completed"
        assert app.screen.query_one("#worst-misses", DataTable).row_count == 1
        app.screen.inspect_selected("#worst-misses")
        await pilot.pause()
        await pilot.press("escape")
        current = wb.templates.load("support-triage")
        current.thresholds["impact"] = ConfidenceGate(automate_at_or_above=0.73)
        wb.templates.save(current, overwrite=True)
        await app.push_screen(ThresholdScreen(wb, report))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ThresholdScreen)
        assert not screen.dirty
        slider = screen.query_one("#confidence-slider", ThresholdSlider)
        slider.focus()
        await pilot.press("end")
        assert slider.value == 100
        assert screen.dirty
        assert "0" in str(screen.query_one("#threshold-preview", Static).render())
        screen.save()
        gate = wb.templates.load("support-triage").thresholds["route"]
        assert isinstance(gate, ConfidenceGate) and gate.automate_at_or_above == 1
        impact_gate = wb.templates.load("support-triage").thresholds["impact"]
        assert isinstance(impact_gate, ConfidenceGate)
        assert impact_gate.automate_at_or_above == 0.73
        assert len(evaluator.requests) == 1
        screen.query_one("#threshold-question", Select).value = "refund_requested"
        await pilot.pause()
        assert screen.query_one("#no-slider", ThresholdSlider).display
        assert not slider.display
        await pilot.press("escape")
        assert isinstance(app.screen, EvalScreen)


async def test_batch_tui_output_and_saved_resume(
    wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluator = mock_runs(wb, monkeypatch)
    path = dataset(tmp_path)
    output = tmp_path / "out.jsonl"
    app = JevApp(wb, start="batch")
    async with app.run_test(size=(120, 52)) as pilot:
        assert isinstance(app.screen, JobScreen)
        app.screen.query_one("#dataset-path", Input).value = str(path)
        app.screen.query_one("#output-path", Input).value = str(output)
        await finish_job(app, pilot, app.screen.prepare(run=True))
        assert isinstance(app.screen, EvalScreen)
        assert output.exists()
        await pilot.press("escape")
        assert isinstance(app.screen, JobScreen)
        assert app.screen.query_one(DataTable).row_count == 1
        report = app.screen.selected()
        assert report
        app.screen.query_one("#resume-id", Input).value = report.id
        await finish_job(app, pilot, app.screen.prepare(run=True))
        assert len(evaluator.requests) == 1


async def test_compare_tui_same_state(wb: Workbench, monkeypatch: pytest.MonkeyPatch) -> None:
    evaluator = mock_runs(wb, monkeypatch)
    app = JevApp(wb, start="compare")
    async with app.run_test(size=(130, 48)) as pilot:
        assert isinstance(app.screen, CompareScreen)
        app.screen.query_one(TextArea).load_text('{"ticket":"refund please"}')
        app.screen.begin()
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        await pilot.click("#discard")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, CompareResultScreen)
        assert app.screen.report.status == "completed"
        assert evaluator.requests[0]["state"] == evaluator.requests[1]["state"]


async def test_tui_cost_confirmation_precedes_calls(
    wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluator = mock_runs(wb, monkeypatch)
    wb.settings.confirm_cost_usd = 0
    app = JevApp(wb, start="eval")
    async with app.run_test(size=(120, 52)) as pilot:
        assert isinstance(app.screen, JobScreen)
        screen = app.screen
        screen.query_one("#dataset-path", Input).value = str(dataset(tmp_path))
        await finish_job(app, pilot, screen.prepare(run=True), approve=False)
        assert isinstance(app.screen, Confirm)
        assert not evaluator.requests
        await pilot.click("#keep")
        await app.workers.wait_for_complete()
        assert not evaluator.requests
        await finish_job(app, pilot, screen.prepare(run=True), approve=False)
        assert isinstance(app.screen, Confirm)
        await pilot.click("#discard")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(evaluator.requests) == 1
        assert isinstance(app.screen, EvalScreen)


async def test_tui_resume_uses_saved_design_when_yaml_missing(
    wb: Workbench, tmp_path: Path, design: Template
) -> None:
    path = dataset(tmp_path)
    report = await BatchService(wb).run(design, path, kind="eval", evaluator=MockEvaluator())
    wb.templates.path(design.name).unlink()
    app = JevApp(wb, start="eval")
    async with app.run_test(size=(120, 48)) as pilot:
        assert isinstance(app.screen, JobScreen)
        app.screen.query_one("#resume-id", Input).value = report.id
        app.screen.query_one("#dataset-path", Input).value = str(path)
        await finish_job(app, pilot, app.screen.prepare(run=True))
        assert isinstance(app.screen, EvalScreen)
        assert app.screen.is_mounted
        assert app.screen.query_one("#eval-question", Select).is_mounted
        assert app.screen.query_one("#worst-misses", DataTable).row_count == 1
        assert len(wb.storage.history()) == 1
