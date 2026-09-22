"""User-supplied files and nested editor text fail safely without losing drafts."""

import errno
import json
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Select, TextArea
from typer.testing import CliRunner

from jevlab.cli.app import app as cli
from jevlab.cli.common import local_error
from jevlab.core.service import Workbench
from jevlab.core.templates import MAX_NESTING, load_json, load_yaml
from jevlab.tui.app import JevApp
from jevlab.tui.editor import QuestionEditor, TemplateEditor
from jevlab.tui.fields import Field

DEEP_INPUT = "[" * 1100 + "0" + "]" * 1100


@pytest.mark.parametrize("loader", [load_json, load_yaml])
def test_shared_parser_turns_unsupported_nesting_into_actionable_validation(loader: object) -> None:
    assert callable(loader)
    with pytest.raises(ValueError, match="too deeply nested.*Reduce nested"):
        loader(DEEP_INPUT)


def test_json_import_has_same_size_boundary_as_yaml() -> None:
    with pytest.raises(ValueError, match="2 MB limit"):
        load_json('"' + "x" * 2_000_000 + '"')


@pytest.mark.parametrize("loader", [load_json, load_yaml])
def test_local_nesting_bound_counts_containers_not_brackets_in_text(loader: object) -> None:
    assert callable(loader)
    assert loader('"[[[a description]]]"') == "[[[a description]]]"
    allowed = "[" * MAX_NESTING + "0" + "]" * MAX_NESTING
    assert isinstance(loader(allowed), list)
    with pytest.raises(ValueError, match=f"at most {MAX_NESTING} levels"):
        loader("[" + allowed + "]")


@pytest.mark.parametrize(
    ("kind", "control", "button"),
    [
        ("template", "state-example", "save-template"),
        ("template", "thresholds", "save-template"),
        ("question", "instructions", "apply-question"),
        ("question", "criteria", "apply-question"),
    ],
)
async def test_nested_paste_keeps_editor_alive_and_preserves_unsaved_work(
    wb: Workbench,
    kind: str,
    control: str,
    button: str,
) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        if kind == "template":
            editor = TemplateEditor(wb)
            other = "description"
            draft = "Keep this unfinished design description."
        else:
            editor = QuestionEditor("route", wb.templates.load("support-triage").questions["route"])
            other = "question-id"
            draft = "my_route"
        await app.push_screen(editor)
        await pilot.pause()
        if kind == "template":
            editor.query_one("#state-format", Select).value = "json"
            editor.query_one("#state-example", TextArea).load_text(
                '{"message": "Please refund the duplicate charge."}'
            )
        editor.query_one(f"#{other}", Input).value = draft
        writing = editor.query_one(f"#{control}", TextArea)
        original = writing.text
        writing.load_text(DEEP_INPUT)
        await pilot.pause()
        assert app.screen is editor and editor.is_mounted
        assert "too deeply nested" in editor.query_one(f"#field-{control}", Field).error
        assert writing.text == DEEP_INPUT
        assert editor.query_one(f"#{other}", Input).value == draft
        assert editor.query_one(f"#{button}", Button).disabled
        assert wb.storage.history() == []
        writing.load_text(original)
        await pilot.pause()
        assert not editor.query_one(f"#field-{control}", Field).error
        assert not editor.query_one(f"#{button}", Button).disabled
        assert editor.query_one(f"#{other}", Input).value == draft


@pytest.mark.parametrize("machine", [False, True])
@pytest.mark.parametrize("kind", ["missing", "directory", "encoding"])
def test_bad_state_file_has_specific_cli_reason_and_no_run(
    wb: Workbench,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    machine: bool,
    kind: str,
) -> None:
    monkeypatch.setenv("JEVLAB_HOME", str(wb.root))
    path = tmp_path / "synthetic-state.json"
    expected = "does not exist"
    if kind == "directory":
        path.mkdir()
        expected = "folder where a file is required"
    elif kind == "encoding":
        path.write_bytes(b"\xff\xfe")
        expected = "not readable as UTF-8"
    args = ["run", "support-triage", "--state", str(path)]
    if machine:
        args.append("--json")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2
    if machine:
        payload = json.loads(result.stdout)
        assert payload["schema_version"] == 1 and payload["ok"] is False
        assert payload["error"]["code"] == "local_error"
        assert expected in payload["error"]["message"]
        assert "exception_type" in payload["error"]["details"]
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert expected in " ".join(result.stderr.split())
        assert "Next:" in result.stderr
        assert "Traceback" not in result.stderr
    assert wb.storage.history() == []


@pytest.mark.parametrize(
    ("failure", "reason", "fix"),
    [
        (
            PermissionError(errno.EACCES, "secret credential", "secret path"),
            "denied access",
            "permissions",
        ),
        (OSError(errno.ENOSPC, "secret credential"), "free disk space", "Free disk space"),
        (
            NotADirectoryError(errno.ENOTDIR, "secret credential"),
            "actually a file",
            "Check each folder",
        ),
    ],
)
def test_file_diagnostics_classify_safely_without_exception_contents(
    failure: OSError,
    reason: str,
    fix: str,
) -> None:
    error = local_error(failure)
    assert reason in error.message and fix in error.fix
    assert error.details == {"exception_type": type(failure).__name__, "errno": failure.errno}
    assert "secret" not in json.dumps(error.as_dict())


def test_unknown_local_failure_admits_uncertainty_and_keeps_only_safe_type() -> None:
    error = local_error(ValueError("A synthetic secret must not become a diagnostic."))
    assert "exact cause is unknown" in error.message
    assert "--verbose" in error.fix
    assert error.details == {"exception_type": "ValueError"}
    assert "synthetic secret" not in json.dumps(error.as_dict())
