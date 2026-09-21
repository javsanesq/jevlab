"""Local HTTP requests go through the real SDK mock; no paid inference."""

import asyncio
from collections.abc import AsyncIterator

import httpx2
import pytest
from conftest import MockEvaluator
from typesafe_sdk import JSONContent

from jev.core.client import Evaluation
from jev.core.models import Template
from jev.core.service import Workbench
from jev.server.app import MAX_BODY_BYTES, create_app, server_token

TOKEN = "offline-local-api-token-32-characters"
URL = "http://127.0.0.1:8766"


def client(wb: Workbench, evaluator: MockEvaluator, **kwargs: int) -> httpx2.AsyncClient:
    application = create_app(wb, TOKEN, evaluator=evaluator, **kwargs)
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=application),
        base_url=URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


async def test_http_run_auth_history_and_routing(wb: Workbench) -> None:
    evaluator = MockEvaluator()
    async with client(wb, evaluator) as http:
        health = await http.get("/health", headers={"Authorization": ""})
        assert health.status_code == 200
        templates = await http.get("/templates")
        assert templates.json()["data"][0]["name"] == "support-triage"
        response = await http.post(
            "/templates/support-triage/run",
            json={"state": {"ticket": {"message": "Refund please."}}},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["routing"]["route"]["disposition"] == "automate"
        assert wb.storage.get(data["id"]).response == data["response"]
        assert evaluator.requests[0]["state"] == {"ticket": {"message": "Refund please."}}
        assert TOKEN not in response.text


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Authorization": "Bearer wrong-token"}, 401),
        ({"Authorization": ""}, 401),
        ({"Host": "malicious.example:8766"}, 400),
        ({"Origin": "https://malicious.example"}, 403),
        ({"Origin": "http://localhost:8766"}, 403),
    ],
)
async def test_auth_origin_and_host_never_call_jev(
    wb: Workbench, headers: dict[str, str], status: int
) -> None:
    evaluator = MockEvaluator()
    async with client(wb, evaluator) as http:
        response = await http.post(
            "/templates/support-triage/run", json={"state": "test"}, headers=headers
        )
        assert response.status_code == status
    assert not evaluator.requests and not wb.storage.history()


