"""Interactive job preferences never weaken unattended cost gates or single-run output."""

import asyncio
import json
from pathlib import Path

import pytest
from test_b_cli import evaluation, runner, spending, terminal, use_evaluator
from test_phase3_tui import dataset, finish_job, mock_runs
from textual.widgets import Button, Checkbox, Input

from jevlab.cli.app import app
from jevlab.cli.evaluation import authorize
from jevlab.core.config import load_settings
from jevlab.core.errors import JevError
from jevlab.core.jobs import BatchService
from jevlab.core.models import Settings
from jevlab.core.service import Workbench
from jevlab.core.spending import SpendScope
from jevlab.tui.app import JevApp
from jevlab.tui.evaluation import EvalScreen, JobScreen
from jevlab.tui.screens import SettingsScreen
from jevlab.tui.spending import JobCostConfirm


def test_existing_settings_keep_job_prompts_enabled() -> None:
    settings = Settings.model_validate({"schema_version": 1})
    assert settings.confirm_batch_cost and settings.confirm_eval_cost


@pytest.mark.parametrize("scope", ["batch", "eval"])
def test_cli_remembers_only_accepted_scope(
    scope: SpendScope, wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal(monkeypatch)
    answers = iter([True, True, False])
    prompts: list[str] = []

    def respond(message: str, **_kwargs: object) -> bool:
        prompts.append(message)
        return next(answers)

    monkeypatch.setattr(spending.typer, "confirm", respond)
    assert authorize(1, 42, "Estimate only.", False, False, False, wb=wb, scope=scope)
    settings = load_settings(wb.root)
    assert getattr(settings, f"confirm_{scope}_cost") is False
    other = "eval" if scope == "batch" else "batch"
    assert getattr(settings, f"confirm_{other}_cost") is True
    assert len(prompts) == 2 and "Don't ask again" in prompts[1]
    assert authorize(5000, 100, "Estimate only.", False, False, False, wb=wb, scope=scope)
    assert len(prompts) == 2  # Persisted choice skips only this interactive scope.
    with pytest.raises(JevError, match="No request was sent"):
        authorize(1, 42, "Estimate only.", False, False, False, wb=wb, scope=other)
    assert len(prompts) == 3


@pytest.mark.parametrize("scope", ["batch", "eval"])
def test_cli_decline_never_remembers_and_yes_is_one_off(
    scope: SpendScope, wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal(monkeypatch)
    prompts: list[str] = []

    def decline(message: str, **_kwargs: object) -> bool:
        prompts.append(message)
        return False

    monkeypatch.setattr(spending.typer, "confirm", decline)
    with pytest.raises(JevError, match="No request was sent"):
        authorize(1, 42, "Estimate only.", False, False, False, wb=wb, scope=scope)
    assert len(prompts) == 1
    assert authorize(1, 42, "Estimate only.", False, True, False, wb=wb, scope=scope)
    assert len(prompts) == 1
    assert getattr(load_settings(wb.root), f"confirm_{scope}_cost") is True


@pytest.mark.parametrize("scope", ["batch", "eval"])
@pytest.mark.parametrize("machine", [False, True])
def test_remembered_preference_never_bypasses_script_budget(
    scope: SpendScope, machine: bool, wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb.update_settings(wb.settings.with_updates({f"confirm_{scope}_cost": False}))
    monkeypatch.setattr(evaluation, "interactive", lambda machine=False: False)
    with pytest.raises(JevError, match="No calls were started"):
        authorize(5000, None, "Unknown price.", True, False, machine, wb=wb, scope=scope)
    assert authorize(5000, None, "Unknown price.", True, True, machine, wb=wb, scope=scope)


@pytest.mark.parametrize("scope", ["batch", "eval"])
def test_cli_job_yes_runs_once_without_disabling_future_prompts(
    scope: SpendScope, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import MockEvaluator

    path = dataset(tmp_path)
    fake = MockEvaluator()
    use_evaluator(wb, fake, monkeypatch)
    monkeypatch.setattr(evaluation, "workbench", lambda: wb)
    terminal(monkeypatch)
    arguments = (
        ["batch", "support-triage", "--input", str(path), "--output", str(tmp_path / "out.jsonl")]
        if scope == "batch"
        else ["eval", "run", "support-triage", str(path)]
    )
    result = runner.invoke(app, [*arguments, "--yes"])
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 1 and "Estimated charge:" in result.stderr
    assert "Continue and allow" not in result.stderr and "Don't ask again" not in result.stderr
    assert getattr(load_settings(wb.root), f"confirm_{scope}_cost") is True
    assert len(BatchService(wb).list(kind=scope)) == 1


@pytest.mark.parametrize("scope", ["batch", "eval"])
def test_machine_job_with_remembered_preference_still_requires_yes(
    scope: SpendScope, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = dataset(tmp_path)
    wb.update_settings(
        wb.settings.with_updates({f"confirm_{scope}_cost": False, "confirm_cost_usd": 0})
    )
    monkeypatch.setattr(evaluation, "workbench", lambda: wb)
    arguments = (
        ["batch", "support-triage", "--input", str(path), "--output", str(tmp_path / "out.jsonl")]
        if scope == "batch"
        else ["eval", "run", "support-triage", str(path)]
    )
    result = runner.invoke(app, [*arguments, "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1 and payload["ok"] is False
    assert payload["error"]["code"] == "cost_confirmation"
    assert "Continue and allow" not in result.output and "Don't ask again" not in result.output
    assert len(wb.storage.history()) == 0


@pytest.mark.parametrize("scope", ["batch", "eval"])
async def test_tui_checked_cancel_keeps_prompt_but_checked_accept_persists(
    scope: SpendScope, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluator = mock_runs(wb, monkeypatch)
    path = dataset(tmp_path)
    app = JevApp(wb, start=scope)
    async with app.run_test(size=(120, 52)) as pilot:
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(path)
        if scope == "batch":
            screen.query_one("#output-path", Input).value = str(tmp_path / "out.jsonl")
        await finish_job(app, pilot, screen.prepare(run=True), approve=False)
        assert isinstance(app.screen, JobCostConfirm)  # Even this one-row file prompts.
        # Mount queues focus through app.call_later, followed by a widget Focus event.
        # A single pilot.pause() only drains events queued when its barrier started.
        async with asyncio.timeout(5):
            while not app.screen.query_one("#keep", Button).has_focus:
                await pilot.pause()
        assert app.screen.query_one("#keep", Button).has_focus
        app.screen.query_one("#remember-cost", Checkbox).value = True
        await pilot.click("#keep")
        await app.workers.wait_for_complete()
        assert not evaluator.requests
        assert getattr(load_settings(wb.root), f"confirm_{scope}_cost") is True

        await finish_job(app, pilot, screen.prepare(run=True), approve=False)
        assert isinstance(app.screen, JobCostConfirm)
        app.screen.query_one("#remember-cost", Checkbox).value = True
        await pilot.click("#discard")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, EvalScreen), app.last_error
        assert len(evaluator.requests) == 1
        saved = load_settings(wb.root)
        assert getattr(saved, f"confirm_{scope}_cost") is False
        other = "eval" if scope == "batch" else "batch"
        assert getattr(saved, f"confirm_{other}_cost") is True

        await pilot.press("escape")
        if scope == "batch":
            screen.query_one("#output-path", Input).value = str(tmp_path / "second.jsonl")
        await screen.prepare(run=True).wait()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, EvalScreen) and len(evaluator.requests) == 2


async def test_settings_can_restore_both_scoped_prompts(wb: Workbench) -> None:
    wb.update_settings(
        wb.settings.with_updates({"confirm_batch_cost": False, "confirm_eval_cost": False})
    )
    app = JevApp(wb)
    async with app.run_test(size=(100, 40)) as pilot:
        settings = SettingsScreen(wb)
        app.push_screen(settings)
        await pilot.pause()
        settings.query_one("#confirm-batch-cost", Checkbox).value = True
        settings.query_one("#confirm-eval-cost", Checkbox).value = True
        await pilot.press("ctrl+s")
        saved = load_settings(wb.root)
        assert saved.confirm_batch_cost and saved.confirm_eval_cost
