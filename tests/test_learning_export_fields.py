"""Learning, coach, and export fields explain and validate their local inputs."""

from pathlib import Path

import pytest
from conftest import MockEvaluator
from textual.widgets import Button, Collapsible, Input, Select, TextArea

from jev.coach.service import Coach
from jev.core.content import lesson
from jev.core.exporting import export_template
from jev.core.models import Template
from jev.core.service import Workbench
from jev.tui.app import JevApp
from jev.tui.fields import Field
from jev.tui.harness import ExportScreen, export_path_error
from jev.tui.learning import CoachScreen, LessonScreen, dataset_export_path


@pytest.mark.parametrize("simple", [True, False])
async def test_lesson_guidance_and_inline_field_validation(wb: Workbench, simple: bool) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple" if simple else "expert"}))
    app = JevApp(wb)
    async with app.run_test(size=(120, 48)) as pilot:
        screen = LessonScreen(wb, lesson("1"))
        app.push_screen(screen)
        await pilot.pause()
        for field in screen.query(Field):
            assert field.label and field.description and field.example
            assert field.query_one(Collapsible).collapsed is not simple
        name = screen.query_one("#lesson-template", Input)
        name.value = "bad name"
        await pilot.pause()
        assert "lowercase" in screen.query_one("#field-lesson-template", Field).error
        assert screen.query_one("#edit-draft", Button).disabled
        name.value = "my-practice"
        fields = screen.query_one("#lesson-fields", Input)
        fields.value = "missing_field"
        await pilot.pause()
        assert "Available:" in screen.query_one("#field-lesson-fields", Field).error
        fields.value = "ticket,ticket"
        await pilot.pause()
        assert "repeated" in screen.query_one("#field-lesson-fields", Field).error
        fields.value = ",".join(lesson("1").default_fields)
        await pilot.pause()
        assert not screen.query_one("#field-lesson-fields", Field).error
        assert not screen.query_one("#grade-lesson", Button).disabled
        assert not wb.storage.history()


async def test_coach_validation_tracks_mode_without_provider_calls(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = await wb.run(design, "Synthetic support question", evaluator=MockEvaluator())

    def unexpected_init(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid coach input must not initialize a provider request.")

    monkeypatch.setattr(Coach, "__init__", unexpected_init)
    app = JevApp(wb)
    async with app.run_test(size=(120, 48)) as pilot:
        screen = CoachScreen(wb)
        app.push_screen(screen)
        await pilot.pause()
        assert "goal" in screen.query_one("#field-coach-intent", Field).error.lower()
        screen.ask()
        await app.workers.wait_for_complete()
        screen.query_one("#coach-intent", TextArea).load_text("Route support messages by topic.")
        await pilot.pause()
        assert not screen.query_one("#ask-coach", Button).disabled
        target = screen.query_one("#coach-target", Input)
        target.value = "Not a slug"
        await pilot.pause()
        assert "lowercase" in screen.query_one("#field-coach-target", Field).error
        screen.query_one("#coach-mode", Select).value = "critique"
        target.value = "missing-design"
        await pilot.pause()
        field = screen.query_one("#field-coach-target", Field)
        assert field.label == "Design to review" and field.example == "support-triage"
        assert "No saved design" in field.error
        assert not screen.query_one("#field-coach-intent", Field).display
        target.value = design.name
        await pilot.pause()
        assert not field.error
        assert not screen.query_one("#ask-coach", Button).disabled
        screen.query_one("#coach-mode", Select).value = "explain"
        target.value = "missing-run"
        await pilot.pause()
        assert field.label == "Saved result ID"
        assert "No unique result" in field.error
        target.value = saved.id[:8]
        await pilot.pause()
        assert not field.error
        assert not screen.query_one("#ask-coach", Button).disabled


async def test_coach_can_still_review_an_unsaved_editor_draft(
    wb: Workbench, design: Template
) -> None:
    draft = design.model_copy(deep=True)
    draft.name = "not-yet-saved"
    app = JevApp(wb)
    async with app.run_test(size=(120, 48)) as pilot:
        screen = CoachScreen(wb, mode="critique", target=draft.name, template=draft)
        app.push_screen(screen)
        await pilot.pause()
        assert not screen.query_one("#field-coach-target", Field).error
        assert not screen.query_one("#ask-coach", Button).disabled
        assert draft.name not in wb.templates.names()


def test_export_path_rules_match_new_file_writer(tmp_path: Path) -> None:
    assert ".py" in (export_path_error("") or "")
    assert ".py" in (export_path_error(str(tmp_path / "decision.txt")) or "")
    destination = tmp_path / "new-project" / "decisions" / "support.py"
    assert export_path_error(str(destination)) is None
    existing = tmp_path / "existing.py"
    existing.write_text("# Keep this file.\n")
    assert "already exists" in (export_path_error(str(existing)) or "")
    assert "parent path is not a folder" in (export_path_error(str(existing / "child.py")) or "")
    dangling = tmp_path / "link.py"
    dangling.symlink_to(tmp_path / "absent")
    assert "already exists" in (export_path_error(str(dangling)) or "")
    assert "parent path is not a folder" in (export_path_error(str(dangling / "child.py")) or "")
    assert export_path_error("bad\x00path.py")


async def test_export_field_feedback_and_professional_output_are_preserved(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(120, 50)) as pilot:
        screen = ExportScreen(wb)
        app.push_screen(screen)
        await pilot.pause()
        for field in screen.query(Field):
            assert field.label and field.description and field.example
        field = screen.query_one("#field-export-path", Field)
        assert "Choose a new .py filename" in field.error
        assert screen.query_one("#export-save", Button).disabled
        path = screen.query_one("#export-path", Input)
        path.value = str(tmp_path / "wrong.txt")
        await pilot.pause()
        assert "need a .py filename" in field.error
        destination = tmp_path / "new-project" / "decision.py"
        path.value = str(destination)
        await pilot.pause()
        assert not field.error
        assert not screen.query_one("#export-save", Button).disabled
        screen.save()
        assert destination.read_text() == export_template(design)
        assert not wb.storage.history()


def test_dataset_export_prompt_requires_new_jsonl_file(tmp_path: Path) -> None:
    assert ".jsonl" in (dataset_export_path(str(tmp_path / "cases.csv")) or "")
    nested = tmp_path / "missing-folder" / "cases.jsonl"
    assert dataset_export_path(str(nested)) is None
    assert not nested.parent.exists()  # Validation itself must not create folders.
    output = tmp_path / "cases.jsonl"
    assert dataset_export_path(str(output)) is None
    output.write_text("{}\n")
    assert "already exists" in (dataset_export_path(str(output)) or "")
