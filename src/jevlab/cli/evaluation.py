"""Datasets, resumable jobs, empirical thresholds, and paired comparisons."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from typesafe_sdk import Noul

from jevlab.cli.common import (
    JsonFlag,
    console,
    emit,
    guarded,
    launch,
    stderr,
    verbose_errors,
    workbench,
)
from jevlab.cli.spending import confirm_spend, interactive
from jevlab.core.compare import ComparisonReport, compare, comparison_plan
from jevlab.core.errors import JevError
from jevlab.core.evaluation import EvalReport, threshold_stats
from jevlab.core.files import read_text
from jevlab.core.jobs import BatchService, JobReport
from jevlab.core.models import ConfidenceGate, Gate, NoulGate, Template
from jevlab.core.pricing import format_cost
from jevlab.core.service import Workbench, parse_state
from jevlab.core.spending import SpendEstimate, SpendScope
from jevlab.presentation import human_error

datasets_app = typer.Typer(invoke_without_command=True, help="Register CSV/JSONL dataset paths.")
eval_app = typer.Typer(invoke_without_command=True, help="Evaluate designs and tune review gates.")
Concurrency = Annotated[int, typer.Option(min=1, max=32, help="Maximum simultaneous Jev calls.")]
Rate = Annotated[
    float, typer.Option("--rate", min=0.01, max=100, help="Maximum new calls per second.")
]
Yes = Annotated[
    bool, typer.Option("--yes", help="Skip this cost prompt; do not change preferences.")
]


def authorize(
    calls: int,
    cost: int | None,
    note: str,
    required: bool,
    yes: bool,
    machine: bool,
    *,
    wb: Workbench | None = None,
    scope: SpendScope | None = None,
) -> bool:
    """Show the estimate before any call, and never prompt in machine mode."""
    if interactive(machine):
        return confirm_spend(
            SpendEstimate(calls, cost, f"Run {calls:,} live Jev decisions.", note),
            yes=yes,
            wb=wb,
            scope=scope,
        )
    stderr.print(Text(f"{calls:,} Jev calls · estimated {format_cost(cost)}. {note}"))
    if required and not yes:
        if not machine and sys.stdin.isatty():
            yes = typer.confirm("Run at the displayed estimate?")
        if not yes:
            raise JevError(
                "cost_confirmation",
                "No calls were started.",
                "Inspect the plan, then pass --yes to authorize this estimate.",
            )
    return yes


def progress_display(machine: bool) -> Progress:
    return Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.completed:.0f}/{task.total:.0f}"),
        TimeElapsedColumn(),
        console=stderr,
        disable=machine or not stderr.is_terminal,
        transient=True,
    )


@datasets_app.callback()
@guarded
def datasets(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    if ctx.invoked_subcommand:
        return
    list_datasets(json_output=json_output)


@datasets_app.command("list")
@guarded
def list_datasets(json_output: JsonFlag = False) -> None:
    data = BatchService(workbench()).datasets()
    if json_output:
        emit([item.model_dump(mode="json") for item in data])
        return
    table = Table("Path", "Format", "Rows", "Labeled", "SHA256", box=None)
    for item in data:
        table.add_row(
            Text(item.path),
            item.format,
            Text(f"{item.rows:,}", justify="right"),
            Text(f"{item.labeled_rows:,}", justify="right"),
            item.sha256[:12],
        )
    console.print(table)
    console.print(Text("Dataset files are referenced in place; contents are not copied."))


@datasets_app.command("import")
@guarded
def import_dataset(
    path: Path,
    template: Annotated[str, typer.Option(help="Validate labels against this saved template.")],
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    result = BatchService(wb).register_dataset(
        path.expanduser(), wb.templates.load(template), require_labels=True
    )
    emit(result.model_dump(mode="json")) if json_output else console.print(
        Text(f"Registered {result.rows:,} labeled rows at {result.path}\nSHA256 {result.sha256}")
    )


@eval_app.callback()
@guarded
def evaluations(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    if ctx.invoked_subcommand:
        return
    wb = workbench()
    if json_output:
        emit([item.model_dump(mode="json") for item in BatchService(wb).list(kind="eval")])
    elif sys.stdin.isatty():
        launch(wb, "eval")
    else:
        display_jobs(BatchService(wb).list(kind="eval"))


def display_jobs(jobs: list[JobReport]) -> None:
    table = Table("Job", "Kind", "Template", "Status", "Success", "$ est.", box=None)
    for job in jobs:
        table.add_row(
            job.id[:8],
            job.kind,
            Text(job.template_name),
            job.status,
            Text(f"{job.succeeded:,}/{job.total:,}", justify="right"),
            Text(format_cost(job.known_cost_nanousd), justify="right"),
        )
    console.print(table)


@eval_app.command("plan")
@guarded
def plan_evaluation(template: str, dataset: Path, json_output: JsonFlag = False) -> None:
    wb = workbench()
    plan = BatchService(wb).plan(wb.templates.load(template), dataset.expanduser(), kind="eval")
    emit(plan.model_dump(mode="json")) if json_output else console.print(
        Text(plan.model_dump_json(indent=2))
    )


def display_metrics(report: EvalReport) -> None:
    table = Table("Question", "Correct / labeled", "Accuracy", "Answered", "Score MAE", box=None)
    for name, metrics in report.per_question.items():
        table.add_row(
            Text(name),
            Text(f"{metrics.correct:,}/{metrics.total:,}", justify="right"),
            Text(f"{metrics.accuracy:.2%}", justify="right"),
            Text(f"{metrics.answered:,}", justify="right"),
            Text(
                f"{metrics.mean_absolute_error:.2f}"
                if metrics.mean_absolute_error is not None
                else "—",
                justify="right",
            ),
        )
    console.print(table)
    timing = Table("Input tokens", "Output tokens", "Mean ms", "P50 ms", "P95 ms", box=None)
    timing.add_row(
        Text(f"{report.input_tokens:,}", justify="right"),
        Text(f"{report.output_tokens:,}", justify="right"),
        *[
            Text(f"{value:,.0f}" if value is not None else "—", justify="right")
            for value in (report.latency_mean_ms, report.latency_p50_ms, report.latency_p95_ms)
        ],
    )
    console.print(timing)
    console.print(
        Text(
            "Resolved models: "
            + (
                ", ".join(
                    f"{model} ({count:,} runs)" for model, count in report.resolved_models.items()
                )
                if report.resolved_models
                else "unknown"
            )
        )
    )
    for name, metrics in report.per_question.items():
        chart = Table(f"{name}: probability bin", "Cases", "Predicted", "Observed", box=None)
        for bucket in metrics.calibration:
            if not bucket.count:
                continue
            chart.add_row(
                f"{bucket.lower:.2f}–{bucket.upper:.2f}",
                Text(str(bucket.count), justify="right"),
                Text(f"{bucket.mean_probability:.2f}", justify="right"),
                Text(f"{bucket.observed_accuracy:.2f}", justify="right"),
            )
        console.print(chart)
        if metrics.confusion_matrix:
            labels = sorted(metrics.confusion_matrix)
            confusion = Table(f"{name}: actual → predicted", *labels, box=None)
            for label in labels:
                confusion.add_row(
                    Text(label),
                    *[
                        Text(str(metrics.confusion_matrix[label].get(other, 0)), justify="right")
                        for other in labels
                    ],
                )
            console.print(confusion)
        if metrics.worst_misses:
            misses = Table(
                f"{name}: worst misses", "Expected", "Predicted", "Probability", "Run", box=None
            )
            for miss in metrics.worst_misses[:5]:
                misses.add_row(
                    Text(miss.case_id),
                    Text(str(miss.expected)),
                    Text(str(miss.predicted)),
                    Text(f"{miss.probability:.2f}", justify="right"),
                    Text(miss.run_id[:8] if miss.run_id else "—"),
                )
            console.print(misses)
    console.print(
        Text(
            "Accuracy includes failed/missing calls in its denominator. "
            "Choice/Score confidence and answer probabilities are separate; "
            "Noul has no confidence. "
            "Use --json for all bins and cases."
        )
    )


def display_report(report: JobReport, machine: bool) -> None:
    complete = report.status == "completed"
    if machine:
        envelope: dict[str, object] = {
            "schema_version": 1,
            "ok": complete,
            "data": report.model_dump(mode="json"),
        }
        if not complete:
            envelope["error"] = JevError(
                "job_incomplete",
                "Some rows have not completed successfully.",
                f"Inspect job {report.id}; resume pending rows or explicitly retry failures.",
                4,
            ).as_dict()
        typer.echo(json.dumps(envelope))
    else:
        if report.evaluation:
            display_metrics(report.evaluation)
        console.print(
            Text(
                f"{report.status} · {report.succeeded:,}/{report.total:,} succeeded · "
                f"{report.failed:,} failed · {report.unknown:,} unknown · "
                f"{report.pending:,} pending\n"
                f"Known cost {format_cost(report.known_cost_nanousd)} · "
                f"{report.unknown_cost_runs:,} unknown-cost runs\n"
                f"{report.input_tokens:,} input / {report.output_tokens:,} output tokens · "
                f"{report.latency_ms:,} ms summed call latency\nJob: {report.id}"
            )
        )
        if report.output_path:
            console.print(Text(f"Output: {report.output_path}"))
        if report.error:
            console.print(
                Text(human_error(JevError.from_dict(report.error), verbose=verbose_errors()))
            )
        elif not complete:
            console.print(
                Text("Inspect per-call errors with jevlab history --status failed --json.")
            )


@eval_app.command("run")
@guarded
def run_evaluation(
    template: str,
    dataset: Path,
    concurrency: Concurrency = 4,
    rate: Rate = 2.0,
    resume: Annotated[str | None, typer.Option(help="Resume this evaluation job.")] = None,
    retry_failed: Annotated[bool, typer.Option("--retry-failed")] = False,
    retry_unknown: Annotated[
        bool, typer.Option("--retry-unknown", help="May repeat a remotely completed/billed call.")
    ] = False,
    yes: Yes = False,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    service = BatchService(wb)
    design = wb.templates.load(template)
    path = dataset.expanduser()
    plan = service.plan(
        design,
        path,
        kind="eval",
        resume_id=resume,
        retry_failed=retry_failed,
        retry_unknown=retry_unknown,
    )
    yes = authorize(
        plan.remaining_calls,
        plan.estimated_cost_nanousd,
        plan.estimate_note,
        plan.requires_confirmation,
        yes,
        json_output,
        wb=wb,
        scope="eval",
    )
    with progress_display(json_output) as progress:
        task = progress.add_task("Evaluating", total=plan.calls)
        report = asyncio.run(
            service.run(
                design,
                path,
                kind="eval",
                concurrency=concurrency,
                requests_per_second=rate,
                authorize_cost=yes,
                expected_plan=plan if interactive(json_output) else None,
                resume_id=resume,
                retry_failed=retry_failed,
                retry_unknown=retry_unknown,
                progress=lambda done, total: progress.update(task, completed=done, total=total),
            )
        )
    display_report(report, json_output)
    if report.status != "completed":
        raise typer.Exit(4)


@eval_app.command("show")
@guarded
def show_evaluation(job_id: str, json_output: JsonFlag = False) -> None:
    service = BatchService(workbench())
    report = service.get(job_id)
    if report.kind != "eval":
        raise JevError(
            "not_an_eval", "That job is a batch run.", "Inspect it with jevlab batch --json."
        )
    # Inspection succeeds even for a failed job; the saved status describes execution.
    if json_output:
        emit(report.model_dump(mode="json"))
    else:
        display_report(report, False)


@eval_app.command("tune")
@guarded
def tune_evaluation(
    job_id: str,
    question: str,
    threshold: Annotated[float | None, typer.Option(min=0, max=1)] = None,
    no_below: Annotated[float | None, typer.Option("--no-below", min=0, max=1)] = None,
    yes_above: Annotated[float | None, typer.Option("--yes-above", min=0, max=1)] = None,
    save: Annotated[
        bool, typer.Option("--save", help="Save the gate to the matching template.")
    ] = False,
    json_output: JsonFlag = False,
) -> None:
    service = BatchService(workbench())
    report = service.get(job_id)
    if report.kind != "eval" or report.evaluation is None:
        raise JevError(
            "not_an_eval", "This job has no evaluation metrics.", "Complete an eval first."
        )
    design = service.template(job_id)
    if question not in design.questions:
        raise JevError(
            "unknown_question",
            "That question is not in this eval.",
            "Use an evaluated question ID.",
        )
    gate: Gate
    if isinstance(design.questions[question], Noul):
        if threshold is not None or no_below is None or yes_above is None:
            raise JevError(
                "invalid_gate",
                "Noul needs two probability boundaries; it has no confidence field.",
                "Pass --no-below 0.1 --yes-above 0.9 (lower must be strictly below upper).",
            )
        gate = NoulGate(no_at_or_below=no_below, yes_at_or_above=yes_above)
    else:
        if threshold is None or no_below is not None or yes_above is not None:
            raise JevError(
                "invalid_gate", "Choice/Score use a confidence threshold.", "Pass --threshold 0.8."
            )
        gate = ConfidenceGate(automate_at_or_above=threshold)
    stats = threshold_stats(report.evaluation.per_question[question], gate)
    data: dict[str, object] = {
        "job_id": report.id,
        "question": question,
        "stats": stats.model_dump(mode="json"),
        "saved": False,
        "note": "Empirical tuning on this dataset; verify on a separate holdout before deployment.",
    }
    if save:
        updated = service.save_thresholds(job_id, {question: gate})
        data.update(saved=True, template=updated.name)
    if json_output:
        emit(data)
    else:
        accuracy = f"{stats.accuracy:.2%}" if stats.accuracy is not None else "—"
        console.print(
            Text(
                f"{question} · coverage {stats.coverage:.2%} · automated accuracy {accuracy}\n"
                f"Automate {stats.automated:,}/{stats.total:,} · review {stats.review:,}\n"
                f"{'Saved' if save else 'Preview only'} · {data['note']}"
            )
        )


@guarded
def batch(
    template: Annotated[str | None, typer.Argument()] = None,
    input_path: Annotated[Path | None, typer.Option("--input")] = None,
    output: Annotated[Path | None, typer.Option()] = None,
    resume: Annotated[
        str | None, typer.Option(help="Resume a saved job ID or unique prefix.")
    ] = None,
    retry_failed: Annotated[bool, typer.Option("--retry-failed")] = False,
    retry_unknown: Annotated[
        bool, typer.Option("--retry-unknown", help="May repeat a remotely completed/billed call.")
    ] = False,
    concurrency: Concurrency = 4,
    rate: Rate = 2.0,
    yes: Yes = False,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    service = BatchService(wb)
    if not template and not resume and input_path is None and output is None:
        if retry_failed or retry_unknown:
            raise JevError(
                "resume_required", "Retry flags need a saved job.", "Pass --resume JOB_ID."
            )
        if json_output:
            emit([item.model_dump(mode="json") for item in service.list(kind="batch")])
        else:
            launch(wb, "batch")
        return
    if resume:
        previous = service.get(resume)
        if previous.kind != "batch":
            raise JevError("not_a_batch", "That job is an eval.", "Choose a batch job ID.")
        design = wb.templates.load(template) if template else service.template(resume)
        path = input_path.expanduser() if input_path else Path(previous.dataset.path)
        target = (
            output.expanduser()
            if output
            else Path(previous.output_path)
            if previous.output_path
            else None
        )
    else:
        if not template or input_path is None or output is None:
            raise JevError(
                "batch_input",
                "A new batch needs template, input, and output.",
                "Use jevlab batch TEMPLATE --input data.jsonl --output results.jsonl.",
            )
        design = wb.templates.load(template)
        path, target = input_path.expanduser(), output.expanduser()
    plan = service.plan(
        design, path, resume_id=resume, retry_failed=retry_failed, retry_unknown=retry_unknown
    )
    yes = authorize(
        plan.remaining_calls,
        plan.estimated_cost_nanousd,
        plan.estimate_note,
        plan.requires_confirmation,
        yes,
        json_output,
        wb=wb,
        scope="batch",
    )
    with progress_display(json_output) as progress:
        task = progress.add_task("Running batch", total=plan.calls)
        report = asyncio.run(
            service.run(
                design,
                path,
                output=target,
                concurrency=concurrency,
                requests_per_second=rate,
                authorize_cost=yes,
                expected_plan=plan if interactive(json_output) else None,
                progress=lambda done, total: progress.update(task, completed=done, total=total),
                resume_id=resume,
                retry_failed=retry_failed,
                retry_unknown=retry_unknown,
            )
        )
    display_report(report, json_output)
    if report.status != "completed":
        raise typer.Exit(4)


def display_comparison(report: ComparisonReport) -> None:
    table = Table("Question", "Left", "Right", "Value Δ", "Confidence L / R", box=None)
    for difference in report.differences:
        left_confidence = (
            f"{difference.left_confidence:.2f}" if difference.left_confidence is not None else "—"
        )
        right_confidence = (
            f"{difference.right_confidence:.2f}" if difference.right_confidence is not None else "—"
        )
        table.add_row(
            Text(difference.question_id),
            Text(str(difference.left_value)),
            Text(str(difference.right_value)),
            Text(
                f"{difference.value_delta:+.2f}" if difference.value_delta is not None else "—",
                justify="right",
            ),
            Text(f"{left_confidence} / {right_confidence}", justify="right"),
        )
    console.print(table)
    for difference in report.differences:
        if difference.note:
            console.print(Text(f"{difference.question_id}: {difference.note}"))
        if difference.probability_deltas:
            console.print(
                Text(
                    f"{difference.question_id} probability changes (right − left): "
                    + ", ".join(
                        f"{key} {value:+.2f}"
                        for key, value in difference.probability_deltas.items()
                    )
                )
            )
    console.print(
        Text(
            f"{report.status} · known cost {format_cost(report.known_cost_nanousd)} · "
            f"{report.unknown_cost_runs} unknown-cost runs\n"
            f"Left run: {report.left.id}\nRight run: {report.right.id}"
        )
    )


@guarded
def compare_command(
    left: Annotated[str | None, typer.Argument()] = None,
    right: Annotated[str | None, typer.Argument()] = None,
    state: Annotated[str | None, typer.Option(help="File path, or - for stdin.")] = None,
    text: Annotated[str | None, typer.Option(help="Inline state.")] = None,
    format: Annotated[str | None, typer.Option(help="State format: text or json.")] = None,
    left_model: str | None = None,
    right_model: str | None = None,
    yes: Yes = False,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    machine = json_output or state == "-"
    if left is None and right is None and state is None and text is None:
        if machine:
            emit(
                {
                    "templates": wb.templates.names(),
                    "note": "Supply LEFT RIGHT and --state or --text.",
                }
            )
        else:
            launch(wb, "compare")
        return
    if left is None or right is None or (state is None) == (text is None):
        raise JevError(
            "comparison_input",
            "Choose two templates and exactly one state source.",
            "Use jevlab compare LEFT RIGHT --state file.json or --text 'state'.",
        )
    left_design, right_design = wb.templates.load(left), wb.templates.load(right)
    if left_model:
        left_design = Template.model_validate(left_design.model_dump() | {"model": left_model})
    if right_model:
        right_design = Template.model_validate(right_design.model_dump() | {"model": right_model})
    input_format = format or left_design.state.format
    if input_format not in ("text", "json"):
        raise JevError("invalid_format", "Unknown state format.", "Choose text or json.")
    payload = (
        sys.stdin.read(2_000_001)
        if state == "-"
        else read_text(Path(state).expanduser())
        if state
        else text or ""
    )
    if len(payload.encode()) > 2_000_000:
        raise JevError(
            "state_too_large", "State exceeds the 2 MB import limit.", "Trim it before comparing."
        )
    parsed = parse_state(payload, input_format)
    plan = comparison_plan(wb, left_design, right_design, parsed)
    yes = authorize(
        plan.calls,
        plan.estimated_cost_nanousd,
        plan.estimate_note,
        plan.requires_confirmation,
        yes,
        machine,
    )
    report = asyncio.run(compare(wb, left_design, right_design, parsed, authorize_cost=yes))
    if machine:
        envelope: dict[str, object] = {
            "schema_version": 1,
            "ok": report.status == "completed",
            "data": report.model_dump(mode="json"),
        }
        if report.status != "completed":
            envelope["error"] = JevError(
                "comparison_failed",
                "One or both Jev calls failed.",
                "Inspect the saved run errors before retrying.",
                4,
            ).as_dict()
        typer.echo(json.dumps(envelope))
    else:
        display_comparison(report)
    if report.status != "completed":
        raise typer.Exit(4)
