"""The guide remains free, readable offline, and available in installed packages."""

import importlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import typer
from typer.testing import CliRunner

from jev.cli.app import app
from jev.cli.guide import GUIDE_TITLE, PAGER_HELP, guide, load_guide, render_guide_html

guide_module = importlib.import_module("jev.cli.guide")
DOCUMENT = "# Beginner's guide\n\nWelcome to jev.\n\n```sh\njev demo\n```\n"


@pytest.fixture
def guide_cli(monkeypatch: pytest.MonkeyPatch) -> typer.Typer:
    cli = typer.Typer()
    # Keep command parsing identical to the full application without initializing a workbench.
    cli.command("guide")(guide)
    cli.command("other")(lambda: None)
    monkeypatch.setattr(guide_module, "load_guide", lambda: DOCUMENT)
    return cli


@pytest.mark.parametrize("extra", [[], ["--web"]])
def test_json_never_opens_pager_browser_or_user_storage(
    extra: list[str], guide_cli: typer.Typer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "not-created"
    monkeypatch.setenv("JEV_HOME", str(root))
    pager = Mock(side_effect=AssertionError("JSON must not open the pager"))
    browser = Mock(side_effect=AssertionError("JSON must not open a browser"))
    monkeypatch.setattr(guide_module.console, "pager", pager)
    monkeypatch.setattr(guide_module.webbrowser, "open", browser)
    result = CliRunner().invoke(guide_cli, ["guide", *extra, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "ok": True,
        "data": {"title": GUIDE_TITLE, "format": "markdown", "content": DOCUMENT},
    }
    assert result.stderr == ""
    assert not root.exists()
    pager.assert_not_called()
    browser.assert_not_called()


def test_redirected_guide_is_complete_markdown_without_pager(
    guide_cli: typer.Typer, monkeypatch: pytest.MonkeyPatch
) -> None:
    pager = Mock(side_effect=AssertionError("Redirected output must not open the pager"))
    monkeypatch.setattr(guide_module.console, "pager", pager)
    result = CliRunner().invoke(guide_cli, ["guide"])
    assert result.exit_code == 0
    assert result.stdout == DOCUMENT
    assert result.stderr == ""


def test_interactive_guide_uses_pager_with_exit_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def pager() -> Any:
        yield

    output = Mock()
    output.pager.side_effect = pager
    monkeypatch.setattr(guide_module, "console", output)
    monkeypatch.setattr(guide_module, "load_guide", lambda: DOCUMENT)
    monkeypatch.setattr(guide_module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(guide_module.sys.stdout, "isatty", lambda: True)
    guide()
    output.pager.assert_called_once_with()
    assert str(output.print.call_args_list[0].args[0]) == PAGER_HELP
    assert output.print.call_args_list[1].args[0].markup == DOCUMENT


def test_browser_receives_local_html_from_authoritative_content(
    guide_cli: typer.Typer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    browser = Mock(return_value=True)
    monkeypatch.setattr(guide_module.webbrowser, "open", browser)
    result = CliRunner().invoke(guide_cli, ["guide", "--web"])
    destination = tmp_path / "docs" / "guide.html"
    assert result.exit_code == 0, result.output
    assert destination.is_file()
    html = destination.read_text()
    assert "Welcome to jev." in html and "jev demo" in html
    assert "Opened the beginner's guide in your browser." in result.stdout
    assert f"Local copy: {destination}" in result.stdout
    browser.assert_called_once_with(destination.as_uri(), new=2)
    assert not (tmp_path / "jev.db").exists()


@pytest.mark.parametrize("raised", [False, True])
def test_browser_failure_keeps_readable_file_and_actionable_error(
    raised: bool, guide_cli: typer.Typer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    browser = Mock(return_value=False)
    if raised:
        browser.side_effect = OSError("synthetic private browser details")
    monkeypatch.setattr(guide_module.webbrowser, "open", browser)
    result = CliRunner().invoke(guide_cli, ["guide", "--web"])
    assert result.exit_code == 2
    assert result.stdout == ""
    # Rich wraps prose according to terminal width and the temporary path length.
    error_text = " ".join(result.stderr.split())
    assert "default browser could not be opened" in error_text
    assert "run jev guide to read it here" in error_text
    assert (tmp_path / "docs" / "guide.html").is_file()
    assert "Traceback" not in result.output
    assert "synthetic private browser details" not in result.output


def test_html_escapes_literals_and_has_no_active_content() -> None:
    html = render_guide_html(
        "# Help\n\n```text\n<script>alert('unsafe')</script>\n```\n\n"
        "[TypeSafe](https://docs.typesafe.ai)\n"
    )
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "https://docs.typesafe.ai" in html
    assert "<a " not in html
    assert "default-src 'none'" in html


def test_editable_loading_reads_latest_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "GUIDE.md"
    source.write_text(DOCUMENT)
    monkeypatch.setattr(guide_module, "_editable_guide_path", lambda: source)
    assert load_guide() == DOCUMENT
    source.write_text("# Revised guide\n")
    assert load_guide() == "# Revised guide\n"


def test_package_resource_is_used_outside_source_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resource = tmp_path / "resources" / "GUIDE.md"
    resource.parent.mkdir()
    resource.write_text(DOCUMENT)
    monkeypatch.setattr(guide_module, "_editable_guide_path", lambda: None)
    monkeypatch.setattr(guide_module, "files", lambda package: tmp_path)
    assert load_guide() == DOCUMENT


def test_missing_packaged_guide_has_actionable_json_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(guide_module, "_editable_guide_path", lambda: None)
    monkeypatch.setattr(guide_module, "files", lambda package: tmp_path)
    result = CliRunner().invoke(app, ["guide", "--json"])
    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["error"]["code"] == "guide_unavailable"
    assert "Reinstall" in data["error"]["fix"]
    assert result.stderr == ""
