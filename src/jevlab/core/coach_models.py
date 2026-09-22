"""Provider model identifiers, verified against official docs on 2026-09-20.

These are editable defaults, not an allowlist or a claim about account access.
"""

import re
from typing import Literal

CoachProvider = Literal["anthropic", "openai"]
DEFAULT_COACH_MODELS: dict[CoachProvider, str] = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-5.6-luna",
}
MODEL_ALIASES: dict[CoachProvider, dict[str, str]] = {
    "anthropic": {"opus 5": "claude-opus-5", "haiku 4.5": "claude-haiku-4-5-20251001"},
    "openai": {"gpt 5.6 luna": "gpt-5.6-luna"},
}


def recognized_coach_provider(value: str) -> CoachProvider | None:
    """Recognize legacy shared model values without guessing custom model ownership."""
    value = value.strip()
    for provider, aliases in MODEL_ALIASES.items():
        if value.lower() in aliases:
            return provider
    if value.startswith("claude-"):
        return "anthropic"
    if value.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")):
        return "openai"
    return None


def normalize_coach_model(value: str, provider: CoachProvider) -> str:
    """Normalize documented display names; reject secrets and malformed IDs safely."""
    value = value.strip()
    value = MODEL_ALIASES[provider].get(value.lower(), value)
    if (
        not value
        or value.startswith(("sk-", "sk_", "Bearer "))
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", value)
    ):
        raise ValueError("Use a provider API model ID, without spaces or credentials.")
    if (provider == "openai" and value.startswith("claude-")) or (
        provider == "anthropic" and value.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))
    ):
        raise ValueError("That model belongs to the other coach provider.")
    return value
