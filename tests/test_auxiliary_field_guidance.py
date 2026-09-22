"""Guidance on dialogs, comparison and search works before submitting a request."""

from pathlib import Path

from textual.widgets import Input, Select, TextArea

from jevlab.core.service import Workbench
from jevlab.core.templates import dump_template
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Prompt, YamlEditor, state_file
from jevlab.tui.evaluation import CompareScreen
from jevlab.tui.fields import Field
from jevlab.tui.guidance import Explain, Glossary


async def test_path_dialog_explains_and_blocks_missing_file(wb: Workbench, tmp_path: Path) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple"}))
    app = JevApp(wb)
    selected: list[str | None] = []
    async with app.run_test(size=(80, 24)) as pilot:
        dialog = Prompt(
            "Information file",
            description="Read text or JSON into the Playground.",
            example="~/Downloads/ticket.json",
            validator=state_file,
        )
        app.push_screen(dialog, selected.append)
        await pilot.pause()
        field = dialog.query_one(Field)
        assert "Choose a text or JSON file" in field.error
        dialog.query_one(Input).value = str(tmp_path / "missing.json")
        await pilot.pause()
        assert "does not exist" in field.error
        await pilot.press("enter")
        assert app.screen is dialog and selected == []
        await pilot.press("ctrl+e")
        assert isinstance(app.screen, Explain)
        assert "Information file" in app.screen.explanation.title
        await pilot.press("escape")
        source = tmp_path / "ticket.json"
        source.write_text('{"ticket": "A duplicate charge"}')
        dialog.query_one(Input).value = str(source)
        await pilot.pause()
        assert not field.error
        await pilot.press("enter")
        assert selected == [str(source)]


async def test_yaml_validation_is_visible_before_apply(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        editor = YamlEditor(dump_template(wb.templates.load("support-triage")))
        app.push_screen(editor)
        await pilot.pause()
        editor.query_one(TextArea).load_text("name: [unfinished")
        await pilot.pause()
        assert editor.query_one(Field).error
        assert app.screen is editor
        editor.query_one(TextArea).load_text(dump_template(wb.templates.load("support-triage")))
        await pilot.pause()
        assert not editor.query_one(Field).error


async def test_compare_validates_model_and_format_without_requests(wb: Workbench) -> None:
    app = JevApp(wb, start="compare")
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        assert isinstance(screen, CompareScreen)
        screen.query_one("#left-model", Input).value = "jev"
        screen.query_one("#compare-state", TextArea).load_text("not JSON")
        await pilot.pause()
        assert "model family" in screen.query_one("#field-left-model", Field).error
        assert "JSON" in screen.query_one("#field-compare-state", Field).error
        screen.begin()
        assert app.screen is screen and not screen.busy
        assert wb.storage.history() == []
        screen.query_one("#left-model", Input).value = ""
        screen.query_one("#compare-format", Select).value = "text"
        await pilot.pause()
        assert not screen.query_one("#field-left-model", Field).error
        assert not screen.query_one("#field-compare-state", Field).error
        screen.query_one("#compare-left", Select).value = Select.NULL
        await pilot.pause()
        assert "Choose a saved design" in screen.query_one("#field-compare-left", Field).error
        screen.begin()
        assert app.screen is screen and not screen.busy
        assert wb.storage.history() == []


async def test_glossary_filter_accepts_free_text_and_returns_to_form(wb: Workbench) -> None:
    app = JevApp(wb, start="compare")
    async with app.run_test(size=(80, 24)) as pilot:
        previous = app.screen
        previous.query_one("#compare-state", TextArea).load_text('{"ticket": "refund"}')
        await pilot.press("ctrl+g")
        assert isinstance(app.screen, Glossary)
        field = app.screen.query_one(Field)
        app.screen.query_one(Input).value = "confidence"
        await pilot.pause()
        assert field.label and field.description and field.example
        assert not field.error
        await pilot.press("escape")
        assert app.screen is previous
        assert previous.query_one(TextArea).text == '{"ticket": "refund"}'
