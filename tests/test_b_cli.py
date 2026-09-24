"""Human spending consent and friendly errors without changing scripting contracts."""

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import MockEvaluator
from test_learning import LabeledEvaluator
from typer.testing import CliRunner
from typesafe_sdk import JSONContent

from jevlab.cli.app import app
from jevlab.cli.evaluation import authorize
from jevlab.cli.spending import confirm_spend
from jevlab.coach.service import Coach, CoachResult
from jevlab.core.client import Evaluator
from jevlab.core.content import lesson, pattern, starter
from jevlab.core.credentials import Credentials
from jevlab.core.errors import JevError
from jevlab.core.models import Run, Settings, Template
from jevlab.core.service import Workbench
from jevlab.core.spending import (
    SpendEstimate,
    combine_estimates,
    estimate_coach,
    estimate_run,
)
from jevlab.presentation import human_error

commands = importlib.import_module("jevlab.cli.app")
learning = importlib.import_module("jevlab.cli.learning")
spending = importlib.import_module("jevlab.cli.spending")
evaluation = importlib.import_module("jevlab.cli.evaluation")
harness = importlib.import_module("jevlab.cli.harness")
runner = CliRunner()


def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    # CliRunner substitutes stdin. Model terminal detection itself, keeping the
    # machine argument so --json and --state - remain genuinely exempt.
    for module in (spending, learning, evaluation):
        monkeypatch.setattr(module, "interactive", lambda machine=False: not machine)


