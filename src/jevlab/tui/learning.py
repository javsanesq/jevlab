"""Guided practice, a pattern browser, and optional coach panels."""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)

from jevlab.coach.errors import coach_error
from jevlab.coach.service import Coach, CoachResult
from jevlab.core.content import (
    Lesson,
    Pattern,
    export_dataset,
    lesson,
    lessons,
    pattern,
    patterns,
    starter,
)
from jevlab.core.errors import JevError
from jevlab.core.learning import GradeReport, Learning, exercise_plan
from jevlab.core.models import ConfidenceGate, Gate, NoulGate, Template
from jevlab.core.pricing import format_cost
from jevlab.core.service import Workbench
from jevlab.core.spending import SpendEstimate, estimate_coach
from jevlab.core.templates import fork_template
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.dialogs import Confirm, Prompt
from jevlab.tui.editor import TemplateEditor
from jevlab.tui.fields import Field, identifier, output_file, required
from jevlab.tui.screens import Playground, RawScreen, ResultScreen, SettingsScreen
from jevlab.tui.spending import confirm_spend, flow_error


def dataset_export_path(value: str) -> str | None:
    try:
        if error := output_file(value):
            return error
        path = Path(value).expanduser()
        if path.exists() or path.is_symlink():
            return (
                "This path already exists. Choose a new .jsonl filename; "
                "existing data is not replaced."
            )
    except (OSError, RuntimeError, ValueError):
        return "This path cannot be read. Choose an existing folder and a new .jsonl filename."
    return None


def gate_label(gate: Gate | None) -> str:
    if isinstance(gate, ConfidenceGate):
        return f"confidence ≥{gate.automate_at_or_above:.2f}"
    if isinstance(gate, NoulGate):
        marker = " *" if round(gate.no_at_or_below, 2) == round(gate.yes_at_or_above, 2) else ""
        return f"no ≤{gate.no_at_or_below:.2f}; yes ≥{gate.yes_at_or_above:.2f}{marker}"
    return "review all"


def answer_summary(answers: Mapping[str, object]) -> str:
    """Readable case labels; the full machine representation stays in More options."""

    def word(value: object) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, float):
            return f"{value:.2f}"
        return "no answer" if value is None else str(value)

    return "; ".join(f"{name}: {word(value)}" for name, value in answers.items())


