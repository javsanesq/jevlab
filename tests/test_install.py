"""Coach extras survive editable tool upgrades, independently of the dev environment."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def installer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "install.py"
    spec = importlib.util.spec_from_file_location("jev_test_installer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(module.os, "get_exec_path", lambda: [str(tmp_path / "bin")])
    return module


def receipt(tool_dir: Path, content: str) -> None:
    destination = tool_dir / "jev-workbench" / "uv-receipt.toml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content)


def fake_uv(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tool_dir: Path,
    *,
    dir_status: int = 0,
    install_status: int = 0,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append(command)
        if command == ["uv", "tool", "dir"]:
            assert capture_output and text
            return subprocess.CompletedProcess(command, dir_status, f"{tool_dir}\n", "")
        assert command[:3] == ["uv", "tool", "install"]
        assert not capture_output and not text
        return subprocess.CompletedProcess(command, install_status, "", "")

    monkeypatch.setattr(installer.subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    ("recorded", "requested", "expected"),
    [
        (["anthropic", "openai"], None, ["anthropic", "openai"]),
        (["openai"], "anthropic", ["anthropic", "openai"]),
        (["anthropic"], "both", ["anthropic", "openai"]),
        (["openai"], "openai", ["openai"]),
        ([], None, []),
    ],
)
def test_upgrade_preserves_recorded_extras_and_unions_requested(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recorded: list[str],
    requested: str | None,
    expected: list[str],
) -> None:
    tools = tmp_path / "custom tools directory"
    # Name normalization follows Python package naming; unrelated requirements do not add extras.
    receipt(
        tools,
        "[tool]\nrequirements = ["
        f'{{name = "Jev_Workbench", extras = {recorded!r}}}, '
        '{name = "other-tool", extras = ["anthropic", "openai"]}]',
    )
    calls = fake_uv(installer, monkeypatch, tools)
    monkeypatch.setattr(sys, "argv", ["install.py", *(["--coach", requested] if requested else [])])
    with pytest.raises(SystemExit) as exited:
        installer.main()
    assert exited.value.code == 0
    assert installer.__file__ is not None
    project = Path(installer.__file__).resolve().parent.parent
    assert calls == [
        ["uv", "tool", "dir"],
        [
            "uv",
            "tool",
            "install",
            "--editable",
            str(project) + (f"[{','.join(expected)}]" if expected else ""),
        ],
    ]


def test_fresh_install_ignores_other_tools_and_environment_keys(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tools = tmp_path / "tools"
    other = tools / "other-tool"
    other.mkdir(parents=True)
    (other / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{name="other-tool", extras=["anthropic", "openai"]}]'
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    calls = fake_uv(installer, monkeypatch, tools)
    monkeypatch.setattr(sys, "argv", ["install.py"])
    with pytest.raises(SystemExit) as exited:
        installer.main()
    assert exited.value.code == 0
    assert "--extra" not in calls[-1]


@pytest.mark.parametrize(
    "content",
    [
        'this is not TOML: "private-url"',
        '[tool]\nrequirements = [{name="jev-workbench", extras="anthropic"}]',
        '[tool]\nrequirements = [{name="jev-workbench", extras=[1]}]',
        '[tool]\nrequirements = [{name="other-tool", extras=["anthropic"]}]',
        '[tool]\nrequirements = [{name="jev-workbench"}, {name="jev-workbench"}]',
        '[tool]\nrequirements = "invalid"',
        'tool = "invalid"',
    ],
)
def test_malformed_receipt_stops_before_install_with_actionable_error(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str,
) -> None:
    receipt(tmp_path, content)
    calls = fake_uv(installer, monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["install.py", "--coach", "both"])
    with pytest.raises(SystemExit) as exited:
        installer.main()
    assert exited.value.code == 1
    assert calls == [["uv", "tool", "dir"]]
    error = capsys.readouterr().err
    assert "No installation was changed" in error
    assert "--editable '.[anthropic,openai]'" in error
    assert "private-url" not in error


def test_missing_receipt_for_existing_tool_does_not_silently_drop_extras(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "jev-workbench").mkdir()
    calls = fake_uv(installer, monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["install.py"])
    with pytest.raises(SystemExit) as exited:
        installer.main()
    assert exited.value.code == 1
    assert calls == [["uv", "tool", "dir"]]


def test_tool_directory_failure_is_not_a_fresh_install(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = fake_uv(installer, monkeypatch, tmp_path, dir_status=2)
    monkeypatch.setattr(sys, "argv", ["install.py"])
    with pytest.raises(SystemExit) as exited:
        installer.main()
    assert exited.value.code == 1
    assert calls == [["uv", "tool", "dir"]]
    assert "Run uv tool dir to diagnose" in capsys.readouterr().err


def test_existing_path_collision_warns_and_preserves_uv_ownership_check(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = fake_uv(installer, monkeypatch, tmp_path, install_status=2)
    other_command = tmp_path / "other" / "bin" / "jev"
    monkeypatch.setattr(installer.shutil, "which", lambda _name: str(other_command))
    monkeypatch.setattr(sys, "argv", ["install.py"])
    with pytest.raises(SystemExit) as exited:
        installer.main()
    assert exited.value.code == 2
    assert f"jev already exists at {other_command}" in capsys.readouterr().err
    assert "--force" not in calls[-1]


def test_project_environment_does_not_hide_later_path_collision(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_uv(installer, monkeypatch, tmp_path)
    assert installer.__file__ is not None
    project = Path(installer.__file__).resolve().parent.parent
    project_bin = project / ".venv" / "bin"
    other_bin = tmp_path / "bin"
    other_bin.mkdir()
    (other_bin / "jev").touch()
    monkeypatch.setattr(installer.shutil, "which", lambda _name: str(project_bin / "jev"))
    monkeypatch.setattr(installer.os, "get_exec_path", lambda: [str(project_bin), str(other_bin)])
    monkeypatch.setattr(sys, "argv", ["install.py"])
    with pytest.raises(SystemExit):
        installer.main()
    assert f"Warning: existing jev command(s): {other_bin / 'jev'}" in capsys.readouterr().err


def test_missing_uv_has_no_raw_traceback(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("private execution detail")

    monkeypatch.setattr(installer.subprocess, "run", unavailable)
    monkeypatch.setattr(sys, "argv", ["install.py"])
    with pytest.raises(SystemExit) as exited:
        installer.main()
    assert exited.value.code == 1
    error = capsys.readouterr().err
    assert "Install uv and retry make install" in error
    assert "private execution detail" not in error
