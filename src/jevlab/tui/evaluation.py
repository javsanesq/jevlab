"""Dataset jobs, evaluation inspection, routing experiments, and comparison."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    ProgressBar,
    Select,
    Static,
    TextArea,
)

from jevlab.core.compare import ComparisonReport, compare, comparison_plan
from jevlab.core.errors import JevError
from jevlab.core.evaluation import QuestionMetrics, threshold_stats
from jevlab.core.files import read_text
from jevlab.core.jobs import (
    DEFAULT_CONCURRENCY,
    DEFAULT_REQUESTS_PER_SECOND,
    BatchService,
    JobReport,
)
from jevlab.core.models import ConfidenceGate, Gate, NoulGate, Template, validate_jev_model
from jevlab.core.pricing import format_cost
from jevlab.core.service import Workbench, parse_state
from jevlab.core.spending import SpendEstimate
from jevlab.presentation import human_error
from jevlab.rendering import render_run
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.dialogs import Confirm, Prompt, state_file
from jevlab.tui.fields import Field, input_file, numeric, output_file
from jevlab.tui.screens import RawScreen, ResultScreen
from jevlab.tui.spending import confirm_spend, flow_error


def number(value: float | None, *, percent: bool = False) -> Text:
    return Text(
        "—" if value is None else f"{value:.2%}" if percent else f"{value:.2f}", justify="right"
    )


class ThresholdSlider(Static, can_focus=True):
    """A keyboard-accessible terminal slider; 0.01 increments and explicit endpoints."""

    value: reactive[int] = reactive(80)
    BINDINGS = [
        Binding("left,minus", "decrease", "− 0.01", show=False),
        Binding("right,plus", "increase", "+ 0.01", show=False),
        Binding("home", "minimum", "0.00", show=False),
        Binding("end", "maximum", "1.00", show=False),
    ]

    class Changed(Message):
        def __init__(self, slider: "ThresholdSlider") -> None:
            super().__init__()
            self.slider = slider

    def __init__(self, label: str, value: int = 80, *, id: str) -> None:
        super().__init__(id=id, classes="threshold-slider")
        self.label = label
        self.value = value

    def render(self) -> Text:
        filled = round(self.value / 100 * 30)
        return Text(
            f"{self.label:<24} {'━' * filled}●{'─' * (30 - filled)} {self.value / 100:>5.2f}"
        )

    def watch_value(self) -> None:
        self.refresh()

    def action_decrease(self) -> None:
        self.value = max(0, self.value - 1)
        self.post_message(self.Changed(self))

    def action_increase(self) -> None:
        self.value = min(100, self.value + 1)
        self.post_message(self.Changed(self))

    def action_minimum(self) -> None:
        self.value = 0
        self.post_message(self.Changed(self))

    def action_maximum(self) -> None:
        self.value = 100
        self.post_message(self.Changed(self))


def calibration_plot(metrics: QuestionMetrics) -> Text:
    grid = [[" " for _ in range(31)] for _ in range(11)]
    for index in range(11):
        grid[10 - index][index * 3] = "·"
    for bucket in metrics.calibration:
        if (
            bucket.count
            and bucket.mean_probability is not None
            and bucket.observed_accuracy is not None
        ):
            x = min(30, round(bucket.mean_probability * 30))
            y = 10 - min(10, round(bucket.observed_accuracy * 10))
            grid[y][x] = "●"
    lines = ["Calibration: do predicted chances match results?"]
    for index, row in enumerate(grid):
        lines.append(f"{1 - index / 10:>4.1f} │{''.join(row)}")
    lines += [
        "     └───────────────────────────────",
        "      0.00          0.50          1.00",
        "● groups of real results   · perfect match; empty groups omitted",
    ]
    return Text("\n".join(lines))


class JobScreen(WorkbenchScreen):
    """Both batch and eval use the same persisted and resumable execution path."""

    def __init__(self, wb: Workbench, kind: Literal["batch", "eval"] = "eval") -> None:
        super().__init__(wb)
        self.kind: Literal["batch", "eval"] = kind
        self.service = BatchService(wb)
        self.busy = False
        self.report: JobReport | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static(
                "TEST A DESIGN / compare with known answers"
                if self.kind == "eval"
                else "PROCESS A FILE / run the same design on many examples",
                classes="eyebrow",
            )
            yield Static(
                "Choose a saved design and a data file. A test (eval) compares Jev's answers "
                "with your expected answers. A batch processes many examples. Files stay where "
                "you put them. More options shows the file format and speed settings.",
                classes="muted",
            )
            names = self.wb.templates.names()
            yield Field(
                Select(
                    [(name, name) for name in names],
                    value=names[0] if names else Select.NULL,
                    id="job-template",
                ),
                "Saved design (template)",
                "The same questions are used for every row. For an evaluation, expected labels "
                "must match these questions.",
                "support-triage",
                validator=self.template_error,
            )
            yield Field(
                Input(id="dataset-path"),
                "File containing the cases",
                "Choose a CSV or JSONL file. Each row needs state; evaluations also need "
                "expected answers. The file is read in place.",
                "~/Downloads/support-cases.jsonl",
                validator=input_file,
            )
            if self.kind == "batch":
                yield Field(
                    Input(id="output-path"),
                    "Results file",
                    "Save one JSON record per line for another program to read. Missing folders "
                    "are created when the job starts. Any suffix works; .jsonl makes the format "
                    "clear. Existing files are kept unless resuming their original job.",
                    "~/Desktop/support-results.jsonl",
                    validator=self.output_error,
                )
            yield Button("More options", id="job-more", classes="options-toggle")
            yield Static(
                "JSONL records: id, state, expected. CSV columns: id, state, "
                "expected.<question>. State is the information supplied; expected is the "
                "answer you want the model to give.",
                classes="muted advanced",
            )
            with Horizontal(classes="form-row advanced"):
                with Vertical():
                    yield Field(
                        Input(str(DEFAULT_CONCURRENCY), id="job-concurrency", type="integer"),
                        "Cases running at once",
                        "Between 1 and 32 requests can run together. Lower this if the provider "
                        "rejects bursts.",
                        str(DEFAULT_CONCURRENCY),
                        validator=numeric("Concurrent requests", 1, 32, integer=True),
                    )
                with Vertical():
                    yield Field(
                        Input(f"{DEFAULT_REQUESTS_PER_SECOND:g}", id="job-rate", type="number"),
                        "New requests per second",
                        "Limit how quickly new cases start. Use more than 0 and at most 1,000. "
                        "The rate halves automatically after a provider rate-limit response.",
                        f"{DEFAULT_REQUESTS_PER_SECOND:g}",
                        validator=numeric("Request rate", 0, 1000, exclusive_minimum=True),
                    )
            yield Field(
                Input(id="resume-id"),
                "Saved job to continue (optional)",
                "Leave blank for a new job, or use Load for resume below to fill a saved job's "
                "full ID.",
                "Select a saved row, then choose Load for resume.",
                classes="advanced",
            )
            yield Field(
                Checkbox("Retry failed examples", id="retry-failed"),
                "Retry failed cases",
                "When resuming, repeat cases that recorded a failure. This can create new "
                "billable requests.",
                "Check after correcting a rejected model or question.",
                classes="advanced",
            )
            yield Field(
                Checkbox(
                    "Retry uncertain examples (may charge twice)",
                    id="retry-unknown",
                ),
                "Retry cases with unknown outcomes",
                "The provider may already have processed these cases. Retrying can charge twice "
                "and requires separate confirmation.",
                "A connection dropped before the response arrived.",
                classes="advanced",
            )
            yield Static(
                "Preview validates every row and estimates cost before execution.",
                id="job-status",
                markup=False,
                classes="muted",
            )
            yield ProgressBar(total=100, show_eta=False, id="job-progress")
            with Horizontal(classes="buttons"):
                yield Button("Check file and price", id="job-preview")
                yield Button("Start with Jev", id="job-run", variant="primary")
                yield Button("Cancel", id="job-cancel", disabled=True)
            yield Static("SAVED JOBS / select to inspect or resume", classes="eyebrow")
            yield DataTable(id="jobs-table", cursor_type="row")
            with Horizontal(classes="buttons"):
                yield Button("Inspect", id="job-inspect")
                yield Button("Load for resume", id="job-resume")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "ID", "Template", "Status", "Done / rows", "Known cost"
        )
        self.refresh_jobs()
        self.validate_fields()

    @on(Input.Changed)
    def validate_fields(self) -> bool:
        return self.validate_draft(require_output=True)

    def output_error(self, value: str) -> str | None:
        existing: Path | None = None
        if self.is_mounted and (resume := self.query_one("#resume-id", Input).value.strip()):
            try:
                report = self.service.get(resume)
                if report.kind == self.kind and report.output_path:
                    existing = Path(report.output_path)
            except JevError:
                pass  # The resume field reports the unavailable job separately.
        return output_file(value, require_jsonl=False, resume_path=existing)

    def template_error(self, value: str) -> str | None:
        # Resuming uses the immutable job snapshot, even if its YAML was removed.
        # The resume field validates that snapshot's existence and job kind.
        if self.is_mounted and self.query_one("#resume-id", Input).value.strip():
            return None
        if value not in self.wb.templates.names():
            return "Choose a saved design, such as support-triage."
        return None

    def validate_draft(self, *, require_output: bool) -> bool:
        if not self.is_mounted:
            return False
        valid = True
        for field in self.query(Field):
            field_valid = field.validate()
            if require_output or field.id != "field-output-path":
                valid = field_valid and valid
        resume = self.query_one("#resume-id", Input).value.strip()
        message = ""
        if resume:
            try:
                report = self.service.get(resume)
                if report.kind != self.kind:
                    message = f"This is a {report.kind} job. Select a saved {self.kind} job below."
            except JevError:
                message = (
                    "No saved job has that ID. Select a saved job below and choose Load for resume."
                )
        self.query_one("#field-resume-id", Field).set_error(message)
        valid = valid and not message
        # Preview still checks every dataset row. Inline checks never call a model.
        return valid

    def refresh_jobs(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for job in self.service.list(kind=self.kind):
            table.add_row(
                job.id[:8],
                job.template_name,
                job.status,
                Text(f"{job.succeeded + job.failed + job.unknown}/{job.total}", justify="right"),
                Text(format_cost(job.known_cost_nanousd), justify="right"),
                key=job.id,
            )

    def options(self) -> tuple[Template, Path, dict[str, object]]:
        name = str(self.query_one("#job-template", Select).value)
        path = Path(self.query_one("#dataset-path", Input).value).expanduser()
        resume = self.query_one("#resume-id", Input).value.strip() or None
        template = self.service.template(resume) if resume else self.wb.templates.load(name)
        return (
            template,
            path,
            {
                "resume_id": resume,
                "retry_failed": self.query_one("#retry-failed", Checkbox).value,
                "retry_unknown": self.query_one("#retry-unknown", Checkbox).value,
            },
        )

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for widget in self.query("Input, Select, Checkbox"):
            widget.disabled = busy
        for name in ("job-preview", "job-run", "job-resume"):
            self.query_one(f"#{name}", Button).disabled = busy
        self.query_one("#job-cancel", Button).disabled = not busy

    def request_close(self, callback: Callable[[], object]) -> None:
        if self.busy:
            self.notify("Cancel the active job before leaving; completed rows are saved.")
        else:
            callback()

    @work(group="dataset-job", exit_on_error=False)
    async def prepare(self, run: bool = False) -> None:
        if self.busy:
            return
        if not self.validate_draft(require_output=run):
            self.query_one("#job-status", Static).update(
                "Correct the marked fields before continuing. No request was sent."
            )
            return
        if run:
            self.execute()
            return
        self.set_busy(True)
        status = self.query_one("#job-status", Static)
        try:
            template, path, options = self.options()
            resume = options["resume_id"]
            retry_failed, retry_unknown = (
                bool(options["retry_failed"]),
                bool(options["retry_unknown"]),
            )
            plan = await asyncio.to_thread(
                self.service.plan,
                template,
                path,
                kind=self.kind,
                resume_id=str(resume) if resume else None,
                retry_failed=retry_failed,
                retry_unknown=retry_unknown,
            )
            await asyncio.to_thread(
                self.service.register_dataset, path, template, require_labels=self.kind == "eval"
            )
            status.update(
                f"{plan.remaining_calls:,} calls · estimated "
                f"{format_cost(plan.estimated_cost_nanousd)} · {plan.estimate_note}"
            )
        except Exception as error:
            status.update(
                flow_error(
                    error, screen=self, next_step="Check the design name and data file location."
                )
            )
        finally:
            self.set_busy(False)

    @work(group="dataset-execute", exit_on_error=False)
    async def execute(self) -> None:
        if self.busy:
            return
        if not self.validate_draft(require_output=True):
            self.query_one("#job-status", Static).update(
                "Correct the marked fields before continuing. No request was sent."
            )
            return
        self.set_busy(True)
        status = self.query_one("#job-status", Static)
        try:
            template, path, options = self.options()
            concurrency = int(self.query_one("#job-concurrency", Input).value)
            rate = float(self.query_one("#job-rate", Input).value)
            output_text = (
                self.query_one("#output-path", Input).value.strip() if self.kind == "batch" else ""
            )
            if self.kind == "batch" and not output_text:
                raise JevError(
                    "output_required",
                    "A batch needs a results file.",
                    "Choose a new .jsonl file location.",
                )

            resume = options["resume_id"]
            plan = await asyncio.to_thread(
                self.service.plan,
                template,
                path,
                kind=self.kind,
                resume_id=str(resume) if resume else None,
                retry_failed=bool(options["retry_failed"]),
                retry_unknown=bool(options["retry_unknown"]),
            )
            status.update(
                f"{plan.remaining_calls:,} calls · estimated "
                f"{format_cost(plan.estimated_cost_nanousd)} · {plan.estimate_note}"
            )
            if bool(options["retry_unknown"]) and not await self.app.push_screen_wait(
                Confirm(
                    "Some earlier requests may already have finished at the provider. "
                    "Retrying uncertain examples can charge for the same example twice. "
                    "Allow those retries? The estimate is shown before requests start.",
                    accept_label="Allow retry",
                    cancel_label="Cancel retries",
                )
            ):
                status.update("Cancelled. No uncertain example was retried.")
                return
            if not await confirm_spend(
                self,
                SpendEstimate(
                    plan.remaining_calls,
                    plan.estimated_cost_nanousd,
                    "Send the remaining examples in this file to Jev.",
                    plan.estimate_note,
                ),
                action="Start with Jev",
            ):
                status.update("Cancelled before starting. No online request was sent.")
                return

            def progress(done: int, total: int) -> None:
                self.query_one("#job-progress", ProgressBar).update(total=total, progress=done)
                status.update(
                    f"Saved {done:,}/{total:,} rows. Completed rows are retained if cancelled."
                )

            self.report = await self.service.run(
                template,
                path,
                kind=self.kind,
                output=Path(output_text).expanduser() if output_text else None,
                concurrency=concurrency,
                requests_per_second=rate,
                authorize_cost=True,
                resume_id=str(resume) if resume else None,
                retry_failed=bool(options["retry_failed"]),
                retry_unknown=bool(options["retry_unknown"]),
                progress=progress,
                expected_plan=plan,
            )
            status.update(f"Saved {self.report.id} · {self.report.status}")
            self.refresh_jobs()
            self.app.push_screen(EvalScreen(self.wb, self.report))
        except asyncio.CancelledError:
            status.update(
                "Cancelled. Resume the saved job; uncertain requests require explicit retry."
            )
            self.refresh_jobs()
            raise
        except Exception as error:
            status.update(
                flow_error(
                    error, screen=self, next_step="Check the file, speed settings, and saved job."
                )
            )
            try:
                self.refresh_jobs()
            except Exception:
                pass  # Preserve the displayed cause; a database fault can prevent refreshing.
        finally:
            self.set_busy(False)

    def selected(self) -> JobReport | None:
        table = self.query_one(DataTable)
        if table.row_count:
            return self.service.get(
                str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            )
        return None

    @on(DataTable.RowSelected)
    def inspect_job(self) -> None:
        if report := self.selected():
            self.app.push_screen(EvalScreen(self.wb, report))

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "job-preview":
            self.prepare()
        elif action == "job-run":
            self.prepare(run=True)
        elif action == "job-cancel":
            self.workers.cancel_group(self, "dataset-job")
            self.workers.cancel_group(self, "dataset-execute")
        elif action == "job-inspect":
            self.inspect_job()
        elif action == "job-resume" and (report := self.selected()):
            self.options_revealed = True
            self.apply_mode()
            self.query_one("#resume-id", Input).value = report.id
            self.query_one("#dataset-path", Input).value = report.dataset.path
            selector = self.query_one("#job-template", Select)
            names = sorted(set(self.wb.templates.names()) | {report.template_name})
            selector.set_options([(name, name) for name in names])
            selector.value = report.template_name
            if self.kind == "batch":
                self.query_one("#output-path", Input).value = report.output_path or ""
            self.query_one("#job-status", Static).update(
                "Resume uses the saved template revision and checks the dataset fingerprint."
            )


class EvalScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench, report: JobReport) -> None:
        super().__init__(wb)
        self.report = report

    def compose(self) -> ComposeResult:
        report = self.report
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static(
                f"{report.kind.upper()} / {report.id[:8]} / {report.status}", classes="eyebrow"
            )
            yield Static(
                f"{report.succeeded:,}/{report.total:,} succeeded · {report.failed:,} failed · "
                f"{report.unknown:,} uncertain · {format_cost(report.known_cost_nanousd)} known "
                f"cost · {report.unknown_cost_runs:,} unknown-cost runs",
                markup=False,
            )
            if report.error:
                yield Static(human_error(JevError.from_dict(report.error)), markup=False)
            yield Static(
                "Accuracy is the share of examples with the expected answer. Select an "
                "example to see what Jev decided. More options shows detailed statistics.",
                classes="muted",
            )
            yield Button("More options", id="eval-more", classes="options-toggle")
            if report.evaluation:
                evaluation = report.evaluation
                mean = (
                    f"{evaluation.latency_mean_ms:,.0f}"
                    if evaluation.latency_mean_ms is not None
                    else "—"
                )
                p95 = (
                    f"{evaluation.latency_p95_ms:,.0f}"
                    if evaluation.latency_p95_ms is not None
                    else "—"
                )
                versions = (
                    ", ".join(
                        f"{model} ({count})" for model, count in evaluation.resolved_models.items()
                    )
                    or "unavailable"
                )
                yield Static(
                    f"Average wait {mean} ms · 95% finished within {p95} ms · "
                    f"{evaluation.input_tokens:,} input tokens (pieces of text)\n"
                    f"Resolved models: {versions}. Pin versions when tuning thresholds.",
                    markup=False,
                    classes="muted advanced",
                )
                yield Field(
                    Select(
                        [(key, key) for key in evaluation.per_question],
                        value=next(iter(evaluation.per_question)),
                        allow_blank=False,
                        id="eval-question",
                    ),
                    "Question to inspect",
                    "Show accuracy, probability checks and mistakes for one question.",
                    next(iter(evaluation.per_question)),
                )
                yield Static("", id="eval-metrics")
                yield Static("", id="eval-extra-metrics", classes="advanced")
                yield Static(
                    "Probability error (Brier) compares predicted chances with known answers; "
                    "closer to zero is better. Score error is the average distance from the "
                    "expected score.",
                    classes="muted advanced",
                )
                yield Static("", id="calibration-plot")
                yield Static("", id="calibration-table", classes="advanced")
                yield Static("", id="confusion-matrix", classes="advanced")
                yield Static(
                    "SURPRISING MISTAKES / high-probability wrong answers first", classes="eyebrow"
                )
                yield DataTable(id="worst-misses", cursor_type="row")
                with Horizontal(classes="buttons"):
                    yield Button("Inspect miss", id="inspect-miss")
                    yield Button("Adjust review rules", id="tune-thresholds", variant="primary")
            yield Static("ALL ROWS / inspect completed or failed calls", classes="eyebrow")
            yield DataTable(id="job-rows", cursor_type="row")
            with Horizontal(classes="buttons"):
                yield Button("Inspect row", id="inspect-job-row")
                yield Button("Full report as JSON", id="full-eval-report", classes="advanced")
            yield Static(
                "Failed examples count against accuracy. The calibration chart compares "
                "predicted chances with how often returned answers were correct. Adjust rules "
                "on one set of examples, then test them on different examples.",
                classes="muted",
            )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#job-rows", DataTable)
        if self.report.error:
            self.report_error(JevError.from_dict(self.report.error), notify=False)
        table.add_columns("Case", "Status", "Run")
        for item in BatchService(self.wb).rows(self.report.id):
            table.add_row(
                item.case_id,
                item.status,
                item.run_id[:8] if item.run_id else "—",
                key=str(item.index),
            )
        if self.report.evaluation:
            self.query_one("#worst-misses", DataTable).add_columns(
                "Case", "Expected", "Predicted", "P(predicted)", "Confidence"
            )
            self.refresh_question()

    @on(Select.Changed, "#eval-question")
    def refresh_question(self) -> None:
        if not self.is_mounted or not self.report.evaluation:
            return
        name = str(self.query_one("#eval-question", Select).value)
        q = self.report.evaluation.per_question[name]
        summary = Table("Correct / examples", "Accuracy", "Answers returned", box=None)
        summary.add_row(
            Text(f"{q.correct}/{q.total}", justify="right"),
            number(q.accuracy, percent=True),
            Text(str(q.answered), justify="right"),
        )
        self.query_one("#eval-metrics", Static).update(summary)
        extra = Table("Probability error (Brier)", "Average score error", box=None)
        extra.add_row(number(q.brier_score), number(q.mean_absolute_error))
        self.query_one("#eval-extra-metrics", Static).update(extra)
        self.query_one("#calibration-plot", Static).update(calibration_plot(q))
        bins = Table("Probability bin", "Count", "Mean probability", "Observed accuracy", box=None)
        for bucket in q.calibration:
            if bucket.count:
                bins.add_row(
                    f"{bucket.lower:.2f}–{bucket.upper:.2f}",
                    Text(str(bucket.count), justify="right"),
                    number(bucket.mean_probability),
                    number(bucket.observed_accuracy),
                )
        self.query_one("#calibration-table", Static).update(bins)
        matrix = Table(title="Confusion matrix · rows expected / columns predicted", box=None)
        if q.confusion_matrix:
            labels = list(q.confusion_matrix)
            matrix.add_column("Expected")
            for label in labels:
                matrix.add_column(label, justify="right")
            for label in labels:
                matrix.add_row(
                    label, *(str(q.confusion_matrix[label].get(col, 0)) for col in labels)
                )
            self.query_one("#confusion-matrix", Static).update(matrix)
        else:
            self.query_one("#confusion-matrix", Static).update("")
        misses = self.query_one("#worst-misses", DataTable)
        misses.clear()
        for item in q.worst_misses:
            misses.add_row(
                item.case_id,
                Text(str(item.expected)),
                Text(str(item.predicted)),
                number(item.probability),
                number(item.confidence),
                key=item.run_id,
            )

    def inspect_selected(self, selector: str) -> None:
        table = self.query_one(selector, DataTable)
        if not table.row_count:
            return
        key = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        if selector == "#job-rows":
            item = next(
                row for row in BatchService(self.wb).rows(self.report.id) if str(row.index) == key
            )
            key = item.run_id or ""
        if key:
            self.app.push_screen(ResultScreen(self.wb, self.wb.storage.get(key)))

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        self.inspect_selected(f"#{event.data_table.id}")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "inspect-miss":
            self.inspect_selected("#worst-misses")
        elif action == "inspect-job-row":
            self.inspect_selected("#job-rows")
        elif action == "tune-thresholds":
            self.app.push_screen(ThresholdScreen(self.wb, self.report))
        elif action == "full-eval-report":
            self.app.push_screen(
                RawScreen(self.wb, self.report.model_dump_json(indent=2), "JOB REPORT")
            )


class ThresholdScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench, report: JobReport) -> None:
        super().__init__(wb)
        self.report = report
        self.template = BatchService(wb).template(report.id)
        self.gates: dict[str, Gate] = {}
        self.baseline_gates = dict(self.template.thresholds)
        try:
            current = wb.templates.load(self.template.name)
            if current.model_dump(exclude={"thresholds"}) == self.template.model_dump(
                exclude={"thresholds"}
            ):
                self.baseline_gates = dict(current.thresholds)
        except JevError:
            pass
        self.dirty = False
        self.loading = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("REVIEW RULES / decide when a person should check", classes="eyebrow")
            yield Static(
                "Thresholds are cutoffs for automatic decisions. Focus a slider and use ←/→ "
                "to change it. Coverage is the share handled automatically; automated accuracy "
                "is the share of those decisions that were correct. This preview is free.",
                classes="muted",
            )
            yield Field(
                Select(
                    [(key, key) for key in self.template.questions],
                    value=next(iter(self.template.questions)),
                    allow_blank=False,
                    id="threshold-question",
                ),
                "Question to adjust",
                "Choose which answer's rule for human review to change.",
                next(iter(self.template.questions)),
            )
            yield Field(
                ThresholdSlider("Minimum confidence", id="confidence-slider"),
                "Minimum certainty for automatic handling",
                "For Choice and Score, confidence at or above this cutoff allows automation.",
                "0.80",
            )
            yield Field(
                ThresholdSlider("P(yes) at or below: no", 10, id="no-slider"),
                "Automatic no cutoff",
                "For Noul, handle the answer as no when the chance of yes is "
                "at or below this number.",
                "0.10",
            )
            yield Field(
                ThresholdSlider("P(yes) at or above: yes", 90, id="yes-slider"),
                "Automatic yes cutoff",
                "For Noul, handle the answer as yes at or above this number. "
                "The gap between cutoffs goes to a person.",
                "0.90",
            )
            yield Static(
                "Confidence measures how concentrated the choices are. P(yes) means the "
                "estimated chance of yes. Cases between the two yes/no cutoffs go to a person.",
                classes="muted",
            )
            yield Static("", id="threshold-preview")
            yield Static("", id="threshold-status", markup=False, classes="muted")
            with Horizontal(classes="buttons"):
                yield Button(
                    "Save review rules", id="save-thresholds", variant="primary", disabled=True
                )
            yield Static(
                "These results describe this set of examples. They do not guarantee future "
                "accuracy. Test again if you change the questions.",
                classes="muted",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.load_question()

    @on(Select.Changed, "#threshold-question")
    def load_question(self) -> None:
        if not self.is_mounted:
            return
        self.loading = True
        name = str(self.query_one("#threshold-question", Select).value)
        noul = self.template.questions[name].type == "noul"
        gate = self.gates.get(name, self.baseline_gates.get(name))
        self.query_one("#field-confidence-slider", Field).display = not noul
        self.query_one("#confidence-slider", ThresholdSlider).display = not noul
        for key in ("no-slider", "yes-slider"):
            self.query_one(f"#field-{key}", Field).display = noul
            self.query_one(f"#{key}", ThresholdSlider).display = noul
        if noul:
            boundary = (
                gate
                if isinstance(gate, NoulGate)
                else NoulGate(no_at_or_below=0.1, yes_at_or_above=0.9)
            )
            self.query_one("#no-slider", ThresholdSlider).value = round(
                boundary.no_at_or_below * 100
            )
            self.query_one("#yes-slider", ThresholdSlider).value = round(
                boundary.yes_at_or_above * 100
            )
        else:
            threshold = gate.automate_at_or_above if isinstance(gate, ConfidenceGate) else 0.8
            self.query_one("#confidence-slider", ThresholdSlider).value = round(threshold * 100)
        self.loading = False
        self.preview()

    def preview(self, *, record: bool = False) -> None:
        if not self.report.evaluation:
            return
        name = str(self.query_one("#threshold-question", Select).value)
        try:
            gate: Gate
            if self.template.questions[name].type == "noul":
                gate = NoulGate(
                    no_at_or_below=self.query_one("#no-slider", ThresholdSlider).value / 100,
                    yes_at_or_above=self.query_one("#yes-slider", ThresholdSlider).value / 100,
                )
            else:
                gate = ConfidenceGate(
                    automate_at_or_above=self.query_one("#confidence-slider", ThresholdSlider).value
                    / 100
                )
            result = threshold_stats(self.report.evaluation.per_question[name], gate)
            table = Table("Automated", "Review", "Coverage", "Automated accuracy", box=None)
            table.add_row(
                Text(str(result.automated), justify="right"),
                Text(str(result.review), justify="right"),
                number(result.coverage, percent=True),
                number(result.accuracy, percent=True),
            )
            self.query_one("#threshold-preview", Static).update(table)
            self.query_one("#threshold-status", Static).update(
                "Preview only; choose Save review rules to update the saved design."
            )
            if record:
                self.gates[name] = gate
            self.query_one("#save-thresholds", Button).disabled = not bool(self.gates)
        except ValueError:
            self.query_one("#threshold-status", Static).update(
                flow_error(
                    JevError(
                        "invalid_threshold",
                        "The yes and no ranges overlap.",
                        "Keep the no cutoff below the yes cutoff.",
                    )
                )
            )
            self.query_one("#save-thresholds", Button).disabled = True

    @on(ThresholdSlider.Changed)
    def slider_changed(self) -> None:
        if self.is_mounted and not self.loading:
            self.dirty = True
            self.preview(record=True)

    @on(Button.Pressed, "#save-thresholds")
    def save(self) -> None:
        try:
            BatchService(self.wb).save_thresholds(self.report.id, self.gates)
            self.dirty = False
            self.baseline_gates.update(self.gates)
            self.gates.clear()
            self.query_one("#save-thresholds", Button).disabled = True
            self.query_one("#threshold-status", Static).update(
                "Saved review rules in your design. Earlier results keep their original rules."
            )
        except (JevError, OSError, ValueError) as error:
            self.query_one("#threshold-status", Static).update(flow_error(error, screen=self))

    def request_close(self, callback: Callable[[], object]) -> None:
        if self.dirty:

            def discarded(value: bool | None) -> None:
                if value:
                    callback()

            self.app.push_screen(
                Confirm("Discard unsaved threshold changes?"),
                discarded,
            )
        else:
            callback()


class CompareScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench) -> None:
        super().__init__(wb)
        self.busy = False

    def compose(self) -> ComposeResult:
        names = self.wb.templates.names()
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("COMPARE / try two designs on the same information", classes="eyebrow")
            with Horizontal(classes="form-row"):
                yield Field(
                    Select(
                        [(name, name) for name in names],
                        value=names[0] if names else Select.NULL,
                        id="compare-left",
                    ),
                    "First saved design",
                    "The left-hand result uses this template's questions and review rules.",
                    "support-triage",
                    validator=lambda value: (
                        None if value in names else "Choose a saved design, such as support-triage."
                    ),
                )
                yield Field(
                    Select(
                        [(name, name) for name in names],
                        value=names[0] if names else Select.NULL,
                        id="compare-right",
                    ),
                    "Second saved design",
                    "The right-hand result uses this template with the same information.",
                    "support-triage-variant",
                    validator=lambda value: (
                        None if value in names else "Choose a saved design, such as support-triage."
                    ),
                )
            yield Button("More options", id="compare-more", classes="options-toggle")
            with Horizontal(classes="form-row advanced"):
                yield Field(
                    Input(id="left-model"),
                    "First model (optional)",
                    "Leave empty to use the first design's saved model. "
                    "Set a version to compare models.",
                    "jev-latest",
                    validator=self.model_error,
                )
                yield Field(
                    Input(id="right-model"),
                    "Second model (optional)",
                    "Leave empty to use the second design's saved model. "
                    "Set a version to compare models.",
                    "jev-latest",
                    validator=self.model_error,
                )
            yield Field(
                Select(
                    [("Structured information (JSON)", "json"), ("Plain text", "text")],
                    value="json",
                    allow_blank=False,
                    id="compare-format",
                ),
                "Information format",
                "JSON holds named fields; plain text holds a message or document.",
                "Structured information (JSON)",
            )
            yield Field(
                TextArea("{}", id="compare-state", show_line_numbers=True),
                "Information both designs will judge",
                "Paste the same facts for both designs; keep instructions in their templates.",
                '{"ticket": {"message": "I was charged twice."}}',
            )
            yield Static(
                "Paste the information (state) to judge. Both designs receive it. "
                "Comparing makes two paid Jev requests; you will confirm the price first.",
                id="compare-status",
                markup=False,
                classes="muted",
            )
            with Horizontal(classes="buttons"):
                yield Button("Load file", id="compare-load")
                yield Button("Compare with Jev", id="compare-run", variant="primary")
                yield Button("Cancel", id="compare-cancel", disabled=True)
        yield Footer()

    @staticmethod
    def model_error(value: str) -> str | None:
        if not value.strip():
            return None
        try:
            validate_jev_model(value.strip())
        except ValueError as error:
            return str(error)
        return None

    @on(TextArea.Changed, "#compare-state")
    @on(Select.Changed, "#compare-format")
    def validate_state(self) -> None:
        if not self.is_mounted:
            return
        error = None
        try:
            parse_state(
                self.query_one("#compare-state", TextArea).text,
                str(self.query_one("#compare-format", Select).value),
            )
        except JevError as failure:
            error = str(failure)
        self.query_one("#field-compare-state", Field).set_error(error)

    def designs(self) -> tuple[Template, Template]:
        designs = []
        for side in ("left", "right"):
            template = self.wb.templates.load(str(self.query_one(f"#compare-{side}", Select).value))
            model = self.query_one(f"#{side}-model", Input).value.strip()
            if model:
                template.model = model
            designs.append(template)
        return designs[0], designs[1]

    @on(Button.Pressed, "#compare-run")
    def begin(self) -> None:
        self.validate_state()
        if any(not field.validate() for field in self.query(Field)):
            return
        self.execute()

    @work(group="compare", exit_on_error=False)
    async def execute(self) -> None:
        if self.busy:
            return
        self.busy = True
        for widget in self.query("Input, Select, TextArea, #compare-run, #compare-load"):
            widget.disabled = True
        self.query_one("#compare-cancel", Button).disabled = False
        try:
            left, right = self.designs()
            state = parse_state(
                self.query_one(TextArea).text, str(self.query_one("#compare-format", Select).value)
            )
            plan = comparison_plan(self.wb, left, right, state)
            if not await confirm_spend(
                self,
                SpendEstimate(
                    2,
                    plan.estimated_cost_nanousd,
                    "Ask Jev to use both designs on the same information.",
                    plan.estimate_note,
                ),
                action="Compare designs",
            ):
                self.query_one("#compare-status", Static).update(
                    "Cancelled before comparing. No online request was sent."
                )
                return
            result = await compare(self.wb, left, right, state, authorize_cost=True)
            self.app.push_screen(CompareResultScreen(self.wb, result))
        except Exception as error:
            self.query_one("#compare-status", Static).update(flow_error(error, screen=self))
        except asyncio.CancelledError:
            self.query_one("#compare-status", Static).update(
                "Cancelled. Both attempted calls are recorded; remote completion may be unknown."
            )
            raise
        finally:
            self.busy = False
            for widget in self.query("Input, Select, TextArea, #compare-run, #compare-load"):
                widget.disabled = False
            self.query_one("#compare-cancel", Button).disabled = True

    @on(Button.Pressed, "#compare-cancel")
    def cancel(self) -> None:
        self.workers.cancel_group(self, "compare")

    @on(Button.Pressed, "#compare-load")
    def load(self) -> None:
        def read(path: str | None) -> None:
            if path:
                try:
                    self.query_one(TextArea).load_text(read_text(Path(path).expanduser()))
                except (OSError, ValueError, UnicodeError):
                    self.notify("Cannot load that UTF-8 state file.", severity="error")

        self.app.push_screen(
            Prompt(
                "File containing the information to compare",
                description="Load a UTF-8 text or JSON file; both designs receive the same facts.",
                example="~/Downloads/ticket.json",
                validator=state_file,
            ),
            read,
        )

    def request_close(self, callback: Callable[[], object]) -> None:
        if self.busy:
            self.notify("Cancel the comparison before leaving.")
        elif self.query_one(TextArea).text.strip() not in ("", "{}"):

            def discarded(value: bool | None) -> None:
                if value:
                    callback()

            self.app.push_screen(
                Confirm("Discard this comparison state? Saved runs remain in history."),
                discarded,
            )
        else:
            callback()


class CompareResultScreen(WorkbenchScreen):
    def __init__(self, wb: Workbench, report: ComparisonReport) -> None:
        super().__init__(wb)
        self.report = report

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static(f"COMPARISON / {self.report.status}", classes="eyebrow")
            with Horizontal(classes="comparison-columns"):
                for side, run in (("LEFT", self.report.left), ("RIGHT", self.report.right)):
                    with Vertical(classes="comparison-column"):
                        yield Static(
                            f"{side} / {run.template_name}", markup=False, classes="eyebrow"
                        )
                        yield Static(render_run(run, compact=True))
            table = Table(
                "Question",
                "Left",
                "Right",
                "Value change",
                "Probability changes (right − left)",
                box=None,
                padding=(0, 2),
                pad_edge=False,
            )
            for item in self.report.differences:
                table.add_row(
                    item.question_id,
                    number(item.left_value)
                    if isinstance(item.left_value, float)
                    else Text(str(item.left_value)),
                    number(item.right_value)
                    if isinstance(item.right_value, float)
                    else Text(str(item.right_value)),
                    number(item.value_delta),
                    Text(
                        ", ".join(
                            f"{key}: {value:+.2f}" for key, value in item.probability_deltas.items()
                        )
                        if item.comparable
                        else item.note
                    ),
                )
            yield Button("More options", id="comparison-more", classes="options-toggle")
            yield Static(table, classes="advanced")
            yield Static(
                "Matching option labels do not establish equivalent question meanings. This is "
                "one state, not a quality benchmark.",
                classes="muted",
            )
            with Horizontal(classes="buttons"):
                yield Button("Inspect left", id="compare-inspect-left")
                yield Button("Inspect right", id="compare-inspect-right")
                yield Button("Comparison as JSON", id="compare-raw", classes="advanced")
        yield Footer()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("options-toggle"):
            return
        if event.button.id == "compare-raw":
            self.app.push_screen(
                RawScreen(self.wb, self.report.model_dump_json(indent=2), "COMPARISON")
            )
        else:
            run = (
                self.report.left if event.button.id == "compare-inspect-left" else self.report.right
            )
            self.app.push_screen(ResultScreen(self.wb, run))
