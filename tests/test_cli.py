import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from jevlab.cli.app import app
from jevlab.core.config import save_settings
from jevlab.core.models import Settings

runner = CliRunner()


def test_json_crud_and_config(tmp_path: Path) -> None:
    root = Path(os.environ["JEVLAB_HOME"])
    save_settings(root, Settings(credential_mode="environment"))
    for args in [
        ["--json"],
        ["templates", "--json"],
        ["history", "--json"],
        ["doctor", "--json"],
        ["config", "--json"],
    ]:
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["ok"]
    source = root / "templates" / "support-triage.yaml"
    result = runner.invoke(app, ["templates", "new", "my-fork", "--from", str(source), "--json"])
    assert json.loads(result.stdout)["ok"]
    assert (root / "templates" / "my-fork.yaml").exists()
    result = runner.invoke(app, ["config", "--set", "retention_days=30", "--json"])
    assert json.loads(result.stdout)["data"]["settings"]["retention_days"] == 30


def test_missing_key_is_json_and_recorded() -> None:
    save_settings(Path(os.environ["JEVLAB_HOME"]), Settings(credential_mode="environment"))
    result = runner.invoke(
        app,
        ["run", "support-triage", "--state", "-", "--json"],
        input='{"ticket":{"message":"Refund me"}}',
    )
    assert result.exit_code == 3, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "missing_key" and error["run_id"]
    history = runner.invoke(app, ["history", "--json"])
    assert json.loads(history.stdout)["data"]["runs"][0]["status"] == "failed"


def test_real_entrypoint_error_exit_and_json(tmp_path: Path) -> None:
    for args in [
        ["run", "--json"],
        ["unknown", "--json"],
        ["run", "support-triage", "--text", "bad json", "--json"],
    ]:
        result = subprocess.run(
            [sys.executable, "-m", "jevlab", *args], capture_output=True, text=True, cwd=tmp_path
        )
        assert result.returncode == 2, (result.stdout, result.stderr)
        assert json.loads(result.stdout)["ok"] is False
        assert "Traceback" not in result.stderr


def test_no_plaintext_config_keys() -> None:
    result = runner.invoke(app, ["config", "--set", "api_key=never-store-this", "--json"])
    assert result.exit_code == 2
    assert "never-store-this" not in result.stdout
