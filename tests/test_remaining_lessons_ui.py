"""Reliability, primitive-specific routing, and retained learning progress in the UI."""

import json

import pytest
from rich.console import Console
from test_remaining_lessons import LessonEvaluator
from textual.widgets import Button, DataTable, Static
from typer.testing import CliRunner

from jev.cli.app import app as cli
from jev.core.content import lesson, pattern, starter
from jev.core.learning import Learning
from jev.core.models import ConfidenceGate, NoulGate
from jev.core.service import Workbench
from jev.tui.app import JevApp
from jev.tui.learning import GradeScreen, LessonScreen
from jev.tui.screens import ResultScreen


def rendered(widget: Static) -> str:
    console = Console(width=160, color_system=None)
    with console.capture() as output:
        console.print(widget.content)
    return output.get()


async def test_lesson_six_distinguishes_probability_from_confidence_and_inspects_miss(
    wb: Workbench,
) -> None:
    item = lesson("6")
    report = await Learning(wb).grade(
        item,
        starter(item, "confidence-ui", wb.settings.model),
        evaluator=LessonEvaluator(pattern(item.pattern).cases, confident_miss=True),
    )
    app = JevApp(wb, start="learn")
    async with app.run_test(size=(140, 48)) as pilot:
        screen = GradeScreen(wb, report)
        app.push_screen(screen)
        await pilot.pause()
        probabilities = rendered(screen.query_one("#probability-route", Static))
        confidence = rendered(screen.query_one("#confidence-route", Static))
        assert "predicted chances match results" in probabilities
        assert "Mean confidence" in confidence and "Observed accuracy" in confidence
        assert "50.00%" in confidence  # The highest-confidence bin contains one miss.
        assert not screen.query("#confidence-refund_requested")
        assert any("no separate confidence" in rendered(widget) for widget in screen.query(Static))
        table = screen.query_one("#case-grades", DataTable)
        table.move_cursor(row=table.row_count - 1)
        table.focus()
        await pilot.press("enter")
        assert isinstance(app.screen, ResultScreen)


async def test_lesson_seven_shows_both_noul_boundaries_and_local_curves(wb: Workbench) -> None:
    item = lesson("7")
    template = starter(item, "routing-ui", wb.settings.model)
    template.thresholds = {
        "route": ConfidenceGate(automate_at_or_above=0.90),
        "refund_requested": NoulGate(no_at_or_below=0.10, yes_at_or_above=0.90),
    }
    evaluator = LessonEvaluator(pattern(item.pattern).cases)
    report = await Learning(wb).grade(item, template, evaluator=evaluator)
    app = JevApp(wb, start="learn")
    async with app.run_test(size=(140, 48)) as pilot:
        screen = GradeScreen(wb, report)
        app.push_screen(screen)
        await pilot.pause()
        saved = rendered(screen.query_one("#saved-routing", Static))
        assert "confidence ≥0.90" in saved
        assert "no ≤0.10; yes ≥0.90" in saved and "40.00%" in saved
        choice_curve = rendered(screen.query_one("#routing-curve-route", Static))
        noul_curve = rendered(screen.query_one("#routing-curve-refund_requested", Static))
        assert "confidence ≥0.00" in choice_curve
        assert "no ≤0.50; yes ≥0.50 *" in noul_curve
        assert "no ≤0.10; yes ≥0.90" in noul_curve
        assert "no ≤0.00; yes ≥1.00" in noul_curve
        assert any(
            "exact gap remains review" in rendered(widget) for widget in screen.query(Static)
        )
        assert len(evaluator.requests) == 5  # Rendering and previews cannot call Jev again.


async def test_pruned_attempt_preserves_progress_and_disables_inspection(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = lesson("6")
    report = await Learning(wb).grade(
        item,
        starter(item, "pruned-ui", wb.settings.model),
        evaluator=LessonEvaluator(pattern(item.pattern).cases),
    )
    with wb.storage.connect() as connection:
        connection.execute("DELETE FROM learn_attempts WHERE id=?", (report.id,))
    progress = wb.storage.learning_progress()[0]
    assert progress["completed"] == 1 and progress["attempts"] == 1
    assert progress["last_attempt_id"] is None
    app = JevApp(wb, start="learn")
    async with app.run_test(size=(140, 48)) as pilot:
        table = app.screen.query_one(DataTable)
        table.move_cursor(row=5)
        await pilot.press("enter")
        assert isinstance(app.screen, LessonScreen)
        assert "pruned" in rendered(app.screen.query_one("#lesson-status", Static))
        assert app.screen.query_one("#last-attempt", Button).disabled
        assert app.screen.report is None
    monkeypatch.setattr("jev.cli.learning.workbench", lambda: wb)
    result = CliRunner().invoke(cli, ["learn", "inspect", report.id, "--json"])
    assert result.exit_code == 2
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "attempt_not_found" and "Cleanup may prune" in error["fix"]
    result = CliRunner().invoke(cli, ["learn", "progress", "--json"])
    assert json.loads(result.stdout)["data"][0]["completed"] == 1


async def test_inspecting_a_run_pruned_while_report_is_open_stays_in_report(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = lesson("6")
    report = await Learning(wb).grade(
        item,
        starter(item, "pruned-run-ui", wb.settings.model),
        evaluator=LessonEvaluator(pattern(item.pattern).cases),
    )
    app = JevApp(wb, start="learn")
    async with app.run_test(size=(140, 48)) as pilot:
        screen = GradeScreen(wb, report)
        app.push_screen(screen)
        await pilot.pause()
        with wb.storage.connect() as connection:
            connection.execute("DELETE FROM runs WHERE id=?", (report.cases[0].run_id,))
        notices: list[str] = []

        def notify(message: str, **kwargs: object) -> None:
            notices.append(message)

        monkeypatch.setattr(screen, "notify", notify)
        screen.query_one("#case-grades", DataTable).focus()
        await pilot.press("enter")
        assert app.screen is screen
        assert "no longer available" in notices[0]
        assert report.fraction_correct == 1
