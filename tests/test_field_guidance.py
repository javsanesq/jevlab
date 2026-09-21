"""Field guidance is usable in both modes and rejects bad drafts without API calls."""

from pathlib import Path

import pytest
from textual.widgets import Button, Collapsible, Input, Select, TextArea

from jev.core.service import Workbench
from jev.tui.app import JevApp
from jev.tui.editor import QuestionEditor, TemplateEditor
from jev.tui.evaluation import JobScreen
from jev.tui.fields import Field, input_file, numeric, output_file
from jev.tui.guidance import Explain
from jev.tui.screens import Playground, SettingsScreen


@pytest.mark.parametrize("mode", ["simple", "expert"])
async def test_each_main_form_input_has_label_description_example_and_mode_help(
    wb: Workbench,
    mode: str,
) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": mode}))
    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        for screen in (
            TemplateEditor(wb),
            Playground(wb, wb.templates.load("support-triage")),
            SettingsScreen(wb),
            JobScreen(wb, "eval"),
            JobScreen(wb, "batch"),
        ):
            await app.push_screen(screen)
            await pilot.pause()
            for control in screen.query("Input, Select, TextArea, Checkbox"):
                assert isinstance(control.parent, Field), control.id
                field = control.parent
                assert field.label and field.description
                assert field.example or (isinstance(control, Input) and control.password)
                assert field.query_one(Collapsible).collapsed is (mode == "expert")
                if isinstance(control, Input) and not control.password:
                    assert control.placeholder
            app.pop_screen()
            await pilot.pause()
        assert wb.storage.history() == []


