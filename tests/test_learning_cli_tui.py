import json
import os
from pathlib import Path

import pytest
from test_learning import LabeledEvaluator
from textual.widgets import DataTable, Input, TextArea
from typer.testing import CliRunner
from typesafe_sdk import JSONContent

from jevlab.cli.app import app as cli
from jevlab.coach.service import Advice, Coach, CoachResult
from jevlab.core.client import Evaluator
from jevlab.core.config import save_settings
from jevlab.core.content import lesson, pattern
from jevlab.core.models import Run, Settings, Template
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Confirm
from jevlab.tui.editor import TemplateEditor
from jevlab.tui.learning import CoachScreen, GradeScreen, LearnScreen, LessonScreen, LibraryScreen
from jevlab.tui.screens import Playground, ResultScreen

runner = CliRunner()


def test_new_commands_have_json_and_fork_preserves_existing() -> None:
    root = Path(os.environ["JEVLAB_HOME"])
    save_settings(root, Settings(credential_mode="environment"))
    commands = [
        ["learn", "--json"],
        ["learn", "show", "1", "--json"],
        ["library", "--json"],
        ["library", "show", "groundedness", "--json"],
        ["coach", "--json"],
        ["learn", "progress", "--json"],
        ["learn", "start", "1", "--name", "practice", "--json"],
        ["learn", "plan", "1", "--template", "practice", "--json"],
        ["library", "fork", "groundedness", "my-grounding", "--json"],
    ]
    for command in commands:
        result = runner.invoke(cli, command)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["ok"]
    duplicate = runner.invoke(cli, ["library", "fork", "groundedness", "my-grounding", "--json"])
    assert duplicate.exit_code == 2
    grade = runner.invoke(cli, ["learn", "grade", "1", "--template", "practice", "--json"])
    assert grade.exit_code == 4
    data = json.loads(grade.stdout)["data"]
    assert data["status"] == "failed" and not data["passed"]
    assert "estimated" in grade.stderr
    coach = runner.invoke(cli, ["coach", "critique", "practice", "--json"])
    assert coach.exit_code == 3 and json.loads(coach.stdout)["error"]["code"] == "coach_disabled"


async def test_learning_editor_grade_case_inspection_and_resume(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_run = wb.run
    fake = LabeledEvaluator(pattern(lesson("1").pattern).cases)

    async def mocked_run(
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
    ) -> Run:
        return await original_run(template, state, evaluator=fake, parent_run_id=parent_run_id)

    monkeypatch.setattr(wb, "run", mocked_run)
    app = JevApp(wb, start="learn")
    async with app.run_test(size=(120, 48)) as pilot:
        assert isinstance(app.screen, LearnScreen)
        await pilot.press("enter")
        assert isinstance(app.screen, LessonScreen)
        await pilot.click("#edit-draft")
        assert isinstance(app.screen, TemplateEditor)
        await pilot.press("ctrl+s", "escape")
        assert isinstance(app.screen, LessonScreen)
        await pilot.click("#grade-lesson")
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        await pilot.click("#discard")
        await pilot.pause(0.6)
        assert isinstance(app.screen, GradeScreen)
        assert app.screen.report is not None
        assert app.screen.report.passed
        await pilot.click("#inspect-case")
        assert isinstance(app.screen, ResultScreen)
    restarted = Workbench(wb.root)
    assert restarted.storage.learning_progress()[0]["completed"] == 1


async def test_library_fork_and_example(wb: Workbench) -> None:
    app = JevApp(wb, start="library")
    async with app.run_test(size=(120, 42)) as pilot:
        assert isinstance(app.screen, LibraryScreen)
        assert app.screen.query_one(DataTable).row_count == 7
        await pilot.click("#try-pattern")
        assert isinstance(app.screen, Playground)
        await pilot.press("escape")
        await pilot.click("#fork-pattern")
        app.screen.query_one(Input).value = "my-library-fork"
        await pilot.press("enter")
        assert isinstance(app.screen, TemplateEditor)
        assert "my-library-fork" not in wb.templates.names()
        await pilot.press("ctrl+s")
        assert "my-library-fork" in wb.templates.names()


async def test_coach_proposal_requires_editor_save(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def proposed(self: Coach, intent: str, name: str) -> CoachResult:
        proposal = design.model_copy(deep=True)
        proposal.name = name
        return CoachResult(
            kind="design",
            provider="openai",
            model="test-model",
            advice=Advice(
                summary="Try a narrow judgment.",
                observations=["Use Choice."],
                next_experiment="Test one example.",
                template=proposal,
            ),
            input_tokens=10,
            output_tokens=10,
            latency_ms=10,
        )

    monkeypatch.setattr(Coach, "design", proposed)
    wb.update_settings(wb.settings.with_updates({"coach_provider": "openai"}))
    app = JevApp(wb, start="coach")
    async with app.run_test(size=(120, 42)) as pilot:
        assert isinstance(app.screen, CoachScreen)
        app.screen.query_one("#coach-intent", TextArea).load_text("Route support.")
        await pilot.click("#ask-coach")
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        await pilot.click("#discard")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "my-proposed-design" not in wb.templates.names()
        await pilot.click("#edit-proposal")
        assert isinstance(app.screen, TemplateEditor)
        await pilot.press("ctrl+s")
        assert "my-proposed-design" in wb.templates.names()
        assert not wb.storage.history()
