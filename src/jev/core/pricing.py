"""Dated published-rate estimates; unknown is never represented as zero."""

from decimal import ROUND_HALF_UP, Decimal

PRICE_SOURCE = "https://docs.typesafe.ai/models"
PRICE_DATE = "2026-09-20"
RATES = {"jev-1.13.0": Decimal("0.042")}


def price(model: str | None, input_tokens: int | None) -> tuple[int | None, dict[str, object]]:
    rate = RATES.get(model or "")
    snapshot: dict[str, object] = {
        "source": PRICE_SOURCE,
        "verified_on": PRICE_DATE,
        "currency": "USD",
        "input_per_million": str(rate) if rate is not None else None,
        "output_per_million": "0" if rate is not None else None,
        "basis": "published-rate estimate; excludes unknown attempts and account-specific terms",
    }
    if rate is None or input_tokens is None:
        return None, snapshot
    cost = (Decimal(input_tokens) * rate * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cost), snapshot


def format_cost(nanousd: int | None) -> str:
    return "unknown" if nanousd is None else f"${Decimal(nanousd) / 1_000_000_000:.8f}"
