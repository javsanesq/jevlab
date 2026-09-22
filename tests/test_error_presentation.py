"""Specific provider evidence survives SDK, CLI, TUI, and saved-history views."""

import json
from typing import Any

import httpx2
import pytest
from conftest import MockEvaluator
from textual.widgets import Static
from typer.testing import CliRunner
from typesafe_sdk import JSONContent

from jevlab.cli.app import app as cli
from jevlab.core.client import SDKClient
from jevlab.core.errors import JevError
from jevlab.core.models import Run, Settings, Template
from jevlab.core.service import Workbench
from jevlab.presentation import error_for_run, human_error
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import ErrorDetails
from jevlab.tui.screens import History, Playground, ResultScreen

HTTP_FAILURES = [
    (400, "Unknown model: jev", "api_usage_error", "model_not_found", "jev-latest"),
    (
        400,
        "Question route contains unsupported criteria.",
        "invalid_question",
        "bad_request",
        "field",
    ),
    (401, "This API key has expired.", "invalid_api_key", "authentication", "jevlab config"),
    (403, "Project cannot use this model.", "permission_denied", "permission", "project"),
    (429, "Account has insufficient_quota.", "insufficient_quota", "quota", "billing"),
    (404, "Model not found: retired-test", "model_not_found", "model_not_found", "Model"),
    (404, "Resource not found.", "not_found", "not_found", "doctor --online"),
    (413, "Request too large.", "payload_too_large", "context_limit", "Shorten"),
    (
        400,
        "Maximum context length exceeded.",
        "context_length_exceeded",
        "context_limit",
        "Shorten",
    ),
    (
        422,
        "Question impact must have at least two criteria.",
        "validation_error",
        "api_validation",
        "constraint",
    ),
    (429, "Slow down: per-minute request limit exceeded.", "rate_limit", "rate_limit", "Wait"),
    (503, "Inference worker is temporarily offline.", "unavailable", "api_error", "later"),
]


def install_mock_run(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], status: int
) -> MockEvaluator:
    original = wb.run
    evaluator = MockEvaluator(body, [status])

    async def run(
        template: Template, state: JSONContent, *, parent_run_id: str | None = None
    ) -> Run:
        return await original(template, state, evaluator=evaluator, parent_run_id=parent_run_id)

    monkeypatch.setattr(wb, "run", run)
    monkeypatch.setattr("jevlab.cli.app.workbench", lambda: wb)
    # Cost prompts are tested separately; exercise the real run/error boundary here.
    monkeypatch.setattr("jevlab.cli.app.confirm_spend", lambda *args, **kwargs: None)
    return evaluator


@pytest.mark.parametrize(("status", "message", "provider_code", "code", "fix"), HTTP_FAILURES)
def test_http_reason_survives_cli_json_and_verbose_history(
    wb: Workbench,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    message: str,
    provider_code: str,
    code: str,
    fix: str,
) -> None:
    body = {
        "detail": {"message": message, "error_type": provider_code},
        "trace": "body-only-detail",
    }
    evaluator = install_mock_run(wb, monkeypatch, body, status)
    runner = CliRunner()
    args = ["run", "support-triage", "--text", '{"ticket":{"message":"Synthetic refund"}}']
    failed = runner.invoke(cli, args)
    normal = " ".join(failed.stderr.split())
    assert failed.exit_code == 4 and failed.stdout == ""
    for value in (message, fix, f"HTTP status: {status}", "Request ID: test-request", "Saved run:"):
        assert value in normal
    assert "body-only-detail" not in normal and "Traceback" not in normal

    machine = runner.invoke(cli, [*args, "--json"])
    envelope = json.loads(machine.stdout)
    assert machine.exit_code == 4 and machine.stderr == ""
    assert envelope["schema_version"] == 1 and envelope["ok"] is False
    error = envelope["error"]
    assert error["code"] == code and error["message"] == message
    assert error["http_status"] == status and error["provider_code"] == provider_code
    assert error["details"]["response_body"] == body

    requests_before_history = len(evaluator.requests)
    ordinary_history = runner.invoke(cli, ["history", "show", error["run_id"]])
    assert ordinary_history.exit_code == 0 and ordinary_history.stderr == ""
    assert message in " ".join(ordinary_history.stdout.split())
    assert "body-only-detail" not in ordinary_history.stdout
    history = runner.invoke(cli, ["--verbose", "history", "show", error["run_id"]])
    assert history.exit_code == 0 and history.stderr == ""
    for value in (message, "test-request", "response_body", "body-only-detail", provider_code):
        assert value in " ".join(history.stdout.split())
    assert len(evaluator.requests) == requests_before_history


