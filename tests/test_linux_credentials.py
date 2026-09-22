"""Linux credential selection is explicit, safe, and usable without a desktop."""

import json
import sys
from types import ModuleType

import pytest

from jevlab.core.credentials import Credentials, native_store
from jevlab.core.errors import JevError


class Store:
    def __init__(self, value: str | None = None, *, broken: bool = False) -> None:
        self.value = value
        self.broken = broken

    def get_password(self, service: str, username: str) -> str | None:
        if self.broken:
            raise RuntimeError("synthetic backend internals must remain private")
        return self.value

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.broken:
            raise RuntimeError("synthetic backend internals must remain private")
        self.value = password


def test_linux_selects_only_secret_service(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ModuleType("keyring.backends.SecretService")
    service.Keyring = Store  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring.backends.SecretService", service)
    monkeypatch.setattr(sys, "platform", "linux")
    backend = native_store()
    assert type(backend) is Store


def test_linux_credential_sources_and_headless_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-env-key")
    assert Credentials(store=Store("synthetic-protected-key")).resolve() == (
        "synthetic-protected-key",
        "secret service",
    )
    credentials = Credentials(store=Store(broken=True))
    assert credentials.resolve() == (
        "synthetic-env-key",
        "environment (Secret Service unavailable)",
    )
    assert Credentials("environment", Store(broken=True)).resolve() == (
        "synthetic-env-key",
        "environment",
    )
    assert "synthetic-env-key" not in json.dumps(credentials.status())


def test_linux_missing_service_gives_actionable_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(JevError) as caught:
        Credentials(store=Store(broken=True)).save("typesafe", "synthetic-key")
    error = caught.value
    assert error.code == "keychain_unavailable"
    assert "Linux Secret Service" in error.message
    assert "D-Bus" in error.fix
    assert "TYPESAFE_API_KEY" in error.fix
    assert "synthetic" not in json.dumps(error.as_dict())
