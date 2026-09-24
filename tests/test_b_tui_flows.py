"""Cancel-first spending and progressive disclosure in the advanced terminal flows."""

import json
import sqlite3
from pathlib import Path

import pytest
from conftest import MockEvaluator
from test_learning import LabeledEvaluator
from test_phase3_tui import dataset, mock_runs
from textual.widgets import Button, Checkbox, Input, Static, TextArea
from typesafe_sdk import JSONContent

from jevlab.coach.service import Coach
from jevlab.core.client import Evaluator
from jevlab.core.content import lesson, pattern, starter
from jevlab.core.jobs import JobPlan
from jevlab.core.models import Run, Template
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Confirm
from jevlab.tui.evaluation import CompareScreen, JobScreen
from jevlab.tui.harness import ExportScreen
from jevlab.tui.learning import CoachScreen, GradeScreen, LessonScreen


def simple(wb: Workbench) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple", "tour_completed": True}))


def ask_every_time(wb: Workbench) -> None:
    """A zero budget makes every priced request ask, exercising the dialog itself."""
    wb.update_settings(wb.settings.with_updates({"confirm_cost_usd": 0}))


@pytest.mark.parametrize("kind", ["batch", "eval"])
async def test_job_cost_prompt_defaults_to_cancel_above_budget_at_small_terminal(
    wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    ask_every_time(wb)
    simple(wb)
    evaluator = mock_runs(wb, monkeypatch)
    app = JevApp(wb, start=kind)
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(dataset(tmp_path))
        if kind == "batch":
            screen.query_one("#output-path", Input).value = str(tmp_path / "result.jsonl")
        await screen.prepare(run=True).wait()
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        assert "Estimated cost:" in app.screen.message
        assert app.focused is app.screen.query_one("#keep", Button)
        assert not evaluator.requests
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert not evaluator.requests and wb.storage.history() == []
        assert "Cancelled before starting" in str(screen.query_one("#job-status", Static).content)


async def test_compare_unknown_cost_is_explained_and_cancellable(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    simple(wb)
    evaluator = mock_runs(wb, monkeypatch)
    app = JevApp(wb, start="compare")
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        assert isinstance(screen, CompareScreen)
        screen.query_one("#left-model", Input).value = "unknown-model"
        screen.query_one(TextArea).load_text('{"ticket":"A billing question"}')
        screen.begin()
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        assert "cost is unknown" in app.screen.message
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert not evaluator.requests
        assert not screen.busy


@pytest.mark.parametrize("mode", ["design", "critique", "explain"])
async def test_every_coach_mode_confirms_before_request(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    ask_every_time(wb)
    simple(wb)
    wb.update_settings(wb.settings.with_updates({"coach_provider": "openai"}))
    target = "my-design" if mode == "design" else design.name
    if mode == "explain":
        saved = await wb.run(design, {"ticket": "Please refund me"}, evaluator=MockEvaluator())
        target = saved.id

    async def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Cancelling must not invoke the coach.")

    monkeypatch.setattr(Coach, "ask", forbidden)
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        screen = CoachScreen(wb, mode=mode, target=target)
        await app.push_screen(screen)
        screen.query_one("#coach-intent", TextArea).load_text("Route a support ticket.")
        screen.ask()
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        assert "optional coach" in app.screen.message
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert "Cancelled before asking" in str(screen.query_one("#coach-response", Static).content)
        assert screen.result is None and not screen.busy


async def test_lesson_can_cancel_before_any_jev_call(wb: Workbench) -> None:
    ask_every_time(wb)
    simple(wb)
    item = lesson("1")
    template = starter(item, f"lesson-{item.id}", wb.settings.model)
    wb.templates.save(template)
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        screen = LessonScreen(wb, item)
        await app.push_screen(screen)
        screen.begin_grade()
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        assert "example answers" in app.screen.message
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert wb.storage.history() == [] and screen.report is None


async def test_optional_feedback_has_separate_confirmation_and_keeps_grade(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_every_time(wb)
    simple(wb)
    wb.update_settings(wb.settings.with_updates({"coach_provider": "openai"}))
    item = lesson("1")
    template = starter(item, f"lesson-{item.id}", wb.settings.model)
    wb.templates.save(template)
    fake = LabeledEvaluator(pattern(item.pattern).cases)
    original_run = wb.run

    async def run(
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        return await original_run(
            template, state, evaluator=fake, parent_run_id=parent_run_id, run_id=run_id
        )

    async def no_feedback(*args: object, **kwargs: object) -> None:
        pytest.fail("Declining optional feedback must not call the coach.")

    monkeypatch.setattr(wb, "run", run)
    monkeypatch.setattr(Coach, "feedback", no_feedback)
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        screen = LessonScreen(wb, item)
        await app.push_screen(screen)
        screen.begin_grade()
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        await pilot.press("tab", "enter")
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        assert "already saved" in app.screen.message
        assert len(wb.storage.history()) == len(pattern(item.pattern).cases)
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, GradeScreen)
        assert app.screen.report.feedback is None
        assert wb.storage.learning_progress()[0]["attempts"] == 1


async def test_retrying_uncertain_rows_needs_separate_permission_before_price(
    wb: Workbench, design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_every_time(wb)
    simple(wb)
    app = JevApp(wb, start="eval")
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(dataset(tmp_path))
        monkeypatch.setattr(
            screen,
            "options",
            lambda: (
                design,
                dataset(tmp_path),
                {"resume_id": "saved-job", "retry_failed": False, "retry_unknown": True},
            ),
        )
        monkeypatch.setattr(
            screen.service,
            "plan",
            lambda *args, **kwargs: JobPlan(
                calls=1,
                remaining_calls=1,
                estimated_input_tokens=100,
                estimated_cost_nanousd=4200,
                requires_confirmation=False,
            ),
        )
        screen.execute()
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        assert "charge for the same example twice" in app.screen.message
        await pilot.press("tab", "enter")
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        assert "Estimated cost:" in app.screen.message
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert wb.storage.history() == []


async def test_more_options_reveals_saved_capabilities_in_simple_mode(wb: Workbench) -> None:
    simple(wb)
    app = JevApp(wb, start="eval")
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        assert isinstance(screen, JobScreen)
        assert screen.has_class("simple-mode")
        screen.query_one("#job-more", Button).focus()
        await pilot.press("enter")
        assert not screen.has_class("simple-mode")
        assert screen.query_one("#retry-unknown", Checkbox).display
        await app.push_screen(ExportScreen(wb))
        await pilot.pause()
        export = app.screen
        assert export.has_class("simple-mode")
        assert not export.query_one("#export-code", TextArea).display
        export.query_one("#export-more", Button).focus()
        await pilot.press("enter")
        assert export.query_one("#export-code", TextArea).display
        assert "TypeSafeClient" in export.query_one("#export-code", TextArea).text


async def test_invalid_file_error_has_what_why_next(wb: Workbench, tmp_path: Path) -> None:
    simple(wb)
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps({"wrong": "shape"}) + "\n")
    app = JevApp(wb, start="eval")
    async with app.run_test(size=(80, 24)):
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(path)
        await screen.prepare().wait()
        message = str(screen.query_one("#job-status", Static).content)
        assert all(text in message for text in ("Error:", "Next:"))
        assert "Traceback" not in message


@pytest.mark.parametrize("error", [sqlite3.OperationalError("secret"), RuntimeError("secret")])
async def test_preview_worker_failure_surfaces_safely_and_restores_controls(
    wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    simple(wb)
    app = JevApp(wb, start="eval")
    async with app.run_test(size=(80, 24)):
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(dataset(tmp_path))

        def fail(*args: object, **kwargs: object) -> None:
            raise error

        monkeypatch.setattr(screen.service, "plan", fail)
        await screen.prepare().wait()
        message = str(screen.query_one("#job-status", Static).content)
        assert ("What happened:" in message or "Error:" in message) and "Next:" in message
        assert "jevlab doctor" in message
        assert "secret" not in message and "secret" not in app.last_error
        assert "Details:" in app.last_error
        assert not screen.busy
        assert not screen.query_one("#job-run", Button).disabled


async def test_comparison_worker_failure_does_not_leave_a_running_message(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    simple(wb)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("private internal context")

    monkeypatch.setattr("jevlab.tui.evaluation.comparison_plan", fail)
    app = JevApp(wb, start="compare")
    async with app.run_test(size=(80, 24)):
        screen = app.screen
        assert isinstance(screen, CompareScreen)
        await screen.execute().wait()
        message = str(screen.query_one("#compare-status", Static).content)
        assert "internal error" in message and "private internal context" not in message
        assert not screen.busy and not screen.query_one("#compare-run", Button).disabled
