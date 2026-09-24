"""First-use CLI guidance and dataset import conveniences for developers."""

import asyncio
import importlib
import json
from pathlib import Path

import pytest
from conftest import MockEvaluator
from typer.testing import CliRunner

from jevlab.cli.app import app
from jevlab.core.datasets import iter_dataset
from jevlab.core.errors import JevError
from jevlab.core.models import Template
from jevlab.core.service import Workbench

commands = importlib.import_module("jevlab.cli.app")
runner = CliRunner()


def test_text_for_json_template_explains_expected_shape(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    result = runner.invoke(app, ["run", "support-triage", "--text", "I was charged twice"])
    assert result.exit_code == 2
    assert "support-triage expects JSON state" in result.stderr
    assert '{"ticket": {"message":' in result.stderr and "--format text" in result.stderr
    assert not wb.storage.history()


@pytest.mark.parametrize("suffix", [".jsonl", ".csv"])
def test_dataset_file_is_not_sent_as_one_state(
    suffix: str, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    path = tmp_path / f"cases{suffix}"
    path.write_text('{"state": "one"}\n{"state": "two"}\n')
    result = runner.invoke(app, ["run", "support-triage", "--state", str(path), "--json"])
    assert result.exit_code == 2
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "state_is_dataset" and "jevlab batch" in error["fix"]
    assert not wb.storage.history()


def test_history_shows_unknown_latency_as_a_dash(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    with pytest.raises(JevError):  # No key: recorded, but no request timing exists.
        asyncio.run(wb.run(wb.templates.load("support-triage"), {"ticket": "hello"}))
    asyncio.run(
        wb.run(wb.templates.load("support-triage"), {"ticket": "x"}, evaluator=MockEvaluator())
    )
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 0
    failed = next(line for line in result.stdout.splitlines() if "failed" in line)
    assert "—" in failed and " 0 " not in failed


def test_root_json_has_no_internal_build_fields() -> None:
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 0
    assert set(json.loads(result.stdout)["data"]) == {"version", "data_directory"}


def test_every_cli_command_has_help_text() -> None:
    import typer.main

    group = typer.main.get_command(app)
    missing: list[str] = []

    def visit(command: object, path: str) -> None:
        commands_ = getattr(command, "commands", {})
        for name, child in commands_.items():
            if not (child.help or child.short_help):
                missing.append(f"{path} {name}".strip())
            visit(child, f"{path} {name}")

    visit(group, "")
    assert not missing, missing


def test_choice_label_errors_list_valid_options(tmp_path: Path, design: Template) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"state": {"ticket": "x"}, "expected": {"route": "Billing"}}) + "\n")
    with pytest.raises(JevError) as caught:
        list(iter_dataset(path, design))
    assert "billing, technical, other" in caught.value.message
    assert "docs/REFERENCE.md" in caught.value.fix


def test_csv_columns_become_nested_json_state(tmp_path: Path, design: Template) -> None:
    path = tmp_path / "cases.csv"
    path.write_text(
        "id,ticket.message,ticket.plan,expected.route,expected.refund_requested\n"
        'a,"Refund, please",pro,billing,TRUE\n'
    )
    rows = list(iter_dataset(path, design))
    assert rows[0].id == "a"
    assert rows[0].state == {"ticket": {"message": "Refund, please", "plan": "pro"}}
    assert rows[0].expected == {"route": "billing", "refund_requested": True}


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("ticket,ticket.message", "conflicts"),
        ("ticket..message", "empty name segment"),
        ("ticket.message,Expected.route", "expected.<question>"),
        ("state,extra", "only id and expected"),
    ],
)
def test_csv_state_columns_reject_ambiguity(
    tmp_path: Path, design: Template, header: str, message: str
) -> None:
    path = tmp_path / "cases.csv"
    values = ",".join("x" for _ in header.split(","))
    path.write_text(f"{header}\n{values}\n")
    with pytest.raises(JevError, match=message):
        list(iter_dataset(path, design))


def test_csv_state_columns_need_a_json_template(tmp_path: Path, design: Template) -> None:
    text_design = Template.model_validate(
        design.model_dump() | {"state": {"description": "Plain text.", "format": "text"}}
    )
    path = tmp_path / "cases.csv"
    path.write_text("subject,body\nHello,World\n")
    with pytest.raises(JevError, match="JSON-format template"):
        list(iter_dataset(path, text_design))
