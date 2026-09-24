"""One budget rule decides interactive prompts; unattended cost gates stay unchanged."""

import json
from pathlib import Path

import pytest
from test_b_cli import evaluation, runner, spending, terminal, use_evaluator
from test_phase3_tui import dataset, finish_job, mock_runs
from textual.widgets import Input

from jevlab.cli.app import app
from jevlab.cli.evaluation import authorize
from jevlab.core.config import load_settings, save_settings
from jevlab.core.errors import JevError
from jevlab.core.jobs import BatchService
from jevlab.core.models import Settings
from jevlab.core.pricing import over_budget, price
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Confirm
from jevlab.tui.evaluation import EvalScreen, JobScreen
from jevlab.tui.screens import SettingsScreen


def test_retired_job_preferences_still_load_and_are_dropped_on_save(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        "schema_version = 1\nconfirm_batch_cost = false\nconfirm_eval_cost = true\n"
    )
    settings = load_settings(tmp_path)
    assert settings.confirm_cost_usd == 1.0
    save_settings(tmp_path, settings)
    text = (tmp_path / "config.toml").read_text()
    assert "confirm_batch_cost" not in text and "confirm_eval_cost" not in text


def test_alias_estimates_use_the_rate_it_resolved_to_on_the_price_date() -> None:
    pinned, _ = price("jev-1.13.0", 1_000)
    alias, snapshot = price("jev-latest", 1_000)
    assert alias == pinned == 42_000
    assert snapshot["priced_as"] == "jev-1.13.0"
    assert price("jev-9.9.9", 1_000)[0] is None
    assert not over_budget(42_000, 1.0) and over_budget(42_000, 0) and over_budget(None, 100)


def test_cli_within_budget_starts_without_prompt(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal(monkeypatch)
    prompts: list[str] = []
    monkeypatch.setattr(
        spending.typer, "confirm", lambda message, **_kwargs: prompts.append(message)
    )
    assert authorize(1, 42, "Estimate only.", False, False, False, wb=wb)
    assert authorize(5000, 999_999_999, "Estimate only.", False, False, False, wb=wb)
    assert not prompts


@pytest.mark.parametrize("cost", [None, 1_000_000_001])
def test_cli_unknown_or_over_budget_prompts_and_decline_sends_nothing(
    cost: int | None, wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal(monkeypatch)
    prompts: list[str] = []

    def decline(message: str, **_kwargs: object) -> bool:
        prompts.append(message)
        return False

    monkeypatch.setattr(spending.typer, "confirm", decline)
    with pytest.raises(JevError, match="No request was sent"):
        authorize(1, cost, "Estimate only.", True, False, False, wb=wb)
    assert len(prompts) == 1
    assert authorize(1, cost, "Estimate only.", True, True, False, wb=wb)
    assert len(prompts) == 1  # --yes is a one-off authorization.


def test_raised_budget_removes_prompt(wb: Workbench, monkeypatch: pytest.MonkeyPatch) -> None:
    terminal(monkeypatch)
    monkeypatch.setattr(spending.typer, "confirm", lambda *_args, **_kwargs: False)
    wb.update_settings(wb.settings.with_updates({"confirm_cost_usd": 5}))
    assert authorize(1, 4_000_000_000, "Estimate only.", False, False, False, wb=wb)


@pytest.mark.parametrize("machine", [False, True])
def test_unattended_budget_gate_still_requires_yes(
    machine: bool, wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evaluation, "interactive", lambda machine=False: False)
    with pytest.raises(JevError, match="No calls were started"):
        authorize(5000, None, "Unknown price.", True, False, machine, wb=wb)
    assert authorize(5000, None, "Unknown price.", True, True, machine, wb=wb)


@pytest.mark.parametrize("scope", ["batch", "eval"])
def test_cli_job_within_budget_runs_with_a_notice(
    scope: str, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 1 and "within your $1" in result.stderr
    assert "Continue and allow" not in result.stderr
    assert len(BatchService(wb).list(kind=scope)) == 1


@pytest.mark.parametrize("scope", ["batch", "eval"])
def test_machine_job_over_budget_still_requires_yes(
    scope: str, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = dataset(tmp_path)
    wb.update_settings(wb.settings.with_updates({"confirm_cost_usd": 0}))
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
    assert "Continue and allow" not in result.output
    assert len(wb.storage.history()) == 0


@pytest.mark.parametrize("scope", ["batch", "eval"])
async def test_tui_prompts_only_above_budget(
    scope: str, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        assert isinstance(app.screen, EvalScreen), app.last_error
        assert len(evaluator.requests) == 1

        await pilot.press("escape")
        wb.update_settings(wb.settings.with_updates({"confirm_cost_usd": 0}))
        if scope == "batch":
            screen.query_one("#output-path", Input).value = str(tmp_path / "second.jsonl")
        await finish_job(app, pilot, screen.prepare(run=True), approve=False)
        assert isinstance(app.screen, Confirm)
        await pilot.click("#keep")
        await app.workers.wait_for_complete()
        assert len(evaluator.requests) == 1


async def test_settings_screen_saves_confirmation_budget(wb: Workbench) -> None:
    app = JevApp(wb)
    async with app.run_test(size=(100, 40)) as pilot:
        settings = SettingsScreen(wb)
        app.push_screen(settings)
        await pilot.pause()
        settings.query_one("#confirm-cost", Input).value = "2.5"
        await pilot.press("ctrl+s")
        assert load_settings(wb.root).confirm_cost_usd == 2.5
        assert Settings.model_validate({"confirm_cost_usd": "0"}).confirm_cost_usd == 0
