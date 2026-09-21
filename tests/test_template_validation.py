"""Prevent malformed model requests and preserve actionable local validation."""

import json
from pathlib import Path

import pytest
import yaml
from conftest import MockEvaluator
from pydantic import ValidationError
from textual.widgets import Button, Input, Static, TextArea
from typer.testing import CliRunner

from jev.cli.app import app as cli
from jev.core.config import load_settings
from jev.core.errors import JevError
from jev.core.models import Run, Settings, Template
from jev.core.service import Workbench
from jev.core.storage import now
from jev.core.templates import dump_template, parse_template, revision_hash, validation_message
from jev.tui.app import JevApp
from jev.tui.editor import QuestionEditor, TemplateEditor
from jev.tui.screens import SettingsScreen


@pytest.mark.parametrize(
    "model", ["jev", "", " ", "jev latest", "jev-latest\n", "```jev-latest```"]
)
def test_reject_malformed_model_in_template_and_settings(design: Template, model: str) -> None:
    data = design.model_dump(mode="json")
    data["model"] = model
    with pytest.raises(JevError) as error:
        parse_template(yaml.safe_dump(data))
    assert "model" in error.value.message
    assert "jev-latest" in error.value.message
    with pytest.raises(ValidationError, match="jev-latest"):
        Settings(model=model)


@pytest.mark.parametrize("model", ["jev-latest", "jev-preview", "jev-1.13.0", "jev-9.4.2-beta"])
def test_model_validation_is_not_a_version_allowlist(design: Template, model: str) -> None:
    design.model = model
    assert parse_template(dump_template(design)).model == model
    assert Settings(model=model).model == model


def test_invalid_config_identifies_model_and_repair(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('model = "jev"\n')
    with pytest.raises(JevError) as error:
        load_settings(tmp_path)
    assert "model: " in error.value.message
    assert "model family" in error.value.message
    assert "jev-latest" in str(error.value)


def test_validation_messages_do_not_echo_sensitive_inputs(design: Template) -> None:
    data = design.model_dump(mode="json")
    data["model"] = "sk-offline-example-not-a-real-key"
    with pytest.raises(JevError) as error:
        parse_template(yaml.safe_dump(data))
    assert "sk-offline" not in str(error.value)
    assert "credentials" in str(error.value)
    with pytest.raises(yaml.YAMLError) as syntax:
        yaml.safe_load("key: !sk-offline-example-not-a-real-key value")
    message = validation_message(syntax.value)
    assert "Invalid YAML" in message and "[redacted]" in message
    assert "sk-offline" not in message


async def test_historical_bad_model_is_inspectable_but_never_sent(
    wb: Workbench, design: Template
) -> None:
    design.model = "jev"
    run = Run(
        id="historical-model-error",
        template_hash=revision_hash(design),
        template_name=design.name,
        started_at=now(),
        requested_model="jev",
        request={"model": "jev", "state": "synthetic state"},
        sdk_version="test",
    )
    wb.storage.create_run(run, design)
    saved = wb.storage.template_for(wb.storage.get(run.id))
    assert saved.model == "jev"
    evaluator = MockEvaluator()
    with pytest.raises(JevError, match="model family"):
        await wb.rerun(run.id, evaluator=evaluator)
    assert not evaluator.requests
    assert wb.storage.template_for(run).model == "jev"


def test_historical_read_preserves_previously_permitted_score_whitespace(design: Template) -> None:
    data = design.model_dump(mode="json")
    data["questions"]["impact"]["criteria"] = [" ", "Severe"]
    serialized = yaml.safe_dump(data)
    assert parse_template(serialized, historical=True).questions["impact"].criteria == [
        " ",
        "Severe",
    ]
    with pytest.raises(JevError, match="level 0 has no description"):
        parse_template(serialized)


def test_cli_invalid_model_reports_specific_reason_without_request(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_HOME", str(wb.root))
    design.model = "jev"
    wb.templates.path(design.name).write_text(dump_template(design))
    result = CliRunner().invoke(
        cli, ["run", design.name, "--state", "-", "--json"], input='{"message":"synthetic"}'
    )
    assert result.exit_code == 2
    error = json.loads(result.stdout)["error"]
    assert "model family" in error["message"]
    assert "jev-latest" in error["message"]
    assert wb.storage.history() == []


def test_cli_invalid_default_model_explains_repair_and_preserves_config(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_HOME", str(wb.root))
    original = (wb.root / "config.toml").read_bytes()
    result = CliRunner().invoke(cli, ["config", "--set", "model=jev", "--json"])
    assert result.exit_code == 2
    error = json.loads(result.stdout)["error"]
    assert "model family" in error["message"]
    assert "model=jev-latest" in error["fix"]
    assert (wb.root / "config.toml").read_bytes() == original


@pytest.mark.parametrize("criteria", [[" ", "Severe"], ["Mild", "\t\n"]])
def test_score_blank_level_has_specific_location(design: Template, criteria: list[str]) -> None:
    data = design.model_dump(mode="json")
    data["questions"]["impact"]["criteria"] = criteria
    with pytest.raises(JevError) as error:
        parse_template(yaml.safe_dump(data))
    assert "Score impact level" in error.value.message
    assert "Describe when that level applies" in error.value.message


async def test_model_fields_validate_while_editing(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(120, 42)) as pilot:
        for screen, field, status, button in [
            (TemplateEditor(wb), "template-model", "model-validation", "save-template"),
            (SettingsScreen(wb), "config-model", "config-model-validation", "save-settings"),
        ]:
            app.push_screen(screen)
            await pilot.pause()
            screen.query_one(f"#{field}", Input).value = "jev"
            await pilot.pause()
            assert "model family" in str(screen.query_one(f"#{status}", Static).render())
            assert "jev-latest" in str(screen.query_one(f"#{status}", Static).render())
            assert screen.query_one(f"#{button}", Button).disabled
            screen.query_one(f"#{field}", Input).value = "jev-latest"
            await pilot.pause()
            assert not screen.query_one(f"#{button}", Button).disabled
            app.pop_screen()
            await pilot.pause()


@pytest.mark.parametrize(
    ("field", "text", "expected"),
    [
        ("instructions", "", "needs explicit instructions"),
        ("instructions", '{"question":', "Invalid JSON at line 1"),
        ("criteria", "[Only one]", "needs 2–10 levels"),
        ("criteria", "[Mild, '   ']", "Score impact level 1 has no description"),
        ("criteria", "[Mild,", "Invalid YAML at line"),
        ("criteria", "wrong: shape", "criteria"),
    ],
)
async def test_question_editor_retains_specific_edit_time_failure(
    wb: Workbench, design: Template, field: str, text: str, expected: str
) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(120, 42)) as pilot:
        editor = QuestionEditor("impact", design.questions["impact"])
        app.push_screen(editor)
        await pilot.pause()
        editor.query_one(f"#{field}", TextArea).load_text(text)
        await pilot.pause()
        assert expected in str(editor.query_one("#question-validation", Static).render())
        assert editor.query_one("#apply-question", Button).disabled
        assert app.screen is editor
