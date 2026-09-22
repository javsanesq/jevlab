"""Native protected credential stores, with environment fallback; never plaintext."""

import asyncio
import os
import sys
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from typing import Literal, Protocol, cast

from jevlab.core.errors import JevError

Provider = Literal["typesafe", "anthropic", "openai"]
ENV_KEYS: dict[Provider, str] = {
    "typesafe": "TYPESAFE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}
SERVICE = "jevlab"
LEGACY_SERVICE = "jev-workbench"


class KeyStore(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...


def native_store() -> KeyStore:
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring

        return cast(KeyStore, Keyring())
    if sys.platform == "linux":
        # Select the Secret Service backend explicitly. keyring's automatic
        # selection can include third-party backends that persist plaintext.
        from keyring.backends.SecretService import Keyring

        return cast(KeyStore, Keyring())
    raise JevError(
        "keychain_unavailable",
        "Protected credential storage is unavailable on this platform.",
        "Use the provider environment variable with credential_mode=environment.",
    )


def secure_store_name() -> str:
    return "Secret Service" if sys.platform == "linux" else "Keychain"


def secure_store_description() -> str:
    return "Linux Secret Service" if sys.platform == "linux" else "macOS Keychain"


def secure_store_fix(provider: Provider) -> str:
    if sys.platform == "linux":
        return (
            "Start and unlock a Secret Service provider in your desktop D-Bus session, "
            f"or use {ENV_KEYS[provider]} with credential_mode=environment."
        )
    return (
        "Unlock your login Keychain and allow access, or use "
        f"{ENV_KEYS[provider]} with credential_mode=environment."
    )


class Credentials:
    def __init__(self, mode: str = "keychain", store: KeyStore | None = None) -> None:
        self.mode = mode
        self._store = store

    def _backend(self) -> KeyStore:
        return self._store if self._store is not None else native_store()

    def resolve(self, provider: Provider = "typesafe") -> tuple[str | None, str]:
        if self.mode == "keychain":
            try:
                backend = self._backend()
                value = backend.get_password(SERVICE, provider)
                if value and value.strip():
                    return value.strip(), secure_store_name().lower()
                value = backend.get_password(LEGACY_SERVICE, provider)
                if value and value.strip():
                    return value.strip(), f"{secure_store_name().lower()} (legacy jev-workbench)"
            except Exception:
                value = os.environ.get(ENV_KEYS[provider], "").strip()
                return (
                    (value, f"environment ({secure_store_name()} unavailable)")
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
                f"Run jevlab config or set {ENV_KEYS[provider]} in your environment.",
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
                f"Could not save the key to {secure_store_description()}.",
                secure_store_fix(provider),
                3,
            ) from None

    def status(self, *, timeout_seconds: float = 5.0) -> dict[str, object]:
        """Check all sources within one deadline, without passing keys between threads."""
        result: dict[str, object] = {}
        completed: Queue[tuple[Provider, bool, str]] = Queue()
        cancelled = Event()
        deadline = monotonic() + timeout_seconds

        def retrieve() -> None:
            for provider in ENV_KEYS:
                if cancelled.is_set():
                    return
                try:
                    value, source = self.resolve(provider)
                    present = bool(value)
                    del value
                except Exception:
                    present, source = False, "unavailable"
                if cancelled.is_set():
                    return
                completed.put((provider, present, source))

        Thread(target=retrieve, name="jevlab-keychain-status", daemon=True).start()
        try:
            while len(result) < len(ENV_KEYS):
                try:
                    provider, present, source = completed.get(
                        timeout=max(0.0, deadline - monotonic())
                    )
                except Empty:
                    break
                status: dict[str, object] = {"present": present, "source": source}
                if source == "unavailable":
                    status["error"] = JevError(
                        "keychain_unavailable",
                        f"The {provider} API key could not be checked in {secure_store_name()}.",
                        secure_store_fix(provider),
                        3,
                    ).as_dict()
                result[provider] = status
        finally:
            cancelled.set()
        for provider in ENV_KEYS:
            if provider not in result:
                result[provider] = {
                    "present": False,
                    "source": "unavailable",
                    "error": JevError(
                        "credential_timeout",
                        f"Checking the {provider} API key did not finish before the "
                        "diagnostic deadline; whether a key is present is unknown.",
                        secure_store_fix(provider),
                        3,
                    ).as_dict(),
                }
        return result


async def resolve_credentials(
    credentials: Credentials, provider: Provider
) -> tuple[str | None, str]:
    """Keep native credential prompts out of the event loop and its shutdown executor.

    Native calls cannot be cancelled. A daemon worker discards a late result after
    cancellation, without printing exceptions or retaining a pending async task.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[tuple[str | None, str]] = loop.create_future()

    def deliver(result: tuple[str | None, str] | Exception) -> None:
        if future.done():
            return
        if isinstance(result, Exception):
            future.set_exception(result)
        else:
            future.set_result(result)

    def retrieve() -> None:
        try:
            result: tuple[str | None, str] | Exception = credentials.resolve(provider)
        except Exception as error:
            result = error
        try:
            loop.call_soon_threadsafe(deliver, result)
        except RuntimeError:
            pass  # Cancellation may already have closed the caller's event loop.

    Thread(target=retrieve, name="jevlab-keychain", daemon=True).start()
    return await future


async def require_credentials(credentials: Credentials, *, timeout_seconds: float) -> str:
    """Bound TypeSafe credential lookup separately from a potentially billable call."""
    try:
        async with asyncio.timeout(timeout_seconds):
            key, _ = await resolve_credentials(credentials, "typesafe")
    except TimeoutError:
        raise JevError(
            "credential_timeout",
            "Reading the TypeSafe API key timed out. No API request was sent.",
            secure_store_fix("typesafe"),
            3,
        ) from None
    if not key:
        raise JevError(
            "missing_key",
            "No typesafe API key is available.",
            "Run jevlab config or set TYPESAFE_API_KEY in your environment.",
            3,
        )
    return key
