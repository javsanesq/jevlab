"""Beginner controls preserve data, stay keyboard-accessible, and never spend implicitly."""

import json

import pytest
from conftest import MockEvaluator
from textual.widgets import Button, Collapsible, Input, Select, Static, TextArea

from jevlab.core.config import load_settings
from jevlab.core.demo import load_demo
from jevlab.core.models import Settings
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Confirm, ErrorDetails
from jevlab.tui.editor import QuestionEditor, TemplateEditor
from jevlab.tui.guidance import Explain, Glossary
from jevlab.tui.screens import Home, Playground, ResultScreen, SettingsScreen


def simple(wb: Workbench) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple"}))


def test_new_settings_default_to_simple_mode_and_unfinished_tour() -> None:
    settings = Settings()
    assert settings.ui_mode == "simple" and not settings.tour_completed


async def test_more_tools_reveals_features_without_changing_saved_mode(wb: Workbench) -> None:
    simple(wb)
    app = JevApp(wb)
    async with app.run_test(size=(100, 40)) as pilot:
        home = app.screen
        assert isinstance(home, Home) and home.has_class("simple-mode")
        tools = home.query_one("#home-tools", Collapsible)
        assert tools.collapsed
        tools.query_one("CollapsibleTitle").focus()
        await pilot.press("enter")
        assert not tools.collapsed
        assert home.query_one("#eval", Button).region.height > 0
        assert load_settings(wb.root).ui_mode == "simple"
        await pilot.press("enter")
        assert tools.collapsed and home.has_class("simple-mode")


async def test_explain_and_glossary_preserve_a_password_without_reading_it(wb: Workbench) -> None:
    simple(wb)
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        settings = SettingsScreen(wb)
        app.push_screen(settings)
        await pilot.pause()
        secret = "synthetic-password-do-not-read"
        entry = settings.query_one("#api-key", Input)
        entry.value = secret
        entry.focus()
        await pilot.press("ctrl+e")
        assert isinstance(app.screen, Explain)
        explanation = app.screen.explanation
        assert "key" in explanation.body.lower()
        assert secret not in explanation.body
        await pilot.press("ctrl+g")
        assert isinstance(app.screen, Glossary)
        await pilot.press("escape", "escape")
        assert app.screen is settings and entry.value == secret and entry.has_focus
        assert wb.storage.history() == []


async def test_settings_changes_mode_and_keeps_hidden_advanced_values(wb: Workbench) -> None:
    simple(wb)
    before = wb.settings.model_dump()
    app = JevApp(wb)
    async with app.run_test(size=(100, 38)) as pilot:
        settings = SettingsScreen(wb)
        app.push_screen(settings)
        await pilot.pause()
        assert not settings.query_one(".settings-advanced").display
        settings.query_one("#ui-mode", Select).value = "expert"
        await pilot.press("ctrl+s")
        assert settings.query_one(".settings-advanced").display
        assert load_settings(wb.root).ui_mode == "expert"
        for field in ("anthropic_model", "openai_model", "retention_bytes", "max_retries"):
            assert wb.settings.model_dump()[field] == before[field]
        await pilot.press("escape")
        # Home's secondary tools stay optional in either saved display mode.
        assert app.screen.query_one("#home-tools", Collapsible).collapsed


