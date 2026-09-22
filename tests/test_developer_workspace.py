"""Developer entry and cross-field journeys, with no credentials or network access."""

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Select, Static, TextArea

from jevlab.core.credentials import Credentials
from jevlab.core.models import Template
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.editor import QuestionEditor, TemplateEditor
from jevlab.tui.guidance import DemoScreen, TourScreen
from jevlab.tui.screens import Home, Playground, SettingsScreen


def visible_button(screen: Home | TemplateEditor | Playground, selector: str) -> Button:
    button = screen.query_one(selector, Button)
    assert button.visible and button.display
    assert button.region.y >= 1
    assert button.region.bottom <= 23
    assert button.region.right <= 80
    return button


async def test_fresh_small_terminal_demo_new_save_and_playground(wb: Workbench) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple", "tour_completed": False}))
    # A user's damaged example cannot prevent creating a new, valid design.
    (wb.root / "templates" / "support-triage.yaml").write_text("not: a valid template\n")
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        assert isinstance(app.screen, Home)
        for name in ("playground", "edit", "new", "demo", "history", "settings"):
            visible_button(app.screen, f"#{name}")
        await pilot.click("#demo")
        assert isinstance(app.screen, DemoScreen)
        assert "RECORDED EXAMPLE" in str(app.screen.query_one("#demo-notice", Static).render())
        await pilot.press("escape", "ctrl+n")
        assert isinstance(app.screen, TemplateEditor)
        editor = app.screen
        assert list(editor.questions) == ["route"]
        visible_button(editor, "#save-template")
        visible_button(editor, "#edit-question")
        editor.query_one("#template-name", Input).value = "first-design"
        await pilot.press("ctrl+s")
        saved = wb.templates.load("first-design")
        assert len(saved.questions) == 1 and saved.thresholds == {}
        await app.push_screen(Playground(wb, saved))
        await pilot.pause()
        assert isinstance(app.screen, Playground)
        visible_button(app.screen, "#run-jev")
        assert wb.storage.history() == []


async def test_tour_remains_explicitly_available(wb: Workbench) -> None:
    app = JevApp(wb, start="tour")
    async with app.run_test(size=(80, 24)):
        assert isinstance(app.screen, TourScreen)


async def test_playground_context_survives_focus_scroll_and_template_change(
    wb: Workbench, design: Template
) -> None:
    variant = design.model_copy(update={"name": "routing-variant", "model": "jev-latest"})
    wb.templates.save(variant)
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        screen = Playground(wb, design)
        await app.push_screen(screen)
        await pilot.pause()
        context = screen.query_one("#play-context", Static)
        assert screen.focused is screen.query_one("#state-editor", TextArea)
        assert "support-triage" in str(context.content)
        assert context.region.y == 1 and context.region.height == 1
        screen.query_one("#play-template", Select).value = variant.name
        await pilot.pause()
        screen.query_one(VerticalScroll).scroll_end(animate=False)
        await pilot.pause()
        assert variant.name in str(context.content) and variant.model in str(context.content)
        assert "support-triage" not in str(context.content)
        assert context.region.y == 1 and context.region.height == 1
        visible_button(screen, "#run-jev")


@pytest.mark.parametrize(
    ("old_name", "new_kind"), [("route", "noul"), ("refund_requested", "choice")]
)
async def test_primitive_change_clears_incompatible_gate_and_saves(
    wb: Workbench, design: Template, old_name: str, new_kind: str
) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple"}))
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        editor = TemplateEditor(wb, design)
        await app.push_screen(editor)
        assert old_name in design.thresholds
        editor.edit_question(old_name)
        await pilot.pause()
        question = app.screen
        assert isinstance(question, QuestionEditor)
        question.query_one("#question-type", Select).value = new_kind
        await pilot.pause()
        question.query_one("#question-id", Input).value = "changed_question"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.screen is editor
        assert "human review" in str(editor.query_one("#threshold-notice", Static).content)
        assert not editor.query_one("#save-template", Button).disabled
        await pilot.press("ctrl+s")
        saved = wb.templates.load(design.name)
        assert saved.questions["changed_question"].type == new_kind
        assert old_name not in saved.questions
        assert old_name not in saved.thresholds and "changed_question" not in saved.thresholds
        assert len(saved.thresholds) == len(design.thresholds) - 1


async def test_environment_key_save_enables_the_source_it_advertises(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Store:
        def __init__(self) -> None:
            self.values: dict[tuple[str, str], str] = {}

        def set_password(self, service: str, username: str, password: str) -> None:
            self.values[(service, username)] = password

        def get_password(self, service: str, username: str) -> str | None:
            return self.values.get((service, username))

    store = Store()
    monkeypatch.setattr("jevlab.core.credentials.native_store", lambda: store)
    app = JevApp(wb)
    async with app.run_test(size=(80, 24)) as pilot:
        screen = SettingsScreen(wb)
        await app.push_screen(screen)
        assert str(screen.query_one("#save-key", Button).label) == "Save key and use Keychain"
        assert "Environment-only mode" in str(screen.query_one("#key-source", Static).content)
        screen.query_one("#api-key", Input).value = "synthetic-offline-credential"
        await screen.save_key().wait()
        await pilot.pause()
        assert screen.query_one("#api-key", Input).value == ""
        assert wb.settings.credential_mode == "keychain"
        assert Credentials(wb.settings.credential_mode).resolve()[1] == "keychain"
        assert "Keychain lookup is active" in str(
            screen.query_one("#settings-status", Static).content
        )
        assert wb.storage.history() == []


async def test_renaming_without_changing_primitive_preserves_gate(
    wb: Workbench, design: Template
) -> None:
    app = JevApp(wb)
    async with app.run_test() as pilot:
        editor = TemplateEditor(wb, design)
        await app.push_screen(editor)
        editor.edit_question("route")
        await pilot.pause()
        app.screen.query_one("#question-id", Input).value = "destination"
        app.screen.query_one("#instructions", TextArea).load_text("Which support team is needed?")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert editor.build().thresholds["destination"] == design.thresholds["route"]
