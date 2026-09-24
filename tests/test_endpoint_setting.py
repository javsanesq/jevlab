"""The TypeSafe endpoint is an explicit, validated setting; the environment cannot redirect it."""

import json
from pathlib import Path

import httpx2
import pytest
from conftest import RESPONSE
from pydantic import ValidationError
from typer.testing import CliRunner

from jevlab.cli.app import app
from jevlab.core.client import SDKClient
from jevlab.core.jobs import BatchService
from jevlab.core.models import DEFAULT_BASE_URL, Settings, Template
from jevlab.core.service import Workbench


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://api.typesafe.ai", "https://api.typesafe.ai"),
        ("https://gateway.example.com/typesafe/", "https://gateway.example.com/typesafe"),
        ("http://127.0.0.1:9000", "http://127.0.0.1:9000"),
        ("http://localhost:9000/", "http://localhost:9000"),
    ],
)
def test_accepts_https_roots_and_loopback_http(value: str, expected: str) -> None:
    assert Settings(base_url=value).base_url == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://api.typesafe.ai",
        "ftp://api.typesafe.ai",
        "https://user:secret@api.typesafe.ai",
        "https://api.typesafe.ai/?key=secret",
        "https://api.typesafe.ai/#fragment",
        "api.typesafe.ai",
        "",
    ],
)
def test_rejects_unsafe_or_malformed_endpoints(value: str) -> None:
    with pytest.raises(ValidationError, match="https://"):
        Settings(base_url=value)


async def test_client_uses_configured_endpoint_not_the_environment(
    monkeypatch: pytest.MonkeyPatch, design: Template
) -> None:
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://unexpected.example.com")
    seen: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json=RESPONSE)

    settings = Settings(base_url="https://gateway.example.com/typesafe")
    async with SDKClient("synthetic-key", settings, transport=httpx2.MockTransport(respond)) as c:
        await c.evaluate(design, {"ticket": "refund"})
    assert seen == ["https://gateway.example.com/typesafe/v1/systemone"]
    assert Settings().base_url == DEFAULT_BASE_URL


def test_config_sets_and_reports_endpoint() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["config", "--set", "base_url=https://gateway.example.com/", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["settings"]["base_url"] == (
        "https://gateway.example.com"
    )
    rejected = runner.invoke(app, ["config", "--set", "base_url=http://example.com", "--json"])
    assert rejected.exit_code == 2
    assert json.loads(rejected.stdout)["error"]["code"] == "invalid_setting"


def test_changed_endpoint_invalidates_an_approved_job_plan(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    source = tmp_path / "cases.jsonl"
    source.write_text(json.dumps({"state": {"ticket": "refund"}}) + "\n")
    service = BatchService(wb)
    approved = service.plan(design, source)
    wb.settings = wb.settings.with_updates({"base_url": "https://gateway.example.com"})
    changed = service.plan(design, source)
    assert approved._approval_fingerprint != changed._approval_fingerprint
