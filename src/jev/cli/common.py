"""Shared CLI output, error boundaries, and local application setup."""

import json
import sqlite3
import sys
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import Annotated, ParamSpec, TypeVar

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.text import Text

from jev.core.config import data_directory
from jev.core.errors import JevError
from jev.core.service import Workbench
from jev.core.templates import validation_message
from jev.presentation import human_error

console = Console()
stderr = Console(stderr=True)
JsonFlag = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON; never prompt.")]
P = ParamSpec("P")
R = TypeVar("R")
_verbose: ContextVar[bool] = ContextVar("jev_verbose_errors", default=False)


def configure_error_details(enabled: bool) -> None:
    _verbose.set(enabled)


def verbose_errors() -> bool:
    return _verbose.get()


def emit(data: object) -> None:
    typer.echo(json.dumps({"schema_version": 1, "ok": True, "data": data}, ensure_ascii=False))


def emit_error(error: JevError, machine: bool) -> None:
    if machine:
        typer.echo(json.dumps({"schema_version": 1, "ok": False, "error": error.as_dict()}))
    else:
        stderr.print(Text(human_error(error, verbose=_verbose.get())))


def guarded[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        machine = bool(kwargs.get("json_output")) or kwargs.get("state") == "-"
        try:
            return function(*args, **kwargs)
        except JevError as error:
            emit_error(error, machine)
            raise typer.Exit(error.exit_code) from None
        except ValidationError as invalid:
            error = JevError(
                "invalid_input",
                validation_message(invalid),
                "Correct the named field using its stated requirements, then try again.",
                details={"exception_type": type(invalid).__name__},
            )
            emit_error(error, machine)
            raise typer.Exit(2) from None
        except (OSError, sqlite3.Error, UnicodeError, ValueError):
            error = JevError(
                "local_error",
                "Could not read or save the requested data.",
                "Check input format, permissions, and available disk space.",
            )
            emit_error(error, machine)
            raise typer.Exit(2) from None
        except (typer.Exit, typer.Abort):
            raise
        except Exception as failure:
            error = JevError(
                "internal_error",
                "jev encountered an unexpected internal problem; its cause is unknown.",
                "Run jev doctor, then retry. "
                "Use jev --verbose before the command for safe details.",
                details={"exception_type": type(failure).__name__},
            )
            emit_error(error, machine)
            raise typer.Exit(2) from None

    return wrapper


def workbench() -> Workbench:
    return Workbench(data_directory())


def launch(wb: Workbench, screen: str = "home", name: str | None = None) -> None:
    from jev.tui.app import JevApp

    if not sys.stdin.isatty():
        raise JevError(
            "terminal_required", "The TUI needs an interactive terminal.", "Use --json for scripts."
        )
    JevApp(wb, start=screen, template_name=name).run()
