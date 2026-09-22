"""CLI and Textual harness flows remain offline and isolate their local profiles."""

import ast
import hashlib
import json
import os
import socket
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from conftest import MockEvaluator
from textual.widgets import Button, Input, Select, Static, TextArea
from typer.testing import CliRunner

from jevlab.cli.app import app as cli
from jevlab.core.config import save_settings
from jevlab.core.models import Run, Settings, Template
from jevlab.core.retention import CleanupPlan, CleanupReport
from jevlab.core.retention import cleanup as actual_cleanup
from jevlab.core.service import Workbench
from jevlab.core.storage import Storage
from jevlab.core.templates import revision_hash
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Confirm
from jevlab.tui.harness import CleanupScreen, ExportScreen
from jevlab.tui.screens import Home

runner = CliRunner()
TOKEN = "synthetic-local-test-token-never-a-provider-key"


def profile() -> Workbench:
    root = Path(os.environ["JEVLAB_HOME"])
    save_settings(root, Settings(credential_mode="environment"))
    return Workbench(root, maintain=False)


def expired_run(wb: Workbench) -> Run:
    design = wb.templates.load("support-triage")
    old = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    run = Run(
        id="expired-harness-test",
        template_hash=revision_hash(design),
        template_name=design.name,
        started_at=old,
        finished_at=old,
        status="succeeded",
        requested_model=design.model,
        sdk_version="offline-test",
        request={"state": "test"},
    )
    wb.storage.create_run(run, design)
    return run