class LearnScreen(WorkbenchScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static("LEARN / ten short lessons with practice", classes="eyebrow")
            yield Static(
                "Read an example, edit a design, and test it with real Jev calls.", classes="muted"
            )
            yield DataTable(id="lesson-table", cursor_type="row")
            yield Button("Open lesson", id="open-lesson", variant="primary")
            yield Static(
                "Practice datasets are small and visible. "
                "A passing exercise is not production validation.",
                classes="muted",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Lesson", "Title", "Attempts", "Best", "Completed")
        self.refresh_lessons()
        self.query_one(DataTable).focus()

    def on_screen_resume(self) -> None:
        if self.is_mounted:
            self.refresh_lessons()

    def refresh_lessons(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        progress = {
            (p["lesson_id"], p["content_version"]): p for p in self.wb.storage.learning_progress()
        }
        for item in lessons():
            saved = progress.get((item.id, item.version), {})
            table.add_row(
                item.id[:2],
                item.title,
                Text(str(saved.get("attempts", 0)), justify="right"),
                Text(f"{float(str(saved.get('best_fraction', 0))):.0%}", justify="right"),
                "yes" if saved.get("completed") else "—",
                key=item.id,
            )

    @on(DataTable.RowSelected)
    @on(Button.Pressed, "#open-lesson")
    def open_lesson(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count:
            name = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            self.app.push_screen(LessonScreen(self.wb, lesson(name)))


class LessonScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench, item: Lesson) -> None:
        super().__init__(wb)
        self.item = item
        self.report: GradeReport | None = None
        self.busy = False
        self.baseline: tuple[str, str] = ("", "")

    def compose(self) -> ComposeResult:
        item = self.item
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static(f"LESSON {item.id[:2]} / {item.title}", classes="eyebrow")
            yield Static(item.concept, markup=False)
            yield Label("Concrete example")
            yield Static(item.example, markup=False)
            yield Label("Your exercise")
            yield Static(item.exercise, markup=False)
            yield Field(
                Input(f"lesson-{item.id}", id="lesson-template"),
                "Design to practice with",
                "Name your lesson template. Edit draft creates it if it is new; testing uses "
                "the version you saved in the editor.",
                f"lesson-{item.id}",
                validator=self.validate_template_name,
            )
            yield Button("More options", id="lesson-more", classes="options-toggle")
            yield Field(
                Input(",".join(item.default_fields), id="lesson-fields"),
                "Information to send",
                "Choose the dataset fields Jev can see, separated by commas. Removing a field "
                "hides it from every practice case; labels are never sent.",
                ", ".join(item.default_fields),
                validator=self.validate_state_fields,
                classes="advanced",
            )
            yield Static("", id="lesson-estimate", markup=False, classes="muted")
            with Horizontal(classes="buttons"):
                yield Button("Edit draft", id="edit-draft", variant="primary")
                yield Button("View dataset", id="lesson-data")
                yield Button("Preview cost", id="lesson-plan")
                yield Button("Test my design", id="grade-lesson")
            yield Static("\n".join(item.tips), markup=False, classes="muted")
            yield Static("", id="lesson-status", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Inspect last attempt", id="last-attempt", disabled=True)
                yield Button("Cancel grading", id="cancel-grade", disabled=True)
            yield Static(
                "Testing sends the selected information to TypeSafe. You will confirm the "
                "price first. Optional coach feedback has its own price and confirmation.",
                classes="muted",
            )
        yield Footer()

    def on_mount(self) -> None:
        for row in self.wb.storage.learning_progress():
            if row["lesson_id"] != self.item.id or row["content_version"] != self.item.version:
                continue
            if not row.get("last_attempt_id"):
                self.query_one("#lesson-status", Static).update(
                    "Earlier attempt details were pruned; your lesson progress is preserved."
                )
                continue
            try:
                self.report = GradeReport.model_validate(
                    self.wb.storage.attempt(str(row["last_attempt_id"]))
                )
            except (JevError, ValueError):
                self.query_one("#lesson-status", Static).update(
                    "Earlier attempt details are unavailable; your lesson progress is preserved."
                )
            else:
                self.query_one("#last-attempt", Button).disabled = False
                self.query_one("#lesson-template", Input).value = self.report.template_name
                self.query_one("#lesson-fields", Input).value = ",".join(self.report.fields)
        self.baseline = self.snapshot()
        self.preview()

    def snapshot(self) -> tuple[str, str]:
        return (
            self.query_one("#lesson-template", Input).value,
            self.query_one("#lesson-fields", Input).value,
        )

    def request_close(self, callback: Callable[[], object]) -> None:
        if not self.busy and self.snapshot() == self.baseline:
            callback()
            return

        def leave(confirmed: bool | None) -> None:
            if confirmed:
                self.workers.cancel_group(self, "lesson-grade")
                callback()

        self.app.push_screen(
            Confirm(
                "Leave this exercise? Unrun input changes are discarded; "
                "active remote calls may still complete and incur cost."
            ),
            leave,
        )

    def fields(self) -> list[str]:
        return [
            x.strip() for x in self.query_one("#lesson-fields", Input).value.split(",") if x.strip()
        ]

    def validate_template_name(self, value: str) -> str | None:
        if error := identifier(value):
            return error
        if value in self.wb.templates.names():
            try:
                self.wb.templates.load(value)
            except JevError as error:
                return f"{error.message} {error.fix}"
        return None

    def validate_state_fields(self, value: str) -> str | None:
        fields = [part.strip() for part in value.split(",") if part.strip()]
        available = sorted({key for case in pattern(self.item.pattern).cases for key in case.state})
        if not fields:
            return "Choose at least one field. Available: " + ", ".join(available) + "."
        if len(fields) != len(set(fields)):
            return "A field is repeated. List each field once, separated by commas."
        if set(fields) - set(available):
            return "A field is not in this dataset. Available: " + ", ".join(available) + "."
        return None

    @on(Input.Changed, "#lesson-template")
    @on(Input.Changed, "#lesson-fields")
    def validate_fields(self) -> bool:
        if not self.is_mounted:
            return False
        valid = all([field.validate() for field in self.query(Field)])
        for selector in ("#grade-lesson", "#edit-draft", "#lesson-plan"):
            self.query_one(selector, Button).disabled = self.busy or not valid
        return valid

    def on_screen_resume(self) -> None:
        if self.is_mounted and not self.busy:
            self.preview()

    def design(self, *, allow_starter: bool = False) -> Template:
        name = self.query_one("#lesson-template", Input).value
        self.wb.templates.path(name)
        if name not in self.wb.templates.names() and allow_starter:
            return starter(self.item, name, self.wb.settings.model)
        return self.wb.templates.load(name)

    def preview(self) -> None:
        if not self.validate_fields():
            return
        try:
            plan = exercise_plan(self.wb, self.item, self.design(allow_starter=True), self.fields())
            self.query_one("#lesson-estimate", Static).update(
                f"{plan.calls} Jev calls · estimated {format_cost(plan.estimated_cost_nanousd)}\n"
                + plan.estimate_note
                + "\n"
                + "\n".join(plan.design_feedback)
            )
        except Exception as error:
            self.query_one("#lesson-estimate", Static).update(flow_error(error, screen=self))

    def begin_grade(self) -> None:
        self.grade()

    @work(group="lesson-grade", exit_on_error=False)
    async def grade(self) -> None:
        if self.busy or not self.validate_fields():
            return
        self.busy = True
        for selector in ("#grade-lesson", "#edit-draft"):
            self.query_one(selector, Button).disabled = True
        for node in self.query(Input):
            node.disabled = True
        self.query_one("#cancel-grade", Button).disabled = False
        status = self.query_one("#lesson-status", Static)
        try:
            design = self.design()
            fields = self.fields()
            plan = exercise_plan(self.wb, self.item, design, fields)
            if not await confirm_spend(
                self,
                SpendEstimate(
                    plan.calls,
                    plan.estimated_cost_nanousd,
                    "Test your design against the lesson's example answers using Jev.",
                    plan.estimate_note,
                ),
                action="Test my design",
            ):
                status.update("Cancelled before testing. No online request was sent.")
                return
            self.report = await Learning(self.wb).grade(
                self.item,
                design,
                fields,
                authorize_cost=True,
                progress=lambda count, total: status.update(f"Graded {count}/{total} cases…"),
            )
            self.baseline = self.snapshot()
            if self.wb.settings.coach_provider != "disabled":
                feedback_settings = self.wb.settings.model_copy(deep=True)
                feedback_data = {
                    "lesson": self.item.model_dump(mode="json"),
                    "template": design.model_dump(mode="json"),
                    "grade": self.report.model_dump(mode="json"),
                }
                if await confirm_spend(
                    self,
                    estimate_coach(feedback_settings, feedback_data),
                    action="Ask for feedback",
                    detail="Your Jev grade is already saved. Cancelling keeps it unchanged.",
                ):
                    status.update("Jev grade saved. Asking the optional coach for feedback…")
                    try:
                        advice = await Coach(feedback_settings).feedback(
                            self.item, design, self.report
                        )
                        self.report.feedback = advice.model_dump(mode="json")
                    except Exception as error:
                        failure = coach_error(error, provider=feedback_settings.coach_provider)
                        self.report.feedback = {"error": failure.as_dict()}
                        self.report_error(failure)
                    self.wb.storage.save_attempt(self.report.model_dump())
            status.update(
                f"Saved attempt {self.report.id[:8]} · {self.report.fraction_correct:.0%}"
            )
            self.app.push_screen(GradeScreen(self.wb, self.report))
            self.query_one("#last-attempt", Button).disabled = False
        except Exception as error:
            status.update(flow_error(error, screen=self))
        except asyncio.CancelledError:
            status.update(
                "Cancelled. Completed calls remain in history; remote cost may be unknown."
            )
            raise
        finally:
            self.busy = False
            if self.is_mounted:
                for selector in ("#grade-lesson", "#edit-draft"):
                    self.query_one(selector, Button).disabled = False
                for node in self.query(Input):
                    node.disabled = False
                self.query_one("#cancel-grade", Button).disabled = True
                self.validate_fields()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "edit-draft":
            if not self.validate_fields():
                return
            try:
                design = self.design(allow_starter=True)
                if design.name not in self.wb.templates.names():
                    self.wb.templates.save(design)
                self.app.push_screen(TemplateEditor(self.wb, design))
            except (JevError, ValueError) as error:
                self.query_one("#lesson-estimate", Static).update(flow_error(error, screen=self))
        elif action == "lesson-data":
            self.app.push_screen(
                RawScreen(
                    self.wb,
                    "\n".join(
                        x.model_dump_json(indent=2) for x in pattern(self.item.pattern).cases
                    ),
                    "BUNDLED LABELED CASES / synthetic practice data",
                )
            )
        elif action == "lesson-plan":
            self.preview()
        elif action == "grade-lesson":
            self.begin_grade()
        elif action == "last-attempt" and self.report:
            self.app.push_screen(GradeScreen(self.wb, self.report))
        elif action == "cancel-grade":
            self.workers.cancel_group(self, "lesson-grade")


class GradeScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench, report: GradeReport) -> None:
        super().__init__(wb)
        self.report = report

    def compose(self) -> ComposeResult:
        report = self.report
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static(
                f"EXERCISE / {report.fraction_correct:.0%} / {report.status}", classes="eyebrow"
            )
            table = Table("Question", "Correct", "Accuracy", "Score error", box=None)
            for name, q in report.per_question.items():
                table.add_row(
                    name,
                    Text(f"{q.correct}/{q.total}", justify="right"),
                    Text(f"{q.accuracy:.2%}", justify="right"),
                    Text(
                        f"{q.mean_absolute_error:.2f}"
                        if q.mean_absolute_error is not None
                        else "—",
                        justify="right",
                    ),
                )
            yield Static(table)
            yield Static(
                f"{'Exercise passed' if report.passed else 'Needs work'} · "
                f"{format_cost(report.known_cost_nanousd)} known cost · "
                f"{report.unknown_cost_runs} unknown-cost runs · {report.latency_ms:,} ms",
                markup=False,
                classes="muted",
            )
            yield Static(
                "Accuracy means how often the answer matched the lesson's example answer. "
                "Score error is the average distance from the expected score. These small "
                "practice sets do not prove how well it will work elsewhere.",
                classes="muted",
            )
            if report.evaluation:
                from jevlab.tui.evaluation import calibration_plot

                yield Static(report.analysis_note, markup=False, classes="muted")
                for name, metrics in report.evaluation.per_question.items():
                    yield Static(
                        f"{name} / predicted-class calibration", markup=False, classes="eyebrow"
                    )
                    yield Static(calibration_plot(metrics), id=f"probability-{name}")
                    if metrics.confidence_calibration:
                        yield Static(
                            "Each bin groups answers with similar confidence. Observed "
                            "accuracy is how often those answers were correct.",
                            classes="muted",
                        )
                        confidence = Table(
                            "Bin", "Cases", "Mean confidence", "Observed accuracy", box=None
                        )
                        for bucket in metrics.confidence_calibration:
                            if not bucket.count:
                                continue
                            confidence.add_row(
                                f"{bucket.lower:.2f}–{bucket.upper:.2f}",
                                Text(str(bucket.count), justify="right"),
                                Text(
                                    f"{bucket.mean_probability:.2f}"
                                    if bucket.mean_probability is not None
                                    else "—",
                                    justify="right",
                                ),
                                Text(
                                    f"{bucket.observed_accuracy:.2%}"
                                    if bucket.observed_accuracy is not None
                                    else "—",
                                    justify="right",
                                ),
                            )
                        yield Static(
                            f"{name} / confidence versus observed accuracy (separate measure)",
                            markup=False,
                            classes="eyebrow",
                        )
                        yield Static(confidence, id=f"confidence-{name}")
                    elif metrics.primitive == "noul":
                        yield Static(
                            f"{name}: Noul reports P(yes), with no separate confidence.",
                            markup=False,
                            classes="muted",
                        )
            if report.routing:
                routing = Table(
                    "Question",
                    "Saved gate",
                    "Automated",
                    "Coverage",
                    "Automated accuracy",
                    box=None,
                )
                for name, stats in report.routing.items():
                    routing.add_row(
                        name,
                        gate_label(stats.gate),
                        Text(f"{stats.automated}/{stats.total}", justify="right"),
                        Text(f"{stats.coverage:.2%}", justify="right"),
                        Text(
                            f"{stats.accuracy:.2%}" if stats.accuracy is not None else "—",
                            justify="right",
                        ),
                    )
                yield Static(routing, id="saved-routing")
            for name, passed in report.learning_checks.items():
                yield Static(
                    f"{'Pass' if passed else 'Needs work'} / {name.replace('_', ' ')}", markup=False
                )
            if report.routing_curves:
                yield Static(
                    "ROUTING EXPERIMENT / local previews, no additional calls", classes="eyebrow"
                )
                for name, curve in report.routing_curves.items():
                    yield Static(name, markup=False, classes="eyebrow")
                    table = Table("Gate", "Coverage", "Automated accuracy", box=None)
                    for stats in curve[::4]:
                        table.add_row(
                            gate_label(stats.gate),
                            Text(f"{stats.coverage:.2%}", justify="right"),
                            Text(
                                f"{stats.accuracy:.2%}" if stats.accuracy is not None else "—",
                                justify="right",
                            ),
                        )
                    yield Static(table, id=f"routing-curve-{name}")
                yield Static(
                    "Noul sweeps both boundaries symmetrically; cases between them go to review. "
                    "* Boundaries can look equal at 2 decimals; the exact gap remains review. "
                    "See Full report for exact values.",
                    markup=False,
                    classes="muted",
                )
            yield DataTable(id="case-grades", cursor_type="row")
            yield Static(
                "Select a case to inspect its Jev probabilities. "
                "Labels are bundled practice targets.",
                classes="muted",
            )
            with Horizontal(classes="buttons"):
                yield Button("Inspect case", id="inspect-case", variant="primary")
                yield Button("More options", id="grade-more", classes="options-toggle")
                yield Button("Full report as JSON", id="grade-report", classes="advanced")
            if report.feedback:
                yield Static("COACH FEEDBACK / advice only", classes="eyebrow")
                failure = report.feedback.get("error")
                advice = report.feedback.get("advice")
                if isinstance(failure, dict):
                    yield Static(
                        flow_error(JevError.from_dict(failure)),
                        markup=False,
                    )
                elif isinstance(advice, dict):
                    yield Static(
                        str(advice.get("summary", ""))
                        + "\nTry next: "
                        + str(advice.get("next_experiment", "")),
                        markup=False,
                    )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        if self.report.feedback and isinstance(failure := self.report.feedback.get("error"), dict):
            self.report_error(JevError.from_dict(failure), notify=False)
        table.add_columns("Case", "Correct", "Expected", "Predicted", "Run")
        for row in self.report.cases:
            table.add_row(
                row.id,
                Text(f"{sum(row.correct.values())}/{len(row.expected)}", justify="right"),
                Text(answer_summary(row.expected)),
                Text(answer_summary(row.predicted)),
                row.run_id[:8] if row.run_id else "not completed",
                key=row.id,
            )

    @on(DataTable.RowSelected)
    @on(Button.Pressed, "#inspect-case")
    def inspect_case(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count:
            row_id = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            row = next(x for x in self.report.cases if x.id == row_id)
            if row.run_id:
                try:
                    run = self.wb.storage.get(row.run_id)
                except JevError:
                    self.notify(
                        "This run is no longer available, possibly after history cleanup. "
                        "The exercise summary remains in Full report.",
                        severity="warning",
                    )
                else:
                    self.app.push_screen(ResultScreen(self.wb, run))

    @on(Button.Pressed, "#grade-report")
    def inspect_report(self) -> None:
        self.app.push_screen(
            RawScreen(
                self.wb,
                self.report.model_dump_json(indent=2),
                "EXERCISE REPORT / feedback is advisory",
            )
        )


class LibraryScreen(WorkbenchScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="page"):
            yield Static(
                "EXAMPLE LIBRARY / starting points for your own designs", classes="eyebrow"
            )
            yield DataTable(id="patterns", cursor_type="row")
            yield Static("", id="pattern-notes", markup=False, classes="muted")
            with Horizontal(classes="buttons"):
                yield Button("Try example", id="try-pattern", variant="primary")
                yield Button("Copy and edit", id="fork-pattern")
                yield Button("More options", id="library-more", classes="options-toggle")
            with Horizontal(classes="buttons advanced"):
                yield Button("Design + examples as JSON", id="pattern-data")
                yield Button("Save example data", id="export-pattern-data")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Example", "Question types", "Cases")
        for item in patterns():
            table.add_row(
                item.title,
                ", ".join(dict.fromkeys(q.type.title() for q in item.template.questions.values())),
                Text(str(len(item.cases)), justify="right"),
                key=item.id,
            )

    def selected(self) -> Pattern | None:
        table = self.query_one(DataTable)
        if table.row_count:
            return pattern(str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value))
        return None

    @on(DataTable.RowHighlighted)
    def highlight(self) -> None:
        if item := self.selected():
            self.query_one("#pattern-notes", Static).update(item.when_to_use + "\n\n" + item.notes)

    @on(DataTable.RowSelected)
    def open_pattern(self) -> None:
        if item := self.selected():
            self.app.push_screen(Playground(self.wb, item.template))

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        item = self.selected()
        if not item:
            return
        if event.button.id == "try-pattern":
            self.open_pattern()
        elif event.button.id == "pattern-data":
            self.app.push_screen(
                RawScreen(
                    self.wb, item.model_dump_json(indent=2), "PATTERN / design, labels, and sources"
                )
            )
        elif event.button.id == "export-pattern-data":

            def export(path: str | None) -> None:
                if path:
                    try:
                        saved = export_dataset(item.id, Path(path))
                        self.notify(f"Exported labeled cases to {saved}", timeout=8)
                    except JevError as error:
                        self.report_error(error, timeout=8)

            self.app.push_screen(
                Prompt(
                    "Save the example dataset",
                    str(Path.cwd() / f"{item.id}.jsonl"),
                    description="Write the synthetic practice cases and their expected answers "
                    "to a new JSONL file. Existing files are kept unchanged.",
                    example="~/Downloads/support-cases.jsonl",
                    validator=dataset_export_path,
                ),
                export,
            )
        elif event.button.id == "fork-pattern":

            def fork(name: str | None) -> None:
                if name:
                    try:
                        editor = TemplateEditor(self.wb, fork_template(item.template, name))
                        editor.original_name = None
                        self.app.push_screen(editor)
                    except ValueError:
                        self.notify("Use a valid new lowercase name.", severity="error")

            self.app.push_screen(
                Prompt(
                    "Name this copy",
                    item.id + "-variant",
                    description="Give this editable copy its own name so the library example "
                    "stays unchanged. Save it in the editor when ready.",
                    example="my-support-routing",
                    validator=identifier,
                ),
                fork,
            )


class CoachScreen(WorkbenchScreen):
    def __init__(
        self,
        wb: Workbench,
        *,
        mode: str = "design",
        target: str = "",
        template: Template | None = None,
    ) -> None:
        super().__init__(wb)
        self.mode, self.target, self.template = mode, target, template
        self.result: CoachResult | None = None
        self.busy = False
        self.baseline: tuple[str, str, str] = ("", "", "")

    def snapshot(self) -> tuple[str, str, str]:
        return (
            self.query_one("#coach-intent", TextArea).text,
            self.query_one("#coach-target", Input).value,
            str(self.query_one("#coach-mode", Select).value),
        )

    def on_mount(self) -> None:
        self.baseline = self.snapshot()
        self.update_field_guidance()

    def provider_status(self) -> str:
        return (
            f"Provider: {self.wb.settings.coach_provider} · "
            f"model: {self.wb.settings.coach_model or 'not selected'}\n"
            "This sends the selected design/state to that provider. "
            "Advice cannot replace Jev's judgments. "
            "You will see an estimated price before each request."
        )

    def on_screen_resume(self) -> None:
        if self.is_mounted:
            self.query_one("#coach-provider-status", Static).update(self.provider_status())
            self.validate_fields()

    def request_close(self, callback: Callable[[], object]) -> None:
        if not self.busy and self.snapshot() == self.baseline:
            callback()
            return

        def leave(confirmed: bool | None) -> None:
            if confirmed:
                self.workers.cancel_group(self, "coach")
                callback()

        self.app.push_screen(
            Confirm(
                "Leave the coach? Advice and unsaved input are not retained here. "
                "An active request may still incur cost."
            ),
            leave,
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("COACH / help writing and improving a design", classes="eyebrow")
            yield Static(
                self.provider_status(),
                markup=False,
                classes="muted",
                id="coach-provider-status",
            )
            yield Field(
                Select(
                    [
                        ("Help me create a design", "design"),
                        ("Review a saved design", "critique"),
                        ("Explain saved result", "explain"),
                    ],
                    value=self.mode,
                    allow_blank=False,
                    id="coach-mode",
                ),
                "How the coach should help",
                "Create proposes questions, review critiques a design, and explain discusses "
                "a saved result. The coach gives advice; it never makes a Jev decision.",
                "Review a saved design",
            )
            yield Field(
                TextArea(id="coach-intent"),
                "What should your new design decide?",
                "Describe the goal and the information you can provide. The coach uses this "
                "to propose a template you can edit before saving.",
                "Route customer messages to billing, technical support, or general support.",
                validator=self.validate_intent,
            )
            yield Field(
                Input(self.target or "my-proposed-design", id="coach-target"),
                "New design name",
                "Choose a lowercase name for the proposed template. Asking does not save it.",
                "refund-routing",
                validator=self.validate_target,
            )
            with Horizontal(classes="buttons"):
                yield Button("Ask coach", id="ask-coach", variant="primary")
                yield Button("Edit proposal", id="edit-proposal", disabled=True)
                yield Button("Advice as JSON", id="raw-advice", disabled=True, classes="advanced")
                yield Button("Settings", id="coach-settings")
            yield Button("More options", id="coach-more", classes="options-toggle")
            yield Static("", id="coach-response", markup=False)
        yield Footer()

    def validate_intent(self, value: str) -> str | None:
        if str(self.query_one("#coach-mode", Select).value) != "design":
            return None
        return required("The goal", "what you want to decide, such as routing support messages")(
            value
        )

    def validate_target(self, value: str) -> str | None:
        mode = str(self.query_one("#coach-mode", Select).value)
        if mode in ("design", "critique"):
            if error := identifier(value):
                return error
        try:
            if mode == "critique" and not (self.template and value == self.template.name):
                self.wb.templates.load(value)
            elif mode == "explain":
                if not value.strip():
                    return "Enter a saved result ID. Copy it from Past results."
                self.wb.storage.get(value)
        except JevError as error:
            if error.code == "not_found":
                return (
                    "No saved design has that name. Choose one from Home, such as support-triage."
                )
            if error.code == "run_not_found":
                return "No unique result matches this ID. Copy a longer ID from Past results."
            return f"{error.message} {error.fix}"
        except (OSError, sqlite3.Error):
            return (
                "The saved design or result could not be read. "
                "Run jevlab doctor to check local storage."
            )
        return None

    @on(Select.Changed, "#coach-mode")
    def update_field_guidance(self) -> None:
        if not self.is_mounted:
            return
        mode = str(self.query_one("#coach-mode", Select).value)
        label, description, example = {
            "design": (
                "New design name",
                "Choose a lowercase name for the proposed template. Asking does not save it.",
                "refund-routing",
            ),
            "critique": (
                "Design to review",
                "Use a saved template name, or the current editor draft when opened from it. "
                "The coach suggests changes without editing your design.",
                "support-triage",
            ),
            "explain": (
                "Saved result ID",
                "Copy an ID from Past results. A unique prefix works too. The coach sees that "
                "run's saved state and response; its explanation is a hypothesis.",
                "a1b2c3d4",
            ),
        }[mode]
        field = self.query_one("#field-coach-target", Field)
        field.label, field.description, field.example = label, description, example
        field.query_one(Label).update(label)
        field.query_one(".field-description", Static).update(description)
        field.query_one(".field-example", Static).update(f"Example: {example}")
        self.query_one("#coach-target", Input).placeholder = example
        self.query_one("#field-coach-intent", Field).display = mode == "design"
        self.validate_fields()

    @on(Input.Changed, "#coach-target")
    @on(TextArea.Changed, "#coach-intent")
    def validate_fields(self) -> bool:
        if not self.is_mounted:
            return False
        valid = all([field.validate() for field in self.query(Field)])
        self.query_one("#ask-coach", Button).disabled = self.busy or not valid
        return valid

    @work(group="coach", exit_on_error=False)
    async def ask(self) -> None:
        if self.busy or not self.validate_fields():
            return
        self.busy = True
        for widget in self.query("Input, Select, TextArea, #ask-coach, #coach-settings"):
            widget.disabled = True
        self.result = None
        self.query_one("#edit-proposal", Button).disabled = True
        self.query_one("#raw-advice", Button).disabled = True
        output = self.query_one("#coach-response", Static)
        output.update("Preparing a price estimate…")
        try:
            coach_settings = self.wb.settings.model_copy(deep=True)
            coach = Coach(coach_settings)
            mode = str(self.query_one("#coach-mode", Select).value)
            target = self.query_one("#coach-target", Input).value
            request: Callable[[], Awaitable[CoachResult]]
            if mode == "design":
                self.wb.templates.path(target)
                intent = self.query_one("#coach-intent", TextArea).text
                if not intent.strip():
                    raise JevError(
                        "intent_required",
                        "There is no goal to work from.",
                        "Describe your goal first.",
                    )
                payload: object = {"intent": intent, "schema": Template.model_json_schema()}
                request = partial(coach.design, intent, target)
            elif mode == "critique":
                design = (
                    self.template
                    if self.template and target == self.template.name
                    else self.wb.templates.load(target)
                )
                payload = design.model_dump(mode="json")
                request = partial(coach.critique, design)
            else:
                run = self.wb.storage.get(target)
                design = self.wb.storage.template_for(run)
                payload = {"template": design.model_dump(mode="json"), "run": run.model_dump()}
                request = partial(coach.explain, run, design)
            if not await confirm_spend(
                self,
                estimate_coach(coach_settings, payload),
                action="Ask coach",
                detail="The coach gives advice. Only Jev can make the actual decision.",
            ):
                output.update("Cancelled before asking. No online request was sent.")
                return
            output.update("Requesting advice…")
            result = await request()
            self.result = result
            advice = result.advice
            output.update(
                f"{result.disclaimer}\n\n{advice.summary}\n\n"
                + "\n\n".join(advice.observations)
                + f"\n\nTest next: {advice.next_experiment}\n\n{result.input_tokens} input / "
                f"{result.output_tokens} output tokens · {result.latency_ms:,} ms · cost unknown"
            )
            self.query_one("#edit-proposal", Button).disabled = advice.template is None
            self.query_one("#raw-advice", Button).disabled = False
        except JevError as error:
            output.update(flow_error(error, screen=self))
        except asyncio.CancelledError:
            if self.is_mounted:
                output.update(
                    "Coach request cancelled. A dispatched request may still finish and incur "
                    "cost at the provider. Ask again only when you intend another request."
                )
            raise
        except Exception as error:
            output.update(
                flow_error(
                    coach_error(error, provider=self.wb.settings.coach_provider), screen=self
                )
            )
        finally:
            self.busy = False
            if self.is_mounted:
                for widget in self.query("Input, Select, TextArea, #ask-coach, #coach-settings"):
                    widget.disabled = False
                self.validate_fields()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ask-coach":
            self.ask()
        elif event.button.id == "coach-settings":
            self.app.push_screen(SettingsScreen(self.wb))
        elif event.button.id == "edit-proposal" and self.result and self.result.advice.template:
            editor = TemplateEditor(self.wb, self.result.advice.template.model_copy(deep=True))
            editor.original_name = None
            self.app.push_screen(editor)
        elif event.button.id == "raw-advice" and self.result:
            self.app.push_screen(
                RawScreen(
                    self.wb,
                    self.result.model_dump_json(indent=2),
                    "COACH ADVICE / source-backed hypotheses, not a reasoning trace",
                )
            )
