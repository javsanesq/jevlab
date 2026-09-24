"""Workbench, playground, results, history, and settings screens."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    TextArea,
)

from jevlab.core.coach_models import normalize_coach_model
from jevlab.core.config import PRIVACY
from jevlab.core.credentials import (
    Credentials,
    Provider,
    secure_store_description,
    secure_store_fix,
    secure_store_name,
)
from jevlab.core.doctor import inspect
from jevlab.core.errors import JevError
from jevlab.core.files import read_text
from jevlab.core.models import Run, Template, validate_jev_model
from jevlab.core.pricing import format_cost
from jevlab.core.service import Workbench, parse_state
from jevlab.core.templates import context_estimate, dump_template, fork_template, validation_message
from jevlab.presentation import error_for_run, human_error
from jevlab.rendering import render_run
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.dialogs import Confirm, Prompt, state_file
from jevlab.tui.editor import TemplateEditor
from jevlab.tui.fields import Field, identifier, numeric


class ExplainedResult(Static):
    can_focus = True


class Home(WorkbenchScreen):
    compact_fields = True
    BINDINGS = [
        *WorkbenchScreen.BINDINGS,
        Binding("ctrl+n", "new", "New"),
        Binding("ctrl+d", "fork", "Fork"),
        Binding("slash", "search", "Search"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page compact-page", id="home-page"):
            yield Static("JEVLAB / WORKBENCH", classes="eyebrow")
            yield Static(
                "Design, inspect, and test Jev decisions. Enter opens a template; Ctrl+E explains.",
                classes="workspace-hint",
            )
            if self.wb.profile_notice:
                yield Static(self.wb.profile_notice, classes="muted", markup=False)
            yield Field(
                Input(id="template-search"),
                "Find a saved design",
                "Filter names and descriptions as you type. Leave empty to see all designs.",
                "support",
            )
            yield DataTable(id="template-table", cursor_type="row", zebra_stripes=False)
            with Horizontal(classes="buttons"):
                yield Button("Open", id="playground", variant="primary")
                yield Button("Edit", id="edit")
                yield Button("New", id="new")
                yield Button("Demo", id="demo")
                yield Button("History", id="history")
                yield Button("Settings", id="settings")
            with Collapsible(title="More tools", collapsed=True, id="home-tools"):
                with Horizontal(classes="buttons"):
                    yield Button("Evaluate", id="eval")
                    yield Button("Compare", id="compare")
                    yield Button("Batch", id="batch")
                    yield Button("Export", id="export")
                with Horizontal(classes="buttons"):
                    yield Button("Examples", id="library")
                    yield Button("Learn", id="learn")
                    yield Button("Coach", id="coach")
                    yield Button("Tour", id="tour")
                    yield Button("Cleanup", id="cleanup")
            yield Static("", id="home-summary", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Template", "Model", "Questions", "Description")
        self.refresh_list()
        self.query_one(DataTable).focus(scroll_visible=False)

    def on_screen_resume(self) -> None:
        if self.is_mounted:
            self.refresh_list()

    def refresh_list(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        search = self.query_one(Input).value.lower()
        for name in self.wb.templates.names():
            try:
                design = self.wb.templates.load(name)
                if search not in f"{name} {design.description}".lower():
                    continue
                table.add_row(
                    Text(name),
                    Text(design.model),
                    Text(str(len(design.questions)), justify="right"),
                    Text(design.description),
                    key=name,
                )
            except JevError:
                table.add_row(
                    Text(name), "invalid", "—", "Open the YAML file to repair it", key=name
                )
        runs = self.wb.storage.history(limit=1)
        recent = f"Last run: {runs[0].template_name} / {runs[0].status}" if runs else "No runs yet"
        self.query_one("#home-summary", Static).update(f"{recent}    ·    Ctrl+P for all actions")

    def selected(self) -> str | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            self.notify("No template selected. Create one with Ctrl+N.")
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    @on(Input.Changed, "#template-search")
    def filter_templates(self) -> None:
        self.refresh_list()

    @on(DataTable.RowSelected)
    def open_row(self) -> None:
        self.open_playground()

    def open_playground(self) -> None:
        name = self.selected()
        if name:
            try:
                self.app.push_screen(Playground(self.wb, self.wb.templates.load(name)))
            except JevError as error:
                self.report_error(error)

    def action_new(self) -> None:
        self.app.push_screen(TemplateEditor(self.wb))

    def action_fork(self) -> None:
        name = self.selected()
        if not name:
            return

        def fork(new_name: str | None) -> None:
            if new_name:
                try:
                    design = fork_template(self.wb.templates.load(name), new_name)
                    editor = TemplateEditor(self.wb, design)
                    editor.original_name = None
                    self.app.push_screen(editor)
                except JevError as error:
                    self.report_error(error)
                except ValueError:
                    self.notify("Use a new lowercase template name.", severity="error")

        self.app.push_screen(
            Prompt(
                "Name this copy",
                f"{name}-variant",
                description="Give the copy its own name so you can change it independently.",
                example="support-routing-variant",
                validator=identifier,
            ),
            fork,
        )

    def action_search(self) -> None:
        self.query_one(Input).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "playground":
            self.open_playground()
        elif button == "new":
            self.action_new()
        elif button == "edit":
            name = self.selected()
            if name:
                try:
                    self.app.push_screen(TemplateEditor(self.wb, self.wb.templates.load(name)))
                except JevError as error:
                    self.report_error(error)
        elif button == "history":
            self.app.push_screen(History(self.wb))
        elif button == "settings":
            self.app.push_screen(SettingsScreen(self.wb))
        elif button in ("tour", "demo"):
            from jevlab.tui.guidance import DemoScreen, TourScreen

            self.app.push_screen(TourScreen(self.wb) if button == "tour" else DemoScreen(self.wb))
        elif button in ("learn", "library", "coach"):
            from jevlab.tui.learning import CoachScreen, LearnScreen, LibraryScreen

            screen = {"learn": LearnScreen, "library": LibraryScreen, "coach": CoachScreen}[button]
            self.app.push_screen(screen(self.wb))
        elif button in ("export", "cleanup"):
            from jevlab.tui.harness import CleanupScreen, ExportScreen

            self.app.push_screen(
                ExportScreen(self.wb) if button == "export" else CleanupScreen(self.wb)
            )
        elif button in ("eval", "batch", "compare"):
            from jevlab.tui.evaluation import CompareScreen, JobScreen

            if button == "compare":
                self.app.push_screen(CompareScreen(self.wb))
            else:
                self.app.push_screen(JobScreen(self.wb, "eval" if button == "eval" else "batch"))


class Playground(WorkbenchScreen):
    compact_fields = True
    BINDINGS = [
        *WorkbenchScreen.BINDINGS,
        Binding("ctrl+r", "run", "Run Jev"),
        Binding("ctrl+o", "load", "Load file"),
    ]

    def __init__(self, wb: Workbench, template: Template, previous: Run | None = None) -> None:
        super().__init__(wb)
        self.design = template
        self.previous = previous
        self.busy = False
        self.baseline: tuple[str, str] = ("", "")

    def template_context(self) -> str:
        return f"PLAYGROUND / {self.design.name} · {self.design.model}"

    def compose(self) -> ComposeResult:
        example = self.previous.request["state"] if self.previous else self.design.state.example
        state_format = (
            ("json" if isinstance(example, (dict, list)) else "text")
            if self.previous
            else self.design.state.format
        )
        text = (
            json.dumps(example, indent=2, ensure_ascii=False)
            if state_format == "json"
            else str(example or "")
        )
        yield Header()
        yield Static(self.template_context(), id="play-context", markup=False)
        with Horizontal(classes="workspace-actions"):
            yield Button("Get answers", id="run-jev", variant="primary")
            yield Button("Load file", id="load-state")
            yield Button("Inspect design", id="inspect-design", classes="advanced")
            yield Button("Cancel request", id="cancel-run", disabled=True)
        with VerticalScroll(classes="page compact-page"):
            yield Static("TRY A TEMPLATE / ask Jev about your information", classes="eyebrow")
            names = self.wb.templates.names()
            if self.design.name not in names:
                names.append(self.design.name)
            yield Field(
                Select(
                    [(name, name) for name in names],
                    value=self.design.name,
                    allow_blank=False,
                    disabled=bool(self.previous),
                    id="play-template",
                ),
                "Saved design (template)",
                "These saved questions decide what Jev looks for in your information.",
                "support-triage",
            )
            yield Static(
                self.design.state.description, id="state-guidance", markup=False, classes="muted"
            )
            yield Field(
                Select(
                    [
                        ("JSON — information in named fields", "json"),
                        ("Text — ordinary words", "text"),
                    ],
                    value=state_format,
                    allow_blank=False,
                    id="play-format",
                ),
                "Information format",
                "Match this to your input: ordinary words for Text; named fields in braces for "
                "JSON.",
                '{"ticket": {"message": "Please refund the duplicate charge."}}',
            )
            yield Field(
                TextArea(
                    text, id="state-editor", show_line_numbers=self.wb.settings.ui_mode == "expert"
                ),
                "Information for this case (state)",
                "Give Jev the facts needed for this judgment. Sending shares them with "
                "TypeSafe; keep passwords out.",
                "A customer says they were charged twice for one order.",
            )
            yield Static("", id="context-estimate", markup=False, classes="muted advanced")
            yield Static(
                f"{self.design.model} · sends state to TypeSafe · saves locally",
                id="play-status",
                markup=False,
            )
            yield Button("More options", id="play-options", classes="options-toggle")
        yield Footer()

    def on_mount(self) -> None:
        self.update_estimate()
        self.validate_state()
        self.baseline = self.snapshot()
        self.query_one("#state-editor", TextArea).focus()

    def snapshot(self) -> tuple[str, str]:
        return (
            self.query_one("#state-editor", TextArea).text,
            str(self.query_one("#play-format", Select).value),
        )

    def request_close(self, callback: Callable[[], object]) -> None:
        if not self.busy and self.snapshot() == self.baseline:
            callback()
            return

        def discard(yes: bool | None) -> None:
            if yes:
                self.workers.cancel_group(self, "inference")
                callback()

        message = (
            "Cancel and leave? Remote completion and cost may be unknown."
            if self.busy
            else "Discard state changes that have not been run?"
        )
        self.app.push_screen(Confirm(message), discard)

    def update_estimate(self) -> None:
        text = self.query_one("#state-editor", TextArea).text
        estimate = context_estimate(self.design, text)
        self.query_one("#context-estimate", Static).update(
            f"~{estimate['estimated_total_tokens']:,} total tokens / 64k    "
            f"~{estimate['estimated_state_plus_longest']:,} state + longest / 32k    "
            "approximate; server counts may differ"
        )

    @on(TextArea.Changed, "#state-editor")
    def state_changed(self) -> None:
        self.update_estimate()
        self.validate_state()

    @on(Select.Changed, "#play-format")
    def validate_state(self) -> bool:
        if not self.is_mounted:
            return False
        try:
            text = self.query_one("#state-editor", TextArea).text
            if not text.strip():
                message = "Add a case to judge, such as a customer's message."
            else:
                if self.query_one("#play-format", Select).value == "json":
                    try:
                        json.loads(text)
                    except json.JSONDecodeError as error:
                        raise JevError(
                            "invalid_state",
                            f"Invalid JSON at line {error.lineno}, "
                            f"column {error.colno}: {error.msg}.",
                            "Use double quotes around field names and text, or choose Text.",
                        ) from None
                parse_state(text, str(self.query_one("#play-format", Select).value))
                message = ""
        except JevError as error:
            message = f"{error.message} {error.fix}".strip()
        self.query_one("#field-state-editor", Field).set_error(message)
        self.query_one("#run-jev", Button).disabled = self.busy or bool(message)
        return not message

    @on(Select.Changed, "#play-template")
    def template_changed(self, event: Select.Changed) -> None:
        if self.previous or str(event.value) == self.design.name:
            return
        try:
            self.design = self.wb.templates.load(str(event.value))
            self.query_one("#play-context", Static).update(self.template_context())
            self.query_one("#state-guidance", Static).update(self.design.state.description)
            self.query_one("#play-format", Select).value = self.design.state.format
            self.query_one("#play-status", Static).update(
                f"{self.design.model} · state preserved; check its shape"
            )
            self.update_estimate()
        except JevError as error:
            self.report_error(error)

    def action_load(self) -> None:
        if self.busy:
            return

        def loaded(value: str | None) -> None:
            if value:
                try:
                    self.query_one("#state-editor", TextArea).load_text(
                        read_text(Path(value).expanduser())
                    )
                except (OSError, ValueError):
                    self.notify(
                        "Could not load UTF-8 text. Check the path and 2 MB size limit.",
                        severity="error",
                    )

        self.app.push_screen(
            Prompt(
                "File containing your information",
                description="Load a UTF-8 text or JSON file into the information box; "
                "choose the matching format.",
                example="~/Downloads/ticket.json",
                validator=state_file,
            ),
            loaded,
        )

    @work(group="inference", exit_on_error=False)
    async def action_run(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.query_one("#run-jev", Button).disabled = True
        self.query_one("#cancel-run", Button).disabled = False
        self.query_one("#state-editor", TextArea).read_only = True
        self.query_one("#play-template", Select).disabled = True
        self.query_one("#play-format", Select).disabled = True
        self.query_one("#load-state", Button).disabled = True
        self.query_one("#play-status", Static).update("Checking the information before sending…")
        try:
            state = parse_state(
                self.query_one("#state-editor", TextArea).text,
                str(self.query_one("#play-format", Select).value),
            )
            self.query_one("#play-status", Static).update("Asking Jev…")
            result = await self.wb.run(
                self.design, state, parent_run_id=self.previous.id if self.previous else None
            )
            self.baseline = self.snapshot()
            self.query_one("#play-status", Static).update(
                f"Saved {result.id[:8]} · {result.latency_ms} ms"
            )
            self.app.push_screen(ResultScreen(self.wb, result))
        except JevError as error:
            self.query_one("#play-status", Static).update(human_error(error))
            self.report_error(error, timeout=10)
        except asyncio.CancelledError:
            if self.is_mounted:
                self.query_one("#play-status", Static).update(
                    "Cancelled locally; remote completion and cost may be unknown."
                )
            raise
        except Exception:
            error = JevError(
                "internal_error",
                "An unexpected problem stopped the request; its cause is not known.",
                "Run jevlab doctor. Check history before retrying: "
                "a sent request may still have incurred a cost.",
            )
            self.query_one("#play-status", Static).update(human_error(error))
            self.report_error(error, timeout=10)
        finally:
            self.busy = False
            if self.is_mounted:
                self.query_one("#run-jev", Button).disabled = False
                self.query_one("#cancel-run", Button).disabled = True
                self.query_one("#state-editor", TextArea).read_only = False
                self.query_one("#play-template", Select).disabled = bool(self.previous)
                self.query_one("#play-format", Select).disabled = False
                self.query_one("#load-state", Button).disabled = False

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-jev":
            self.action_run()
        elif event.button.id == "load-state":
            self.action_load()
        elif event.button.id == "cancel-run":
            self.workers.cancel_group(self, "inference")
        elif event.button.id == "inspect-design":
            self.app.push_screen(RawScreen(self.wb, dump_template(self.design), "TEMPLATE YAML"))


class RawScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench, text: str, title: str = "RAW JSON") -> None:
        super().__init__(wb)
        self.text, self.title_text = text, title

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static(self.title_text, classes="eyebrow")
            yield Static(
                "Detailed saved data for experienced users. Esc returns to the previous screen.",
                classes="muted",
            )
            yield TextArea(self.text, read_only=True, show_line_numbers=True, id="raw-content")
        yield Footer()


class ResultScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench, result: Run) -> None:
        super().__init__(wb)
        self.result = result

    def on_mount(self) -> None:
        super().on_mount()
        if error := error_for_run(self.result):
            self.report_error(error, notify=False)

    def on_screen_resume(self) -> None:
        super().on_screen_resume()
        if error := error_for_run(self.result):
            self.report_error(error, notify=False)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static(
                f"RESULT / {self.result.template_name} / {self.result.id[:8]}",
                classes="eyebrow",
                markup=False,
            )
            yield ExplainedResult(
                render_run(self.result), id="result-visuals", classes="focusable-result"
            )
            yield Static(
                "Confidence describes how firmly Jev favors its answer; it is not a guarantee. "
                "Human review means a person should check the case. "
                "This app does not contact anyone.",
                classes="muted",
            )
        with Horizontal(classes="buttons result-actions"):
            yield Button("Raw JSON", id="raw-json", classes="advanced")
            yield Button("Try this case again", id="replay", variant="primary")
            yield Button("Ask coach (paid)", id="explain-coach", classes="advanced")
            yield Button("More options", id="result-options", classes="options-toggle")
        yield Footer()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "raw-json":
            self.app.push_screen(
                RawScreen(
                    self.wb, json.dumps(self.result.model_dump(), indent=2, ensure_ascii=False)
                )
            )
        elif event.button.id == "replay":
            try:
                self.app.push_screen(
                    Playground(self.wb, self.wb.storage.template_for(self.result), self.result)
                )
            except JevError as error:
                self.report_error(error)
            except Exception:
                self.report_error(
                    JevError(
                        "history_unavailable",
                        "This saved design could not be reopened.",
                        "Return to past results and refresh. "
                        "It may have been removed by history cleanup.",
                    )
                )
        elif event.button.id == "explain-coach":
            from jevlab.tui.learning import CoachScreen

            self.app.push_screen(CoachScreen(self.wb, mode="explain", target=self.result.id))


class History(WorkbenchScreen):
    BINDINGS = [*WorkbenchScreen.BINDINGS, Binding("slash", "search", "Search")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("PAST RESULTS / find and reopen earlier answers", classes="eyebrow")
            with Horizontal(classes="form-row"):
                yield Field(
                    Input(id="history-search"),
                    "Find words in past results",
                    "Search saved information, answers and questions. Empty shows all.",
                    "refund",
                )
                yield Field(
                    Input(id="history-template"),
                    "Saved design name",
                    "Show runs for this exact template name. Leave empty for every design.",
                    "support-triage",
                )
            yield Button("More options", id="history-options", classes="options-toggle")
            with Horizontal(classes="form-row advanced"):
                yield Field(
                    Input(id="history-model"),
                    "Model version",
                    "Filter the version saved with each request. Empty includes every version.",
                    "jev-latest",
                )
                yield Field(
                    Select(
                        [
                            ("All statuses", ""),
                            ("Succeeded", "succeeded"),
                            ("Failed", "failed"),
                            ("Interrupted", "interrupted"),
                            ("Pending / unknown", "pending"),
                        ],
                        value="",
                        allow_blank=False,
                        id="history-status",
                    ),
                    "Request outcome",
                    "Filter by whether the request completed, failed or was interrupted.",
                    "Failed",
                )
            yield DataTable(id="history-table", cursor_type="row")
            with Horizontal(classes="buttons"):
                yield Button("Open result", id="inspect-run", variant="primary")
                yield Button("Daily totals", id="daily-totals")
                yield Button("Template totals", id="template-totals")
            yield Static(
                "Latest 200 matching runs (saved requests). Times use UTC, a shared world clock. "
                "Older history is removed according to Settings.",
                classes="muted",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "Run", "UTC time", "Template", "Model", "Status", "ms", "$ est."
        )
        self.refresh_history()

    def on_screen_resume(self) -> None:
        if self.is_mounted:
            self.refresh_history()

    def refresh_history(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        runs = self.wb.storage.history(
            search=self.query_one("#history-search", Input).value,
            template=self.query_one("#history-template", Input).value,
            model=self.query_one("#history-model", Input).value,
            status=str(self.query_one("#history-status", Select).value),
            limit=200,
        )
        for run in runs:
            table.add_row(
                run.id[:8],
                run.started_at[:19],
                Text(run.template_name),
                Text(run.resolved_model or run.requested_model),
                run.status,
                Text(str(run.latency_ms) if run.latency_ms is not None else "—", justify="right"),
                Text(format_cost(run.cost_nanousd), justify="right"),
                key=run.id,
            )

    @on(Input.Changed)
    @on(Select.Changed)
    def filters_changed(self) -> None:
        self.refresh_history()

    @on(DataTable.RowSelected)
    def inspect_run(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count:
            run_id = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            try:
                self.app.push_screen(ResultScreen(self.wb, self.wb.storage.get(run_id)))
            except JevError as error:
                self.report_error(error)
            except Exception:
                self.report_error(
                    JevError(
                        "history_unavailable",
                        "This saved result could not be opened.",
                        "Refresh past results or run jevlab doctor to check local storage.",
                    )
                )

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("options-toggle"):
            return
        if event.button.id == "inspect-run":
            self.inspect_run()
        else:
            group = "day" if event.button.id == "daily-totals" else "template"
            rows = self.wb.storage.totals(group)
            lines = [
                f"{'Period / template':<28} {'Runs':>6} {'Latency ms':>12} "
                f"{'Cost est.':>15} {'Unknown':>8}"
            ]
            for row in rows:
                lines.append(
                    f"{str(row['bucket']):<28} {row['runs']:>6} {str(row['latency_ms'] or 0):>12} "
                    f"{format_cost(cast(int | None, row['cost_nanousd'])):>15} "
                    f"{row['unknown_cost_runs']:>8}"
                )
            self.app.push_screen(
                RawScreen(self.wb, "\n".join(lines), "TOTALS / all retained history")
            )

    def action_search(self) -> None:
        self.query_one("#history-search", Input).focus()


class SettingsScreen(WorkbenchScreen):
    BINDINGS = [*WorkbenchScreen.BINDINGS, Binding("ctrl+s", "save", "Save settings")]

    def on_mount(self) -> None:
        self.baseline = self.snapshot()
        self.validate_fields()
        self.update_key_mode()

    @on(Select.Changed, "#credential-mode")
    def update_key_mode(self) -> None:
        if not self.is_mounted:
            return
        environment = (
            self.wb.settings.credential_mode == "environment"
            or self.query_one("#credential-mode", Select).value == "environment"
        )
        self.query_one("#save-key", Button).label = (
            f"Save key and use {secure_store_name()}"
            if environment
            else f"Save key to {secure_store_name()}"
        )
        self.query_one("#key-source", Static).update(
            "Environment-only mode is active. Saving a key here switches credential lookup "
            f"to {secure_store_name()} first, with environment variables as fallback."
            if environment
            else f"Keys are read from {secure_store_description()} first, then environment "
            "variables. Saving costs nothing and makes no API call."
        )

    def validate_model(self) -> bool:
        if not self.is_mounted:
            return False
        try:
            validate_jev_model(self.query_one("#config-model", Input).value)
            message = ""
        except ValueError as error:
            message = str(error)
        self.query_one("#config-model-validation", Static).update(message)
        self.query_one("#field-config-model", Field).set_error(message)
        return not message

    @on(Input.Changed)
    def validate_fields(self) -> bool:
        if not self.is_mounted:
            return False
        for provider in ("anthropic", "openai"):
            field = self.query_one(f"#field-coach-{provider}-model", Field)
            try:
                normalize_coach_model(
                    self.query_one(f"#coach-{provider}-model", Input).value,
                    "anthropic" if provider == "anthropic" else "openai",
                )
                field.set_error("")
            except ValueError as error:
                field.set_error(str(error))
        valid = self.validate_model()
        for field in self.query(Field):
            valid = field.validate() and valid
        self.query_one("#save-settings", Button).disabled = not valid
        return valid

    def snapshot(self) -> tuple[str, ...]:
        return (
            *(
                self.query_one(f"#{key}", Input).value
                for key in (
                    "config-model",
                    "timeout",
                    "retries",
                    "retention-days",
                    "retention-bytes",
                    "coach-anthropic-model",
                    "coach-openai-model",
                )
            ),
            str(self.query_one("#ui-mode", Select).value),
            str(self.query_one("#credential-mode", Select).value),
            str(self.query_one("#coach-provider", Select).value),
            self.query_one("#confirm-cost", Input).value,
        )

    def request_close(self, callback: Callable[[], object]) -> None:
        if self.snapshot() == self.baseline and not self.query_one("#api-key", Input).value:
            callback()
            return

        def discard(yes: bool | None) -> None:
            if yes:
                self.query_one("#api-key", Input).value = ""
                callback()

        self.app.push_screen(Confirm("Discard unsaved settings or credential input?"), discard)

    def compose(self) -> ComposeResult:
        config = self.wb.settings
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("SETTINGS / choose how you use Jev", classes="eyebrow")
            yield Field(
                Select(
                    [
                        ("Simple — guided steps and fewer controls", "simple"),
                        ("Expert — all controls and detailed data", "expert"),
                    ],
                    value=config.ui_mode,
                    allow_blank=False,
                    id="ui-mode",
                ),
                "Display mode",
                "Simple shows field explanations. Expert collapses them; both modes have every "
                "feature.",
                "Simple while learning; Expert when familiar with the controls.",
            )
            yield Field(
                Select(
                    [
                        ("TypeSafe — needed for live Jev answers", "typesafe"),
                        ("Anthropic — optional coach", "anthropic"),
                        ("OpenAI — optional coach", "openai"),
                    ],
                    value="typesafe",
                    allow_blank=False,
                    id="key-provider",
                ),
                "Which account is this key for?",
                "TypeSafe provides decisions. Anthropic and OpenAI are separate, optional coach "
                "accounts.",
                "TypeSafe for Get answers.",
            )
            yield Field(
                Input(
                    placeholder="Paste your key here; it stays hidden", password=True, id="api-key"
                ),
                "Private access key (API key)",
                "Paste the key from the selected provider's account page. The field stays "
                f"hidden; save to {secure_store_description()}.",
            )
            yield Button(f"Save key to {secure_store_name()}", id="save-key")
            yield Static(
                f"{secure_store_description()} is protected storage. Saving a key costs "
                "nothing and does not verify it with the provider.",
                classes="muted",
                id="key-source",
            )
            yield Static("", id="settings-status", markup=False)
            yield Static("", id="config-model-validation", markup=False)
            yield Button("Save settings", id="save-settings", variant="primary")
            yield Field(
                Input(f"{config.confirm_cost_usd:g}", id="confirm-cost"),
                "Confirmation budget (US dollars)",
                "Batches, evaluations, comparisons, lessons and coach requests estimated at or "
                "below this amount start without asking. Unknown prices always ask. Single "
                "runs start immediately.",
                "1",
                validator=numeric("Confirmation budget", 0),
            )
            yield Button("More options", id="settings-options", classes="options-toggle")
            with Vertical(classes="advanced settings-advanced"):
                yield Field(
                    Input(config.model, id="config-model"),
                    "Default Jev model version",
                    "New templates start with this model. Existing templates keep their saved "
                    "version.",
                    "jev-latest",
                )
                yield Field(
                    Select(
                        [
                            (
                                f"{secure_store_name()} first, then environment variables",
                                "keychain",
                            ),
                            ("Environment variables only (advanced)", "environment"),
                        ],
                        value=config.credential_mode,
                        allow_blank=False,
                        id="credential-mode",
                    ),
                    "Where to find account keys",
                    f"{secure_store_name()} checks protected storage, then environment "
                    f"variables. Environment mode skips {secure_store_name()}.",
                    f"{secure_store_name()} first on a personal computer.",
                )
                with Horizontal(classes="form-row"):
                    with Vertical():
                        yield Field(
                            Input(str(config.timeout_seconds), id="timeout"),
                            "Seconds to wait for a response",
                            "A request times out after this many seconds. Allowed range: more "
                            "than 0, up to 300.",
                            "10",
                            validator=numeric("Timeout", 0, 300, exclusive_minimum=True),
                        )
                    with Vertical():
                        yield Field(
                            Input(str(config.max_retries), id="retries"),
                            "Automatic retry attempts",
                            "Repeat a temporarily failed request this many times; retries may "
                            "add charges. Use 0 to disable.",
                            "2",
                            validator=numeric("Retry attempts", 0, 5, integer=True),
                        )
                with Horizontal(classes="form-row"):
                    with Vertical():
                        yield Field(
                            Input(str(config.retention_days), id="retention-days"),
                            "Days of history to keep",
                            "Cleanup removes older runs and keeps templates. Preview cleanup "
                            "before removing records.",
                            "90",
                            validator=numeric("History days", 1, integer=True),
                        )
                    with Vertical():
                        yield Field(
                            Input(str(config.retention_bytes), id="retention-bytes"),
                            "History size limit (bytes)",
                            "Cleanup also limits saved history by size. 100,000,000 bytes is "
                            "about 100 MB.",
                            "100000000",
                            validator=numeric("History size", 1_000_000, integer=True),
                        )
                yield Field(
                    Select(
                        [("Off", "disabled"), ("Anthropic", "anthropic"), ("OpenAI", "openai")],
                        value=config.coach_provider,
                        allow_blank=False,
                        id="coach-provider",
                    ),
                    "Optional coach provider",
                    "Choose the service for design advice. It needs its own key and SDK; Jev "
                    "still supplies every decision.",
                    "Off while exploring the recorded demo.",
                )
                yield Field(
                    Input(config.anthropic_model, id="coach-anthropic-model"),
                    "Anthropic coach model",
                    "Use an Anthropic API model ID supported by your account. This setting does "
                    "not change Jev.",
                    "claude-haiku-4-5-20251001",
                )
                yield Field(
                    Input(config.openai_model, id="coach-openai-model"),
                    "OpenAI coach model",
                    "Use an OpenAI API model ID supported by your account. This setting does "
                    "not change Jev.",
                    "gpt-5.6-luna",
                )
                yield Static(
                    "The coach offers advice; Jev supplies decisions. Each provider needs its own "
                    "key and optional software package. Run jevlab doctor --coach in your terminal "
                    "to check both.",
                    classes="muted",
                )
                yield Static("\n".join(PRIVACY.values()), classes="muted", markup=False)
            yield Button("Check this installation (free)", id="doctor")
            yield Static(
                "The installation check has not run yet.", id="doctor-result", markup=False
            )
        yield Footer()

    def action_save(self) -> None:
        if not self.validate_fields():
            return
        values: dict[str, object] = {
            "ui_mode": self.query_one("#ui-mode", Select).value,
            "model": self.query_one("#config-model", Input).value,
            "credential_mode": self.query_one("#credential-mode", Select).value,
            "timeout_seconds": self.query_one("#timeout", Input).value,
            "max_retries": self.query_one("#retries", Input).value,
            "retention_days": self.query_one("#retention-days", Input).value,
            "retention_bytes": self.query_one("#retention-bytes", Input).value,
            "coach_provider": self.query_one("#coach-provider", Select).value,
            "anthropic_model": self.query_one("#coach-anthropic-model", Input).value,
            "openai_model": self.query_one("#coach-openai-model", Input).value,
            "confirm_cost_usd": self.query_one("#confirm-cost", Input).value,
        }
        try:
            self.wb.update_settings(self.wb.settings.with_updates(values))
            self.query_one("#coach-anthropic-model", Input).value = self.wb.settings.anthropic_model
            self.query_one("#coach-openai-model", Input).value = self.wb.settings.openai_model
            self.baseline = self.snapshot()
            self.apply_mode()
            self.update_key_mode()
            self.query_one("#settings-status", Static).update(
                "Settings saved. Existing templates keep their model pin."
            )
        except (ValueError, OSError) as error:
            safe = JevError(
                "file_error" if isinstance(error, OSError) else "invalid_config",
                "Settings could not be written to config.toml."
                if isinstance(error, OSError)
                else validation_message(error),
                "Check that the configuration directory is writable."
                if isinstance(error, OSError)
                else "Correct the named setting, then save again.",
            )
            self.query_one("#settings-status", Static).update(human_error(safe))
            self.report_error(safe)

    @work(group="keychain", exclusive=True, exit_on_error=False)
    async def save_key(self) -> None:
        key_input = self.query_one("#api-key", Input)
        value = key_input.value
        key_input.value = ""
        provider = cast(Provider, str(self.query_one("#key-provider", Select).value))
        key_stored = False
        try:
            await asyncio.to_thread(Credentials().save, provider, value)
            key_stored = True
            # The button explicitly names this mode change before a key is entered.
            unchanged = self.snapshot() == self.baseline
            if self.wb.settings.credential_mode != "keychain":
                self.wb.update_settings(
                    self.wb.settings.with_updates({"credential_mode": "keychain"})
                )
            self.query_one("#credential-mode", Select).value = "keychain"
            if unchanged:
                self.baseline = self.snapshot()
            self.update_key_mode()
            self.query_one("#settings-status", Static).update(
                f"Key stored in {secure_store_description()}; {secure_store_name()} lookup "
                "is active. No API call was made."
            )
        except JevError as error:
            self.query_one("#settings-status", Static).update(human_error(error))
            self.notify(human_error(error), severity="error")
        except OSError:
            error = JevError(
                "file_error",
                "The key was stored, but the credential source could not be saved."
                if key_stored
                else f"The key could not be saved to {secure_store_name()}.",
                f"Check that config.toml is writable, then select {secure_store_name()} in "
                "settings and save. "
                "The stored key will not be used while environment-only mode is active."
                if key_stored
                else secure_store_fix(provider),
            )
            self.query_one("#settings-status", Static).update(human_error(error))
            self.report_error(error)

    @work(group="doctor", exclusive=True, exit_on_error=False)
    async def run_doctor(self) -> None:
        self.query_one("#doctor-result", Static).update("Checking local environment…")
        try:
            report = await asyncio.to_thread(inspect, self.wb)
            if self.wb.settings.ui_mode == "expert":
                message = json.dumps(report, indent=2)
            else:
                credentials = cast(dict[str, dict[str, object]], report["credentials"])
                lines = ["Local check finished. No online request was made."]
                for provider, status in credentials.items():
                    if isinstance(error := status.get("error"), dict):
                        lines.append(f"{provider.title()} key: could not check.")
                        lines.append(human_error(JevError.from_dict(error)))
                        continue
                    lines.append(
                        f"{provider.title()} key: found in {status['source']}"
                        if status["present"]
                        else f"{provider.title()} key: not found. "
                        "Add it above if you want to use this provider."
                    )
                lines.append(
                    f"Saved history: {report['database']}. "
                    f"Storage used: {int(str(report['disk_bytes'])) / 1_000_000:.2f} MB."
                )
                lines.append(
                    "This checks your setup only. "
                    "A saved key may still be rejected by its provider."
                )
                message = "\n".join(lines)
            self.query_one("#doctor-result", Static).update(message)
        except Exception:
            error = JevError(
                "local_error",
                "The installation check could not finish.",
                "Run jevlab doctor in your terminal "
                "and check that the app's data folder is readable.",
            )
            self.query_one("#doctor-result", Static).update(human_error(error))
            self.notify(human_error(error), severity="error")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-settings":
            self.action_save()
        elif event.button.id == "save-key":
            self.save_key()
        elif event.button.id == "doctor":
            self.run_doctor()