async def test_confirmation_palette_cannot_change_pending_request_settings(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        prompt = Confirm("Allow this request?", accept_label="Run", cancel_label="Cancel")
        app.push_screen(prompt)
        await pilot.pause()
        titles = [command.title for command in app.get_system_commands(prompt)]
        assert "Settings" not in titles and "New template" not in titles
        assert "Glossary" in titles and "Explain this" in titles


async def test_playground_starts_without_cost_prompt_and_keeps_post_run_metadata(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    simple(wb)

    from test_b_cli import use_evaluator

    fake = MockEvaluator()
    use_evaluator(wb, fake, monkeypatch)
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("enter")
        screen = app.screen
        assert isinstance(screen, Playground)
        await pilot.press("ctrl+r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, ResultScreen)
        assert len(fake.requests) == 1
        assert app.screen.result.input_tokens == 1000
        assert app.screen.result.output_tokens == 40
        assert app.screen.result.cost_nanousd == 42000
        assert app.screen.result.latency_ms is not None
        assert not screen.busy
        assert not screen.query_one("#state-editor", TextArea).read_only
        assert len(wb.storage.history()) == 1


async def test_result_explanations_are_free_and_raw_details_require_reveal(wb: Workbench) -> None:
    simple(wb)
    recording = load_demo()
    app = JevApp(wb)
    async with app.run_test(size=(100, 40)) as pilot:
        result = ResultScreen(wb, recording.run)
        app.push_screen(result)
        await pilot.pause()
        assert not result.query_one("#raw-json", Button).display
        assert result.query_one("#result-visuals").can_focus
        result.query_one("#result-visuals").focus()
        await pilot.press("ctrl+e")
        assert isinstance(app.screen, Explain)
        await pilot.press("escape")
        result.query_one("#result-options", Button).press()
        await pilot.pause()
        assert result.query_one("#raw-json", Button).display
        assert wb.storage.history() == []


async def test_simple_question_editor_saves_choice_without_yaml(wb: Workbench) -> None:
    simple(wb)
    app = JevApp(wb)
    async with app.run_test(size=(90, 35)) as pilot:
        editor = TemplateEditor(wb)
        app.push_screen(editor)
        await pilot.pause()
        editor.edit_question()
        await pilot.pause()
        question = app.screen
        assert isinstance(question, QuestionEditor) and question.simple
        assert not question.query_one("#criteria-yaml-fields").display
        question.query_one("#instructions", TextArea).text = "Which team should answer?"
        question.query_one("#criterion-name-0", Input).value = "billing"
        question.query_one("#criterion-description-0", Input).value = "Payments and refunds."
        question.query_one("#criterion-name-1", Input).value = "other"
        question.query_one("#criterion-description-1", Input).value = "All other requests."
        await pilot.press("ctrl+s")
        assert app.screen is editor
        built = editor.build()
        assert built.questions["question_2"].criteria == {
            "billing": "Payments and refunds.",
            "other": "All other requests.",
        }
        assert wb.storage.history() == []


@pytest.mark.parametrize("kind", ["score", "noul"])
async def test_simple_question_type_switch_rebuilds_descriptions(wb: Workbench, kind: str) -> None:
    simple(wb)
    app = JevApp(wb)
    async with app.run_test(size=(90, 35)) as pilot:
        question = QuestionEditor(simple=True)
        app.push_screen(question)
        await pilot.pause()
        question.query_one("#question-type", Select).value = kind
        await pilot.pause()
        assert question.friendly_rows == 2
        assert question.query_one("#criterion-name-0", Input).disabled
        criteria = question.criteria_value()
        assert isinstance(criteria, list if kind == "score" else dict)
        if kind == "noul":
            assert set(criteria) == {"true", "false"}


async def test_simple_text_example_is_saved_as_text(wb: Workbench) -> None:
    simple(wb)
    design = wb.templates.load("support-triage")
    design.state.format = "text"
    design.state.example = "A plain message."
    app = JevApp(wb)
    async with app.run_test() as pilot:
        editor = TemplateEditor(wb, design)
        app.push_screen(editor)
        await pilot.pause()
        assert editor.query_one("#state-example", TextArea).text == "A plain message."
        editor.query_one("#state-example", TextArea).text = "Please help."
        assert editor.build().state.example == "Please help."


async def test_switching_question_types_keeps_unfinished_answer_drafts(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        question = QuestionEditor(simple=True)
        app.push_screen(question)
        await pilot.pause()
        question.query_one("#criterion-name-0", Input).value = "my-category"
        question.query_one("#criterion-description-0", Input).value = "An unfinished description."
        question.query_one("#question-type", Select).value = "score"
        await pilot.pause()
        question.query_one("#criterion-description-0", Input).value = "Little impact."
        question.query_one("#question-type", Select).value = "choice"
        await pilot.pause()
        assert question.query_one("#criterion-name-0", Input).value == "my-category"
        assert (
            question.query_one("#criterion-description-0", Input).value
            == "An unfinished description."
        )
        question.query_one("#question-type", Select).value = "score"
        await pilot.pause()
        assert question.query_one("#criterion-description-0", Input).value == "Little impact."


async def test_errors_use_plain_shape_and_reopen_safely(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        app.notify("This field is empty.", severity="error")
        await pilot.press("f2")
        assert isinstance(app.screen, ErrorDetails)
        text = str(app.screen.query_one("#error-details", Static).content)
        assert all(label in text for label in ("What happened:", "Why:", "Next:"))
        await pilot.press("ctrl+g")
        assert isinstance(app.screen, Glossary)
        await pilot.press("escape", "escape")
        assert isinstance(app.screen, Home)


async def test_plain_explanations_leave_answer_and_routing_unchanged(wb: Workbench) -> None:
    from jevlab.core.guidance import explain_answer

    run = await wb.run(
        wb.templates.load("support-triage"), "A test message.", evaluator=MockEvaluator()
    )
    before = run.model_dump_json()
    for name in ("route", "impact", "refund_requested"):
        assert explain_answer(run, name)
    assert run.model_dump_json() == before
    assert json.loads(before)["routing"] == run.routing


async def test_stale_history_row_is_an_actionable_error(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jevlab.core.errors import JevError
    from jevlab.tui.screens import History

    await wb.run(wb.templates.load("support-triage"), "A test.", evaluator=MockEvaluator())
    app = JevApp(wb)
    async with app.run_test() as pilot:
        history = History(wb)
        app.push_screen(history)
        await pilot.pause()

        def gone(*args: object) -> None:
            raise JevError("not_found", "This saved run no longer exists.", "Refresh past results.")

        monkeypatch.setattr(wb.storage, "get", gone)
        history.inspect_run()
        await pilot.pause()
        assert app.screen is history
        assert "Why: This saved run no longer exists" in app.last_error
        await pilot.press("f2")
        assert isinstance(app.screen, ErrorDetails)


async def test_unhandled_tui_failure_never_renders_locals_or_traceback(
    wb: Workbench, capsys: pytest.CaptureFixture[str]
) -> None:
    app = JevApp(wb)
    with pytest.raises(RuntimeError, match="private-marker"):
        async with app.run_test() as pilot:
            app._handle_exception(RuntimeError("private-marker"))
            await pilot.pause()
    rendered = capsys.readouterr().err
    assert "What happened:" in rendered and "Next:" in rendered
    assert "private-marker" not in rendered and "Traceback" not in rendered
