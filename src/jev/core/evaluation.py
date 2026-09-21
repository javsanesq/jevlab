"""Offline evaluation and threshold analysis over persisted Jev answers.

Accuracy and coverage use *all labeled rows*, including failures. Calibration,
Brier scores and Score MAE use returned answers only: missing answers are never
fabricated as zero-probability observations. Threshold tuning performs no calls.
"""

import json
import math
from typing import Literal

from pydantic import Field
from typesafe_sdk import Choice, ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse

from jev.core.client import verify_response
from jev.core.datasets import DatasetRow
from jev.core.errors import JevError
from jev.core.models import ConfidenceGate, Gate, NoulGate, Run, StrictModel, Template


class CalibrationBin(StrictModel):
    lower: float
    upper: float
    count: int = 0
    mean_probability: float | None = None
    observed_accuracy: float | None = None


class Observation(StrictModel):
    case_id: str
    run_id: str
    predicted: str | bool | int
    expected: str | bool | int
    value: str | bool | int | float
    probability: float
    confidence: float | None = None
    correct: bool


class QuestionMetrics(StrictModel):
    primitive: Literal["choice", "score", "noul"]
    total: int = 0
    answered: int = 0
    failed: int = 0
    correct: int = 0
    accuracy: float = 0
    answered_accuracy: float | None = None
    calibration: list[CalibrationBin] = Field(default_factory=list)
    confidence_calibration: list[CalibrationBin] = Field(default_factory=list)
    brier_score: float | None = None
    mean_absolute_error: float | None = None
    confusion_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    worst_misses: list[Observation] = Field(default_factory=list)


class EvalReport(StrictModel):
    template_name: str
    rows: int
    resolved_models: dict[str, int] = Field(default_factory=dict)
    succeeded_runs: int = 0
    failed_runs: int = 0
    missing_runs: int = 0
    known_cost_nanousd: int = 0
    unknown_cost_runs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_total_ms: int = 0
    latency_mean_ms: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    per_question: dict[str, QuestionMetrics]
    methodology: str = (
        "Accuracy/coverage denominator: all labeled rows, including failed or missing runs. "
        "Choice: exact label. Score: nearest level, halves round up; MAE uses continuous value. "
        "Noul: yes at P(yes) >= 0.50. Reliability uses the probability of that predicted class; "
        "confidence reliability is separate. Empty bins have no accuracy. "
        "Brier: sum of squared class errors for Choice/Score (0–2), binary squared error "
        "for Noul (0–1). Brier, reliability and MAE exclude missing answers. "
        "Worst misses lists at most 100 per question. Latency includes all recorded attempts; "
        "percentiles linearly interpolate ordered samples. Threshold metrics describe this "
        "dataset, not held-out guarantees."
    )


class ThresholdStats(StrictModel):
    gate: Gate | None
    total: int
    automated: int
    review: int
    correct: int
    coverage: float
    accuracy: float | None