def use_evaluator(wb: Workbench, fake: Evaluator, monkeypatch: pytest.MonkeyPatch) -> None:
    original = wb.run

    async def mocked(
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        return await original(
            template, state, evaluator=fake, parent_run_id=parent_run_id, run_id=run_id
        )

    monkeypatch.setattr(wb, "run", mocked)


def test_estimates_are_offline_honest_and_do_not_mutate(design: Template) -> None:
    settings = Settings()
    known = estimate_run(design, "Refund please", settings)
    assert known.calls == 1 and known.estimate_nanousd is not None
    assert "not a spending limit" in known.limitations
    other = design.model_copy(update={"model": "jev-future"})
    unknown = estimate_run(other, "Refund please", settings)
    assert unknown.estimate_nanousd is None
    assert design.model == "jev-1.13.0"
    assert estimate_coach(settings, {}).calls == 0
    coach_settings = Settings(coach_provider="openai")
    coach = estimate_coach(coach_settings, {"intent": "Help route messages"})
    assert coach.estimate_nanousd is not None
    unknown_coach = estimate_coach(
        coach_settings.with_updates({"openai_model": "future-model"}), {}
    )
    assert unknown_coach.estimate_nanousd is None
    assert combine_estimates(known, unknown_coach).estimate_nanousd is None
    assert combine_estimates(known, coach).calls == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["coach", "critique", "support-triage"],
        ["coach", "design", "Route support tickets"],
    ],
)
def test_declining_human_action_sends_nothing(
    arguments: list[str], wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Above the confirmation budget, a human is asked before the coach is called.
    wb.update_settings(
        wb.settings.with_updates({"coach_provider": "openai", "confirm_cost_usd": 0})
    )
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    monkeypatch.setattr(learning, "workbench", lambda: wb)
    terminal(monkeypatch)
    result = runner.invoke(app, arguments, input="n\n")
    assert result.exit_code == 2, result.output
    assert "Estimated charge:" in result.stderr
    assert "Error:" in result.stderr and "What happened:" not in result.stderr
    assert "Next:" in result.stderr and "No request was sent" in result.stderr
    assert result.stdout.strip() == "n"  # The terminal echoes the user's answer.
    assert not wb.storage.history()


def test_rerun_starts_without_cost_consent(wb: Workbench, monkeypatch: pytest.MonkeyPatch) -> None:
    design = wb.templates.load("support-triage")
    previous = asyncio.run(wb.run(design, "Refund please", evaluator=MockEvaluator()))
    fake = MockEvaluator()
    use_evaluator(wb, fake, monkeypatch)
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    terminal(monkeypatch)
    result = runner.invoke(app, ["history", "rerun", previous.id])
    assert result.exit_code == 0, result.output
    assert "Estimated charge:" not in result.stderr and "Continue and allow" not in result.output
    assert len(fake.requests) == 1 and len(wb.storage.history()) == 2


def test_coach_explanation_above_budget_requires_new_consent(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = asyncio.run(
        wb.run(wb.templates.load("support-triage"), "Refund please", evaluator=MockEvaluator())
    )
    wb.update_settings(
        wb.settings.with_updates({"coach_provider": "openai", "confirm_cost_usd": 0})
    )
    monkeypatch.setattr(learning, "workbench", lambda: wb)
    terminal(monkeypatch)
    result = runner.invoke(app, ["coach", "explain", previous.id], input="n\n")
    assert result.exit_code == 2 and "Estimated charge:" in result.stderr
    assert len(wb.storage.history()) == 1


@pytest.mark.parametrize(
    "arguments,stdin",
    [
        (["run", "support-triage", "--text", "Refund please", "--format", "text", "--json"], ""),
        (["run", "support-triage", "--state", "-", "--format", "text"], "Refund please"),
    ],
)
def test_scripting_does_not_prompt_or_add_output(
    arguments: list[str], stdin: str, wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = MockEvaluator()
    use_evaluator(wb, fake, monkeypatch)
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    terminal(monkeypatch)
    result = runner.invoke(app, arguments, input=stdin)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] is True
    assert "Estimated charge:" not in result.stderr
    assert "Continue and allow" not in result.output
    assert len(fake.requests) == 1


def test_human_single_run_never_prompts(wb: Workbench, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MockEvaluator()
    use_evaluator(wb, fake, monkeypatch)
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    terminal(monkeypatch)
    result = runner.invoke(
        app, ["run", "support-triage", "--text", "Refund please", "--format", "text"]
    )
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 1
    assert "Continue and allow" not in result.output and "Estimated charge:" not in result.stderr
    assert "1,000 in" in result.stdout and "40 output tokens" in result.stdout
    assert " ms" in result.stdout and "$0.00004200 est." in result.stdout


def test_noninteractive_plain_output_keeps_script_behavior(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = MockEvaluator()
    use_evaluator(wb, fake, monkeypatch)
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    result = runner.invoke(
        app, ["run", "support-triage", "--text", "Refund please", "--format", "text"]
    )
    assert result.exit_code == 0 and len(fake.requests) == 1
    assert "Estimated charge:" not in result.stderr


def test_job_confirmation_applies_above_budget(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal(monkeypatch)
    wb.update_settings(wb.settings.with_updates({"confirm_cost_usd": 0}))
    seen: list[str] = []

    def decline(message: str, **kwargs: object) -> bool:
        seen.append(message)
        return False

    monkeypatch.setattr(spending.typer, "confirm", decline)
    with pytest.raises(JevError, match="No request was sent"):
        authorize(1, 1, "Estimate only.", True, False, False, wb=wb)
    assert len(seen) == 1
    assert authorize(1, 1, "Estimate only.", True, True, False, wb=wb)
    assert len(seen) == 1  # --yes is explicit consent, not a second prompt.
    assert not authorize(1, 1, "Estimate only.", False, False, True, wb=wb)
    with pytest.raises(JevError, match="No calls were started"):
        authorize(1, None, "Unknown estimate.", True, False, True, wb=wb)


@pytest.mark.parametrize("command", ["eval", "batch", "compare"])
def test_job_cli_decline_stops_a_charge_above_budget(
    command: str, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb.update_settings(wb.settings.with_updates({"confirm_cost_usd": 0}))
    dataset = tmp_path / "labeled.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "state": "Please refund a duplicate charge.",
                "expected": {"route": "billing", "impact": 0, "refund_requested": True},
            }
        )
        + "\n"
    )
    monkeypatch.setattr(evaluation, "workbench", lambda: wb)
    terminal(monkeypatch)
    arguments = {
        "eval": ["eval", "run", "support-triage", str(dataset)],
        "batch": [
            "batch",
            "support-triage",
            "--input",
            str(dataset),
            "--output",
            str(tmp_path / "results.jsonl"),
        ],
        "compare": [
            "compare",
            "support-triage",
            "support-triage",
            "--text",
            "Refund please",
            "--format",
            "text",
        ],
    }
    result = runner.invoke(app, arguments[command], input="n\n")
    assert result.exit_code == 2, result.output
    assert "No request was sent" in result.stderr and "Estimated charge:" in result.stderr
    assert not wb.storage.history()
    assert not (tmp_path / "results.jsonl").exists()


def test_declining_feedback_preserves_completed_grade(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = lesson("1")
    design = starter(item, "practice", wb.settings.model)
    wb.templates.save(design)
    wb.update_settings(
        wb.settings.with_updates({"coach_provider": "openai", "confirm_cost_usd": 0})
    )
    fake = LabeledEvaluator(pattern(item.pattern).cases)
    use_evaluator(wb, fake, monkeypatch)
    monkeypatch.setattr(learning, "workbench", lambda: wb)
    terminal(monkeypatch)

    async def forbidden(*args: object, **kwargs: object) -> CoachResult:
        pytest.fail("Declined feedback must not call the coach.")

    monkeypatch.setattr(Coach, "feedback", forbidden)
    result = runner.invoke(app, ["learn", "grade", "1", "--template", "practice"], input="y\nn\n")
    assert result.exit_code == 0, result.output
    assert "completed" in result.stdout
    assert result.stderr.count("Continue and allow") == 2
    assert "No request was sent" in result.stdout  # Feedback error only.
    assert len(wb.storage.history()) == len(pattern(item.pattern).cases)


def test_online_doctor_is_free_and_needs_no_prompt(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    terminal(monkeypatch)
    prompts: list[str] = []
    monkeypatch.setattr(spending.typer, "confirm", lambda message, **_: prompts.append(message))
    result = runner.invoke(app, ["doctor", "--online"])
    # No key in this profile: the model-list check stops before any connection.
    assert result.exit_code == 3 and "missing" not in result.stdout
    assert "$0.00000000" in result.stderr and not prompts


def test_server_start_explains_delegated_spending_without_prompt(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEVLAB_SERVER_TOKEN", "local-test-token-at-least-32-characters")
    monkeypatch.setattr(harness, "workbench", lambda: wb)
    served: list[object] = []
    monkeypatch.setattr(harness.uvicorn.Server, "run", lambda self, **_: served.append(self))
    terminal(monkeypatch)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert "billable Jev call" in result.stderr and "authorize_cost:true" in result.stderr
    assert "Continue and allow" not in result.output and len(served) == 1
    check = runner.invoke(app, ["serve", "--check"])
    assert check.exit_code == 0 and "billable" not in check.output


@pytest.mark.parametrize("verbose", [False, True])
def test_unexpected_error_hides_exception_and_preserves_machine_envelope(
    verbose: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sensitive-exception-contents"

    def fail() -> Workbench:
        raise RuntimeError(secret)

    monkeypatch.setattr(commands, "workbench", fail)
    flags = ["--verbose"] if verbose else []
    human = runner.invoke(app, [*flags, "templates"])
    assert human.exit_code == 2 and human.stdout == ""
    assert all(label in human.stderr for label in ("What happened:", "Why:", "Next:"))
    assert ("Details:" in human.stderr) == verbose
    assert secret not in human.output and "Traceback" not in human.output
    machine = runner.invoke(app, [*flags, "templates", "--json"])
    assert machine.exit_code == 2 and machine.stderr == ""
    data = json.loads(machine.stdout)
    assert data["schema_version"] == 1 and data["ok"] is False
    assert data["error"]["code"] == "internal_error"
    assert secret not in machine.stdout and "Details:" not in machine.stdout


def test_error_detail_is_safe_and_zero_call_estimate_never_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal(monkeypatch)
    error = JevError("safe_code", "Provider rejected sk-test-secret.", "Retry with valid access.")
    assert "sk-test-secret" not in human_error(error, verbose=True)
    assert "safe_code" in human_error(error, verbose=True)
    assert "Details:" not in human_error(error)
    assert not confirm_spend(
        SpendEstimate(0, 0, "Nothing to do.", "No requests."), settings=Settings()
    )


def test_configuration_mode_is_available_to_scripts() -> None:
    result = runner.invoke(app, ["config", "--set", "ui_mode=expert", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["settings"]["ui_mode"] == "expert"


def test_simple_reports_are_readable_and_expert_details_remain(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple"}))
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    report = runner.invoke(app, ["doctor"])
    assert report.exit_code == 0, report.output
    assert "Space used:" in report.stdout and "No internet" in report.stdout
    assert "schema_version" not in report.stdout
    config = runner.invoke(app, ["config", "--set", "ui_mode=simple"])
    assert config.exit_code == 0
    assert "Display: Simple" in config.stdout and "API keys are private" in config.stdout
    expert = runner.invoke(app, ["config", "--set", "ui_mode=expert"])
    assert expert.exit_code == 0
    assert '"ui_mode": "expert"' in expert.stdout
    assert '"coach_timeout_seconds"' in expert.stdout


def test_simple_config_distinguishes_saved_settings_from_live_key_readiness(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple"}))
    monkeypatch.setattr(commands, "workbench", lambda: wb)

    missing = runner.invoke(app, ["config", "--set", "ui_mode=simple"])
    assert missing.exit_code == 0
    assert "Settings saved. No TypeSafe key was found." in missing.stdout
    assert "jevlab config" in missing.stdout and "TYPESAFE_API_KEY" in missing.stdout
    assert "anthropic key:" not in missing.stdout and "openai key:" not in missing.stdout
    assert "Settings saved and ready" not in missing.stdout

    secret = "synthetic-test-key-value"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    found = runner.invoke(app, ["config", "--set", "ui_mode=simple"])
    assert found.exit_code == 0
    assert "A TypeSafe key was found; its validity was not tested." in found.stdout
    assert "typesafe key: found in environment" in found.stdout
    assert secret not in found.output

    machine = runner.invoke(app, ["config", "--json"])
    assert machine.exit_code == 0 and machine.stderr == ""
    payload = json.loads(machine.stdout)
    assert payload["schema_version"] == 1 and payload["ok"] is True
    assert payload["data"]["credentials"]["typesafe"]["present"] is True
    assert secret not in machine.stdout


@pytest.mark.parametrize("key", ["", "synthetic-test-key"])
def test_simple_interactive_setup_only_prompts_for_key_and_switches_mode_after_save(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    wb.update_settings(
        wb.settings.with_updates({"ui_mode": "simple", "credential_mode": "environment"})
    )
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    monkeypatch.setattr(
        commands, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    )
    prompts: list[str] = []
    saved: list[tuple[str, str]] = []

    def prompt(label: str, **_kwargs: object) -> str:
        prompts.append(label)
        return key

    def fail_confirm(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Simple setup must not ask advanced configuration questions")

    def save(_credentials: Credentials, provider: str, value: str) -> None:
        saved.append((provider, value))

    monkeypatch.setattr(commands.typer, "prompt", prompt)
    monkeypatch.setattr(commands.typer, "confirm", fail_confirm)
    monkeypatch.setattr(commands.Credentials, "save", save)
    monkeypatch.setattr(
        commands.Credentials,
        "status",
        lambda _credentials: {"typesafe": {"present": bool(saved), "source": "keychain"}},
    )

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.output
    assert prompts == ["TypeSafe API key (Enter to skip)"]
    assert saved == ([("typesafe", key)] if key else [])
    assert wb.settings.credential_mode == ("keychain" if key else "environment")
    if key:
        assert key not in result.output
    else:
        assert "No TypeSafe key was found" in result.stdout


def test_piped_key_activates_keychain_lookup_without_changing_json_contract(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb.update_settings(wb.settings.with_updates({"credential_mode": "environment"}))
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        commands.Credentials,
        "save",
        lambda _credentials, provider, value: saved.append((provider, value)),
    )
    monkeypatch.setattr(
        commands.Credentials,
        "status",
        lambda _credentials: {"typesafe": {"present": bool(saved), "source": "keychain"}},
    )
    secret = "synthetic-test-key"
    result = runner.invoke(app, ["config", "--key-stdin", "--json"], input=secret)
    assert result.exit_code == 0 and result.stderr == ""
    assert saved == [("typesafe", secret)]
    assert wb.settings.credential_mode == "keychain"
    assert secret not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1 and payload["ok"] is True
    assert payload["data"]["settings"]["credential_mode"] == "keychain"

    conflicting = runner.invoke(
        app,
        ["config", "--set", "credential_mode=environment", "--key-stdin", "--json"],
        input=secret,
    )
    assert conflicting.exit_code == 2 and conflicting.stderr == ""
    assert len(saved) == 1
    assert json.loads(conflicting.stdout)["error"]["code"] == "invalid_setting"


def test_simple_config_does_not_call_an_unchecked_key_ready(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb.update_settings(wb.settings.with_updates({"ui_mode": "simple"}))
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    original_status = commands.Credentials.status

    def unknown_status(credentials: Credentials) -> dict[str, object]:
        status = original_status(credentials)
        status["typesafe"] = {
            "present": False,
            "source": "unavailable",
            "error": JevError(
                "credential_timeout",
                "Checking the TypeSafe key timed out.",
                "Run jevlab doctor after unlocking Keychain.",
            ).as_dict(),
        }
        return status

    monkeypatch.setattr(commands.Credentials, "status", unknown_status)
    result = runner.invoke(app, ["config", "--set", "ui_mode=simple"])
    assert result.exit_code == 0
    assert "The TypeSafe key could not be checked." in result.stdout
    assert "Run jevlab doctor for the cause." in " ".join(result.stdout.split())
    assert "Settings saved and ready" not in result.stdout


@pytest.mark.parametrize("command", ["demo", "tour", "glossary"])
def test_free_commands_registered_with_machine_envelope(command: str) -> None:
    result = runner.invoke(app, [command, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] and result.stderr == ""