@pytest.mark.parametrize("failure", ["network", "timeout", "invalid_response", "unknown"])
def test_non_http_failures_reach_cli_with_safe_distinct_diagnostics(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    original = wb.run

    def respond(request: httpx2.Request) -> httpx2.Response:
        if failure == "network":
            raise httpx2.ConnectError("private transport context", request=request)
        if failure == "timeout":
            raise httpx2.ReadTimeout("private transport context", request=request)
        if failure == "unknown":
            raise RuntimeError("private transport context")
        return httpx2.Response(
            200,
            json={"unexpected": "invalid-success-body"},
            headers={"x-typesafe-request-id": "invalid-request"},
        )

    async def run(template: Template, state: JSONContent) -> Run:
        evaluator = SDKClient(
            "offline-secret", Settings(max_retries=0), transport=httpx2.MockTransport(respond)
        )
        return await original(template, state, evaluator=evaluator)

    monkeypatch.setattr(wb, "run", run)
    monkeypatch.setattr("jevlab.cli.app.workbench", lambda: wb)
    monkeypatch.setattr("jevlab.cli.app.confirm_spend", lambda *args, **kwargs: None)
    result = CliRunner().invoke(
        cli, ["--verbose", "run", "support-triage", "--text", '{"ticket":{"message":"Test"}}']
    )
    assert result.exit_code == 4 and result.stdout == ""
    reason = {
        "network": "network connection",
        "timeout": "time limit",
        "invalid_response": "unusable response",
        "unknown": "cause is unknown",
    }[failure]
    assert reason in " ".join(result.stderr.split())
    assert "exception_type" in result.stderr
    assert (
        "private transport context" not in result.stderr and "offline-secret" not in result.stderr
    )
    if failure == "invalid_response":
        assert "invalid-success-body" in result.stderr and "invalid-request" in result.stderr
        assert "rejected the request" not in result.stderr


@pytest.mark.parametrize(
    "status,message",
    [
        (400, "Unknown model: jev"),
        (422, "Question urgency needs two levels."),
        (429, "Per-minute request limit exceeded."),
    ],
)
async def test_tui_failure_and_f2_restore_full_evidence_from_history(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, status: int, message: str
) -> None:
    body = {
        "detail": {"message": message, "error_type": "api_usage_error"},
        "provider_debug": "retained-technical-detail",
        "literal": "[bold]Keep literal[/bold]",
        "authorization": "Bearer never-display-me",
    }
    evaluator = install_mock_run(wb, monkeypatch, body, status)

    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.press("enter")
        screen = app.screen
        assert isinstance(screen, Playground)
        await screen.action_run().wait()
        summary = str(screen.query_one("#play-status", Static).content)
        assert message in summary and f"HTTP status: {status}" in summary
        assert "Request ID: test-request" in summary
        assert "retained-technical-detail" not in summary
        await pilot.press("f2")
        assert isinstance(app.screen, ErrorDetails)
        details = str(app.screen.query_one("#error-details", Static).content)
        assert "response_body" in details and "retained-technical-detail" in details
        assert message in details and "never-display-me" not in details
        assert "[bold]Keep literal[/bold]" in str(
            app.screen.query_one("#error-details", Static).visual
        )

    requests_before_history = len(evaluator.requests)
    saved = wb.storage.history()[0]
    reopened = JevApp(wb)
    async with reopened.run_test(size=(100, 35)) as pilot:
        await reopened.push_screen(History(wb))
        history = reopened.screen
        assert isinstance(history, History)
        history.inspect_run()
        await pilot.pause()
        assert isinstance(reopened.screen, ResultScreen)
        await pilot.press("f2")
        assert isinstance(reopened.screen, ErrorDetails)
        detail = str(reopened.screen.query_one("#error-details", Static).content)
        for value in (
            message,
            saved.id,
            "test-request",
            "response_body",
            "retained-technical-detail",
        ):
            assert value in detail
        assert "never-display-me" not in detail
    assert len(evaluator.requests) == requests_before_history


async def test_legacy_history_falls_back_to_saved_ids_and_body(
    wb: Workbench, design: Template
) -> None:
    run = await wb.run(design, "Synthetic state", evaluator=MockEvaluator())
    run.error = {
        "code": "api_error",
        "message": "Legacy failure",
        "fix": "Read its saved response.",
    }
    error = error_for_run(run)
    assert error and error.request_id == run.request_id and error.run_id == run.id
    assert error.details and error.details["response_body"] == run.response


def test_diagnostics_are_literal_and_redacted() -> None:
    error = JevError(
        "api_error",
        "[bold]Literal provider text[/bold]\x1b[2J",
        "Contact support.",
        http_status=503,
        request_id="safe-id",
        details={"response_body": {"api_key": "synthetic-sensitive-value", "plain": "safe"}},
    )
    rendered = human_error(error, verbose=True)
    assert "[bold]Literal provider text[/bold]" in rendered and "\x1b" not in rendered
    assert "synthetic-sensitive-value" not in rendered and "[redacted]" in rendered
    assert "failed while processing" in rendered


@pytest.mark.parametrize("machine", [False, True])
def test_config_model_validation_keeps_field_and_remedy(machine: bool) -> None:
    args = ["config", "--set", "model=jev"] + (["--json"] if machine else [])
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2
    if machine:
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is False and envelope["error"]["code"] == "invalid_setting"
        reason = envelope["error"]["message"]
        assert result.stderr == ""
    else:
        reason = " ".join(result.stderr.split())
        assert result.stdout == ""
    assert "model" in reason and "jev-latest" in reason
    assert "Could not read or save" not in reason
