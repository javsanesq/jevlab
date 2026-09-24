"""Offline evaluation evidence: baseline, paired comparison, and holdout verification."""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from jevlab.cli.common import JsonFlag, console, emit, guarded, workbench
from jevlab.core.errors import JevError
from jevlab.core.files import read_text
from jevlab.core.jobs import BatchService
from jevlab.core.models import StrictModel
from jevlab.core.regression import (
    CheckReport,
    FrozenPolicy,
    RegressionReport,
    VerificationReport,
    compare_snapshots,
    read_snapshot,
    snapshot,
    verify_policy,
    write_artifact,
)
from jevlab.core.templates import revision_hash

Output = Annotated[Path, typer.Option(help="New JSON evidence file; existing files are preserved.")]
Probability = Annotated[float, typer.Option(min=0, max=1)]


QUALITY_FAILURE = JevError(
    "quality_gate_failed",
    "The saved evaluation did not meet the requested quality limits.",
    "Inspect the failed checks and changed cases; revise the design before accepting it.",
    5,
)


def emit_quality(report: StrictModel, passed: bool) -> None:
    envelope: dict[str, object] = {
        "schema_version": 1,
        "ok": passed,
        "data": report.model_dump(mode="json"),
    }
    if not passed:
        envelope["error"] = QUALITY_FAILURE.as_dict()
    typer.echo(json.dumps(envelope))


def check_result(report: CheckReport, machine: bool) -> None:
    """Print a CI verdict; exit 5 when a quality limit fails."""
    if machine:
        emit_quality(report, report.passed)
    else:
        console.print(Text("PASS" if report.passed else "FAIL", style="bold"))
        if report.comparison is not None:
            display_regression(report.comparison)
        for failure in report.failures:
            console.print(Text(failure))
        console.print(Text(f"Job {report.job_id}. {report.note}", style="dim"))
    if not report.passed:
        raise typer.Exit(5)


def display_regression(report: RegressionReport) -> None:
    table = Table("Question", "Before", "After", "Improved", "Regressed", box=None)
    for name, item in report.per_question.items():
        table.add_row(
            Text(name),
            *[
                Text(value, justify="right")
                for value in (
                    f"{item.baseline_accuracy:.2%}",
                    f"{item.candidate_accuracy:.2%}",
                    str(item.improved),
                    str(item.regressed),
                )
            ],
        )
    console.print(table)
    changed = Table("Case", "Question", "Change", "Before → after", box=None)
    for case in report.changes[:20]:
        changed.add_row(
            Text(case.case_id),
            Text(case.question),
            case.change,
            Text(
                f"{case.baseline.predicted if case.baseline else 'no answer'} → "
                f"{case.candidate.predicted if case.candidate else 'no answer'}"
            ),
        )
    if report.changes:
        console.print(changed)
        console.print(
            Text(f"{len(report.changes)} changed case/question pairs; --json includes all.")
        )


def quality_result(report: RegressionReport | VerificationReport, machine: bool) -> None:
    if machine:
        emit_quality(report, report.passed)
    else:
        console.print(Text("PASS" if report.passed else "FAIL", style="bold"))
        if isinstance(report, RegressionReport):
            display_regression(report)
        else:
            table = Table("Question", "Automated / total", "Accuracy", "Coverage", box=None)
            for name, stats in report.per_question.items():
                table.add_row(
                    Text(name),
                    *[
                        Text(value, justify="right")
                        for value in (
                            f"{stats.automated}/{stats.total}",
                            f"{stats.accuracy:.2%}" if stats.accuracy is not None else "—",
                            f"{stats.coverage:.2%}",
                        )
                    ],
                )
            console.print(table)
        for failure in report.failures:
            console.print(Text(failure))
        console.print(Text(report.note))
    if not report.passed:
        raise typer.Exit(5)


def register(app: typer.Typer) -> None:
    app.command("baseline", help="Save portable evidence from an evaluation; no API call.")(
        baseline
    )
    app.command("compare", help="Compare paired evaluations and enforce quality limits offline.")(
        compare
    )
    app.command("freeze", help="Freeze tuned thresholds before evaluating a separate holdout.")(
        freeze
    )
    app.command("verify", help="Check frozen thresholds against a later, disjoint evaluation.")(
        verify
    )


@guarded
def baseline(job_id: str, output: Output, json_output: JsonFlag = False) -> None:
    evidence = snapshot(BatchService(workbench()), job_id)
    write_artifact(output, evidence)
    data = {"path": str(output), "job_id": evidence.job_id, "template_hash": evidence.template_hash}
    emit(data) if json_output else console.print(Text(f"Baseline saved to {output}. No API call."))


@guarded
def compare(
    baseline_file: Path,
    candidate: Annotated[
        str, typer.Argument(help="Saved eval job ID or another baseline JSON file.")
    ],
    max_regressions: Annotated[int, typer.Option(min=0)] = 0,
    min_accuracy: Annotated[float | None, typer.Option(min=0, max=1)] = None,
    output: Annotated[
        Path | None, typer.Option(help="Optionally save the complete comparison.")
    ] = None,
    json_output: JsonFlag = False,
) -> None:
    before = read_snapshot(baseline_file)
    after = (
        read_snapshot(Path(candidate))
        if candidate.endswith(".json") or "/" in candidate
        else snapshot(BatchService(workbench()), candidate)
    )
    report = compare_snapshots(
        before, after, max_regressions=max_regressions, min_accuracy=min_accuracy
    )
    if output:
        write_artifact(output, report)
    quality_result(report, json_output)


@guarded
def freeze(
    tuning_job: str,
    template: Annotated[
        str, typer.Option(help="Matching named template or project YAML with selected thresholds.")
    ],
    output: Output,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    evidence = snapshot(BatchService(wb), tuning_job)
    design = wb.templates.load_reference(template)
    policy = FrozenPolicy(template=design, template_hash=revision_hash(design), tuning=evidence)
    write_artifact(output, policy)
    data = {
        "path": str(output),
        "purpose": "tuning",
        "policy_hash": policy.template_hash,
        "next": "Evaluate this unchanged template on separate cases, then use jevlab eval verify.",
    }
    emit(data) if json_output else console.print(
        Text(
            f"Policy frozen to {output}. This records tuning, not held-out verification.\n"
            f"{data['next']}"
        )
    )


@guarded
def verify(
    policy_file: Path,
    job_id: str,
    min_accuracy: Probability = 0.95,
    min_coverage: Probability = 0.0,
    output: Annotated[
        Path | None, typer.Option(help="Optionally save the verification evidence.")
    ] = None,
    json_output: JsonFlag = False,
) -> None:
    policy = FrozenPolicy.model_validate_json(
        read_text(policy_file.expanduser(), limit=100_000_000)
    )
    evidence = snapshot(BatchService(workbench()), job_id)
    report = verify_policy(policy, evidence, min_accuracy=min_accuracy, min_coverage=min_coverage)
    if output:
        write_artifact(output, report)
    quality_result(report, json_output)
