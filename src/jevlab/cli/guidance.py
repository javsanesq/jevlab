"""Free onboarding commands; machine output never opens a UI or reads credentials."""

import sys
from typing import Annotated

import typer
from rich.text import Text

from jevlab.cli.common import JsonFlag, console, emit, guarded, launch, workbench
from jevlab.core.config import data_directory
from jevlab.core.credentials import secure_store_description
from jevlab.core.demo import load_demo
from jevlab.core.errors import JevError
from jevlab.core.guidance import GLOSSARY, WELCOME, explain_answer
from jevlab.core.service import Workbench
from jevlab.rendering import render_run


def register_guidance(app: typer.Typer, *, panel: str) -> None:
    app.command("demo", rich_help_panel=panel)(demo)
    app.command("tour", rich_help_panel=panel)(tour)
    app.command("glossary", rich_help_panel=panel)(glossary)


@guarded
def demo(json_output: JsonFlag = False) -> None:
    """Replay a free recorded example. No key, network call, or run-history entry."""
    recording = load_demo()
    if json_output:
        emit(recording.model_dump(mode="json"))
    elif sys.stdin.isatty():
        launch(Workbench(data_directory(), maintain=False), "demo")
    else:
        console.print(Text(recording.disclaimer))
        console.print(Text(recording.title))
        console.print(Text("Customer: I was charged twice. Please refund the duplicate charge."))
        console.print(render_run(recording.run, compact=True, illustrative=True))
        console.print(Text(recording.provenance))


@guarded
def tour(json_output: JsonFlag = False) -> None:
    """Repeat the skippable welcome tour, with optional key setup and a free demo."""
    recording = load_demo()
    steps = [
        {
            "title": "Add a key when ready",
            "text": "An API key is a private password for your provider account. Run jevlab config "
            f"to save a TypeSafe key securely in {secure_store_description()}. "
            "You can skip this for the demo.",
        },
        {
            "title": "Try the free recorded demo",
            "text": "Run jevlab demo. The teaching answers are replayed from a bundled file; "
            "no live request is sent and there is no charge.",
        },
        {
            "title": "Understand the result",
            "text": explain_answer(recording.run, "route"),
        },
    ]
    if json_output:
        emit({"welcome": WELCOME, "steps": steps, "recording_notice": recording.disclaimer})
    elif sys.stdin.isatty():
        launch(workbench(), "tour")
    else:
        console.print(Text(WELCOME + "\n"))
        for number, step in enumerate(steps, 1):
            console.print(Text(f"{number}. {step['title']}\n{step['text']}\n"))
        console.print(Text("Run jevlab tour in an interactive terminal to follow the tour."))


@guarded
def glossary(
    term: Annotated[str | None, typer.Argument(help="Optional word to explain.")] = None,
    json_output: JsonFlag = False,
) -> None:
    """Read plain explanations and examples of the words used in JevLab."""
    entries = GLOSSARY
    if term:
        entries = {
            name: text for name, text in GLOSSARY.items() if term.casefold() == name.casefold()
        }
        if not entries:
            raise JevError(
                "glossary_term",
                "That word is not in the glossary.",
                "Run jevlab glossary to see all available words.",
            )
    if json_output:
        emit(entries)
    elif sys.stdin.isatty() and term is None:
        launch(workbench(), "glossary")
    else:
        for name, text in entries.items():
            console.print(Text(f"{name}\n{text}\n"))