@pytest.mark.parametrize(
    "body",
    [
        '{"state":null}',
        '{"state":1}',
        '{"state":""}',
        '{"state":"a","state":"b"}',
        '{"state":{"bad":NaN}}',
        '{"state":"a","authorize_cost":"true"}',
        '{"state":"a","model":"invented"}',
    ],
)
async def test_invalid_body_rejected_before_call(wb: Workbench, body: str) -> None:
    evaluator = MockEvaluator()
    async with client(wb, evaluator) as http:
        response = await http.post(
            "/templates/support-triage/run",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
    assert not evaluator.requests


async def test_body_limit_unknown_template_and_rate(wb: Workbench) -> None:
    evaluator = MockEvaluator()
    async with client(wb, evaluator, requests_per_second=1) as http:
        large = await http.post(
            "/templates/support-triage/run",
            content=b"x" * (MAX_BODY_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )
        assert large.status_code == 413
        assert (await http.post("/templates/missing/run", json={"state": "a"})).status_code == 404
        assert (
            await http.post("/templates/support-triage/run", json={"state": "a"})
        ).status_code == 200
        limited = await http.post("/templates/support-triage/run", json={"state": "a"})
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "1"
    assert len(evaluator.requests) == 1


async def test_server_cost_confirmation_and_safe_api_error(wb: Workbench) -> None:
    wb.settings.confirm_cost_usd = 0
    evaluator = MockEvaluator(statuses=[401])
    async with client(wb, evaluator) as http:
        assert (
            await http.post("/templates/support-triage/run", json={"state": "test"})
        ).status_code == 409
        assert not evaluator.requests
        failed = await http.post(
            "/templates/support-triage/run", json={"state": "test", "authorize_cost": True}
        )
        assert failed.status_code == 502
        assert failed.json()["error"]["code"] == "authentication"
        assert "offline-secret" not in failed.text
    assert wb.storage.history()[0].status == "failed"


async def test_server_bounded_concurrency_and_cancellation(wb: Workbench) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    class BlockingEvaluator(MockEvaluator):
        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            started.set()
            await release.wait()
            return await super().evaluate(template, state)

    evaluator = BlockingEvaluator()
    async with client(wb, evaluator, concurrency=1, requests_per_second=100) as http:
        task = asyncio.create_task(
            http.post("/templates/support-triage/run", json={"state": "test"})
        )
        await started.wait()
        busy = await http.post("/templates/support-triage/run", json={"state": "test"})
        assert busy.status_code == 503
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wb.storage.history()[0].status == "interrupted"
        release.set()
        assert (
            await http.post("/templates/support-triage/run", json={"state": "test"})
        ).status_code == 200


def test_local_token_never_reuses_provider_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    from jev.core.errors import JevError

    monkeypatch.delenv("JEV_SERVER_TOKEN", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "provider-key-not-for-local-server")
    with pytest.raises(JevError, match="JEV_SERVER_TOKEN"):
        server_token()
    monkeypatch.setenv("JEV_SERVER_TOKEN", TOKEN)
    assert server_token() == TOKEN


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/missing", 404, "not_found"),
        ("GET", "/templates/", 404, "not_found"),
        ("POST", "/templates", 405, "method_not_allowed"),
        ("GET", "/templates/support-triage/run", 405, "method_not_allowed"),
    ],
)
async def test_every_routing_error_is_guarded_json(
    wb: Workbench, method: str, path: str, status: int, code: str
) -> None:
    evaluator = MockEvaluator()
    async with client(wb, evaluator) as http:
        response = await http.request(method, path)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert response.headers["content-type"] == "application/json"
        if status == 405:
            assert "allow" in response.headers
        unauthorized = await http.request(method, path, headers={"Authorization": ""})
        assert unauthorized.status_code == 401
        hostile = await http.request(method, path, headers={"Host": "external.example"})
        assert hostile.status_code == 400
    assert not evaluator.requests


async def test_streamed_body_is_bounded_without_content_length(wb: Workbench) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"state":"'
        yield b"x" * MAX_BODY_BYTES
        pytest.fail("The server should reject the oversized stream before reading further.")

    evaluator = MockEvaluator()
    async with client(wb, evaluator) as http:
        response = await http.post(
            "/templates/support-triage/run",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "body_too_large"
    assert not evaluator.requests and not wb.storage.history()


async def test_slow_body_times_out_without_consuming_inference_slot(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jev.server.app.BODY_READ_TIMEOUT_SECONDS", 0.01)

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"state":"'
        await asyncio.Event().wait()
        yield b'never"}'

    evaluator = MockEvaluator()
    async with client(wb, evaluator, concurrency=1, requests_per_second=1) as http:
        response = await http.post(
            "/templates/support-triage/run",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 408
        assert response.json()["error"]["code"] == "body_timeout"
        assert not evaluator.requests
        # The abandoned upload did not consume rate or inference concurrency capacity.
        healthy = await http.post("/templates/support-triage/run", json={"state": "test"})
        assert healthy.status_code == 200
    assert len(evaluator.requests) == 1


async def test_unexpected_local_errors_are_safe_json_without_tracebacks(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    private_text = "private-fixture-must-not-appear"

    def broken_names() -> list[str]:
        raise OSError(private_text)

    monkeypatch.setattr(wb.templates, "names", broken_names)
    evaluator = MockEvaluator()
    async with client(wb, evaluator) as http:
        response = await http.get("/templates")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "local_error"
        assert private_text not in response.text
    assert private_text not in caplog.text
    assert not evaluator.requests


@pytest.mark.parametrize("control", ["\x00", "\x1b", "\x7f"])
def test_server_token_rejects_ascii_control_characters(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch, control: str
) -> None:
    from jev.core.errors import JevError

    token = TOKEN + control
    if control != "\x00":  # Operating systems themselves reject NUL in environment values.
        monkeypatch.setenv("JEV_SERVER_TOKEN", token)
        with pytest.raises(JevError):
            server_token()
    with pytest.raises(ValueError):
        create_app(wb, token)
