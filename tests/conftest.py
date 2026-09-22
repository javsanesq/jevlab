"""Offline by default, including accidental outbound socket attempts."""

import copy
import json
import socket
from pathlib import Path
from typing import Any

import httpx2
import pytest
from typesafe_sdk import JSONContent

from jevlab.core.client import Evaluation, SDKClient
from jevlab.core.models import Settings, Template
from jevlab.core.service import Workbench

RESPONSE: dict[str, Any] = {
    "model": "jev-1.13.0",
    "answers": {
        "route": {
            "type": "choice",
            "choice": "billing",
            "confidence": 0.85,
            "probabilities": {"billing": 0.9, "technical": 0.07, "other": 0.03},
        },
        "impact": {
            "type": "score",
            "score": 0.3,
            "confidence": 0.8,
            "legend": {"0": "None", "1": "Workaround", "2": "Blocked"},
            "probabilities": {"0": 0.8, "1": 0.1, "2": 0.1},
        },
        "refund_requested": {"type": "noul", "noul": 0.95},
    },
    "usage": {"input_tokens": 1000, "output_tokens": 40},
    "future_metadata": {"preserve": True},
}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live", action="store_true", default=False, help="Enable billable API tests."
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--live"):
        for item in items:
            if "live" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Requires explicit --live"))


@pytest.fixture(autouse=True)
def isolate(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if "live" in request.node.keywords:
        return
    for name in ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("JEV_HOME", "JEV_SERVER_TOKEN", "JEVLAB_SERVER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JEVLAB_HOME", str(tmp_path / "cli-home"))

    def blocked(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline tests must never make a real network connection.")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def wb(tmp_path: Path) -> Workbench:
    bench = Workbench(tmp_path / "data")
    bench.update_settings(
        Settings(
            credential_mode="environment", max_retries=0, ui_mode="expert", tour_completed=True
        )
    )
    return bench


@pytest.fixture
def design(wb: Workbench) -> Template:
    return wb.templates.load("support-triage")


class MockEvaluator:
    """Use the actual official SDK with a network-free HTTP transport."""

    def __init__(
        self, body: dict[str, Any] | None = None, statuses: list[int] | None = None
    ) -> None:
        self.body = copy.deepcopy(RESPONSE if body is None else body)
        self.statuses = list(statuses or [200])
        self.requests: list[dict[str, Any]] = []

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        def respond(request: httpx2.Request) -> httpx2.Response:
            assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
            self.requests.append(json.loads(request.content))
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx2.Response(
                status,
                json=self.body,
                headers={"x-typesafe-request-id": "test-request", "retry-after-ms": "1"},
            )

        client = SDKClient(
            "offline-secret", Settings(max_retries=1), transport=httpx2.MockTransport(respond)
        )
        return await client.evaluate(template, state)


@pytest.fixture
def evaluator() -> MockEvaluator:
    return MockEvaluator()
