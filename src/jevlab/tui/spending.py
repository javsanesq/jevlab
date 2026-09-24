"""Shared, cancel-first spending prompts for terminal workflows."""

import sqlite3

from pydantic import ValidationError

from jevlab.core.errors import JevError
from jevlab.core.pricing import format_cost, over_budget
from jevlab.core.spending import SpendEstimate
from jevlab.core.templates import validation_message
from jevlab.presentation import human_error
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.dialogs import Confirm


async def confirm_spend(
    screen: WorkbenchScreen,
    estimate: SpendEstimate,
    *,
    action: str = "Continue",
    detail: str = "",
) -> bool:
    """Call from a Textual worker before dispatching any paid request.

    Known estimates within the saved confirmation budget start immediately with a
    short notice; unknown or larger estimates open a cancel-first dialog.
    """
    if estimate.calls == 0:
        return True
    budget = screen.wb.settings.confirm_cost_usd
    if not over_budget(estimate.estimate_nanousd, budget):
        screen.notify(
            f"{estimate.calls:,} request(s), estimated {format_cost(estimate.estimate_nanousd)} "
            f"(within your ${budget:g} confirmation budget).",
            timeout=4,
        )
        return True
    price = (
        f"Estimated cost: {format_cost(estimate.estimate_nanousd)} (US dollars), above your "
        f"${budget:g} confirmation budget."
        if estimate.estimate_nanousd is not None
        else "The cost is unknown because a price could not be verified."
    )
    message = (
        "This uses a paid online service.\n\n"
        f"{estimate.description}\n{estimate.calls:,} request(s). {price}\n\n"
        f"{estimate.limitations}"
    )
    if detail:
        message += f"\n\n{detail}"
    message += "\n\nContinue only if you want these requests to run."
    dialog = Confirm(message, accept_label=action, cancel_label="Cancel this request")
    return bool(await screen.app.push_screen_wait(dialog))


def flow_error(
    error: Exception,
    *,
    next_step: str = "Check your entries and try again.",
    screen: WorkbenchScreen | None = None,
) -> str:
    """Render typed errors, with safe actionable summaries for local failures."""
    if not isinstance(error, JevError):
        if isinstance(error, ValidationError):
            error = JevError(
                "invalid_input",
                validation_message(error),
                "Correct the named field using its stated requirements, then try again.",
                details={"exception_type": type(error).__name__},
            )
        elif isinstance(error, sqlite3.Error):
            error = JevError(
                "storage_error",
                "Local history could not be read or updated.",
                "Run jevlab doctor to check the history database and its folder permissions.",
            )
        elif isinstance(error, (OSError, ValueError)):
            reason = (
                "The file or folder could not be read or written."
                if isinstance(error, OSError)
                else "One of the entered values is missing or not valid."
            )
            error = JevError("invalid_input", reason, next_step)
        else:
            error = JevError(
                "internal_error",
                "An unexpected internal error stopped this action.",
                "Run jevlab doctor, then report the action and the error code if it happens again.",
            )
    message = human_error(error)
    if screen is not None:
        screen.report_error(error)
    return message
