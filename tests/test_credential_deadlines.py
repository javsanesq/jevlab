"""A blocked native prompt must not become an unbounded or billable request."""

import asyncio
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from jev.core import doctor, service
from jev.core.credentials import Credentials, Provider
from jev.core.errors import JevError
from jev.core.jobs import BatchService
from jev.core.models import Template
from jev.core.service import Workbench
from jev.presentation import human_error


@pytest.mark.parametrize("operation", ["run", "doctor"])
def test_blocked_keychain_does_not_block_deadline_or_loop_shutdown(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    release = Event()
    wb.settings.deadline_seconds = 0.03

    def blocked(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        release.wait(5)
        return "synthetic-never-used-credential", "keychain"

    def never_call(*args: object, **kwargs: object) -> None:
        pytest.fail("A timed-out credential lookup must not initialize an API client.")

    monkeypatch.setattr(Credentials, "resolve", blocked)
    monkeypatch.setattr(service, "SDKClient", never_call)
    monkeypatch.setattr(doctor, "AsyncTypeSafeClient", never_call)
    start = monotonic()
    try:
        with pytest.raises(JevError) as caught:
            asyncio.run(
                wb.run(design, "synthetic state") if operation == "run" else doctor.online(wb)
            )
        assert monotonic() - start < 1.0  # Includes asyncio.run's executor shutdown.
        error = caught.value
        assert error.code == "credential_timeout" and error.exit_code == 3
        assert "No API request was sent" in human_error(error)
        assert "Unlock your login Keychain" in human_error(error)
        assert "TYPESAFE_API_KEY" in error.fix
        if operation == "run":
            saved = wb.storage.get(error.run_id or "")
            assert saved.status == "failed" and saved.error == error.as_dict()
            assert saved.response is None and saved.latency_ms is None
    finally:
        release.set()


async def test_cancel_during_key_lookup_records_no_request_sent(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = Event()
    release = Event()

    def blocked(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        entered.set()
        release.wait(5)
        return "synthetic-unused-credential", "keychain"

    monkeypatch.setattr(Credentials, "resolve", blocked)
    task = asyncio.create_task(wb.run(design, "synthetic state"))
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        saved = wb.storage.history()[0]
        assert saved.status == "interrupted"
        assert saved.error and "No API request was sent" in str(saved.error["message"])
        assert saved.latency_ms is None
    finally:
        release.set()


async def test_online_doctor_obeys_network_deadline(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx2
    from typesafe_sdk import AsyncTypeSafeClient

    wb.settings.deadline_seconds = 0.03
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-test-key")

    async def respond(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(1)
        raise AssertionError("The configured deadline should cancel this wait.")

    def client(**kwargs: object) -> AsyncTypeSafeClient:
        return AsyncTypeSafeClient(
            api_key="synthetic-test-key", transport=httpx2.MockTransport(respond)
        )

    monkeypatch.setattr(doctor, "AsyncTypeSafeClient", client)
    start = monotonic()
    with pytest.raises(JevError) as caught:
        await doctor.online(wb)
    assert monotonic() - start < 0.5
    assert caught.value.code == "timeout"


async def test_credential_timeout_stops_batch_before_waiting_on_every_row(
    wb: Workbench, design: Template, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = Event()
    attempts = 0
    wb.settings.deadline_seconds = 0.03
    source = tmp_path / "cases.jsonl"
    source.write_text('{"state":"first synthetic case"}\n{"state":"second case"}\n')

    def blocked(self: Credentials, provider: Provider) -> tuple[str | None, str]:
        nonlocal attempts
        attempts += 1
        release.wait(5)
        return "synthetic-unused-credential", "keychain"

    monkeypatch.setattr(Credentials, "resolve", blocked)
    try:
        report = await BatchService(wb).run(design, source, concurrency=1)
        assert report.status == "failed" and report.finished_at is not None
        assert report.error and report.error["code"] == "credential_timeout"
        assert report.failed == 1 and attempts == 1
        assert len(wb.storage.history()) == 1
    finally:
        release.set()
