"""The models-list diagnostic shares the same credential-safe error boundary."""

import json
from typing import Any

import httpx2
import pytest
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from jev.core import doctor
from jev.core.errors import JevError
from jev.core.service import Workbench


async def test_online_doctor_preserves_reason_without_echoed_key(
    wb: Workbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "synthetic-unprefixed-doctor-credential"
    monkeypatch.setenv("TYPESAFE_API_KEY", key)

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/models"
        return httpx2.Response(
            401,
            json={"detail": {"message": f"This key has expired: {key}"}},
            headers={"x-typesafe-request-id": "synthetic-doctor-request"},
        )

    def client(**kwargs: Any) -> AsyncTypeSafeClient:
        kwargs.setdefault("retry", RetryPolicy(max_retries=0))
        return AsyncTypeSafeClient(
            **kwargs,
            transport=httpx2.MockTransport(respond),
        )

    monkeypatch.setattr(doctor, "AsyncTypeSafeClient", client)
    with pytest.raises(JevError) as caught:
        await doctor.online(wb)
    error = caught.value
    assert error.code == "authentication" and error.http_status == 401
    assert error.message == "This key has expired: [redacted]"
    assert error.request_id == "synthetic-doctor-request"
    assert error.details and error.details["response_body"]
    assert key not in json.dumps(error.as_dict())
