"""Offline estimates for informed spending; no credentials, network, or UI."""

import json
from dataclasses import dataclass
from math import ceil
from typing import cast

from jevlab.core.models import Settings, Template
from jevlab.core.pricing import price
from jevlab.core.templates import context_estimate

# Standard uncached API rates verified 2026-09-20. Nanodollars per token.
# Same dated sources used by coach diagnostics; unlisted models stay unknown.
# https://platform.claude.com/docs/en/about-claude/pricing
# https://developers.openai.com/api/docs/pricing/
COACH_RATES = {
    "claude-opus-5": (5_000, 25_000),
    "claude-sonnet-5": (2_000, 10_000),
    "claude-haiku-4-5": (1_000, 5_000),
    "claude-haiku-4-5-20251001": (1_000, 5_000),
    "gpt-6-astra": (10_000, 50_000),
    "gpt-5.6-sol": (4_000, 20_000),
    "gpt-5.6-terra": (2_000, 12_000),
    "gpt-5.6-luna": (200, 1_200),
}


@dataclass(frozen=True)
class SpendEstimate:
    calls: int
    estimate_nanousd: int | None
    description: str
    limitations: str


def estimate_run(template: Template, state: object, settings: Settings) -> SpendEstimate:
    tokens = cast(int, context_estimate(template, state)["estimated_total_tokens"])
    cost, _ = price(template.model, tokens)
    return SpendEstimate(
        calls=1,
        estimate_nanousd=cost,
        description=f"One live Jev decision using {template.model}.",
        limitations=(
            "Approximate text length at published prices; this is not a spending limit. "
            f"Service overhead and up to {settings.max_retries} retries can increase the charge."
            + (" No verified price is available for this model." if cost is None else "")
        ),
    )


def estimate_coach(settings: Settings, payload: object, *, calls: int = 1) -> SpendEstimate:
    if settings.coach_provider == "disabled":
        return SpendEstimate(0, 0, "The optional coach is turned off.", "No coach call will run.")
    model = settings.coach_model_for()
    rates = COACH_RATES.get(model)
    # Includes a conservative allowance for the shared coach instructions. Callers
    # should pass their full known payload; future lesson feedback stays unknown.
    tokens = ceil(len(json.dumps(payload, ensure_ascii=False, default=str).encode()) / 3) + 2_000
    cost = (
        (tokens * rates[0] + settings.coach_max_output_tokens * rates[1]) * calls if rates else None
    )
    return SpendEstimate(
        calls,
        cost,
        f"{calls} optional coach request(s) using {settings.coach_provider} / {model}.",
        "Approximate input plus the full output allowance at published prices. "
        "This is not a spending limit; a retry or longer input can increase the charge."
        + (" No verified price is available for this model." if rates is None else ""),
    )


def combine_estimates(*estimates: SpendEstimate) -> SpendEstimate:
    known = [item.estimate_nanousd for item in estimates]
    return SpendEstimate(
        sum(item.calls for item in estimates),
        sum(value for value in known if value is not None)
        if all(x is not None for x in known)
        else None,
        " ".join(item.description for item in estimates),
        " ".join(dict.fromkeys(item.limitations for item in estimates)),
    )
