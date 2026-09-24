"""Human-terminal spending prompts; scripted command contracts stay unchanged."""

import sys

import typer
from rich.text import Text

from jevlab.cli.common import stderr
from jevlab.core.errors import JevError
from jevlab.core.models import Settings
from jevlab.core.pricing import format_cost, over_budget
from jevlab.core.spending import SpendEstimate


def interactive(machine: bool = False) -> bool:
    return not machine and sys.stdin.isatty()


def confirm_spend(
    estimate: SpendEstimate,
    *,
    settings: Settings,
    machine: bool = False,
    yes: bool = False,
) -> bool:
    """Ask only when the estimate is unknown or above the configured budget.

    Machine callers never prompt; their own budget gates decide whether --yes is needed.
    """
    if not interactive(machine) or estimate.calls == 0:
        return yes
    cost = format_cost(estimate.estimate_nanousd)
    if yes:
        stderr.print(Text(f"{estimate.description} Estimated {cost}; authorized by --yes."))
        return True
    if not over_budget(estimate.estimate_nanousd, settings.confirm_cost_usd):
        stderr.print(
            Text(
                f"{estimate.description} Estimated {cost}, within your "
                f"${settings.confirm_cost_usd:g} confirmation budget.",
                style="dim",
            )
        )
        return True
    stderr.print(Text(f"{estimate.description}\nEstimated charge: {cost}.\n{estimate.limitations}"))
    if not typer.confirm("Continue and allow this charge?", default=False, err=True):
        raise JevError(
            "cost_confirmation",
            "You chose not to start this paid action. No request was sent.",
            "Raise the budget with jevlab config --set confirm_cost_usd=AMOUNT, or pass --yes.",
        )
    return True
