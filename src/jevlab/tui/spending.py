"""Shared, cancel-first spending prompts for terminal workflows."""

import sqlite3

from pydantic import ValidationError
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, Static

from jevlab.core.errors import JevError
from jevlab.core.pricing import format_cost
from jevlab.core.spending import SpendEstimate, SpendScope
from jevlab.core.templates import validation_message
from jevlab.presentation import human_error
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.dialogs import Confirm


class JobCostConfirm(Confirm):
    """A job-specific opt-out; cancelling never changes the saved preference."""

    def __init__(self, message: str, *, scope: SpendScope, action: str) -> None:
        super().__init__(message, accept_label=action, cancel_label="Cancel this request")
        self.scope = scope
        self.remember = False

    def compose(self) -> ComposeResult:
        label = "batch runs" if self.scope == "batch" else "evaluations"
        with VerticalScroll(classes="dialog"):
            yield Static(self.message, markup=False)
            yield Checkbox(f"Don't ask again before {label}", id="remember-cost")
            yield Static(
                "Applies only after you start this job. Turn confirmations back on in Settings.",
                classes="muted",
            )
            with Horizontal(classes="buttons"):
                yield Button(self.cancel_label, id="keep", variant="primary")
                yield Button(self.accept_label, id="discard")

    @on(Checkbox.Changed, "#remember-cost")
    def remember_changed(self, event: Checkbox.Changed) -> None:
        self.remember = event.value

    def action_help(self) -> None:
        self.notify(
            "Review the estimate before starting. The checkbox remembers this choice only "
            "for this kind of job. Cancel or Esc sends no requests and saves no preference."
        )


async def confirm_spend(
    screen: WorkbenchScreen,
    estimate: SpendEstimate,
    *,
    action: str = "Continue",
    detail: str = "",
    scope: SpendScope | None = None,
) -> bool:
    """Call from a Textual worker before dispatching any paid request."""
    if estimate.calls == 0:
        return True
    preference = f"confirm_{scope}_cost" if scope else None
    if preference and not getattr(screen.wb.settings, preference):
        return True
    price = (
        f"Estimated cost: {format_cost(estimate.estimate_nanousd)} (US dollars)."
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
    dialog = (
        JobCostConfirm(message, scope=scope, action=action)
        if scope
        else Confirm(message, accept_label=action, cancel_label="Cancel this request")
    )
    accepted = bool(await screen.app.push_screen_wait(dialog))
    if accepted and preference and isinstance(dialog, JobCostConfirm) and dialog.remember:
        screen.wb.update_settings(screen.wb.settings.with_updates({preference: False}))
    return accepted


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
