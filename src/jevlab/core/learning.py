"""Small lesson exercises graded by actual Jev answers, never by a coach."""

import asyncio
import json
import math
from collections.abc import Callable
from typing import Literal, cast
from uuid import uuid4

from pydantic import Field
from typesafe_sdk import Choice, ChoiceAnswer, JSONContent, NoulAnswer, Score, SystemOneResponse

from jevlab.core.client import Evaluator
from jevlab.core.content import LabeledCase, Lesson, pattern
from jevlab.core.datasets import DatasetRow
from jevlab.core.errors import JevError
from jevlab.core.evaluation import (
    EvalReport,
    ThresholdStats,
    evaluate_runs,
    threshold_curve,
    threshold_stats,
)
from jevlab.core.models import Run, StrictModel, Template
from jevlab.core.pricing import over_budget, price
from jevlab.core.service import Workbench
from jevlab.core.storage import now
from jevlab.core.templates import context_estimate, dump_template, parse_template


class ExercisePlan(StrictModel):
    lesson_id: str
    calls: int
    fields: list[str]
    available_fields: list[str]
    estimated_input_tokens: int
    estimated_cost_nanousd: int | None
    requires_confirmation: bool
    estimate_note: str = (
        "Characters/4 approximation; server overhead and retries can increase cost."
    )
    design_feedback: list[str]


class QuestionGrade(StrictModel):
    correct: int = 0
    total: int
    accuracy: float = 0
    mean_absolute_error: float | None = None


class CaseGrade(StrictModel):
    id: str
    run_id: str | None = None
    state: dict[str, object]
    expected: dict[str, str | bool | int]
    predicted: dict[str, str | bool | int | float] = Field(default_factory=dict)
    correct: dict[str, bool] = Field(default_factory=dict)
    note: str
    error: dict[str, object] | None = None


class GradeReport(StrictModel):
    id: str
    lesson_id: str
    content_version: int
    template_name: str
    started_at: str
    finished_at: str | None = None
    status: Literal["pending", "completed", "failed", "interrupted"] = "pending"
    fields: list[str]
    plan: ExercisePlan
    cases: list[CaseGrade] = Field(default_factory=list)
    per_question: dict[str, QuestionGrade]
    fraction_correct: float = 0
    passed: bool = False
    known_cost_nanousd: int = 0
    unknown_cost_runs: int = 0
    latency_ms: int = 0
    feedback: dict[str, object] | None = None
    evaluation: EvalReport | None = None
    routing: dict[str, ThresholdStats] = Field(default_factory=dict)
    routing_curves: dict[str, list[ThresholdStats]] = Field(default_factory=dict)
    learning_checks: dict[str, bool] = Field(default_factory=dict)
    analysis_note: str = ""
    grading_rule: str = (
        "Choice: exact label. Noul: P(yes) >= 0.50. Score: nearest level, halves round up; "
        "also report MAE over returned Scores. All labeled answers count toward accuracy, "
        "including failed/unrun cases. "
        "These tiny synthetic exercises are practice, not held-out performance evidence."
    )


def project(case: LabeledCase, fields: list[str]) -> dict[str, object]:
    return {key: case.state[key] for key in fields if key in case.state}


def exercise_plan(
    wb: Workbench, item: Lesson, template: Template, fields: list[str] | None = None
) -> ExercisePlan:
    cases = pattern(item.pattern).cases
    chosen = item.default_fields if fields is None else fields
    available = sorted({key for case in cases for key in case.state})
    if not chosen or len(chosen) != len(set(chosen)) or set(chosen) - set(available):
        raise JevError(
            "invalid_fields",
            "Choose unique, available state fields.",
            "Available: " + ", ".join(available),
        )
    for name, kind in item.question_types.items():
        question = template.questions.get(name)
        if question is None or question.type != kind:
            raise JevError(
                "exercise_shape",
                f"The exercise requires {name}: {kind}.",
                "Keep required question IDs/types; edit instructions and criteria.",
            )
        reference = pattern(item.pattern).template.questions[name]
        if isinstance(question, Score) and isinstance(reference, Score):
            if len(question.criteria) != len(reference.criteria):
                raise JevError(
                    "exercise_shape",
                    "Preserve the exercise's number of Score levels.",
                    "Edit the level descriptions without changing the grading scale.",
                )
        for case in cases:
            expected = case.expected[name]
            if isinstance(question, Choice) and expected not in question.criteria:
                raise JevError(
                    "exercise_labels",
                    "A required Choice label is missing.",
                    "Preserve the exercise's option labels so grading stays comparable.",
                )
            if isinstance(question, Score) and (
                not isinstance(expected, int) or not 0 <= expected < len(question.criteria)
            ):
                raise JevError(
                    "exercise_labels",
                    "Score rubric omits a labeled level.",
                    "Keep the exercise's level order and number of levels.",
                )
    tokens = sum(
        cast(int, context_estimate(template, project(case, chosen))["estimated_total_tokens"])
        for case in cases
    )
    cost, _ = price(template.model, tokens)
    feedback = []
    if missing := set(item.required_fields) - set(chosen):
        feedback.append("Missing evidence fields: " + ", ".join(sorted(missing)))
    if extra := set(chosen) - set(item.required_fields):
        feedback.append("Test removing irrelevant fields: " + ", ".join(sorted(extra)))
    if missing_gates := set(item.required_thresholds) - set(template.thresholds):
        feedback.append(
            "Add threshold gates for this routing exercise: " + ", ".join(sorted(missing_gates))
        )
    return ExercisePlan(
        lesson_id=item.id,
        calls=len(cases),
        fields=chosen,
        available_fields=available,
        estimated_input_tokens=tokens,
        estimated_cost_nanousd=cost,
        requires_confirmation=over_budget(cost, wb.settings.confirm_cost_usd),
        design_feedback=feedback,
    )


