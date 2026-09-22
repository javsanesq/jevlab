"""Explicit, bounded coach checks; credentials stay in memory and advice stays advisory."""

import asyncio
import json
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from math import ceil

import httpx2

from jevlab.coach.errors import coach_error, sanitize_text
from jevlab.coach.service import (
    DIAGNOSTIC_INSTRUCTIONS,
    SYSTEM,
    Coach,
    ProviderAdvisor,
    resolve_credentials,
)
from jevlab.core.coach_models import CoachProvider
from jevlab.core.credentials import ENV_KEYS, Credentials
from jevlab.core.errors import JevError
from jevlab.core.models import Settings, Template

PROVIDERS: tuple[CoachProvider, ...] = ("anthropic", "openai")
PROBE_OUTPUT_TOKENS = 256
PRICE_VERIFIED = "2026-09-20"
PRICE_SOURCES = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "openai": "https://developers.openai.com/api/docs/pricing/",
}
# Nanodollars per token, direct API, standard service, uncached short input.
# Only explicitly verified IDs are priced. Arbitrary configured IDs remain valid
# choices but require an acknowledged unknown estimate; never guess alias prices.
RATES: dict[str, tuple[int, int]] = {
    "claude-opus-5": (5_000, 25_000),
    "claude-sonnet-5": (2_000, 10_000),
    "claude-haiku-4-5": (1_000, 5_000),
    "claude-haiku-4-5-20251001": (1_000, 5_000),
    "gpt-6-astra": (10_000, 50_000),
    "gpt-5.6-sol": (4_000, 20_000),
    "gpt-5.6-terra": (2_000, 12_000),
    "gpt-5.6-luna": (200, 1_200),
}


def probe_payload(settings: Settings) -> dict[str, object]:
    """No local templates, runs, datasets, or user state leave the machine."""
    template = Template.model_validate(
        {
            "name": "coach-connectivity-check",
            "description": "A synthetic template used only to check the coach connection.",
            "model": settings.model,
            "state": {"description": "One short support message.", "format": "text"},
            "questions": {
                "topic": {
                    "type": "choice",
                    "instructions": "Choose the main topic of the support message.",
                    "criteria": {
                        "billing": "Payments, invoices, or refunds.",
                        "other": "All other topics.",
                    },
                }
            },
        }
    )
    return {"template": template.model_dump(mode="json")}


def probe_estimates(settings: Settings) -> list[dict[str, object]]:
    """Offline estimates for confirmation; this function never reads credentials."""
    prompt = json.dumps(
        {"task": "critique", "data": probe_payload(settings)}, ensure_ascii=False, allow_nan=False
    )
    estimated_input_tokens = (
        ceil(len((SYSTEM + DIAGNOSTIC_INSTRUCTIONS + prompt).encode()) / 3) + 100
    )
    rows: list[dict[str, object]] = []
    for provider in PROVIDERS:
        model = settings.coach_model_for(provider)
        rates = RATES.get(model)
        estimate = (
            estimated_input_tokens * rates[0] + PROBE_OUTPUT_TOKENS * rates[1] if rates else None
        )
        rows.append(
            {
                "provider": provider,
                "model": sanitize_text(model),
                "estimated_input_tokens": estimated_input_tokens,
                "max_output_tokens": PROBE_OUTPUT_TOKENS,
                "estimated_cost_nanousd": estimate,
                "price_verified": PRICE_VERIFIED if rates else None,
                "price_source": PRICE_SOURCES[provider],
                "note": (
                    "Approximate input tokens plus the output cap at standard uncached rates; "
                    "not a billing limit. No tools, no cache writes, and no automatic retries."
                    if rates
                    else "No verified rate for this model. Cost is unknown; confirm deliberately."
                ),
            }
        )
    return rows


def sdk_version(provider: CoachProvider) -> str | None:
    """Check the active interpreter, which may differ from the development one."""
    try:
        return version(provider) if find_spec(provider) is not None else None
    except (PackageNotFoundError, ImportError, ValueError):
        return None


