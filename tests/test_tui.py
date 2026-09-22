import pytest
from conftest import MockEvaluator
from textual.command import CommandPalette
from textual.widgets import DataTable, Input, Select, TextArea
from typesafe_sdk import JSONContent

from jevlab.core.models import Run, Template
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Confirm, Help
from jevlab.tui.editor import QuestionEditor, TemplateEditor
from jevlab.tui.screens import History, Home, Playground, ResultScreen, SettingsScreen


async def test_builder_question_and_unsaved_changes(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(120, 42)) as pilot:
        assert isinstance(app.screen, Home)
        await pilot.press("ctrl+n")
        assert isinstance(app.screen, TemplateEditor)
        app.screen.query_one("#template-name", Input).value = "my-template"
        app.screen.edit_question()
        await pilot.pause()
        assert isinstance(app.screen, QuestionEditor)
        app.screen.query_one("#question-id", Input).value = "has_deadline"
        app.screen.query_one("#question-type", Select).value = "noul"
        await pilot.pause()
        app.screen.query_one("#instructions", TextArea).load_text(
            "Does the customer state a deadline?"
        )
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, TemplateEditor)
        await pilot.press("ctrl+s")
        assert "has_deadline" in wb.templates.load("my-template").questions
        app.screen.query_one("#description", Input).value = "Unsaved edit"
        await pilot.press("escape")
        assert isinstance(app.screen, Confirm)
        await pilot.click("#keep")
        assert isinstance(app.screen, TemplateEditor)
        assert app.screen.query_one("#description", Input).value == "Unsaved edit"
        await pilot.press("ctrl+s", "escape")
        assert isinstance(app.screen, Home)
        assert wb.templates.load("my-template").description == "Unsaved edit"


async def test_mocked_playground_results_and_history(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_run = wb.run

    async def mocked_run(
        template: Template, state: JSONContent, *, parent_run_id: str | None = None
    ) -> Run:
        return await real_run(
            template, state, evaluator=MockEvaluator(), parent_run_id=parent_run_id
        )

    monkeypatch.setattr(wb, "run", mocked_run)
    app = JevApp(wb)
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.press("enter")
        assert isinstance(app.screen, Playground)
        await pilot.press("ctrl+r")
        await app.workers.wait_for_complete()
        await pilot.pause(0.4)
        assert isinstance(app.screen, ResultScreen)
        assert app.screen.result.status == "succeeded"
        await pilot.press("escape", "escape")
        assert isinstance(app.screen, Home)
        await pilot.click("#history")
        assert isinstance(app.screen, History)
        table = app.screen.query_one(DataTable)
        assert table.row_count == 1
        app.screen.query_one("#history-search", Input).value = "no such state"
        await pilot.pause()
        assert table.row_count == 0
        app.screen.query_one("#history-search", Input).value = ""
        await pilot.pause()
        assert table.row_count == 1
        await pilot.click("#inspect-run")
        assert isinstance(app.screen, ResultScreen)
        await pilot.click("#replay")
        assert isinstance(app.screen, Playground)
        assert app.screen.previous is not None


async def test_palette_help_and_settings(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.press("ctrl+p")
        assert isinstance(app.screen, CommandPalette)
        await pilot.press("escape", "f1")
        assert isinstance(app.screen, Help)
        await pilot.press("escape")
        await pilot.click("#settings")
        assert isinstance(app.screen, SettingsScreen)
        app.screen.query_one("#config-model", Input).value = "jev-latest"
        await pilot.press("ctrl+s")
        assert wb.settings.model == "jev-latest"


async def test_small_terminal_mount(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        assert isinstance(app.screen, Home)
        await pilot.press("ctrl+p")
        assert isinstance(app.screen, CommandPalette)


async def test_historical_state_format_and_draft_guard(wb: Workbench, design: Template) -> None:
    design.state.format = "text"
    state = {"ticket": {"message": "Please refund the duplicate charge."}}
    result = await wb.run(design, state, evaluator=MockEvaluator())
    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        app.push_screen(Playground(wb, design, result))
        await pilot.pause()
        assert app.screen.query_one("#play-format", Select).value == "json"
        app.screen.query_one("#state-editor", TextArea).load_text('{"changed": true}')
        await pilot.press("escape")
        assert isinstance(app.screen, Confirm)
        await pilot.click("#keep")
        assert isinstance(app.screen, Playground)
        assert app.screen.query_one("#state-editor", TextArea).text == '{"changed": true}'
        await pilot.press("escape")
        await pilot.click("#discard")
        assert isinstance(app.screen, Home)


async def test_quit_checks_unsaved_screen_under_history(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        app.push_screen(SettingsScreen(wb))
        await pilot.pause()
        settings = app.screen
        settings.query_one("#config-model", Input).value = "jev-latest"
        app.push_screen(History(wb))
        await pilot.pause()
        await pilot.press("ctrl+q")
        assert isinstance(app.screen, Confirm)
        await pilot.click("#keep")
        assert isinstance(app.screen, History)
        assert wb.settings.model == "jev-1.13.0"