class Learning:
    def __init__(self, wb: Workbench) -> None:
        self.wb = wb

    async def grade(
        self,
        item: Lesson,
        template: Template,
        fields: list[str] | None = None,
        *,
        authorize_cost: bool = False,
        evaluator: Evaluator | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> GradeReport:
        template = parse_template(dump_template(template))
        plan = exercise_plan(self.wb, item, template, fields)
        if plan.requires_confirmation and not authorize_cost:
            raise JevError(
                "cost_confirmation",
                "Exercise cost needs confirmation.",
                "Review the estimate and explicitly authorize this run.",
            )
        cases = pattern(item.pattern).cases
        report = GradeReport(
            id=str(uuid4()),
            lesson_id=item.id,
            content_version=item.version,
            template_name=template.name,
            started_at=now(),
            fields=plan.fields,
            plan=plan,
            per_question={name: QuestionGrade(total=len(cases)) for name in item.question_types},
        )
        absolute_errors: dict[str, list[float]] = {name: [] for name in item.question_types}
        recorded_runs: dict[str, Run] = {}
        self.wb.storage.save_attempt(report.model_dump())
        try:
            for index, case in enumerate(cases):
                row = CaseGrade(
                    id=case.id,
                    state=project(case, plan.fields),
                    expected={key: case.expected[key] for key in item.question_types},
                    note=case.note,
                )
                report.cases.append(row)
                try:
                    run = await self.wb.run(
                        template, cast(JSONContent, row.state), evaluator=evaluator
                    )
                    row.run_id = run.id
                    recorded_runs[case.id] = run
                    report.known_cost_nanousd += run.cost_nanousd or 0
                    report.unknown_cost_runs += run.cost_nanousd is None
                    report.latency_ms += run.latency_ms or 0
                    response = SystemOneResponse.model_validate_json(json.dumps(run.response))
                    for name, expected in row.expected.items():
                        answer = response.answers[name]
                        if isinstance(answer, ChoiceAnswer):
                            predicted: str | bool | int | float = answer.choice
                            correct = predicted == expected
                        elif isinstance(answer, NoulAnswer):
                            predicted = answer.noul >= 0.5
                            correct = predicted == expected
                        else:
                            predicted = answer.score
                            correct = math.floor(answer.score + 0.5) == expected
                            absolute_errors[name].append(abs(answer.score - cast(int, expected)))
                        row.predicted[name], row.correct[name] = predicted, correct
                        report.per_question[name].correct += int(correct)
                except JevError as error:
                    row.error, row.run_id = error.as_dict(), error.run_id
                    if error.run_id:
                        failed = self.wb.storage.get(error.run_id)
                        recorded_runs[case.id] = failed
                        report.known_cost_nanousd += failed.cost_nanousd or 0
                        report.unknown_cost_runs += failed.cost_nanousd is None
                        report.latency_ms += failed.latency_ms or 0
                    report.status = "failed"
                    break
                self.wb.storage.save_attempt(report.model_dump())
                if progress:
                    progress(index + 1, len(cases))
            if report.status == "pending":
                report.status = "completed"
        except asyncio.CancelledError:
            report.status = "interrupted"
            report.unknown_cost_runs += 1
            raise
        finally:
            report.finished_at = now()
            for name, grade in report.per_question.items():
                grade.accuracy = grade.correct / grade.total
                errors = absolute_errors[name]
                grade.mean_absolute_error = sum(errors) / len(errors) if errors else None
            report.fraction_correct = sum(g.correct for g in report.per_question.values()) / sum(
                g.total for g in report.per_question.values()
            )
            report.passed = (
                report.status == "completed"
                and report.fraction_correct >= item.pass_fraction
                and set(item.required_fields).issubset(plan.fields)
                and set(item.required_thresholds).issubset(template.thresholds)
            )
            report.learning_checks = {
                "required_state_fields": set(item.required_fields).issubset(plan.fields),
                "required_thresholds": set(item.required_thresholds).issubset(template.thresholds),
            }
            if item.analysis != "basic":
                report.evaluation = evaluate_runs(
                    template,
                    [
                        (
                            DatasetRow(
                                id=case.id,
                                index=index,
                                state=cast(JSONContent, project(case, plan.fields)),
                                expected={key: case.expected[key] for key in item.question_types},
                            ),
                            recorded_runs.get(case.id),
                        )
                        for index, case in enumerate(cases)
                    ],
                )
                # Learners may add ungraded questions; diagnostics cover only lesson labels.
                report.evaluation.per_question = {
                    name: report.evaluation.per_question[name] for name in item.question_types
                }
                for name, metrics in report.evaluation.per_question.items():
                    report.routing[name] = threshold_stats(metrics, template.thresholds.get(name))
                    if item.analysis == "routing":
                        report.routing_curves[name] = threshold_curve(metrics)
                experiment = (
                    "Compare the saved gate with the threshold curve without another API call. "
                    if item.analysis == "routing"
                    else "Compare probability reliability with the separate confidence bins. "
                )
                report.analysis_note = (
                    f"Only {len(cases)} synthetic practice cases. Reliability bins and routing "
                    "trade-offs describe these returned answers, not deployment calibration. "
                    + experiment
                    + "Tune using representative validation data and test on held-out cases. "
                    "Routing thresholds do not change the underlying judgment accuracy."
                )
            self.wb.storage.save_attempt(report.model_dump(), final=True)
        return report