async def check_coaches(
    settings: Settings,
    *,
    live: bool,
    credentials: Credentials | None = None,
    transports: Mapping[CoachProvider, httpx2.AsyncBaseTransport] | None = None,
) -> list[dict[str, object]]:
    """Inspect both providers independently, optionally making one logical call each.

    ``live=True`` is a billable action; the caller must obtain spend confirmation.
    A disabled coach remains disabled after checking its provider settings.
    """
    store = credentials or Credentials(settings.credential_mode)
    estimates = probe_estimates(settings)
    reports: list[dict[str, object]] = []
    for provider, estimate in zip(PROVIDERS, estimates, strict=True):
        model = settings.coach_model_for(provider)
        installed = sdk_version(provider)
        blockers: list[dict[str, object]] = []
        key: str | None = None
        source = "unavailable"
        try:
            async with asyncio.timeout(min(settings.coach_timeout_seconds, 5.0)):
                key, source = await resolve_credentials(store, provider)
        except TimeoutError:
            blockers.append(
                JevError(
                    "coach_keychain_timeout",
                    f"The {provider} credential lookup timed out.",
                    f"Unlock macOS Keychain or use {ENV_KEYS[provider]} in environment mode.",
                    3,
                ).as_dict()
            )
        except Exception:
            blockers.append(
                JevError(
                    "coach_credentials",
                    f"The {provider} credential could not be read.",
                    f"Run jevlab config or use {ENV_KEYS[provider]} in environment mode.",
                    3,
                ).as_dict()
            )
        if installed is None:
            blockers.append(
                JevError(
                    "coach_dependency",
                    f"The {provider} SDK is not installed in this jevlab environment.",
                    f"From the project directory, run make install COACH={provider}.",
                    3,
                ).as_dict()
            )
        if key is None and not blockers_contain(
            blockers, "coach_credentials", "coach_keychain_timeout"
        ):
            blockers.append(
                JevError(
                    "missing_key",
                    f"No {provider} API key is available.",
                    "Run jevlab config to save the key or set "
                    f"{ENV_KEYS[provider]} in the environment.",
                    3,
                ).as_dict()
            )
        try:
            probe_settings = settings.with_updates(
                {
                    "coach_provider": provider,
                    "coach_model": model,
                    "coach_max_output_tokens": PROBE_OUTPUT_TOKENS,
                }
            )
        except (ValueError, JevError):
            probe_settings = None
        if probe_settings is None or not model.strip():
            blockers.append(
                JevError(
                    "coach_model_invalid",
                    f"The {provider} API model identifier is missing or invalid.",
                    f"Set {provider}_model to an API model ID from the provider's model docs.",
                    3,
                ).as_dict()
            )
        report: dict[str, object] = {
            "provider": provider,
            "active": settings.coach_provider == provider,
            "sdk_installed": installed is not None,
            "sdk_version": installed,
            "key_found": bool(key),
            "key_source": source,
            "model": sanitize_text(model, (key,) if key else ()),
            "ready": not blockers,
            "blockers": blockers,
            "live_attempted": False,
            "network_checked": False,
            "status": "blocked" if blockers else "not_checked",
            "result": None,
            "error": None,
            "estimate": estimate,
        }
        reports.append(report)
        if not live or blockers or key is None or probe_settings is None:
            continue
        report["live_attempted"] = True
        advisor = ProviderAdvisor(
            key,
            probe_settings,
            max_retries=0,
            transport=transports.get(provider) if transports else None,
        )
        try:
            result = await Coach(probe_settings, advisor=advisor).ask(
                "critique", probe_payload(probe_settings), diagnostic=True
            )
            # Also redact a malformed provider response that echoes a credential.
            safe_result = {
                name: safe_value(value, key)
                for name, value in result.model_dump(mode="json").items()
            }
            rates = RATES.get(model)
            if rates and result.input_tokens is not None and result.output_tokens is not None:
                safe_result["cost_nanousd"] = (
                    result.input_tokens * rates[0] + result.output_tokens * rates[1]
                )
                safe_result["cost_note"] = (
                    f"Estimate from reported tokens at standard rates verified {PRICE_VERIFIED}; "
                    "provider billing is authoritative."
                )
            report.update(status="succeeded", network_checked=True, result=safe_result)
        except Exception as error:
            failure = coach_error(error, provider=provider, secrets=(key,))
            report.update(status="failed", error=failure.as_dict())
            # A timeout/connect failure does not establish provider connectivity.
            report["network_checked"] = failure.code in (
                "coach_authentication",
                "coach_permission",
                "coach_model",
                "coach_billing",
                "coach_rate_limit",
                "coach_request",
                "coach_provider_error",
                "coach_invalid_output",
                "coach_incomplete",
                "coach_output_limit",
                "coach_refused",
                "coach_paused",
                "coach_context_limit",
                "coach_empty_output",
            )
    return reports


def blockers_contain(blockers: list[dict[str, object]], *codes: str) -> bool:
    return any(blocker["code"] in codes for blocker in blockers)


def safe_value(value: object, key: str) -> object:
    """Sanitize string values without truncating or altering the JSON structure."""
    if isinstance(value, str):
        return sanitize_text(value, (key,))
    if isinstance(value, list):
        return [safe_value(item, key) for item in value]
    if isinstance(value, dict):
        return {name: safe_value(item, key) for name, item in value.items()}
    return value