async def test_mode_toggle_and_help_keep_draft_values(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        editor = TemplateEditor(wb)
        await app.push_screen(editor)
        value = editor.query_one("#description", Input)
        value.value = "A draft worth keeping"
        original = editor.snapshot()
        field = editor.query_one("#field-description", Field)
        assert field.query_one(Collapsible).collapsed
        field.query_one(Collapsible).collapsed = False
        assert editor.snapshot() == original
        value.focus()
        await pilot.press("ctrl+e")
        assert isinstance(app.screen, Explain)
        await pilot.press("escape")
        wb.update_settings(wb.settings.with_updates({"ui_mode": "simple"}))
        editor.apply_mode()
        assert not field.query_one(Collapsible).collapsed
        assert editor.snapshot() == original


@pytest.mark.parametrize(
    ("field_id", "invalid", "expected", "valid"),
    [
        ("template-name", "Bad Name", "lowercase", "ticket-routing"),
        ("description", " ", "empty", "Route incoming support messages."),
        ("state-description", " ", "empty", "A customer message."),
        ("template-model", "jev", "model family", "jev-latest"),
    ],
)
async def test_template_validates_input_next_to_field(
    wb: Workbench,
    field_id: str,
    invalid: str,
    expected: str,
    valid: str,
) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        editor = TemplateEditor(wb)
        await app.push_screen(editor)
        editor.query_one(f"#{field_id}", Input).value = invalid
        await pilot.pause()
        assert expected in editor.query_one(f"#field-{field_id}", Field).error
        assert editor.query_one("#save-template", Button).disabled
        editor.query_one(f"#{field_id}", Input).value = valid
        await pilot.pause()
        assert not editor.query_one(f"#field-{field_id}", Field).error
        assert not editor.query_one("#save-template", Button).disabled
        assert wb.storage.history() == []


async def test_template_json_and_threshold_errors_clear_on_first_valid_edit(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        editor = TemplateEditor(wb)
        await app.push_screen(editor)
        example = editor.query_one("#state-example", TextArea)
        example.load_text('{"ticket":')
        await pilot.pause()
        assert "line 1" in editor.query_one("#field-state-example", Field).error
        assert editor.query_one("#save-template", Button).disabled
        example.load_text('{"ticket": "A refund request."}')
        await pilot.pause()
        assert not editor.query_one("#save-template", Button).disabled
        gates = editor.query_one("#thresholds", TextArea)
        gates.load_text("route:\n  kind: confidence\n  automate_at_or_above: 1.5")
        await pilot.pause()
        assert "thresholds.route" in editor.query_one("#field-thresholds", Field).error
        assert editor.query_one("#save-template", Button).disabled
        gates.load_text("route:\n  kind: confidence\n  automate_at_or_above: 0.9")
        await pilot.pause()
        assert not editor.query_one("#field-thresholds", Field).error
        assert not editor.query_one("#save-template", Button).disabled


async def test_question_inline_duplicates_and_primitive_guidance(wb: Workbench) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple"}))
    app = JevApp(wb)
    async with app.run_test() as pilot:
        question = QuestionEditor(simple=True)
        await app.push_screen(question)
        await pilot.pause()
        question.query_one("#instructions", TextArea).load_text("Which team should handle this?")
        question.query_one("#criterion-name-1", Input).value = "matches"
        await pilot.pause()
        assert "repeats" in question.query_one("#field-criterion-name-1", Field).error
        assert question.query_one("#apply-question", Button).disabled

        question.query_one("#criterion-name-1", Input).value = "other"
        await pilot.pause()
        assert not question.query_one("#field-criterion-name-1", Field).error
        assert not question.query_one("#apply-question", Button).disabled
        explanation = question.query_one("#field-question-type", Field).description
        assert all(
            term in explanation for term in ("Choice:", "Score:", "Noul:", "lowest to highest")
        )
        question.query_one("#question-type", Select).value = "score"
        await pilot.pause()
        assert (
            "lowest to highest"
            in question.query_one("#field-criterion-description-0", Field).description
        )
        question.query_one("#criterion-description-0", Input).value = ""
        await pilot.pause()
        assert "empty" in question.query_one("#field-criterion-description-0", Field).error
        assert question.query_one("#apply-question", Button).disabled


async def test_duplicate_question_name_is_rejected_before_applying(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        editor = TemplateEditor(wb)
        await app.push_screen(editor)
        editor.edit_question("impact")
        await pilot.pause()
        question = app.screen
        assert isinstance(question, QuestionEditor)
        question.query_one("#question-id", Input).value = "route"
        await pilot.pause()
        assert "already uses" in question.query_one("#field-question-id", Field).error
        assert question.query_one("#apply-question", Button).disabled
        assert not question.query_one("#field-criteria", Field).error
        question.query_one("#question-id", Input).value = "impact"
        await pilot.pause()
        assert not question.query_one("#field-question-id", Field).error
        assert not question.query_one("#apply-question", Button).disabled


async def test_playground_json_location_and_format_change_validate_without_run(
    wb: Workbench,
) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        screen = Playground(wb, wb.templates.load("support-triage"))
        await app.push_screen(screen)
        screen.query_one("#state-editor", TextArea).load_text("{ticket: 'Refund'}")
        await pilot.pause()
        assert "line 1" in screen.query_one("#field-state-editor", Field).error
        assert "double quotes" in screen.query_one("#field-state-editor", Field).error
        assert screen.query_one("#run-jev", Button).disabled
        screen.query_one("#play-format", Select).value = "text"
        await pilot.pause()
        assert not screen.query_one("#field-state-editor", Field).error
        assert not screen.query_one("#run-jev", Button).disabled
        assert screen.query_one("#state-editor", TextArea).text == "{ticket: 'Refund'}"
        assert wb.storage.history() == []


@pytest.mark.parametrize(
    ("field_id", "invalid", "expected", "valid"),
    [
        ("timeout", "0", "greater than 0", "10"),
        ("retries", "1.5", "whole number", "2"),
        ("retention-days", "0", "at least 1", "90"),
        ("retention-bytes", "2", "1000000", "100000000"),
        ("coach-openai-model", "claude-opus-5", "other coach", "gpt-5.6-luna"),
    ],
)
async def test_settings_validate_locally_without_changing_saved_settings(
    wb: Workbench,
    field_id: str,
    invalid: str,
    expected: str,
    valid: str,
) -> None:
    original = wb.settings.model_dump()
    app = JevApp(wb)
    async with app.run_test() as pilot:
        screen = SettingsScreen(wb)
        await app.push_screen(screen)
        screen.query_one(f"#{field_id}", Input).value = invalid
        await pilot.pause()
        assert expected in screen.query_one(f"#field-{field_id}", Field).error
        assert screen.query_one("#save-settings", Button).disabled
        screen.query_one(f"#{field_id}", Input).value = valid
        await pilot.pause()
        assert not screen.query_one(f"#field-{field_id}", Field).error
        assert not screen.query_one("#save-settings", Button).disabled
        assert wb.settings.model_dump() == original


async def test_batch_paths_rate_and_resume_errors_show_before_execution(
    wb: Workbench, tmp_path: Path
) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        screen = JobScreen(wb, "batch")
        await app.push_screen(screen)
        screen.query_one("#dataset-path", Input).value = str(tmp_path / "missing.jsonl")
        screen.query_one("#output-path", Input).value = str(tmp_path / "missing" / "results.jsonl")
        screen.query_one("#job-rate", Input).value = "0"
        screen.query_one("#resume-id", Input).value = "no-such-job"
        await pilot.pause()
        assert "does not exist" in screen.query_one("#field-dataset-path", Field).error
        assert not screen.query_one("#field-output-path", Field).error
        assert not (tmp_path / "missing").exists()  # Creation is deferred until a job starts.
        assert "greater than 0" in screen.query_one("#field-job-rate", Field).error
        assert "Load for resume" in screen.query_one("#field-resume-id", Field).error
        assert wb.storage.history() == []


def test_path_and_numeric_validators_never_create_files(tmp_path: Path) -> None:
    assert numeric("Rate", 0, 1000, exclusive_minimum=True)("nan")
    assert numeric("Retries", 0, 5, integer=True)("1.5")
    assert "omit decimal points" in (numeric("Retries", 0, 5, integer=True)("4.0") or "")
    assert "exponents" in (numeric("Retries", 0, 5, integer=True)("4e0") or "")
    assert numeric("Retries", 0, 5, integer=True)("4") is None
    assert numeric("Rate", 0, 1000, exclusive_minimum=True)("0.25") is None
    source = tmp_path / "cases.csv"
    source.write_text("id,state\n1,hello\n")
    assert input_file(str(source)) is None
    target = tmp_path / "results.jsonl"
    assert output_file(str(target)) is None
    assert not target.exists()
