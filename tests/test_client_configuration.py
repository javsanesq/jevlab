"""SDK setup failures reach history and both interfaces before any request is sent."""

import json
import traceback

import httpx2
import pytest
from textual.widgets import Static
from typer.testing import CliRunner
from typesafe_sdk import TypeSafeError

from jevlab.cli.app import app as cli
from jevlab.core.client import SDKClient
from jevlab.core.errors import JevError
from jevlab.core.models import Settings, Template
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import ErrorDetails
from jevlab.tui.screens import Playground


@pytest.mark.parametrize("credential", ["offline invalid-key", "offline\ninvalid-key", "offline-ñ"])
async def test_malformed_key_reason_is_saved_without_disclosing_key(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch, credential: str
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", credential)
    requests: list[object] = []

    async def unexpected_request(*args: object, **kwargs: object) -> httpx2.Response:
        requests.append(None)
        pytest.fail("Client initialization must fail before dispatching an HTTP request.")

    monkeypatch.setattr(httpx2.AsyncClient, "request", unexpected_request)
    with pytest.raises(JevError, match="printable ASCII characters without whitespace") as caught:
        await wb.run(design, {"ticket": "Synthetic input"})
    error = caught.value
    assert error.code == "client_configuration" and error.exit_code == 3
    assert "No API request was sent" in error.message and "jevlab config" in error.fix
    assert error.details == {"exception_type": "TypeSafeError", "request_sent": False}
    assert requests == [] and not error.retryable and error.http_status is None
    saved = wb.storage.get(error.run_id or "")
    assert saved.status == "failed" and saved.error == error.as_dict()
    assert saved.response is None and saved.latency_ms is None and saved.cost_nanousd is None
    assert credential not in json.dumps(saved.model_dump(), ensure_ascii=False)
    assert credential not in "".join(traceback.format_exception(error))
    assert credential.encode() not in wb.storage.path.read_bytes()


def test_setup_boundary_redacts_a_credential_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = "synthetic-client-setup-secret"

    def rejected_client(**kwargs: object) -> None:
        raise TypeSafeError(f"Invalid client option for {credential}")

    monkeypatch.setattr("jevlab.core.client.AsyncTypeSafeClient", rejected_client)
    with pytest.raises(JevError) as caught:
        SDKClient(credential, Settings())
    assert "Invalid client option" in caught.value.message
    assert "[redacted]" in caught.value.message
    assert credential not in json.dumps(caught.value.as_dict())
    assert credential not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("machine", [False, True])
def test_cli_reports_specific_client_setup_reason_and_credential_exit_code(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, machine: bool
) -> None:
    credential = "offline invalid-key"
    monkeypatch.setenv("TYPESAFE_API_KEY", credential)
    monkeypatch.setattr("jevlab.cli.app.workbench", lambda: wb)
    args = ["run", "support-triage", "--text", '{"ticket":"Synthetic input"}']
    result = CliRunner().invoke(cli, [*args, "--json"] if machine else args)
    assert result.exit_code == 3
    if machine:
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "client_configuration"
        assert error["details"]["request_sent"] is False
        assert error["run_id"]
        rendered = error["message"] + error["fix"]
        assert result.stderr == ""
    else:
        rendered = " ".join(result.stderr.split())
        assert result.stdout == "" and "Saved run:" in rendered
        assert "client settings are invalid" in rendered
    assert "printable ASCII characters without whitespace" in rendered
    assert "No API request was sent" in rendered and "jevlab config" in rendered
    assert credential not in result.output and "Traceback" not in result.output


async def test_tui_and_f2_show_client_setup_reason(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential = "offline invalid-key"
    monkeypatch.setenv("TYPESAFE_API_KEY", credential)
    app = JevApp(wb)
    async with app.run_test(size=(100, 35)) as pilot:
        await app.push_screen(Playground(wb, wb.templates.load("support-triage")))
        screen = app.screen
        assert isinstance(screen, Playground)
        await screen.action_run().wait()
        summary = str(screen.query_one("#play-status", Static).content)
        assert "printable ASCII characters without whitespace" in summary
        assert "No API request was sent" in summary and "jevlab config" in summary
        assert credential not in summary
        await pilot.press("f2")
        assert isinstance(app.screen, ErrorDetails)
        details = str(app.screen.query_one("#error-details", Static).content)
        assert "client_configuration" in details and '"request_sent": false' in details
        assert credential not in details
