"""Lesson exercises, bundled patterns, and explicitly requested coach operations."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

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
from jevlab.coach.service import Coach, CoachResult
from jevlab.core.content import export_dataset, lesson, lessons, pattern, patterns, starter
from jevlab.core.errors import JevError
from jevlab.core.files import atomic_write
from jevlab.core.learning import GradeReport, Learning, exercise_plan
from jevlab.core.models import ConfidenceGate, Gate, NoulGate, Template
from jevlab.core.pricing import format_cost
from jevlab.core.spending import SpendEstimate, estimate_coach
from jevlab.core.templates import dump_template, fork_template
from jevlab.presentation import human_error

learn_app = typer.Typer(invoke_without_command=True, help="Practice Jev design in ten lessons.")
library_app = typer.Typer(invoke_without_command=True, help="Open and fork bundled patterns.")
coach_app = typer.Typer(invoke_without_command=True, help="Optional proposals and critique.")


def gate_label(gate: Gate | None) -> str:
    if isinstance(gate, ConfidenceGate):
        return f"confidence ≥{gate.automate_at_or_above:.2f}"
    if isinstance(gate, NoulGate):
        marker = " *" if round(gate.no_at_or_below, 2) == round(gate.yes_at_or_above, 2) else ""
        return f"no ≤{gate.no_at_or_below:.2f}; yes ≥{gate.yes_at_or_above:.2f}{marker}"
    return "review all"


def selected_fields(value: str | None) -> list[str] | None:
    return (
        [field.strip() for field in value.split(",") if field.strip()]
        if value is not None
        else None
    )


@learn_app.callback()
@guarded
def learn(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    if ctx.invoked_subcommand:
        return
    wb = workbench()
    if json_output:
        emit(
            {
                "lessons": [x.model_dump() for x in lessons()],
                "progress": wb.storage.learning_progress(),
            }
        )
    elif sys.stdin.isatty():
        launch(wb, "learn")
    else:
        for item in lessons():
            console.print(Text(f"{item.id}  {item.title}"))


@learn_app.command("show")
@guarded
def show_lesson(lesson_id: str, json_output: JsonFlag = False) -> None:
    item = lesson(lesson_id)
    data = {
        "lesson": item.model_dump(),
        "cases": [x.model_dump() for x in pattern(item.pattern).cases],
    }
    emit(data) if json_output else console.print(
        Text(
            f"{item.title}\n\n{item.concept}\n\nExample: {item.example}\n\n"
            f"Exercise: {item.exercise}\n\n" + "\n".join(item.tips)
        )
    )


@learn_app.command("start")
@guarded
def start_lesson(lesson_id: str, name: str | None = None, json_output: JsonFlag = False) -> None:
    wb, item = workbench(), lesson(lesson_id)
    design = starter(item, name or f"lesson-{item.id}", wb.settings.model)
    path = wb.templates.save(design)
    data = {
        "template": design.name,
        "path": str(path),
        "fields": item.default_fields,
        "exercise": item.exercise,
    }
    emit(data) if json_output else console.print(
        Text(
            f"Saved draft {design.name}. Edit it with jevlab templates edit {design.name}.\n"
            f"{item.exercise}"
        )
    )


@learn_app.command("plan")
@guarded
def plan_lesson(
    lesson_id: str,
    template: Annotated[str, typer.Option(help="Saved exercise template.")],
    fields: str | None = None,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    plan = exercise_plan(
        wb, lesson(lesson_id), wb.templates.load(template), selected_fields(fields)
    )
    emit(plan.model_dump()) if json_output else console.print(Text(plan.model_dump_json(indent=2)))


@learn_app.command("grade")
@guarded
def grade_lesson(
    lesson_id: str,
    template: Annotated[str, typer.Option(help="Saved exercise template.")],
    fields: str | None = None,
    yes: Annotated[bool, typer.Option("--yes", help="Authorize a high/unknown estimate.")] = False,
    json_output: JsonFlag = False,
) -> None:
    wb, item = workbench(), lesson(lesson_id)
    design = wb.templates.load(template)
    plan = exercise_plan(wb, item, design, selected_fields(fields))
    if interactive(json_output):
        yes = confirm_spend(
            SpendEstimate(
                plan.calls,
                plan.estimated_cost_nanousd,
                f"Grade your practice design with {plan.calls} live Jev decisions.",
                plan.estimate_note
                + (
                    " Optional coach feedback will ask for separate confirmation after grading."
                    if wb.settings.coach_provider != "disabled"
                    else ""
                ),
            ),
            yes=yes,
        )
    else:
        stderr.print(
            Text(
                f"{plan.calls} Jev calls · estimated {format_cost(plan.estimated_cost_nanousd)}. "
                + plan.estimate_note
            )
        )
    if plan.requires_confirmation and not yes:
        if not json_output and sys.stdin.isatty():
            yes = typer.confirm("Run this exercise at the displayed estimate?")
        if not yes:
            raise JevError(
                "cost_confirmation",
                "Exercise was not started.",
                "Review jevlab learn plan, then pass --yes to authorize.",
            )

    async def execute() -> GradeReport:
        report = await Learning(wb).grade(item, design, plan.fields, authorize_cost=yes)
        if wb.settings.coach_provider != "disabled":
            try:
                confirm_spend(
                    estimate_coach(
                        wb.settings,
                        {
                            "lesson": item.model_dump(),
                            "template": design.model_dump(mode="json"),
                            "grade": report.model_dump(),
                        },
                    ),
                    machine=json_output,
                )
                feedback = await Coach(wb.settings).feedback(item, design, report)
                report.feedback = feedback.model_dump(mode="json")
            except JevError as error:
                report.feedback = {"error": error.as_dict()}
            wb.storage.save_attempt(report.model_dump())
        return report

    report = asyncio.run(execute())
    if json_output:
        envelope: dict[str, object] = {
            "schema_version": 1,
            "ok": report.status == "completed",
            "data": report.model_dump(),
        }
        if report.status != "completed":
            envelope["error"] = JevError(
                "exercise_failed",
                "Some exercise calls could not be completed.",
                f"Inspect the saved attempt with jevlab learn inspect {report.id} --json.",
                4,
            ).as_dict()
        typer.echo(json.dumps(envelope))
    else:
        table = Table("Question", "Correct", "Accuracy", "Score MAE", box=None)
        for name, result in report.per_question.items():
            table.add_row(
                name,
                Text(f"{result.correct}/{result.total}", justify="right"),
                Text(f"{result.accuracy:.2%}", justify="right"),
                Text(
                    f"{result.mean_absolute_error:.2f}"
                    if result.mean_absolute_error is not None
                    else "—",
                    justify="right",
                ),
            )
        console.print(table)
        if report.routing:
            routing = Table("Question", "Saved gate", "Coverage", "Automated accuracy", box=None)
            for name, stats in report.routing.items():
                routing.add_row(
                    name,
                    gate_label(stats.gate),
                    Text(f"{stats.coverage:.2%}", justify="right"),
                    Text(
                        f"{stats.accuracy:.2%}" if stats.accuracy is not None else "—",
                        justify="right",
                    ),
                )
            console.print(routing)
            if any("*" in gate_label(stats.gate) for stats in report.routing.values()):
                console.print(
                    Text(
                        "* Boundaries round to the same 2 decimals; the exact gap remains review. "
                        "Use jevlab learn inspect --json for exact values."
                    )
                )
        if report.analysis_note:
            console.print(Text(report.analysis_note))
        for check, passed in report.learning_checks.items():
            console.print(Text(f"{'Pass' if passed else 'Needs work'} / {check.replace('_', ' ')}"))
        console.print(
            Text(
                f"{report.status} · exercise {'passed' if report.passed else 'needs work'} "
                f"· {format_cost(report.known_cost_nanousd)} known cost "
                f"· {report.unknown_cost_runs} unknown-cost runs\nAttempt: {report.id}"
            )
        )
        console.print(Text(report.grading_rule))
        if report.feedback:
            if isinstance(error := report.feedback.get("error"), dict):
                console.print(
                    Text(human_error(JevError.from_dict(error), verbose=verbose_errors()))
                )
            else:
                console.print(Text(json.dumps(report.feedback, indent=2)))
    if report.status != "completed":
        raise typer.Exit(4)


@learn_app.command("progress")
@guarded
def progress(json_output: JsonFlag = False) -> None:
    data = workbench().storage.learning_progress()
    emit(data) if json_output else console.print(Text(json.dumps(data, indent=2)))


@learn_app.command("inspect")
@guarded
def inspect_attempt(attempt_id: str, json_output: JsonFlag = False) -> None:
    try:
        data = workbench().storage.attempt(attempt_id)
    except JevError as error:
        if error.code != "attempt_not_found":
            raise
        raise JevError(
            "attempt_not_found",
            "This attempt is unavailable or its ID is ambiguous.",
            "Use a full ID from jevlab learn progress --json. Cleanup may prune attempt details; "
            "completion and best scores remain saved.",
        ) from None
    emit(data) if json_output else console.print(Text(json.dumps(data, indent=2)))


@library_app.callback()
@guarded
def library(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    if ctx.invoked_subcommand:
        return
    if json_output:
        emit(
            [
                {"id": p.id, "title": p.title, "when_to_use": p.when_to_use, "cases": len(p.cases)}
                for p in patterns()
            ]
        )
    elif sys.stdin.isatty():
        launch(workbench(), "library")
    else:
        for item in patterns():
            console.print(Text(f"{item.id}  {item.when_to_use}"))


@library_app.command("show")
@guarded
def show_pattern(pattern_id: str, json_output: JsonFlag = False) -> None:
    item = pattern(pattern_id)
    emit(item.model_dump(mode="json")) if json_output else console.print(
        Text(
            f"{item.title}\n{item.when_to_use}\n{item.notes}\n\n{dump_template(item.template)}"
            + "\nLabeled cases:\n"
            + "\n".join(x.model_dump_json() for x in item.cases)
        )
    )


@library_app.command("fork")
@guarded
def fork_pattern(pattern_id: str, name: str, json_output: JsonFlag = False) -> None:
    wb = workbench()
    path = wb.templates.save(fork_template(pattern(pattern_id).template, name))
    emit({"path": str(path), "template": name}) if json_output else typer.echo(f"Saved {path}")


@library_app.command("export-data")
@guarded
def export_pattern_dataset(
    pattern_id: str,
    output: Path,
    json_output: JsonFlag = False,
) -> None:
    path = export_dataset(pattern_id, output)
    data = {"pattern": pattern_id, "path": str(path), "format": "jsonl"}
    emit(data) if json_output else typer.echo(f"Exported labeled cases to {path}")


@coach_app.callback()
@guarded
def coach(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    if ctx.invoked_subcommand:
        return
    wb = workbench()
    if json_output:
        emit(
            {
                "provider": wb.settings.coach_provider,
                "model": wb.settings.coach_model,
                "note": "Advice only. No coach call was made.",
            }
        )
    else:
        launch(wb, "coach")


def display_advice(result: CoachResult, machine: bool) -> None:
    if machine:
        emit(result.model_dump(mode="json"))
        return
    advice = result.advice
    console.print(
        Text(
            f"{result.disclaimer}\n\n{advice.summary}\n\n"
            + "\n".join(advice.observations)
            + f"\n\nTest next: {advice.next_experiment}"
        )
    )
    if advice.template:
        console.print(Text(dump_template(advice.template)))
    console.print(
        Text(
            f"{result.provider} / {result.model} · {result.latency_ms:,} ms · "
            f"{result.input_tokens} input / {result.output_tokens} output tokens · cost unknown"
        )
    )


@coach_app.command("design")
@guarded
def design_from_intent(
    intent: str,
    name: str = "proposed-design",
    output: Path | None = None,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    wb.templates.path(name)
    if output and output.exists():
        raise JevError("already_exists", "The proposal file already exists.", "Choose a new path.")
    confirm_spend(
        estimate_coach(
            wb.settings,
            {
                "intent": intent,
                "name": name,
                "schema": Template.model_json_schema(),
                "model": wb.settings.model,
            },
        ),
        machine=json_output,
    )
    result = asyncio.run(Coach(wb.settings).design(intent, name))
    if output and result.advice.template:
        atomic_write(output, dump_template(result.advice.template))
    display_advice(result, json_output)


@coach_app.command("critique")
@guarded
def critique_template(template: str, json_output: JsonFlag = False) -> None:
    wb = workbench()
    design = wb.templates.load(template)
    confirm_spend(
        estimate_coach(wb.settings, {"template": design.model_dump(mode="json")}),
        machine=json_output,
    )
    result = asyncio.run(Coach(wb.settings).critique(design))
    display_advice(result, json_output)


@coach_app.command("explain")
@guarded
def explain_result(run_id: str, json_output: JsonFlag = False) -> None:
    wb = workbench()
    run = wb.storage.get(run_id)
    design = wb.storage.template_for(run)
    if run.status == "succeeded":
        confirm_spend(
            estimate_coach(
                wb.settings,
                {
                    "template": design.model_dump(mode="json"),
                    "state": run.request["state"],
                    "response": run.response,
                },
            ),
            machine=json_output,
        )
    result = asyncio.run(Coach(wb.settings).explain(run, design))
    display_advice(result, json_output)
