import copy
import json
from typing import Any

import pytest
from typer.testing import CliRunner

from jevlab.cli import diagnostics
from jevlab.cli.app import app
from jevlab.core.models import Settings

runner = CliRunner()


def rows(*, live: bool = False) -> list[dict[str, object]]:
    return [
        {
            "provider": provider,
            "sdk_installed": True,
            "sdk_version": "test-version",
            "key_found": True,
            "key_source": "keychain",
            "model": Settings().coach_model_for(provider),
            "ready": True,
            "blockers": [],
            "live_attempted": live,
            "status": "succeeded" if live else "not_checked",
            "error": None,
            "result": {"advice": {"summary": "Make the evidence boundary explicit."}}
            if live
            else None,
        }
        for provider in ("anthropic", "openai")
    ]


def mock_checks(monkeypatch: pytest.MonkeyPatch, *, failure: bool = False) -> list[bool]:
    calls: list[bool] = []

    async def check(settings: Settings, *, live: bool) -> list[dict[str, object]]:
        calls.append(live)
        result = copy.deepcopy(rows(live=live))
        if live and failure:
            result[0].update(
                status="failed",
                result=None,
                error={
                    "code": "coach_billing",
                    "message": "Anthropic says the credit balance is too low.",
                    "fix": "Check the Anthropic API billing page.",
                },
            )
        return result

    monkeypatch.setattr(diagnostics, "check_coaches", check)
    return calls


def test_coach_doctor_offline_preserves_single_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = mock_checks(monkeypatch)
    result = runner.invoke(app, ["doctor", "--coach", "--offline", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["ok"] and payload["schema_version"] == 1
    assert payload["data"]["live_requested"] is False and calls == [False]
    assert len(payload["data"]["estimates"]) == 2 and result.stderr == ""


def test_coach_doctor_machine_requires_spend_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = mock_checks(monkeypatch)
    result = runner.invoke(app, ["doctor", "--coach", "--json"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "cost_confirmation"
    assert "--yes" in payload["error"]["fix"] and calls == [False]
    assert result.stderr == ""


def test_coach_doctor_partial_failure_keeps_both_provider_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = mock_checks(monkeypatch, failure=True)
    result = runner.invoke(app, ["doctor", "--coach", "--yes", "--json"])
    assert result.exit_code == 4, result.output
    payload = json.loads(result.stdout)
    assert not payload["ok"] and calls == [False, True]
    providers = payload["data"]["providers"]
    assert providers[0]["error"]["code"] == "coach_billing"
    assert providers[1]["status"] == "succeeded" and providers[1]["result"]["advice"]
    assert result.stderr == "" and "Traceback" not in result.output


def test_coach_doctor_human_reports_setup_cost_and_advice(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = mock_checks(monkeypatch)
    result = runner.invoke(app, ["doctor", "--coach", "--yes"])
    assert result.exit_code == 0, result.output
    assert calls == [False, True]
    for phrase in ("SDK test-version", "key found in keychain", "about $", "Live call succeeded"):
        assert phrase in result.stdout
    assert "Make the evidence boundary explicit" in result.stdout


def test_coach_doctor_missing_sdk_is_reported_without_live_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def check(settings: Settings, *, live: bool) -> list[dict[str, object]]:
        calls.append(live)
        result = rows()
        for provider in result:
            provider.update(
                sdk_installed=False,
                sdk_version=None,
                ready=False,
                status="blocked",
                blockers=[
                    {
                        "code": "coach_dependency",
                        "message": "The SDK is not installed.",
                        "fix": "Run make install COACH=both.",
                    }
                ],
            )
        return result

    monkeypatch.setattr(diagnostics, "check_coaches", check)
    result = runner.invoke(app, ["doctor", "--coach", "--yes", "--json"])
    assert result.exit_code == 3
    payload: dict[str, Any] = json.loads(result.stdout)
    assert payload["data"]["providers"][0]["key_found"]
    assert payload["data"]["providers"][0]["blockers"][0]["code"] == "coach_dependency"
    assert calls == [False]


@pytest.mark.parametrize("args", [["--coach", "--online"], ["--offline"], ["--yes"]])
def test_coach_doctor_conflicting_options_are_safe_json(args: list[str]) -> None:
    result = runner.invoke(app, ["doctor", *args, "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_options"
