"""macOS Keychain, with environment fallback. Never use a plaintext backend."""

import os
import sys
from typing import Literal, Protocol, cast

from jev.core.errors import JevError

Provider = Literal["typesafe", "anthropic", "openai"]
ENV_KEYS: dict[Provider, str] = {
    "typesafe": "TYPESAFE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}
SERVICE = "jev-workbench"


class KeyStore(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...


def native_store() -> KeyStore:
    if sys.platform != "darwin":
        raise JevError(
            "keychain_unavailable", "macOS Keychain is unavailable.", "Use environment mode."
        )
    from keyring.backends.macOS import Keyring

    return cast(KeyStore, Keyring())


class Credentials:
    def __init__(self, mode: str = "keychain", store: KeyStore | None = None) -> None:
        self.mode = mode
        self._store = store

    def _backend(self) -> KeyStore:
        return self._store if self._store is not None else native_store()

    def resolve(self, provider: Provider = "typesafe") -> tuple[str | None, str]:
        if self.mode == "keychain":
            try:
                value = self._backend().get_password(SERVICE, provider)
                if value and value.strip():
                    return value.strip(), "keychain"
            except Exception:
                value = os.environ.get(ENV_KEYS[provider], "").strip()
                return (
                    (value, "environment (Keychain unavailable)")
                    if value
                    else (None, "unavailable")
                )
        value = os.environ.get(ENV_KEYS[provider], "").strip()
        return (value, "environment") if value else (None, "missing")

    def require(self, provider: Provider = "typesafe") -> str:
        key, _ = self.resolve(provider)
        if not key:
            raise JevError(
                "missing_key",
                f"No {provider} API key is available.",
                f"Run jev config or set {ENV_KEYS[provider]} in your environment.",
                3,
            )
        return key

    def save(self, provider: Provider, value: str) -> None:
        if not value.strip():
            raise JevError("empty_key", "The key is empty.", "Enter a nonempty key.")
        try:
            self._backend().set_password(SERVICE, provider, value.strip())
        except Exception:
            raise JevError(
                "keychain_unavailable",
                "Could not save the key to macOS Keychain.",
                f"Unlock your login Keychain, or use {ENV_KEYS[provider]} in environment mode.",
                3,
            ) from None

    def status(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for provider in ENV_KEYS:
            value, source = self.resolve(provider)
            result[provider] = {"present": bool(value), "source": source}
        return result