@pytest.mark.parametrize("lang", ["python", "langchain", "pydantic-ai"])
def test_export_cli_json_contains_compilable_keyless_module(lang: str) -> None:
    wb = profile()
    result = runner.invoke(cli, ["export", "support-triage", "--lang", lang, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1 and payload["ok"]
    assert payload["data"]["lang"] == lang and payload["data"]["path"] is None
    ast.parse(payload["data"]["code"])
    assert "TYPESAFE_API_KEY" in payload["data"]["code"]
    assert not wb.storage.history()


def test_export_cli_writes_new_file_and_does_not_clobber(tmp_path: Path) -> None:
    profile()
    destination = tmp_path / "decision.py"
    command = ["export", "support-triage", "--output", str(destination), "--json"]
    saved = runner.invoke(cli, command)
    assert saved.exit_code == 0, saved.output
    data = json.loads(saved.stdout)["data"]
    assert data["path"] == str(destination.resolve())
    assert destination.read_text() == data["code"]
    destination.write_text("# User edits must survive.\n")
    duplicate = runner.invoke(cli, command)
    assert duplicate.exit_code == 2
    assert json.loads(duplicate.stdout)["error"]["code"] == "already_exists"
    assert destination.read_text() == "# User edits must survive.\n"


def test_clean_cli_dry_run_then_apply_json() -> None:
    wb = profile()
    saved = expired_run(wb)
    before = hashlib.sha256(wb.storage.path.read_bytes()).hexdigest()
    preview = runner.invoke(cli, ["clean", "--dry-run", "--json"])
    assert preview.exit_code == 0, preview.output
    data = json.loads(preview.stdout)["data"]
    assert data["dry_run"] and not data["applied"] and data["plan"]["delete"]["runs"] == 1
    assert wb.storage.get(saved.id).id == saved.id
    assert hashlib.sha256(wb.storage.path.read_bytes()).hexdigest() == before
    applied = runner.invoke(cli, ["clean", "--json"])
    assert applied.exit_code == 0, applied.output
    data = json.loads(applied.stdout)["data"]
    assert data["applied"] and not data["dry_run"] and data["deleted"]["runs"] == 1
    assert wb.storage.history() == [] and wb.storage.health() == "ok"


def test_serve_check_is_offline_and_never_emits_token(monkeypatch: pytest.MonkeyPatch) -> None:
    profile()
    monkeypatch.setenv("JEVLAB_SERVER_TOKEN", TOKEN)

    def no_bind(self: socket.socket, address: object) -> None:
        pytest.fail("serve --check must not bind a socket")

    monkeypatch.setattr(socket.socket, "bind", no_bind)
    result = runner.invoke(cli, ["serve", "--check", "--port", "8767", "--rate", "3", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["check_only"] and data["url"] == "http://127.0.0.1:8767"
    assert data["requests_per_second"] == 3 and not data["network_checked"]
    assert data["token_source"] == "JEVLAB_SERVER_TOKEN"
    assert TOKEN not in result.output


def test_serve_missing_token_has_json_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEVLAB_SERVER_TOKEN", raising=False)
    result = runner.invoke(cli, ["serve", "--check", "--json"])
    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert not payload["ok"] and payload["error"]["code"] == "server_token_missing"


def test_serve_bind_failure_emits_only_error_json(monkeypatch: pytest.MonkeyPatch) -> None:
    profile()
    monkeypatch.setenv("JEVLAB_SERVER_TOKEN", TOKEN)
    calls: list[object] = []

    def occupied(self: socket.socket, address: object) -> None:
        calls.append(address)
        raise OSError("synthetic occupied port")

    monkeypatch.setattr(socket.socket, "bind", occupied)
    result = runner.invoke(cli, ["serve", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert not payload["ok"] and payload["error"]["code"] == "server_bind_failed"
    assert calls == [("127.0.0.1", 8766)] and TOKEN not in result.output


def test_startup_retention_prunes_expired_history() -> None:
    wb = profile()
    expired_run(wb)
    restarted = Workbench(wb.root)
    assert restarted.storage.history() == []
    assert restarted.maintenance_error is None


def test_automatic_retention_throttles_even_when_active_data_exceeds_limit(
    wb: Workbench,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[set[str] | None] = []
    clock = [1000.0]

    def observed(
        storage: Storage,
        settings: Settings,
        *,
        dry_run: bool = True,
        protect_run_ids: set[str] | None = None,
    ) -> CleanupReport:
        calls.append(protect_run_ids)
        return CleanupReport(
            dry_run=dry_run,
            bytes_after=settings.retention_bytes + 1,
            within_budget=False,
            plan=CleanupPlan(
                cutoff="test",
                limit_bytes=settings.retention_bytes,
                bytes_before=settings.retention_bytes + 1,
                estimated_bytes_after=settings.retention_bytes + 1,
            ),
        )

    monkeypatch.setattr("jevlab.core.retention.cleanup", observed)
    monkeypatch.setattr("jevlab.core.service.monotonic", lambda: clock[0])
    wb.maintain_history(force=True, protect={"first"})
    clock[0] += 59
    wb.maintain_history(protect={"too-soon"})
    assert calls == [{"first"}]
    clock[0] += 2
    wb.maintain_history(protect={"next"})
    assert calls == [{"first"}, {"next"}]


async def test_maintenance_failure_cannot_change_successful_inference(
    wb: Workbench,
    design: Template,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[set[str] | None] = []

    def unavailable(
        storage: Storage,
        settings: Settings,
        *,
        dry_run: bool = True,
        protect_run_ids: set[str] | None = None,
    ) -> CleanupReport:
        captured.append(protect_run_ids)
        raise sqlite3.OperationalError("synthetic database busy")

    monkeypatch.setattr("jevlab.core.retention.cleanup", unavailable)
    wb.last_maintenance = 0
    run = await wb.run(design, "test", evaluator=MockEvaluator())
    assert run.status == "succeeded" and wb.storage.get(run.id).status == "succeeded"
    assert captured == [{run.id}] and wb.maintenance_error


async def test_export_screen_preview_save_and_no_clobber(wb: Workbench, tmp_path: Path) -> None:
    destination = tmp_path / "from-tui.py"
    app = JevApp(wb)
    async with app.run_test(size=(120, 50)) as pilot:
        await app.push_screen(ExportScreen(wb))
        screen = cast(ExportScreen, app.screen)
        assert "TypeSafeClient" in screen.query_one("#export-code", TextArea).text
        screen.query_one("#export-language", Select).value = "langchain"
        await pilot.pause()
        assert "Runnable" in screen.query_one("#export-code", TextArea).text
        screen.query_one("#export-path", Input).value = str(destination)
        await pilot.click("#export-save")
        ast.parse(destination.read_text())
        assert "Runnable" in destination.read_text()
        destination.write_text("# Personal edits\n")
        await pilot.pause(0.25)  # Textual suppresses presses during its 200 ms active effect.
        screen.query_one("#export-save", Button).focus()
        await pilot.press("enter")
        assert destination.read_text() == "# Personal edits\n"
        assert "already exists" in str(screen.query_one("#export-status", Static).render())
        assert not wb.storage.history()


async def test_cleanup_screen_preview_cancel_and_confirm_apply(wb: Workbench) -> None:
    saved = expired_run(wb)
    app = JevApp(wb)
    async with app.run_test(size=(110, 40)) as pilot:
        await app.push_screen(CleanupScreen(wb))
        await app.workers.wait_for_complete()
        await pilot.pause()
        screen = cast(CleanupScreen, app.screen)
        assert screen.report and screen.report.plan.delete.runs == 1
        assert wb.storage.get(saved.id) and not screen.busy
        await pilot.click("#cleanup-apply")
        assert isinstance(app.screen, Confirm)
        await pilot.press("escape")
        assert app.screen is screen and wb.storage.get(saved.id)
        await pilot.pause(0.25)
        screen.query_one("#cleanup-apply", Button).focus()
        await pilot.press("enter")
        await pilot.click("#discard")
        await app.workers.wait_for_complete()
        assert screen.report and screen.report.deleted.runs == 1
        assert wb.storage.history() == []


async def test_cleanup_screen_waits_for_thread_before_back_or_quit(
    wb: Workbench,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()

    def delayed(storage: Storage, settings: Settings, *, dry_run: bool = True) -> CleanupReport:
        started.set()
        if not release.wait(timeout=5):
            raise AssertionError("test did not release cleanup worker")
        return actual_cleanup(storage, settings, dry_run=dry_run)

    monkeypatch.setattr("jevlab.tui.harness.cleanup", delayed)
    app = JevApp(wb)
    async with app.run_test(size=(110, 40)) as pilot:
        try:
            await app.push_screen(CleanupScreen(wb))
            await pilot.pause()
            screen = cast(CleanupScreen, app.screen)
            assert started.is_set() and screen.busy
            assert screen.query_one("#cleanup-preview", Button).disabled
            await pilot.press("escape", "ctrl+q")
            assert app.screen is screen and screen.busy
        finally:
            release.set()
        await app.workers.wait_for_complete()
        assert not screen.busy
        await pilot.press("escape")
        assert isinstance(app.screen, Home)


async def test_cleanup_screen_busy_preview_cannot_enable_apply(
    wb: Workbench,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = actual_cleanup(wb.storage, wb.settings)
    report.skipped = "database_busy"
    report.notes = ["An active database operation deferred cleanup; retry later."]

    def busy(storage: Storage, settings: Settings, *, dry_run: bool = True) -> CleanupReport:
        return report

    monkeypatch.setattr("jevlab.tui.harness.cleanup", busy)
    app = JevApp(wb)
    async with app.run_test(size=(110, 40)):
        await app.push_screen(CleanupScreen(wb))
        await app.workers.wait_for_complete()
        screen = cast(CleanupScreen, app.screen)
        assert screen.query_one("#cleanup-apply", Button).disabled
        assert not screen.query_one("#cleanup-preview", Button).disabled
        assert "deferred" in str(screen.query_one("#cleanup-status", Static).render())


async def test_cleanup_preview_error_clears_previous_apply_permission(
    wb: Workbench,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(storage: Storage, settings: Settings, *, dry_run: bool = True) -> CleanupReport:
        raise sqlite3.OperationalError("synthetic read failure")

    app = JevApp(wb)
    async with app.run_test(size=(110, 40)) as pilot:
        await app.push_screen(CleanupScreen(wb))
        await app.workers.wait_for_complete()
        screen = cast(CleanupScreen, app.screen)
        assert screen.report is not None
        monkeypatch.setattr("jevlab.tui.harness.cleanup", unavailable)
        screen.run_cleanup()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert screen.report is None and screen.query_one("#cleanup-apply", Button).disabled
        message = str(screen.query_one("#cleanup-status", Static).render())
        assert "Local history could not be read or updated" in message
        assert "jevlab doctor" in message and "synthetic read failure" not in message
