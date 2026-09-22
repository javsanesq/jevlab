"""Bundled teaching cases become strict datasets without overwriting user files."""

import json
from pathlib import Path

import pytest
from textual.widgets import Input
from typer.testing import CliRunner

from jevlab.cli.app import app
from jevlab.core.content import export_dataset, patterns
from jevlab.core.datasets import inspect_dataset
from jevlab.core.errors import JevError
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp


def test_every_library_export_is_a_private_validated_dataset(tmp_path: Path) -> None:
    cases = 0
    for item in patterns():
        path = export_dataset(item.id, tmp_path / "datasets" / f"{item.id}.jsonl")
        info = inspect_dataset(path, item.template, require_labels=True)
        assert info.rows == info.labeled_rows == len(item.cases)
        assert path.stat().st_mode & 0o777 == 0o600
        for line in path.read_text().splitlines():
            assert set(json.loads(line)) == {"id", "state", "expected"}
        cases += info.rows
    assert cases == 29
    assert not list((tmp_path / "datasets").glob(".*"))


@pytest.mark.parametrize("existing", ["file", "symlink", "dangling-symlink"])
def test_export_never_clobbers_existing_paths(tmp_path: Path, existing: str) -> None:
    output = tmp_path / "examples.jsonl"
    target = tmp_path / "target.jsonl"
    if existing == "file":
        output.write_text("preserve this")
    else:
        if existing == "symlink":
            target.write_text("preserve this")
        output.symlink_to(target)
    with pytest.raises(JevError, match="already exists"):
        export_dataset("support-routing", output)
    if existing == "file":
        assert output.read_text() == "preserve this"
    elif existing == "symlink":
        assert output.is_symlink() and target.read_text() == "preserve this"
    else:
        assert output.is_symlink() and not target.exists()
    assert not list(tmp_path.glob(".*"))


def test_export_cli_fork_import_and_duplicate_json(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "support.jsonl"
    for command in [
        ["library", "export-data", "support-routing", str(output), "--json"],
        ["library", "fork", "support-routing", "my-routing", "--json"],
        ["datasets", "import", str(output), "--template", "my-routing", "--json"],
    ]:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["ok"]
    previous = output.read_bytes()
    duplicate = runner.invoke(
        app, ["library", "export-data", "support-routing", str(output), "--json"]
    )
    assert duplicate.exit_code == 2
    assert json.loads(duplicate.stdout)["error"]["code"] == "already_exists"
    assert output.read_bytes() == previous


async def test_library_dataset_export_button(wb: Workbench, tmp_path: Path) -> None:
    destination = tmp_path / "from-library.jsonl"
    application = JevApp(wb, start="library")
    async with application.run_test(size=(120, 42)) as pilot:
        await pilot.click("#export-pattern-data")
        application.screen.query_one(Input).value = str(destination)
        await pilot.press("enter")
        assert len(destination.read_text().splitlines()) == 5
        assert not wb.storage.history()
