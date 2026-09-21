"""Read the shipped beginner's guide without credentials, history, or network access."""

import io
import sys
import webbrowser
from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.terminal_theme import TerminalTheme
from rich.text import Text

from jev.cli.common import JsonFlag, console, emit, guarded
from jev.core.config import data_directory
from jev.core.errors import JevError
from jev.core.files import atomic_write

GUIDE_TITLE = "jev: A complete beginner's guide"
PAGER_HELP = "Reading the guide: Space = next page; b = previous page; q = close."
_HTML_FORMAT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>jev: A complete beginner's guide</title>
<style>
body {{ margin: 0; padding: 1.5rem; background: {background}; color: {foreground}; }}
main {{ max-width: 110ch; margin: auto; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.5;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 14px; }}
{stylesheet}
</style>
</head>
<body><main aria-label="Beginner's guide"><pre>{code}</pre></main></body>
</html>
"""


def _editable_guide_path() -> Path | None:
    """An editable install reads the source file so documentation edits apply immediately."""
    module = Path(__file__).resolve()
    root = module.parents[3]
    if (root / "src" / "jev" / "cli" / "guide.py").resolve() != module:
        return None
    candidate = root / "docs" / "GUIDE.md"
    return candidate if candidate.is_file() else None


def load_guide() -> str:
    """Wheel builds include the same authoritative docs/GUIDE.md as a package resource."""
    try:
        editable = _editable_guide_path()
        if editable is not None:
            return editable.read_text(encoding="utf-8")
        return files("jev").joinpath("resources", "GUIDE.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise JevError(
            "guide_unavailable",
            "The beginner's guide is missing or cannot be read in this installation.",
            "Reinstall jev from the current project, then run jev guide again.",
        ) from None


def render_guide_html(content: str) -> str:
    """Export escaped text and local CSS only; no scripts, remote assets, or user data."""
    document = Console(file=io.StringIO(), record=True, width=100, force_terminal=False)
    # Display link addresses as text so the export contains no active or unsafe link targets.
    document.print(Markdown(content, hyperlinks=False))
    theme = TerminalTheme(
        (16, 19, 23),
        (226, 230, 235),
        [(16, 19, 23), *[(100, 205, 215)] * 6, (226, 230, 235)],
    )
    return document.export_html(theme=theme, code_format=_HTML_FORMAT)


@guarded
def guide(
    web: Annotated[
        bool, typer.Option("--web", help="Open a local HTML copy in your default browser.")
    ] = False,
    json_output: JsonFlag = False,
) -> None:
    """Read the complete beginner's guide. No key or internet connection is needed."""
    content = load_guide()
    if json_output:
        emit({"title": GUIDE_TITLE, "format": "markdown", "content": content})
    elif web:
        destination = data_directory() / "docs" / "guide.html"
        atomic_write(destination, render_guide_html(content))
        try:
            opened = webbrowser.open(destination.as_uri(), new=2)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            raise JevError(
                "browser_unavailable",
                "The guide was saved, but the default browser could not be opened.",
                f"Open {destination} in your browser, or run jev guide to read it here.",
            )
        typer.echo(f"Opened the beginner's guide in your browser.\nLocal copy: {destination}")
    elif sys.stdin.isatty() and sys.stdout.isatty():
        with console.pager():
            console.print(Text(PAGER_HELP))
            console.print(Markdown(content, hyperlinks=False))
    else:
        typer.echo(content.rstrip())
