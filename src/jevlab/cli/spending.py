"""Human-terminal spending prompts; scripted command contracts stay unchanged."""

import sys

import typer
from rich.text import Text

from jevlab.cli.common import stderr
from jevlab.core.errors import JevError
from jevlab.core.pricing import format_cost
from jevlab.core.service import Workbench
from jevlab.core.spending import SpendEstimate, SpendScope


def interactive(machine: bool = False) -> bool:
    return not machine and sys.stdin.isatty()


def confirm_spend(
    estimate: SpendEstimate,
    *,
    machine: bool = False,
    yes: bool = False,
    wb: Workbench | None = None,
    scope: SpendScope | None = None,
) -> bool:
    """Confirm interactive jobs and optional services; single Jev runs bypass this helper."""
    if not interactive(machine) or estimate.calls == 0:
        return yes
    cost = format_cost(estimate.estimate_nanousd)
    stderr.print(Text(f"{estimate.description}\nEstimated charge: {cost}.\n{estimate.limitations}"))
    if yes:
        stderr.print(Text("Spending authorized by --yes."))
        return True
    preference = f"confirm_{scope}_cost" if scope else None
    if wb is not None and preference and not getattr(wb.settings, preference):
        stderr.print(Text(f"Starting with your saved {scope} confirmation preference."))
        return True
    if not typer.confirm("Continue and allow this charge?", default=False, err=True):
        raise JevError(
            "cost_confirmation",
            "You chose not to start this paid action. No request was sent.",
            "You can try the free recorded examples with jevlab demo, or run this action again.",
        )
    if wb is not None and preference:
        label = "batch runs" if scope == "batch" else "evaluations"
        if typer.confirm(
            f"Don't ask again before {label}? You can turn this back on in Settings.",
            default=False,
            err=True,
        ):
            wb.update_settings(wb.settings.with_updates({preference: False}))
    return True
