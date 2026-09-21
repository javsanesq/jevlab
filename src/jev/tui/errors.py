"""Typed error handoff for screens and modal dialogs, without app import cycles."""

from typing import Protocol, cast

from textual.dom import DOMNode

from jev.core.errors import JevError


class ErrorReporter(Protocol):
    def report_error(
        self, error: JevError, *, timeout: float | None = None, notify: bool = True
    ) -> None: ...


def report_error(
    node: DOMNode,
    error: JevError,
    *,
    timeout: float | None = None,
    notify: bool = True,
) -> None:
    """Preserve the error object until its brief and detailed views are rendered."""
    cast(ErrorReporter, node.app).report_error(error, timeout=timeout, notify=notify)
