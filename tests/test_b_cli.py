"""Human spending consent and friendly errors without changing scripting contracts."""

import asyncio
import importlib
import json
from pathlib import Path

import pytest
from conftest import MockEvaluator
from test_learning import LabeledEvaluator
from typer.testing import CliRunner
from typesafe_sdk import JSONContent

from jev.cli.app import app
from jev.cli.evaluation import authorize
from jev.cli.spending import confirm_spend
from jev.coach.service import Coach, CoachResult
from jev.core.client import Evaluator
from jev.core.content import lesson, pattern, starter
from jev.core.errors import JevError
from jev.core.models import Run, Settings, Template
from jev.core.service import Workbench
from jev.core.spending import (
    SpendEstimate,
    combine_estimates,
    estimate_coach,
    estimate_run,
)
from jev.presentation import human_error

commands = importlib.import_module("jev.cli.app")
learning = importlib.import_module("jev.cli.learning")
spending = importlib.import_module("jev.cli.spending")
evaluation = importlib.import_module("jev.cli.evaluation")
harness = importlib.import_module("jev.cli.harness")
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
    wb.update_settings(wb.settings.with_updates({"coach_provider": "openai"}))
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    monkeypatch.setattr(learning, "workbench", lambda: wb)
    terminal(monkeypatch)
    result = runner.invoke(app, arguments, input="n\n")
    assert result.exit_code == 2, result.output
    assert "Estimated charge:" in result.stderr
    assert "What happened:" in result.stderr and "Why:" in result.stderr
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


def test_coach_explanation_requires_new_consent(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = asyncio.run(
        wb.run(wb.templates.load("support-triage"), "Refund please", evaluator=MockEvaluator())
    )
    wb.update_settings(wb.settings.with_updates({"coach_provider": "openai"}))
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


def test_job_confirmation_applies_below_old_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal(monkeypatch)
    seen: list[str] = []

    def decline(message: str, **kwargs: object) -> bool:
        seen.append(message)
        return False

    monkeypatch.setattr(spending.typer, "confirm", decline)
    with pytest.raises(JevError, match="No request was sent"):
        authorize(1, 1, "Estimate only.", False, False, False)
    assert len(seen) == 1
    assert authorize(1, 1, "Estimate only.", False, True, False)
    assert len(seen) == 1  # --yes is explicit consent, not a second prompt.
    assert not authorize(1, 1, "Estimate only.", False, False, True)
    with pytest.raises(JevError, match="No calls were started"):
        authorize(1, None, "Unknown estimate.", True, False, True)


@pytest.mark.parametrize("command", ["eval", "batch", "compare"])
def test_job_cli_decline_stops_even_a_tiny_charge(
    command: str, wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    wb.update_settings(wb.settings.with_updates({"coach_provider": "openai"}))
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


def test_online_doctor_can_cancel_before_connection(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "workbench", lambda: wb)
    terminal(monkeypatch)
    result = runner.invoke(app, ["doctor", "--online"], input="n\n")
    assert result.exit_code == 2
    assert "$0.00000000" in result.stderr
    assert "No request was sent" in result.stderr


def test_server_confirmation_explains_delegated_spending(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_SERVER_TOKEN", "local-test-token-at-least-32-characters")
    monkeypatch.setattr(harness, "workbench", lambda: wb)
    terminal(monkeypatch)
    result = runner.invoke(app, ["serve"], input="n\n")
    assert result.exit_code == 2, result.output
    assert "unknown" in result.stderr and "without individual prompts" in result.stderr
    assert "No request was sent" in result.stderr
    check = runner.invoke(app, ["serve", "--check"])
    assert check.exit_code == 0 and "Continue and allow" not in check.output


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
    assert not confirm_spend(SpendEstimate(0, 0, "Nothing to do.", "No requests."))


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


@pytest.mark.parametrize("command", ["demo", "tour", "glossary"])
def test_free_commands_registered_with_machine_envelope(command: str) -> None:
    result = runner.invoke(app, [command, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] and result.stderr == ""
