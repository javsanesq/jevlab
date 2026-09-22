"""Project YAML stays in its repository through run, edit, and export workflows."""

import json
from pathlib import Path

import pytest
from conftest import MockEvaluator
from test_b_cli import use_evaluator
from textual.widgets import Input, Static
from typer.testing import CliRunner

from jevlab.cli.app import app as cli
from jevlab.core.errors import JevError
from jevlab.core.models import Template
from jevlab.core.service import Workbench
from jevlab.core.templates import dump_template, parse_template
from jevlab.tui.app import JevApp
from jevlab.tui.editor import TemplateEditor


@pytest.fixture
def project_file(tmp_path: Path, design: Template) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    design.name = "project-routing"
    path = project / "decision.yaml"  # Filename intentionally differs from the design name.
    path.write_text(dump_template(design))
    return path


def test_explicit_project_path_and_named_catalog_are_unambiguous(
    wb: Workbench, project_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project_file.parent)
    original = wb.templates.load("support-triage")
    Path("support-triage").write_text("not: a design")
    assert wb.templates.load_reference("support-triage") == original
    resolved = wb.templates.resolve("decision.yaml")
    assert resolved.project_file and resolved.path == project_file
    assert resolved.template.name == "project-routing"
    assert wb.templates.load_reference(Path("decision.yaml")) == resolved.template
    assert "project-routing" not in wb.templates.names()
    with pytest.raises(JevError, match="Invalid template name"):
        wb.templates.load("decision.yaml")  # Server/catalog lookups never open local paths.


@pytest.mark.parametrize(
    ("reference", "code"),
    [("./missing.yaml", "not_found"), ("./decision.txt", "invalid_template_path")],
)
def test_project_path_errors_are_actionable(
    wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str, code: str
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(JevError) as caught:
        wb.templates.resolve(reference)
    assert caught.value.code == code
    assert caught.value.fix


@pytest.mark.parametrize("external_change", ["modified", "deleted"])
def test_project_save_rejects_external_changes_and_retains_them(
    wb: Workbench, project_file: Path, external_change: str
) -> None:
    source = wb.templates.resolve(project_file)
    source.template.description = "Unsaved editor draft."
    external = "# Changed by another editor\n" + project_file.read_text()
    if external_change == "modified":
        project_file.write_text(external)
    else:
        project_file.unlink()
    with pytest.raises(JevError) as caught:
        wb.templates.save_source(source, source.template)
    assert caught.value.code == "template_changed"
    assert "Keep your draft" in caught.value.fix
    if external_change == "modified":
        assert project_file.read_text() == external
    else:
        assert not project_file.exists()


def test_cli_run_and_export_project_file_without_catalog_import(
    wb: Workbench, project_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project_file.parent)
    monkeypatch.setattr("jevlab.cli.app.workbench", lambda: wb)
    monkeypatch.setattr("jevlab.cli.harness.workbench", lambda: wb)
    evaluator = MockEvaluator()
    use_evaluator(wb, evaluator, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["run", "decision.yaml", "--state", "-", "--json"],
        input='{"ticket": {"message": "Please refund the duplicate charge."}}',
    )
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1 and payload["ok"] is True
    assert payload["data"]["template_name"] == "project-routing"
    assert len(evaluator.requests) == 1
    result = runner.invoke(cli, ["export", "decision.yaml", "--json"])
    assert result.exit_code == 0, result.output
    assert "TypeSafeClient" in json.loads(result.stdout)["data"]["code"]
    assert "project-routing" not in wb.templates.names()


def test_cli_replace_project_yaml_preserves_destination_and_catalog(
    wb: Workbench, project_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jevlab.cli.app.workbench", lambda: wb)
    replacement = wb.templates.load("support-triage")
    replacement.description = "Replacement description."
    imported = tmp_path / "replacement.yml"
    imported.write_text(dump_template(replacement))
    result = CliRunner().invoke(
        cli, ["templates", "edit", str(project_file), "--from", str(imported), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["path"] == str(project_file)
    saved = parse_template(project_file.read_text())
    assert saved.name == "project-routing" and saved.description == replacement.description
    assert "project-routing" not in wb.templates.names()


async def test_tui_project_edit_saves_in_place_and_reopens_same_source(
    wb: Workbench, project_file: Path
) -> None:
    app = JevApp(wb, start="edit", template_name=str(project_file))
    async with app.run_test(size=(100, 35)) as pilot:
        editor = app.screen
        assert isinstance(editor, TemplateEditor)
        assert str(project_file) in str(editor.query_one("#template-source", Static).content)
        editor.query_one("#description", Input).value = "A repository-owned decision."
        await pilot.press("ctrl+s")
        assert (
            parse_template(project_file.read_text()).description == "A repository-owned decision."
        )
        # Changing the internal design name must not relocate the source file.
        editor.query_one("#template-name", Input).value = "renamed-design"
        await pilot.press("ctrl+s")
        assert editor.saved_template().name == "renamed-design"
        assert parse_template(project_file.read_text()).name == "renamed-design"
        assert "renamed-design" not in wb.templates.names()
        editor.query_one("#description", Input).value = "Retain my unsaved work."
        external = "# Concurrent external edit\n" + project_file.read_text()
        project_file.write_text(external)
        await pilot.press("ctrl+s")
        assert project_file.read_text() == external
        assert editor.query_one("#description", Input).value == "Retain my unsaved work."
        assert "changed outside this editor" in str(
            editor.query_one("#editor-status", Static).content
        )
