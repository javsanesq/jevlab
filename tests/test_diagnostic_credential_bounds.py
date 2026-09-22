"""Native credential stalls cannot trap diagnostics or paired requests."""

import asyncio
import importlib
import json
from threading import Event
from time import monotonic
from typing import cast

import pytest
from textual.widgets import Static
from typer.testing import CliRunner

from jevlab.core import doctor
from jevlab.core.compare import compare
from jevlab.core.credentials import Credentials, Provider
from jevlab.core.models import Template
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.screens import SettingsScreen


def test_status_keeps_completed_sources_and_stops_after_timed_out_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, returned, unexpected = Event(), Event(), Event()

    def resolve(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        if provider == "typesafe":
            return "synthetic-private-value", "environment"
        if provider == "anthropic":
            release.wait(5)
            returned.set()
            return "synthetic-private-value", "keychain"
        unexpected.set()
        return None, "missing"

    monkeypatch.setattr(Credentials, "resolve", resolve)
    start = monotonic()
    try:
        report = Credentials().status(timeout_seconds=0.03)
        assert monotonic() - start < 1.0
        assert report["typesafe"] == {"present": True, "source": "environment"}
        for provider in ("anthropic", "openai"):
            row = cast(dict[str, object], report[provider])
            assert row["present"] is False and row["source"] == "unavailable"
            error = cast(dict[str, object], row["error"])
            assert error["code"] == "credential_timeout"
            assert "unknown" in str(error["message"])
            assert f"{provider.upper()}_API_KEY" in str(error["fix"])
        assert "synthetic-private-value" not in json.dumps(report)
    finally:
        release.set()
    assert returned.wait(1)
    assert not unexpected.wait(0.03)


async def test_status_is_callable_from_an_active_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        if provider == "anthropic":
            raise RuntimeError("private exception text must not enter diagnostics")
        if provider == "typesafe":
            return "synthetic-private-value", "keychain"
        return None, "missing"

    monkeypatch.setattr(Credentials, "resolve", resolve)
    report = Credentials().status(timeout_seconds=0.5)
    assert report["typesafe"] == {"present": True, "source": "keychain"}
    assert report["openai"] == {"present": False, "source": "missing"}
    error = cast(dict[str, object], cast(dict[str, object], report["anthropic"])["error"])
    assert error["code"] == "keychain_unavailable"
    assert "Unlock your login Keychain" in str(error["fix"])
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize("mode", ["offline_json", "online_json", "human"])
def test_actual_doctor_cli_bounds_preflight_and_reports_unknown_key_status(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    commands = importlib.import_module("jevlab.cli.app")
    release = Event()
    attempts: list[Provider] = []
    wb.settings.deadline_seconds = 0.03
    wb.settings.ui_mode = "simple"

    def blocked(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        attempts.append(provider)
        release.wait(5)
        return "synthetic-unused-credential", "keychain"

    def never_call(*args: object, **kwargs: object) -> None:
        pytest.fail("No online client may start after credential timeout.")

    monkeypatch.setattr(commands, "workbench", lambda: wb)
    monkeypatch.setattr(Credentials, "resolve", blocked)
    monkeypatch.setattr(doctor, "AsyncTypeSafeClient", never_call)
    monkeypatch.setattr(doctor.os, "get_exec_path", lambda: [])
    args = ["doctor"]
    if mode != "human":
        args.append("--json")
    if mode == "online_json":
        args.append("--online")
    start = monotonic()
    try:
        result = CliRunner().invoke(commands.app, args)
        assert monotonic() - start < 1.0
        assert "synthetic-unused-credential" not in result.output
        assert result.stderr == ""
        if mode == "online_json":
            assert result.exit_code == 3, result.output
            assert json.loads(result.stdout)["error"]["code"] == "credential_timeout"
            assert attempts == ["typesafe", "typesafe"]
        elif mode == "offline_json":
            assert result.exit_code == 0, result.output
            rows = json.loads(result.stdout)["data"]["credentials"]
            assert rows["typesafe"]["error"]["code"] == "credential_timeout"
            assert rows["openai"]["source"] == "unavailable"
            assert attempts == ["typesafe"]
        else:
            assert result.exit_code == 0, result.output
            assert "could not check" in result.stdout
            assert "whether a key is present is unknown" in result.stdout
            assert "Unlock your login Keychain" in result.stdout
            assert "not added" not in result.stdout
    finally:
        release.set()


async def test_simple_tui_doctor_explains_timeout_instead_of_claiming_missing_key(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Event()
    wb.settings.deadline_seconds = 0.03
    wb.settings.ui_mode = "simple"

    def blocked(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        release.wait(5)
        return "synthetic-unused-credential", "keychain"

    monkeypatch.setattr(Credentials, "resolve", blocked)
    app = JevApp(wb)
    try:
        async with app.run_test(size=(100, 35)):
            screen = SettingsScreen(wb)
            await app.push_screen(screen)
            await screen.run_doctor().wait()
            text = str(screen.query_one("#doctor-result", Static).content)
            assert "TypeSafe key: not found" not in text
            assert "key: could not check" in text
            assert "whether a key is present is unknown" in text
            assert "Unlock your login Keychain" in text
            assert "synthetic-unused-credential" not in text
    finally:
        release.set()


def test_comparison_keychain_deadline_exits_loop_and_keeps_linked_failures(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    comparisons = importlib.import_module("jevlab.core.compare")
    release = Event()
    attempts = 0
    wb.settings.deadline_seconds = 0.03

    def blocked(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        nonlocal attempts
        attempts += 1
        release.wait(5)
        return "synthetic-unused-credential", "keychain"

    def never_call(*args: object, **kwargs: object) -> None:
        pytest.fail("A timed-out comparison must not initialize an API client.")

    monkeypatch.setattr(Credentials, "resolve", blocked)
    monkeypatch.setattr(comparisons, "SDKClient", never_call)
    start = monotonic()
    try:
        report = asyncio.run(compare(wb, design, design, "Synthetic comparison state"))
        assert monotonic() - start < 1.0  # Includes asyncio.run's executor shutdown.
        assert attempts == 1 and report.status == "failed"
        assert report.right.parent_run_id == report.left.id
        for run in (report.left, report.right):
            saved = wb.storage.get(run.id)
            assert saved.error and saved.error["code"] == "credential_timeout"
            assert "No API request was sent" in str(saved.error["message"])
            assert saved.response is None and saved.cost_nanousd is None
        assert len(wb.storage.history()) == 2
    finally:
        release.set()
