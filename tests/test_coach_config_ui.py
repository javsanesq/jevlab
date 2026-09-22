"""Separate coach models, safe configuration errors, and recoverable TUI failures."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from textual.widgets import Button, Input, Select, Static, TextArea
from textual.worker import WorkerCancelled
from typer.testing import CliRunner

from jevlab.cli.app import app as cli
from jevlab.coach.service import Coach, CoachResult
from jevlab.core.coach_models import DEFAULT_COACH_MODELS, CoachProvider, normalize_coach_model
from jevlab.core.config import load_settings, save_settings
from jevlab.core.errors import JevError
from jevlab.core.models import Settings
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.learning import CoachScreen
from jevlab.tui.screens import SettingsScreen


def test_legacy_config_migrates_display_name_without_changing_other_provider(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.toml").write_text(
        'credential_mode="environment"\ncoach_provider="anthropic"\ncoach_model="Opus 5"\n'
    )
    settings = load_settings(tmp_path)
    assert settings.coach_model == settings.anthropic_model == "claude-opus-5"
    assert settings.openai_model == "gpt-5.6-luna"
    save_settings(tmp_path, settings)
    restored = load_settings(tmp_path)
    assert restored == settings


@pytest.mark.parametrize(
    ("provider", "legacy", "owner", "expected"),
    [
        ("openai", "Opus 5", "anthropic", "claude-opus-5"),
        ("openai", "claude-opus-5", "anthropic", "claude-opus-5"),
        ("openai", "claude-custom", "anthropic", "claude-custom"),
        ("anthropic", "gpt-5.6-luna", "openai", "gpt-5.6-luna"),
        ("anthropic", "gpt-custom", "openai", "gpt-custom"),
        ("anthropic", "GPT 5.6 Luna", "openai", "gpt-5.6-luna"),
        ("anthropic", "o3", "openai", "o3"),
    ],
)
def test_legacy_provider_switch_recovers_model_ownership_and_allows_cli_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: CoachProvider,
    legacy: str,
    owner: str,
    expected: str,
) -> None:
    root = tmp_path / "legacy-profile"
    root.mkdir()
    (root / "config.toml").write_text(
        f'credential_mode="environment"\ncoach_provider="{provider}"\ncoach_model="{legacy}"\n'
    )
    monkeypatch.setenv("JEVLAB_HOME", str(root))
    settings = load_settings(root)
    assert settings.model_dump()[f"{owner}_model"] == expected
    assert settings.coach_provider == provider
    assert settings.coach_model == DEFAULT_COACH_MODELS[provider]
    result = CliRunner().invoke(cli, ["config", "--set", f"coach_provider={owner}", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["settings"]["coach_model"] == expected
    assert load_settings(root).coach_model == expected


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_unrecognized_legacy_custom_model_keeps_selected_provider(provider: str) -> None:
    settings = Settings.model_validate(
        {"coach_provider": provider, "coach_model": "custom-finetuned-model"}
    )
    assert settings.model_dump()[f"{provider}_model"] == "custom-finetuned-model"
    assert settings.coach_model == "custom-finetuned-model"


@pytest.mark.parametrize(
    "values",
    [
        {"coach_provider": "openai", "coach_model": "Opus 5", "anthropic_model": "claude-custom"},
        {"coach_provider": "anthropic", "coach_model": "gpt-custom", "openai_model": "gpt-custom"},
        {"coach_provider": "openai", "openai_model": "claude-opus-5"},
        {"coach_provider": "anthropic", "anthropic_model": "gpt-5.6-luna"},
        {"coach_provider": "openai", "coach_model": "sk-ant-synthetic-sensitive-marker"},
        {"coach_provider": "anthropic", "coach_model": "sk-proj-synthetic-sensitive-marker"},
        {"coach_provider": "openai", "coach_model": "claude-malformed model"},
        {"coach_provider": "anthropic", "coach_model": "gpt-malformed model"},
    ],
)
def test_legacy_recovery_never_weakens_explicit_models_or_accepts_invalid_ids(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


def test_default_and_disabled_models_remain_provider_specific() -> None:
    settings = Settings()
    assert settings.coach_provider == "disabled" and settings.coach_model == ""
    assert settings.anthropic_model == "claude-haiku-4-5-20251001"
    assert settings.openai_model == "gpt-5.6-luna"
    configured = settings.with_updates(
        {"anthropic_model": "claude-custom", "openai_model": "gpt-custom"}
    )
    assert configured.coach_model == ""  # Model configuration does not enable coaching.
    assert configured.coach_model_for("anthropic") == "claude-custom"
    assert configured.coach_model_for("openai") == "gpt-custom"
    for provider in ("anthropic", "openai"):
        enabled = configured.with_updates({"coach_provider": provider})
        assert enabled.coach_model == configured.coach_model_for(provider)
        disabled = enabled.with_updates({"coach_provider": "disabled"})
        assert disabled.coach_model == ""
        assert disabled.anthropic_model == "claude-custom"
        assert disabled.openai_model == "gpt-custom"


def test_legacy_updates_apply_only_to_selected_provider_and_preserve_custom_ids() -> None:
    settings = Settings(coach_provider="anthropic", coach_model="Opus 5")
    selected = settings.with_updates(
        {"coach_provider": "openai", "coach_model": "ft:gpt-custom:organization:run"}
    )
    assert selected.coach_model == selected.openai_model == "ft:gpt-custom:organization:run"
    assert selected.anthropic_model == "claude-opus-5"
    returned = selected.with_updates({"coach_provider": "anthropic"})
    assert returned.coach_model == "claude-opus-5"
    assert returned.openai_model == selected.openai_model
    explicit = Settings(
        coach_provider="anthropic", coach_model="Opus 5", anthropic_model="claude-custom"
    )
    assert explicit.coach_model == "claude-custom"


@pytest.mark.parametrize(
    "updates",
    [
        {"coach_model": "model-without-provider"},
        {"coach_provider": "anthropic", "coach_model": "one", "anthropic_model": "two"},
    ],
)
def test_ambiguous_model_updates_reject_without_echoing_input(updates: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Settings().with_updates(updates)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("anthropic_model", "gpt-5.6-luna"),
        ("openai_model", "claude-opus-5"),
        ("anthropic_model", "sk-ant-synthetic-sensitive-marker"),
        ("openai_model", "sk-proj-synthetic-sensitive-marker"),
        ("openai_model", "Bearer synthetic-sensitive-marker"),
        ("anthropic_model", "display name with spaces"),
        ("coach_provider", "unknown-provider"),
    ],
)
def test_invalid_model_config_is_one_safe_json_envelope_with_exit_two(
    wb: Workbench,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    monkeypatch.setattr("jevlab.cli.app.workbench", lambda: wb)
    before = wb.settings.model_dump()
    result = CliRunner().invoke(cli, ["config", "--set", f"{field}={value}", "--json"])
    assert result.exit_code == 2, result.output
    assert len(result.stdout.splitlines()) == 1
    data = json.loads(result.stdout)
    assert data["schema_version"] == 1 and data["ok"] is False
    assert data["error"]["code"] == "invalid_setting"
    assert "coach provider or model" in data["error"]["message"]
    assert "model ID for that provider" in data["error"]["fix"]
    assert "without spaces or API keys" in data["error"]["fix"]
    assert "synthetic-sensitive-marker" not in result.output
    assert "Traceback" not in result.output and result.stderr == ""
    assert wb.settings.model_dump() == before
    assert "synthetic-sensitive-marker" not in (wb.root / "config.toml").read_text()


def test_human_config_model_error_uses_stderr_and_has_provider_guidance(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jevlab.cli.app.workbench", lambda: wb)
    result = CliRunner().invoke(cli, ["config", "--set", "openai_model=claude-opus-5"])
    assert result.exit_code == 2 and result.stdout == ""
    assert "coach provider or model" in result.stderr
    assert "model ID for that provider" in result.stderr
    assert "Traceback" not in result.stderr


def test_model_validation_message_does_not_echo_secret() -> None:
    with pytest.raises(ValueError) as caught:
        normalize_coach_model("sk-ant-synthetic-sensitive-marker", "anthropic")
    assert "synthetic-sensitive-marker" not in str(caught.value)
    with pytest.raises(ValidationError):
        Settings(anthropic_model="gpt-5.6-luna")


def test_invalid_legacy_file_is_reported_without_echoing_its_contents(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        'coach_provider="openai"\ncoach_model="sk-proj-synthetic-sensitive-marker"\n'
    )
    with pytest.raises(JevError) as caught:
        load_settings(tmp_path)
    assert caught.value.code == "invalid_config"
    assert "synthetic-sensitive-marker" not in str(caught.value)


def test_cli_old_model_field_and_provider_only_switch_keep_separate_models(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jevlab.cli.app.workbench", lambda: wb)
    commands = [
        (["coach_provider=anthropic", "coach_model=Opus 5"], "claude-opus-5"),
        (["coach_provider=openai"], DEFAULT_COACH_MODELS["openai"]),
        (["coach_model=gpt-custom"], "gpt-custom"),
        (["coach_provider=anthropic"], "claude-opus-5"),
        (["coach_provider=disabled"], ""),
    ]
    for updates, active in commands:
        args = ["config", *(argument for update in updates for argument in ("--set", update))]
        result = CliRunner().invoke(cli, [*args, "--json"])
        assert result.exit_code == 0, result.output
        settings = json.loads(result.stdout)["data"]["settings"]
        assert settings["coach_model"] == active
        assert settings["anthropic_model"] == "claude-opus-5"
    assert load_settings(wb.root).openai_model == "gpt-custom"


async def test_settings_screen_saves_two_models_and_switches_provider(wb: Workbench) -> None:
    app = JevApp(wb, start="coach")
    async with app.run_test(size=(120, 48)) as pilot:
        coach = app.screen
        assert isinstance(coach, CoachScreen)
        settings = SettingsScreen(wb)
        app.push_screen(settings)
        await pilot.pause()
        assert not settings.query("#coach-model")
        settings.query_one("#coach-provider", Select).value = "anthropic"
        settings.query_one("#coach-anthropic-model", Input).value = "Opus 5"
        settings.query_one("#coach-openai-model", Input).value = "gpt-custom"
        await pilot.press("ctrl+s")
        assert wb.settings.coach_model == wb.settings.anthropic_model == "claude-opus-5"
        assert wb.settings.openai_model == "gpt-custom"
        assert settings.query_one("#coach-anthropic-model", Input).value == "claude-opus-5"
        settings.query_one("#coach-provider", Select).value = "openai"
        await pilot.press("ctrl+s", "escape")
        await pilot.pause()
        assert app.screen is coach
        assert wb.settings.coach_model == "gpt-custom"
        assert load_settings(wb.root).anthropic_model == "claude-opus-5"
        status = str(coach.query_one("#coach-provider-status", Static).content)
        assert "openai" in status and "gpt-custom" in status


async def test_unexpected_coach_failure_restores_ui_and_never_shows_exception_data(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(self: Coach, intent: str, name: str) -> CoachResult:
        raise RuntimeError("synthetic-sensitive-marker untrusted traceback contents")

    monkeypatch.setattr(Coach, "design", fail)
    app = JevApp(wb, start="coach")
    async with app.run_test(size=(120, 48)) as pilot:
        screen = app.screen
        assert isinstance(screen, CoachScreen)
        screen.query_one("#coach-intent", TextArea).text = "Route support requests."
        await screen.ask().wait()
        await pilot.pause()
        output = str(screen.query_one("#coach-response", Static).content)
        assert "unexpected internal error" in output
        assert "jevlab doctor --coach" in output
        assert "Requesting advice" not in output
        assert "synthetic-sensitive-marker" not in output and "Traceback" not in output
        assert not screen.busy and not screen.query_one("#ask-coach", Button).disabled
        assert screen.query_one("#edit-proposal", Button).disabled
        assert screen.query_one("#raw-advice", Button).disabled
        assert screen.result is None and wb.storage.history() == []


async def test_cancelled_coach_request_restores_ui_with_explicit_status(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    async def wait_for_cancel(self: Coach, intent: str, name: str) -> CoachResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancellation must prevent a response")

    monkeypatch.setattr(Coach, "design", wait_for_cancel)
    app = JevApp(wb, start="coach")
    async with app.run_test(size=(120, 48)) as pilot:
        screen = app.screen
        assert isinstance(screen, CoachScreen)
        screen.query_one("#coach-intent", TextArea).text = "Route support requests."
        worker = screen.ask()
        await asyncio.wait_for(started.wait(), timeout=2)
        assert screen.busy and screen.query_one("#ask-coach", Button).disabled
        worker.cancel()
        with pytest.raises(WorkerCancelled):
            await worker.wait()
        await pilot.pause()
        output = str(screen.query_one("#coach-response", Static).content)
        assert "cancelled" in output and "incur cost" in output
        assert "Requesting advice" not in output
        assert not screen.busy and not screen.query_one("#ask-coach", Button).disabled
        assert screen.result is None and wb.storage.history() == []
