"""Jobs and the local server reuse one key lookup and one pooled SDK client."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from conftest import RESPONSE
from starlette.testclient import TestClient

from jevlab.core import jobs, service
from jevlab.core.client import SDKClient
from jevlab.core.credentials import Credentials, Provider
from jevlab.core.jobs import BatchService
from jevlab.core.models import Settings, Template
from jevlab.core.service import SharedEvaluator, Workbench
from jevlab.server.app import create_app

TOKEN = "local-test-token-at-least-32-characters"


class Counting:
    """Real SDK clients over a network-free transport, counting creation and closure."""

    def __init__(self, statuses: list[int] | None = None) -> None:
        self.clients: list[SDKClient] = []
        self.closed = 0
        self.requests = 0
        self.statuses = list(statuses or [])

    def __call__(self, key: str, settings: Settings) -> SDKClient:
        def respond(request: httpx2.Request) -> httpx2.Response:
            self.requests += 1
            assert request.headers["authorization"].endswith("synthetic-shared-key")
            status = self.statuses.pop(0) if self.statuses else 200
            return httpx2.Response(status, json=RESPONSE if status == 200 else {"error": "slow"})

        client = SDKClient(key, settings, transport=httpx2.MockTransport(respond))
        original = client.aclose

        async def aclose() -> None:
            self.closed += 1
            await original()

        client.aclose = aclose  # type: ignore[method-assign]
        self.clients.append(client)
        return client


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def resolve(self: Credentials, provider: Provider = "typesafe") -> tuple[str, str]:
        seen.append(provider)
        return "synthetic-shared-key", "environment"

    monkeypatch.setattr(Credentials, "resolve", resolve)
    return seen


def cases(tmp_path: Path, count: int) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "".join(json.dumps({"state": {"ticket": f"case {i}"}}) + "\n" for i in range(count))
    )
    return path


async def test_batch_reuses_one_lookup_and_one_client(
    wb: Workbench,
    design: Template,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lookups: list[str],
) -> None:
    factory = Counting()
    monkeypatch.setattr(service, "SDKClient", factory)
    report = await BatchService(wb).run(
        design, cases(tmp_path, 6), concurrency=3, requests_per_second=1000
    )
    assert report.status == "completed" and report.succeeded == 6
    assert lookups == ["typesafe"] and len(factory.clients) == 1
    assert factory.requests == 6 and factory.closed == 1


async def test_single_run_closes_its_own_client(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch, lookups: list[str]
) -> None:
    factory = Counting()
    monkeypatch.setattr(service, "SDKClient", factory)
    run = await wb.run(design, {"ticket": "refund please"})
    assert run.status == "succeeded" and factory.closed == 1 and lookups == ["typesafe"]


async def test_shared_lookup_failure_is_remembered_per_job_but_copied_per_run(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    # No key in this isolated profile: every row gets its own copy of one failure.
    shared = SharedEvaluator(wb.settings)
    errors = []
    for _ in range(2):
        with pytest.raises(Exception) as caught:
            await shared.connect()
        errors.append(caught.value)
    assert errors[0] is not errors[1]
    assert {getattr(error, "code", None) for error in errors} == {"missing_key"}


async def test_rate_limit_backs_off_the_limiter() -> None:
    limiter = jobs._RateLimiter(10)
    loop = asyncio.get_running_loop()
    limiter.back_off(2_500)
    assert limiter.interval == pytest.approx(0.2)
    assert limiter.next_start >= loop.time() + 2.4
    for _ in range(20):
        limiter.back_off()
    assert limiter.interval == 30.0


async def test_batch_slows_after_provider_429(
    wb: Workbench,
    design: Template,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lookups: list[str],
) -> None:
    factory = Counting(statuses=[429])
    monkeypatch.setattr(service, "SDKClient", factory)
    backoffs: list[Any] = []
    original = jobs._RateLimiter.back_off

    def record(self: jobs._RateLimiter, retry_after_ms: object = None) -> None:
        backoffs.append(retry_after_ms)
        original(self, 0)

    monkeypatch.setattr(jobs._RateLimiter, "back_off", record)
    report = await BatchService(wb).run(
        design, cases(tmp_path, 3), concurrency=1, requests_per_second=1000
    )
    assert report.failed == 1 and report.succeeded == 2 and len(backoffs) == 1
    assert factory.requests == 3  # Settings in this fixture disable SDK retries.


def test_server_reuses_one_client_across_requests(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, lookups: list[str]
) -> None:
    factory = Counting()
    monkeypatch.setattr(service, "SDKClient", factory)
    application = create_app(wb, TOKEN, requests_per_second=100)
    headers = {"Authorization": f"Bearer {TOKEN}", "Host": "127.0.0.1:8766"}
    with TestClient(application, base_url="http://127.0.0.1:8766") as client:
        for _ in range(3):
            response = client.post(
                "/templates/support-triage/run",
                json={"state": {"ticket": "refund please"}},
                headers=headers,
            )
            assert response.status_code == 200, response.text
    assert lookups == ["typesafe"] and len(factory.clients) == 1 and factory.closed == 1
