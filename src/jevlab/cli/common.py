"""Shared CLI output, error boundaries, and local application setup."""

import errno
import json
import sqlite3
import sys
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Annotated, ParamSpec, TypeVar

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.text import Text
from typesafe_sdk import JSONContent

from jevlab.core.config import data_directory
from jevlab.core.errors import JevError
from jevlab.core.files import read_text
from jevlab.core.models import Template
from jevlab.core.service import Workbench, parse_state
from jevlab.core.templates import validation_message
from jevlab.presentation import human_error

console = Console()
stderr = Console(stderr=True)


def inherit_json(ctx: typer.Context, value: bool) -> bool:
    """Resolve group flags while parsing, without process-global output state."""
    parent = ctx.parent
    while parent is not None:
        value = value or bool(parent.params.get("json_output"))
        parent = parent.parent
    return value


JsonFlag = Annotated[
    bool,
    typer.Option("--json", help="Emit machine-readable JSON; never prompt.", callback=inherit_json),
]
P = ParamSpec("P")
R = TypeVar("R")
_verbose: ContextVar[bool] = ContextVar("jevlab_verbose_errors", default=False)


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


def local_error(failure: OSError | sqlite3.Error | UnicodeError | ValueError) -> JevError:
    """Classify local failures without exposing exception text, file contents, or secrets."""
    message = "A local operation failed; its exact cause is unknown."
    fix = "Use --verbose for the exception type, then run jevlab doctor and report those details."
    if isinstance(failure, FileNotFoundError):
        message = "A required file or folder does not exist."
        fix = "Check the supplied path. For --state, choose an existing text or JSON file."
    elif isinstance(failure, PermissionError):
        message = "The operating system denied access to the requested file or folder."
        fix = "Check the file's access permissions or choose a folder you can read and write."
    elif isinstance(failure, IsADirectoryError):
        message = "The supplied path points to a folder where a file is required."
        fix = "Choose the file inside that folder, including its filename."
    elif isinstance(failure, NotADirectoryError):
        message = "A folder component of the supplied path is actually a file."
        fix = "Check each folder in the path, then choose the intended input or output file."
    elif isinstance(failure, UnicodeDecodeError):
        message = "The input file is not readable as UTF-8 text."
        fix = "Save or export it as UTF-8 text, JSON, JSONL, or CSV before importing it."
    elif isinstance(failure, OSError) and failure.errno == errno.ENOSPC:
        message = "There is not enough free disk space to save the requested data."
        fix = "Free disk space, then check history before repeating any paid request."
    details: dict[str, object] = {"exception_type": type(failure).__name__}
    if isinstance(failure, OSError) and failure.errno is not None:
        details["errno"] = failure.errno
    return JevError("local_error", message, fix, details=details)


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
        except (OSError, sqlite3.Error, UnicodeError, ValueError) as failure:
            error = local_error(failure)
            emit_error(error, machine)
            raise typer.Exit(2) from None
        except (typer.Exit, typer.Abort):
            raise
        except Exception as failure:
            error = JevError(
                "internal_error",
                "jevlab encountered an unexpected internal problem; its cause is unknown.",
                "Run jevlab doctor, then retry. "
                "Use jevlab --verbose before the command for safe details.",
                details={"exception_type": type(failure).__name__},
            )
            emit_error(error, machine)
            raise typer.Exit(2) from None

    return wrapper


def workbench() -> Workbench:
    wb = Workbench(data_directory())
    if wb.profile_notice:
        stderr.print(Text(wb.profile_notice))
    return wb


def launch(wb: Workbench, screen: str = "home", name: str | None = None) -> None:
    from jevlab.tui.app import JevApp

    if not sys.stdin.isatty():
        raise JevError(
            "terminal_required", "The TUI needs an interactive terminal.", "Use --json for scripts."
        )
    JevApp(wb, start=screen, template_name=name).run()


DATASET_SUFFIXES = (".jsonl", ".csv")


def read_state_payload(state: str | None, text: str | None, *, action: str) -> str:
    """Read exactly one state from --state FILE, --state - (stdin), or --text."""
    if state and state != "-" and Path(state).suffix.lower() in DATASET_SUFFIXES:
        raise JevError(
            "state_is_dataset",
            "--state reads one case, but this file looks like a dataset of many cases.",
            "Use jevlab batch TEMPLATE --input FILE --output results.jsonl, or jevlab eval run "
            "TEMPLATE FILE for labeled cases. To send the whole file as one state, use "
            "--state - < FILE.",
        )
    payload = (
        sys.stdin.read(2_000_001)
        if state == "-"
        else read_text(Path(state).expanduser())
        if state
        else text or ""
    )
    if len(payload.encode()) > 2_000_000:
        raise JevError(
            "state_too_large", "State exceeds the 2 MB import limit.", f"Trim it before {action}."
        )
    return payload


def parse_state_for(design: Template, payload: str, input_format: str) -> JSONContent:
    """Parse one state, explaining what a JSON-format design expects when parsing fails."""
    try:
        return parse_state(payload, input_format)
    except JevError as error:
        if error.code != "invalid_state" or input_format != "json":
            raise
        example = design.state.example
        sample = (
            json.dumps(example, ensure_ascii=False) if isinstance(example, (dict, list)) else None
        )
        if sample and len(sample) > 160:
            sample = sample[:157] + "…"
        raise JevError(
            "invalid_state",
            f"{design.name} expects JSON state: {design.state.description}",
            (f"Pass JSON such as {sample}, " if sample else "Pass a JSON object or array, ")
            + "or add --format text to send plain text instead.",
        ) from None
