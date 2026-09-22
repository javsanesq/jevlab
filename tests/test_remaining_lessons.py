"""The complete curriculum uses actual SDK response shapes with offline transports."""

import json
from typing import cast

import httpx2
import pytest
from typesafe_sdk import Choice, JSONContent, Noul, Score

from jevlab.core.client import Evaluation, SDKClient
from jevlab.core.content import LabeledCase, lesson, lessons, pattern, starter
from jevlab.core.evaluation import threshold_stats
from jevlab.core.learning import GradeReport, Learning, exercise_plan
from jevlab.core.models import ConfidenceGate, NoulGate, Settings, Template
from jevlab.core.service import Workbench
from jevlab.core.templates import dump_template, parse_template


class LessonEvaluator:
    """Controlled uncertainty, preserving the real official SDK validation path."""

    def __init__(
        self, cases: list[LabeledCase], *, confident_miss: bool = False, fail_at: int = -1
    ) -> None:
        self.cases = cases
        self.confident_miss = confident_miss
        self.fail_at = fail_at
        self.requests: list[dict[str, object]] = []

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        index = len(self.requests)
        labels = self.cases[index].expected
        confidence = [0.45, 0.70, 0.83, 0.92, 0.99][index]
        strength = [0.55, 0.60, 0.75, 0.95, 0.98][index]
        answers: dict[str, object] = {}
        for name, question in template.questions.items():
            expected = labels[name]
            if isinstance(question, Choice):
                selected = str(expected)
                if self.confident_miss and index == len(self.cases) - 1:
                    selected = next(option for option in question.criteria if option != expected)
                answers[name] = {
                    "type": "choice",
                    "choice": selected,
                    "confidence": confidence,
                    "probabilities": {
                        option: 0.98 if option == selected else 0.02 / (len(question.criteria) - 1)
                        for option in question.criteria
                    },
                }
            elif isinstance(question, Noul):
                answers[name] = {
                    "type": "noul",
                    "noul": strength if expected else 1 - strength,
                }
            else:
                answers[name] = {
                    "type": "score",
                    "score": float(expected),
                    "confidence": confidence,
                    "legend": {str(level): label for level, label in enumerate(question.criteria)},
                    "probabilities": {
                        str(level): float(level == expected)
                        for level in range(len(question.criteria))
                    },
                }

        def respond(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(json.loads(request.content))
            return httpx2.Response(
                401 if index == self.fail_at else 200,
                json={
                    "model": "jev-1.13.0",
                    "answers": answers,
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                },
            )

        return await SDKClient(
            "offline-key", Settings(max_retries=0), transport=httpx2.MockTransport(respond)
        ).evaluate(template, state)


def test_all_ten_lessons_have_gradeable_drafts_and_preserve_original_ids(wb: Workbench) -> None:
    track = lessons()
    assert len(track) == 10
    assert [item.id for item in track[:5]] == [
        "01-system-one",
        "02-primitives",
        "03-state",
        "04-questions",
        "05-multiple-questions",
    ]
    assert all(item.version == 1 for item in track)
    for index, item in enumerate(track, start=1):
        assert lesson(str(index)) == item
        template = starter(item, f"lesson-{index}", wb.settings.model)
        assert parse_template(dump_template(template)) == template
        assert set(item.required_thresholds).issubset(item.question_types)
        assert set(item.required_fields).issubset(item.default_fields)
        cases = pattern(item.pattern).cases
        assert exercise_plan(wb, item, template).calls == len(cases)
        assert item.concept and item.example and item.exercise and item.tips
        for case in cases:
            assert set(item.required_fields).issubset(case.state)
            for name, question in template.questions.items():
                label = case.expected[name]
                if isinstance(question, Choice):
                    assert label in question.criteria
                elif isinstance(question, Noul):
                    assert type(label) is bool
                elif isinstance(question, Score):
                    assert type(label) is int and 0 <= label < len(question.criteria)


@pytest.mark.parametrize("number", [6, 8, 9, 10])
async def test_remaining_lessons_grade_real_sdk_answers(wb: Workbench, number: int) -> None:
    item = lesson(str(number))
    template = starter(item, f"exercise-{number}", wb.settings.model)
    evaluator = LessonEvaluator(pattern(item.pattern).cases)
    report = await Learning(wb).grade(item, template, evaluator=evaluator)
    assert report.status == "completed" and report.passed and report.fraction_correct == 1
    assert len(evaluator.requests) == len(pattern(item.pattern).cases)
    assert all(
        set(cast(dict[str, object], request["state"])) == set(item.default_fields)
        for request in evaluator.requests
    )
    assert all("expected" not in request for request in evaluator.requests)
    saved = GradeReport.model_validate(wb.storage.attempt(report.id))
    assert saved == report


async def test_confidence_lesson_exposes_real_miss_and_separate_reliability(wb: Workbench) -> None:
    item = lesson("6")
    template = starter(item, "confidence-practice", wb.settings.model)
    evaluator = LessonEvaluator(pattern(item.pattern).cases, confident_miss=True)
    report = await Learning(wb).grade(item, template, evaluator=evaluator)
    assert report.evaluation is not None
    route = report.evaluation.per_question["route"]
    assert route.accuracy == 0.8 and len(route.worst_misses) == 1
    assert route.worst_misses[0].confidence == 0.99
    assert route.worst_misses[0].probability == 0.98
    assert sum(bucket.count for bucket in route.calibration) == 5
    assert sum(bucket.count for bucket in route.confidence_calibration) == 5
    refund = report.evaluation.per_question["refund_requested"]
    assert refund.confidence_calibration == []
    assert all(observation.confidence is None for observation in refund.observations)
    assert "synthetic practice" in report.analysis_note
    assert "not deployment calibration" in report.analysis_note
    assert len(evaluator.requests) == 5


async def test_threshold_lesson_requires_gates_but_never_changes_answer_grade(
    wb: Workbench,
) -> None:
    item = lesson("7")
    template = starter(item, "routing-practice", wb.settings.model)
    cases = pattern(item.pattern).cases
    without = await Learning(wb).grade(item, template, evaluator=LessonEvaluator(cases))
    assert without.fraction_correct == 1 and not without.passed
    assert without.learning_checks["required_thresholds"] is False
    assert without.routing["route"].coverage == 0
    assert without.routing["route"].accuracy is None
    assert "threshold" in " ".join(without.plan.design_feedback)

    template.thresholds = {
        "route": ConfidenceGate(automate_at_or_above=0),
        "refund_requested": NoulGate(no_at_or_below=0.49, yes_at_or_above=0.51),
    }
    permissive = await Learning(wb).grade(item, template, evaluator=LessonEvaluator(cases))
    assert permissive.passed and permissive.routing["route"].coverage == 1
    assert permissive.routing["refund_requested"].coverage == 1
    assert permissive.learning_checks["required_thresholds"] is True

    template.thresholds = {
        "route": ConfidenceGate(automate_at_or_above=0.90),
        "refund_requested": NoulGate(no_at_or_below=0.10, yes_at_or_above=0.90),
    }
    evaluator = LessonEvaluator(cases)
    selective = await Learning(wb).grade(item, template, evaluator=evaluator)
    assert selective.passed and selective.fraction_correct == permissive.fraction_correct
    for name in item.question_types:
        assert selective.routing[name].coverage == 0.4
        assert selective.routing[name].accuracy == 1
        assert selective.routing[name].review == 3
        assert len(selective.routing_curves[name]) == 21
    assert selective.evaluation is not None
    # New policy estimates are computed from saved observations, without another call.
    stats = threshold_stats(
        selective.evaluation.per_question["route"], ConfidenceGate(automate_at_or_above=1)
    )
    assert stats.automated == 0 and stats.accuracy is None
    assert len(evaluator.requests) == 5


async def test_old_grade_reports_still_parse_with_default_diagnostics(wb: Workbench) -> None:
    item = lesson("1")
    template = starter(item, "old-exercise", wb.settings.model)
    report = await Learning(wb).grade(
        item, template, evaluator=LessonEvaluator(pattern(item.pattern).cases)
    )
    old = report.model_dump(
        exclude={"evaluation", "routing", "routing_curves", "learning_checks", "analysis_note"}
    )
    restored = GradeReport.model_validate(old)
    assert restored.passed and restored.evaluation is None
    assert restored.routing == {} and restored.learning_checks == {}


async def test_routing_lesson_keeps_failures_and_unrun_cases_in_coverage(wb: Workbench) -> None:
    item = lesson("7")
    template = starter(item, "partial-routing", wb.settings.model)
    template.thresholds = {
        "route": ConfidenceGate(automate_at_or_above=0),
        "refund_requested": NoulGate(no_at_or_below=0.49, yes_at_or_above=0.51),
    }
    evaluator = LessonEvaluator(pattern(item.pattern).cases, fail_at=1)
    report = await Learning(wb).grade(item, template, evaluator=evaluator)
    assert report.status == "failed" and not report.passed
    assert report.fraction_correct == 0.2
    assert report.evaluation is not None
    assert report.evaluation.failed_runs == 1 and report.evaluation.missing_runs == 3
    for name in item.question_types:
        metrics = report.evaluation.per_question[name]
        assert metrics.total == 5 and metrics.answered == 1 and metrics.failed == 4
        assert sum(bucket.count for bucket in metrics.calibration) == 1
        assert report.routing[name].coverage == 0.2 and report.routing[name].review == 4
    assert len(evaluator.requests) == 2


def test_groundedness_paraphrase_preserves_upper_bound() -> None:
    item = pattern("groundedness")
    assert "up to five" in str(item.cases[0].state["claim"])
    assert item.cases[0].expected["grounded"] is True
    assert "not established" in item.cases[1].note
