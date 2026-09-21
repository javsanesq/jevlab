"""Explanations report saved facts; free commands do not touch credentials or history."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from jev.cli.guidance import register_guidance
from jev.core.demo import load_demo
from jev.core.guidance import GLOSSARY, explain_answer, explain_control
from jev.core.models import Run


@pytest.fixture
def guidance_cli() -> Iterator[typer.Typer]:
    app = typer.Typer()
    register_guidance(app)
    yield app


def test_requested_terms_have_plain_explanations_and_examples() -> None:
    required = {
        "Choice",
        "Score",
        "Noul",
        "State",
        "Question",
        "Confidence",
        "Probability",
        "Calibration",
        "Threshold",
        "Template",
        "Run",
        "Eval",
        "Token",
        "Latency",
    }
    assert required <= GLOSSARY.keys()
    assert all("example" in GLOSSARY[term].lower() for term in required)
    assert "separate" in GLOSSARY["Confidence"]
    assert "no separate confidence" in GLOSSARY["Noul"]


def test_explain_uses_static_ids_and_has_semantic_fallbacks() -> None:
    password_help = explain_control("api-key", "SettingsScreen")
    assert password_help.term == "API key"
    assert "password" in password_help.body
    assert "same case" in explain_control("questions", "TemplateEditor").body
    assert explain_control("confidence-route", "GradeScreen").term == "Confidence"
    assert explain_control(None, "CoachScreen").term == "Coach"
    assert "Ctrl+E" in explain_control("unknown-control", "UnknownScreen").body


def test_recording_is_valid_truthfully_labeled_and_not_a_live_capture() -> None:
    recording = load_demo()
    assert "not captured from a live" in recording.provenance
    assert "not a live call" in recording.disclaimer
    assert "illustrative" in recording.provenance
    assert recording.run.cost_nanousd == 0
    assert recording.run.sdk_version == "illustrative-fixture-no-sdk-call"
    assert recording.run.response is not None
    answers = recording.run.response["answers"]
    assert isinstance(answers, dict)
    assert set(answers) == {"route", "impact", "refund_requested"}


def test_saved_answers_explain_confidence_and_routing_without_new_decision() -> None:
    run = load_demo().run
    choice = explain_answer(run, "route")
    assert "billing" in choice and "0.85" in choice
    assert "separate" in choice and "automatic use" in choice
    assert "not sent or changed" in choice
    score = explain_answer(run, "impact")
    assert "0.30" in score and "0.80" in score and "a person should check" in score
    noul = explain_answer(run, "refund_requested")
    assert "95.00%" in noul and "no separate confidence" in noul
    assert "interpret this as yes" in noul
    assert "automatic use" in noul


def test_no_routing_information_defaults_to_human_review() -> None:
    run = load_demo().run.model_copy(update={"routing": None})
    assert "a person should check" in explain_answer(run, "route")
    assert "interpret this as yes" not in explain_answer(run, "refund_requested")
    assert "no recorded answer" in explain_answer(run, "missing")


def test_failed_run_never_invents_an_answer() -> None:
    run: Run = load_demo().run.model_copy(update={"status": "failed", "response": None})
    assert "no completed answer" in explain_answer(run, "route")
    assert "billing" not in explain_answer(run, "route")


@pytest.mark.parametrize("command", ["demo", "tour", "glossary"])
def test_json_guidance_is_free_without_home_or_key_access(
    command: str, guidance_cli: typer.Typer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def prohibited() -> None:
        pytest.fail("Free machine guidance must not initialize user storage or access keys.")

    monkeypatch.setattr("jev.cli.guidance.workbench", prohibited)
    root = tmp_path / "not-created"
    monkeypatch.setenv("JEV_HOME", str(root))
    result = CliRunner().invoke(guidance_cli, [command, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["schema_version"] == 1 and data["ok"] is True
    assert result.stderr == ""
    assert not root.exists()


def test_text_demo_is_labeled(guidance_cli: typer.Typer) -> None:
    result = CliRunner().invoke(guidance_cli, ["demo"])
    assert result.exit_code == 0
    assert "RECORDED EXAMPLE" in result.output
    assert "No key needed" in result.output
    assert "not captured from a live" in result.output


def test_unknown_glossary_term_is_a_normal_json_error(guidance_cli: typer.Typer) -> None:
    result = CliRunner().invoke(guidance_cli, ["glossary", "unknown-word", "--json"])
    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["ok"] is False and data["error"]["code"] == "glossary_term"
    assert "Traceback" not in result.output
