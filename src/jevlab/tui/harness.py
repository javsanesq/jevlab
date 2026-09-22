"""Preview portable code and inspect retention before applying cleanup."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Select, Static, TextArea

from jevlab.core.errors import JevError
from jevlab.core.exporting import export_template, write_export
from jevlab.core.retention import CleanupReport, cleanup
from jevlab.core.service import Workbench
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.dialogs import Confirm
from jevlab.tui.fields import Field
from jevlab.tui.spending import flow_error


class ExportScreen(WorkbenchScreen):
    def compose(self) -> ComposeResult:
        names = self.wb.templates.names()
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("EXPORT / use a design in your own program", classes="eyebrow")
            yield Static(
                "This advanced feature writes Python code for a developer to use. "
                "Creating the file is free. You can ignore this while learning Jev.",
                classes="muted",
            )
            yield Field(
                Select(
                    [(name, name) for name in names],
                    value=names[0] if names else Select.NULL,
                    id="export-template",
                ),
                "Design to put in your program",
                "Export the questions, model version, and review thresholds from this saved "
                "template. Unsaved editor changes are not included.",
                "support-triage",
                validator=self.validate_template,
            )
            yield Button("More options", id="export-more", classes="options-toggle")
            yield Field(
                Select(
                    [
                        ("Python / official SDK", "python"),
                        ("LangChain Runnable", "langchain"),
                        ("Pydantic AI tool", "pydantic-ai"),
                    ],
                    value="python",
                    allow_blank=False,
                    id="export-language",
                ),
                "Programming tool to connect",
                "Python calls the official SDK directly. LangChain and Pydantic AI add "
                "an adapter for those libraries; the generated file lists its dependencies.",
                "Python / official SDK",
                classes="advanced",
            )
            yield Static(
                "Exports use TYPESAFE_API_KEY in your project environment. No key is included; "
                "generating code makes no API call.",
                classes="muted",
            )
            yield TextArea(
                "", read_only=True, show_line_numbers=True, id="export-code", classes="advanced"
            )
            yield Field(
                Input(id="export-path"),
                "New Python file to create",
                "Save the module as a .py file. Missing folders are created; existing files "
                "and symbolic links are never replaced. This does not call Jev.",
                "~/Desktop/support_decision.py",
                validator=export_path_error,
            )
            yield Static("", id="export-status", markup=False, classes="muted")
        with Horizontal(classes="buttons"):
            yield Button("Refresh code preview", id="export-preview", classes="advanced")
            yield Button("Save code file", id="export-save", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.preview()
        self.validate_fields()

    def validate_template(self, value: str) -> str | None:
        if value not in self.wb.templates.names():
            return "Choose a saved design, such as support-triage."
        try:
            self.wb.templates.load(value)
        except JevError as error:
            return f"{error.message} {error.fix}"
        return None

    @on(Input.Changed, "#export-path")
    @on(Select.Changed)
    def validate_fields(self) -> bool:
        if not self.is_mounted:
            return False
        valid = all([field.validate() for field in self.query(Field)])
        self.query_one("#export-save", Button).disabled = not valid
        return valid

    def language(self) -> Literal["python", "langchain", "pydantic-ai"]:
        return cast(
            Literal["python", "langchain", "pydantic-ai"],
            str(self.query_one("#export-language", Select).value),
        )

    @on(Select.Changed)
    @on(Button.Pressed, "#export-preview")
    def preview(self) -> None:
        if not self.is_mounted:
            return
        try:
            template = self.wb.templates.load(str(self.query_one("#export-template", Select).value))
            self.query_one("#export-code", TextArea).load_text(
                export_template(template, lang=self.language())
            )
            self.query_one("#export-status", Static).update(
                "Preview reflects the saved template. Save template edits before exporting."
            )
        except Exception as error:
            self.query_one("#export-status", Static).update(flow_error(error, screen=self))

    @on(Button.Pressed, "#export-save")
    def save(self) -> None:
        if not self.validate_fields():
            errors = [field.error for field in self.query(Field) if field.error]
            self.query_one("#export-status", Static).update(" ".join(errors))
            return
        try:
            path = self.query_one("#export-path", Input).value.strip()
            if not path:
                raise JevError(
                    "export_path",
                    "There is no destination for the code.",
                    "Enter the location of a new file ending in .py.",
                )
            template = self.wb.templates.load(str(self.query_one("#export-template", Select).value))
            result = write_export(template, Path(path), lang=self.language())
            self.query_one("#export-status", Static).update(
                f"Saved {result}. Install the dependencies listed in the module's header."
            )
        except Exception as error:
            self.query_one("#export-status", Static).update(flow_error(error, screen=self))


def export_path_error(value: str) -> str | None:
    """Match write_export's preconditions without writing or excluding new folders."""
    if not value.strip():
        return "Choose a new .py filename, such as ~/Desktop/support_decision.py."
    try:
        destination = Path(value.strip()).expanduser().absolute()
        if "\x00" in str(destination):
            return "The path contains an invalid character. Retype the folder and .py filename."
        if destination.suffix.lower() != ".py":
            return "Python exports need a .py filename, such as support_decision.py."
        if destination.exists() or destination.is_symlink():
            return "The output path already exists. Choose a new .py filename to keep it unchanged."
        for parent in destination.parents:
            if (parent.exists() or parent.is_symlink()) and not parent.is_dir():
                return "A parent path is not a folder. Choose a folder for the new Python module."
    except (OSError, RuntimeError, ValueError):
        return "This path cannot be read. Check the folder name and your permission to access it."
    return None


class CleanupScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench) -> None:
        super().__init__(wb)
        self.report: CleanupReport | None = None
        self.busy = False

    def request_close(self, callback: Callable[[], object]) -> None:
        if self.busy:
            # Cancelling to_thread does not stop its database transaction. Keep the
            # screen present until maintenance has actually finished.
            self.notify("History maintenance is finishing. Wait before leaving this screen.")
        else:
            callback()

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("CLEANUP / make room by removing old history", classes="eyebrow")
            yield Static(
                "Preview old results that can be removed under your storage settings. "
                "Your saved designs, original data files, keys, and completed lesson progress "
                "are kept. Work still in progress is protected.",
                classes="muted",
            )
            yield Static("", id="cleanup-report")
            with Horizontal(classes="buttons"):
                yield Button("Preview", id="cleanup-preview", variant="primary")
                yield Button("Remove old history", id="cleanup-apply", disabled=True)
            yield Static("", id="cleanup-status", markup=False, classes="muted")
        yield Footer()

    def on_mount(self) -> None:
        self.run_cleanup()

    @work(group="cleanup", exit_on_error=False)
    async def run_cleanup(self, apply: bool = False) -> None:
        if self.busy:
            return
        self.busy = True
        self.report = None
        for button in self.query(Button):
            button.disabled = True
        try:
            self.report = await asyncio.to_thread(
                cleanup, self.wb.storage, self.wb.settings, dry_run=not apply
            )
            counts = self.report.deleted if apply else self.report.plan.delete
            table = Table("History", "Removed" if apply else "Can remove", box=None)
            for label, value in counts.model_dump().items():
                table.add_row(label.replace("_", " "), Text(f"{value:,}", justify="right"))
            self.query_one("#cleanup-report", Static).update(table)
            size_message = (
                f"Database {self.report.plan.bytes_before:,} → {self.report.bytes_after:,} bytes."
                if apply
                else f"Database {self.report.plan.bytes_before:,} bytes; estimated after cleanup "
                f"{self.report.plan.estimated_bytes_after:,} bytes."
            )
            self.query_one("#cleanup-status", Static).update(
                size_message + "\n" + "\n".join(self.report.notes)
            )
        except Exception as error:
            self.query_one("#cleanup-status", Static).update(flow_error(error, screen=self))
        finally:
            self.busy = False
            if self.is_mounted:
                self.query_one("#cleanup-preview", Button).disabled = False
                self.query_one("#cleanup-apply", Button).disabled = (
                    self.report is None or self.report.skipped is not None
                )

    @on(Button.Pressed, "#cleanup-preview")
    def preview(self) -> None:
        self.run_cleanup()

    @on(Button.Pressed, "#cleanup-apply")
    def apply(self) -> None:
        if self.busy or self.report is None or self.report.skipped:
            return

        def confirmed(value: bool | None) -> None:
            if value:
                self.run_cleanup(apply=True)

        self.app.push_screen(
            Confirm(
                "Permanently remove the old history shown in this preview? Your current "
                "storage rules will be checked again before anything is removed.",
                accept_label="Remove history",
                cancel_label="Keep history",
            ),
            confirmed,
        )