def _calibration(
    observations: list[Observation], *, confidence: bool = False
) -> list[CalibrationBin]:
    bins = [CalibrationBin(lower=i / 10, upper=(i + 1) / 10) for i in range(10)]
    probabilities = [0.0] * 10
    correct = [0] * 10
    for observation in observations:
        value = observation.confidence if confidence else observation.probability
        if value is None:
            continue
        index = min(9, int(value * 10))
        bins[index].count += 1
        probabilities[index] += value
        correct[index] += observation.correct
    for index, item in enumerate(bins):
        if item.count:
            item.mean_probability = probabilities[index] / item.count
            item.observed_accuracy = correct[index] / item.count
    return bins


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate_runs(template: Template, rows: list[tuple[DatasetRow, Run | None]]) -> EvalReport:
    """Analyze completed runs against validated labels; preserve failures in totals.

    A stored successful response that no longer validates raises an actionable
    error instead of silently contaminating the evaluation. Full observations
    persist in the report so sliders and CLI threshold changes need no API calls.
    """
    metrics = {
        name: QuestionMetrics(
            primitive=question.type,
            confusion_matrix=(
                {label: dict.fromkeys(question.criteria, 0) for label in question.criteria}
                if isinstance(question, Choice)
                else {}
            ),
        )
        for name, question in template.questions.items()
    }
    report = EvalReport(template_name=template.name, rows=len(rows), per_question=metrics)
    errors: dict[str, list[float]] = {name: [] for name in metrics}
    briers: dict[str, list[float]] = {name: [] for name in metrics}
    latencies: list[int] = []
    for row, run in rows:
        for name in row.expected:
            if name not in metrics:
                raise JevError(
                    "invalid_labels",
                    "Labels reference an unknown question.",
                    "Reimport the dataset.",
                )
            metrics[name].total += 1
        if run is None:
            report.missing_runs += 1
            continue
        report.known_cost_nanousd += run.cost_nanousd or 0
        report.unknown_cost_runs += run.cost_nanousd is None
        report.input_tokens += run.input_tokens or 0
        report.output_tokens += run.output_tokens or 0
        if run.resolved_model:
            report.resolved_models[run.resolved_model] = (
                report.resolved_models.get(run.resolved_model, 0) + 1
            )
        if run.latency_ms is not None:
            latencies.append(run.latency_ms)
        if run.status != "succeeded":
            if run.status == "pending":
                report.missing_runs += 1
            else:
                report.failed_runs += 1
            continue
        try:
            response = SystemOneResponse.model_validate_json(json.dumps(run.response))
            verify_response(template, response)
        except (ValueError, JevError, RecursionError):
            raise JevError(
                "invalid_evaluation_response",
                f"Saved run {run.id} has an invalid response for this template.",
                "Inspect the run and its template revision before evaluating it.",
            ) from None
        report.succeeded_runs += 1
        for name, expected in row.expected.items():
            answer = response.answers[name]
            confidence: float | None = None
            predicted: str | bool | int
            value: str | bool | int | float
            if isinstance(answer, ChoiceAnswer):
                predicted = value = answer.choice
                probability = answer.probabilities[answer.choice]
                confidence = answer.confidence
                brier = sum(
                    (prob - int(label == expected)) ** 2
                    for label, prob in answer.probabilities.items()
                )
                metrics[name].confusion_matrix[str(expected)][answer.choice] += 1
            elif isinstance(answer, ScoreAnswer):
                value = answer.score
                predicted = math.floor(answer.score + 0.5)
                probability = answer.probabilities[predicted]
                confidence = answer.confidence
                brier = sum(
                    (prob - int(level == expected)) ** 2
                    for level, prob in answer.probabilities.items()
                )
                errors[name].append(abs(answer.score - int(expected)))
            else:
                assert isinstance(answer, NoulAnswer)
                value = answer.noul
                predicted = answer.noul >= 0.5
                probability = answer.noul if predicted else 1 - answer.noul
                brier = (answer.noul - int(expected)) ** 2
            observation = Observation(
                case_id=row.id,
                run_id=run.id,
                predicted=predicted,
                expected=expected,
                value=value,
                probability=probability,
                confidence=confidence,
                correct=predicted == expected,
            )
            metrics[name].observations.append(observation)
            briers[name].append(brier)
    for name, item in metrics.items():
        item.answered = len(item.observations)
        item.failed = item.total - item.answered
        item.correct = sum(observation.correct for observation in item.observations)
        item.accuracy = item.correct / item.total if item.total else 0
        item.answered_accuracy = item.correct / item.answered if item.answered else None
        item.calibration = _calibration(item.observations)
        if item.primitive != "noul":
            item.confidence_calibration = _calibration(item.observations, confidence=True)
        item.brier_score = sum(briers[name]) / len(briers[name]) if briers[name] else None
        item.mean_absolute_error = sum(errors[name]) / len(errors[name]) if errors[name] else None
        item.worst_misses = sorted(
            (observation for observation in item.observations if not observation.correct),
            key=lambda observation: (-observation.probability, observation.case_id),
        )[:100]
    report.latency_total_ms = sum(latencies)
    report.latency_mean_ms = sum(latencies) / len(latencies) if latencies else None
    report.latency_p50_ms = _percentile(latencies, 0.5)
    report.latency_p95_ms = _percentile(latencies, 0.95)
    return report


def threshold_stats(metrics: QuestionMetrics, gate: Gate | None) -> ThresholdStats:
    """Apply the same primitive-specific rules as core.thresholds.route.

    Noul automation predicts the *gate's* yes/no branch. With unusual asymmetric
    boundaries, that branch can differ from the P(yes)>=0.5 evaluation label.
    No gate means all cases go to review; failed cases always go to review.
    """
    if gate is not None and ((metrics.primitive == "noul") != isinstance(gate, NoulGate)):
        raise JevError(
            "invalid_threshold",
            "Threshold type does not match the question primitive.",
            "Use two probability boundaries for Noul and a confidence threshold otherwise.",
        )
    automated = correct = 0
    for observation in metrics.observations:
        if isinstance(gate, ConfidenceGate):
            if (
                observation.confidence is not None
                and observation.confidence >= gate.automate_at_or_above
            ):
                automated += 1
                correct += observation.correct
        elif isinstance(gate, NoulGate):
            value = float(observation.value)
            routed: bool | None = None
            if value <= gate.no_at_or_below:
                routed = False
            elif value >= gate.yes_at_or_above:
                routed = True
            if routed is not None:
                automated += 1
                correct += routed == observation.expected
    return ThresholdStats(
        gate=gate,
        total=metrics.total,
        automated=automated,
        review=metrics.total - automated,
        correct=correct,
        coverage=automated / metrics.total if metrics.total else 0,
        accuracy=correct / automated if automated else None,
    )


def threshold_curve(metrics: QuestionMetrics, steps: int = 21) -> list[ThresholdStats]:
    """Sweep confidence 0..1 or symmetric Noul boundaries around 0.5.

    Noul's first point uses an infinitesimal review band because valid gates
    require no < yes. Use threshold_stats for arbitrary asymmetric boundaries.
    """
    if not 2 <= steps <= 1001:
        raise JevError("invalid_steps", "Threshold curve needs 2–1001 points.", "Use 21 points.")
    gates: list[Gate] = []
    for index in range(steps):
        fraction = index / (steps - 1)
        if metrics.primitive == "noul":
            yes = max(0.500000001, 0.5 + fraction / 2)
            gates.append(NoulGate(no_at_or_below=1 - yes, yes_at_or_above=yes))
        else:
            gates.append(ConfidenceGate(automate_at_or_above=fraction))
    return [threshold_stats(metrics, gate) for gate in gates]
