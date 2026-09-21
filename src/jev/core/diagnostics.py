"""Credential-safe provider diagnostics, independent of presentation and SDK types."""

import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from urllib.parse import quote

_CREDENTIAL_FIELDS = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "token",
    "authorization",
    "proxyauthorization",
    "password",
    "secret",
    "clientsecret",
    "cookie",
    "setcookie",
    "xapikey",
}


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove known keys, encoded variants, recognizable tokens, and terminal controls."""
    for secret in secrets:
        if not secret:
            continue
        variants = {secret, quote(secret, safe=""), quote(quote(secret, safe=""), safe="")}
        for ascii_only in (True, False):
            escaped = json.dumps(secret, ensure_ascii=ascii_only)[1:-1]
            variants.update((escaped, json.dumps(escaped)[1:-1]))
        for value in sorted(variants, key=len, reverse=True):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [redacted]", text)
    text = re.sub(r"\bsk[-_][A-Za-z0-9_-]+", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret)"
        r"(\s*[=:]\s*)([^\s,;\"']+)",
        r"\1\2[redacted]",
        text,
    )
    return "".join(
        " " if unicodedata.category(char).startswith("C") and char not in "\n\t" else char
        for char in text
    )


def redact_body(value: object, secrets: Iterable[str] = ()) -> object:
    """Keep complete JSON/text diagnostics, redacting credentials at every nesting level."""
    keys = tuple(secrets)
    if isinstance(value, str):
        # HTTP failures may contain JSON served with a text content type.
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            return redact_text(value, keys)
        if isinstance(decoded, (dict, list)):
            return json.dumps(redact_body(decoded, keys), ensure_ascii=False)
        return redact_text(value, keys)
    if isinstance(value, Mapping):
        return {
            redact_text(str(key), keys): "[redacted]"
            if re.sub(r"[^a-z0-9]", "", str(key).lower()) in _CREDENTIAL_FIELDS
            else redact_body(item, keys)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_body(item, keys) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return "[unsupported diagnostic value]"


def provider_fields(body: object) -> tuple[str, str | None]:
    """Read SDK-supported error/message/detail shapes without rendering an exception."""
    if isinstance(body, str):
        return body, None
    if not isinstance(body, Mapping):
        return "", None
    provider_code = next(
        (
            body[field]
            for field in ("error_type", "code", "type")
            if isinstance(body.get(field), str)
        ),
        None,
    )
    for field in ("error", "message", "detail"):
        value = body.get(field)
        if isinstance(value, str):
            return value, provider_code
        if isinstance(value, Mapping):
            message, nested_code = provider_fields(value)
            if message:
                return message, nested_code or provider_code
        if field == "detail" and isinstance(value, list):
            messages = []
            for entry in value:
                if not isinstance(entry, Mapping) or not isinstance(entry.get("msg"), str):
                    continue
                location = entry.get("loc")
                path = (
                    ".".join(str(x) for x in location if x != "body")
                    if isinstance(location, list)
                    else ""
                )
                messages.append(f"{path}: {entry['msg']}" if path else entry["msg"])
            return "; ".join(messages), provider_code
    return "", provider_code
