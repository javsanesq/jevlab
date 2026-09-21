"""Safe provider diagnostics without requests, headers, or raw exception rendering."""

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from urllib.parse import quote

import httpx2

from jev.core.errors import JevError


def redact_credentials(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove credentials without truncating a structured JSON response."""
    for secret in secrets:
        if not secret:
            continue
        variants = {secret, quote(secret, safe="")}
        for ascii_only in (True, False):
            escaped = json.dumps(secret, ensure_ascii=ascii_only)[1:-1]
            variants.update((escaped, json.dumps(escaped)[1:-1]))
        for value in sorted(variants, key=len, reverse=True):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [redacted]", text)
    text = re.sub(r"\bsk-[a-zA-Z0-9_-]+", "[redacted]", text)
    return text


def sanitize_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Redact known credentials and recognizable tokens before bounding display text."""
    text = redact_credentials(text, secrets)
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in text)
    return " ".join(text.split())[:1200]


def redact_json_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Redact string values while preserving JSON escaping, shape, and long proposals."""
    keys = tuple(secrets)

    def redact(value: object) -> object:
        if isinstance(value, str):
            return redact_credentials(value, keys)
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {redact_credentials(str(key), keys): redact(item) for key, item in value.items()}
        return value

    try:
        return json.dumps(redact(json.loads(text)), ensure_ascii=False)
    except (ValueError, RecursionError):
        # Preserve invalid output for Coach's normal validation failure path.
        return redact_credentials(text, keys)


def _provider_fields(error: Exception) -> tuple[str, str]:
    """Read only documented message/code/type fields, never str(error) or HTTP data."""
    body = getattr(error, "body", None)
    if not isinstance(body, Mapping):
        return "", ""
    payload = body.get("error", body)
    if not isinstance(payload, Mapping):
        return "", ""
    message = payload.get("message", "")
    identity = " ".join(
        value for field in ("code", "type") if isinstance(value := payload.get(field), str)
    )
    return message if isinstance(message, str) else "", identity.lower()


def coach_error(error: Exception, *, provider: str = "", secrets: Iterable[str] = ()) -> JevError:
    """Classify failures with actionable diagnostics and safe provider explanations."""
    secrets = tuple(secrets)
    if isinstance(error, JevError):
        return JevError(
            error.code,
            sanitize_text(error.message, secrets),
            sanitize_text(error.fix, secrets),
            error.exit_code,
            error.retryable,
            request_id=sanitize_text(error.request_id, secrets) if error.request_id else None,
            run_id=sanitize_text(error.run_id, secrets) if error.run_id else None,
            http_status=error.http_status,
            provider_code=sanitize_text(error.provider_code, secrets)
            if error.provider_code
            else None,
        )
    status = getattr(error, "status_code", None)
    message, identity = _provider_fields(error)
    evidence = f"{identity} {message.lower()}"
    prefix = {"anthropic": "Anthropic", "openai": "OpenAI"}.get(provider, "The coach provider")
    code, summary, fix, retryable = (
        "coach_internal",
        "The coach stopped because of an unexpected internal error.",
        "Run jev doctor --coach; if the checks pass, report this failure with its error code.",
        False,
    )
    if status == 401:
        code, summary, fix = (
            "coach_authentication",
            f"{prefix} rejected the API key.",
            "Replace this provider's key with jev config and check that it has not been revoked.",
        )
    elif status == 403:
        code, summary, fix = (
            "coach_permission",
            f"{prefix} denied access to this request.",
            "Check the key's project, organization, region, permissions, and model access.",
        )
    elif status == 404:
        code, summary, fix = (
            "coach_model",
            f"{prefix} could not find the configured model or resource.",
            "Check this provider's API model identifier in jev config and its account access.",
        )
    elif status == 402 or (
        status in (400, 429)
        and any(
            token in evidence
            for token in (
                "insufficient_quota",
                "billing",
                "credit balance",
                "credit balance is too low",
                "spend limit",
                "spending limit",
                "usage limit",
                "monthly limit",
                "exceeded your current quota",
            )
        )
    ):
        code, summary, fix = (
            "coach_billing",
            f"{prefix} cannot run this request because of billing or a spending limit.",
            "Check API billing, credits, and spending limits in this provider's account; "
            "a chat subscription does not supply API credits.",
        )
    elif status == 429:
        code, summary, fix, retryable = (
            "coach_rate_limit",
            f"{prefix} is temporarily rate limiting requests.",
            "Wait before retrying, reduce request frequency, or check the account's rate limits.",
            True,
        )
    elif isinstance(status, int) and status >= 500:
        code, summary, fix, retryable = (
            "coach_provider_error",
            f"{prefix} could not complete the request (HTTP {status}).",
            "Check the provider's service status and retry after a short wait.",
            True,
        )
    elif status in (400, 422):
        code, summary, fix = (
            "coach_request",
            f"{prefix} rejected the request settings.",
            "Check the configured model and output limit; use a model supporting this API.",
        )
    elif (
        status == 408
        or isinstance(error, (TimeoutError, httpx2.TimeoutException))
        or type(error).__name__ == "APITimeoutError"
    ):
        code, summary, fix, retryable = (
            "coach_timeout",
            "The coach did not finish before its time limit.",
            "Check the connection and any Keychain prompt, then retry or increase "
            "coach_timeout_seconds in jev config.",
            True,
        )
    elif isinstance(error, httpx2.NetworkError) or type(error).__name__ == "APIConnectionError":
        code, summary, fix, retryable = (
            "coach_network",
            f"Could not connect to {provider or 'the coach provider'}.",
            "Check internet access, VPN/proxy settings, and the provider's service status.",
            True,
        )
    elif type(error).__name__ == "APIResponseValidationError":
        code, summary, fix, retryable = (
            "coach_invalid_output",
            "The coach provider returned a response its SDK could not read.",
            "No template was saved or executed. Check SDK compatibility and retry deliberately.",
            True,
        )
    # Unknown internal exceptions may contain arbitrary secrets. Only typed HTTP
    # failures expose their documented provider message, never an exception repr.
    if isinstance(status, int) and message:
        summary += f" Provider message: {sanitize_text(message, secrets)}"
    request_id = getattr(error, "request_id", None)
    return JevError(
        code,
        summary,
        fix,
        4,
        retryable,
        request_id=sanitize_text(request_id, secrets) if isinstance(request_id, str) else None,
        http_status=status if isinstance(status, int) and 100 <= status <= 599 else None,
        provider_code=sanitize_text(identity, secrets) or None,
    )
