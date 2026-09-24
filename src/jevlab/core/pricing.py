"""Dated published-rate estimates; unknown is never represented as zero."""

from decimal import ROUND_HALF_UP, Decimal

PRICE_SOURCE = "https://docs.typesafe.ai/models"
PRICE_DATE = "2026-09-20"
RATES = {"jev-1.13.0": Decimal("0.042")}
# Aliases resolve server-side. Estimate them at the version they resolved to on
# PRICE_DATE; recorded runs are priced by the concrete model the API returns.
ALIASES = {"jev-latest": "jev-1.13.0"}


def price(model: str | None, input_tokens: int | None) -> tuple[int | None, dict[str, object]]:
    priced_as = ALIASES.get(model or "", model or "")
    rate = RATES.get(priced_as)
    snapshot: dict[str, object] = {
        "source": PRICE_SOURCE,
        "verified_on": PRICE_DATE,
        "currency": "USD",
        "input_per_million": str(rate) if rate is not None else None,
        "output_per_million": "0" if rate is not None else None,
        "basis": "published-rate estimate; excludes unknown attempts and account-specific terms",
    }
    if rate is not None and priced_as != model:
        snapshot["priced_as"] = priced_as
    if rate is None or input_tokens is None:
        return None, snapshot
    cost = (Decimal(input_tokens) * rate * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cost), snapshot


def over_budget(cost_nanousd: int | None, budget_usd: float) -> bool:
    """Unknown estimates and estimates above the configured budget need consent."""
    return cost_nanousd is None or cost_nanousd / 1_000_000_000 > budget_usd


def format_cost(nanousd: int | None) -> str:
    return "unknown" if nanousd is None else f"${Decimal(nanousd) / 1_000_000_000:.8f}"
