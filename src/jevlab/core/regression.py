"""Portable evaluation evidence and offline regression/holdout checks.

Artifacts contain designs, labels and answers, but no evaluated states or source paths.
Hashes establish exact provenance, not statistical independence or authenticity.
"""

import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator
from typesafe_sdk import Choice, Score

from jevlab.core.datasets import inspect_dataset, iter_dataset
from jevlab.core.errors import JevError
from jevlab.core.evaluation import EvalReport, Observation, ThresholdStats, threshold_stats
from jevlab.core.files import read_text
from jevlab.core.jobs import BatchService
from jevlab.core.models import StrictModel, Template
from jevlab.core.storage import now
from jevlab.core.templates import revision_hash


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Evidence timestamps need an explicit UTC offset.")
    return result


def _valid_observation(template: Template, name: str, item: Observation) -> bool:
    question = template.questions[name]
    if isinstance(question, Choice):
        return (
            isinstance(item.expected, str)
            and item.expected in question.criteria
            and isinstance(item.predicted, str)
            and item.predicted in question.criteria
            and item.value == item.predicted
            and item.confidence is not None
        )
    if isinstance(question, Score):
        return (
            type(item.expected) is int
            and 0 <= item.expected < len(question.criteria)
            and type(item.predicted) is int
            and isinstance(item.value, (int, float))
            and not isinstance(item.value, bool)
            and 0 <= item.value <= len(question.criteria) - 1
            and item.predicted == math.floor(item.value + 0.5)
            and item.confidence is not None
        )
    return (
        type(item.expected) is bool
        and type(item.predicted) is bool
        and isinstance(item.value, (int, float))
        and not isinstance(item.value, bool)
        and 0 <= item.value <= 1
        and item.predicted == (item.value >= 0.5)
        and item.confidence is None
        and abs(item.probability - (item.value if item.predicted else 1 - item.value)) < 1e-12
    )


Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class EvalSnapshot(StrictModel):
    schema_version: Literal[1] = 1
    kind: Literal["jevlab_evaluation"] = "jevlab_evaluation"
    job_id: str
    started_at: str
    finished_at: str
    status: Literal["completed", "failed", "interrupted"]
    template: Template
    template_hash: str
    dataset_sha256: Sha256
    case_hashes: dict[str, Sha256] = Field(min_length=1, max_length=10_000)
    state_hashes: dict[str, Sha256] = Field(min_length=1, max_length=10_000)
    evaluation: EvalReport

    @model_validator(mode="after")
    def consistent(self) -> Self:
        report = self.evaluation
        if _timestamp(self.finished_at) < _timestamp(self.started_at):
            raise ValueError("Evaluation finish time precedes its start.")
        if (
            min(report.succeeded_runs, report.failed_runs, report.missing_runs) < 0
            or report.succeeded_runs + report.failed_runs + report.missing_runs != report.rows
            or (self.status == "completed" and report.succeeded_runs != report.rows)
        ):
            raise ValueError("Evaluation execution counts do not match its rows and status.")
        if self.template_hash != revision_hash(self.template):
            raise ValueError("Template fingerprint differs from the saved design.")
        if set(self.case_hashes) != set(self.state_hashes) or report.rows != len(self.case_hashes):
            raise ValueError("Case fingerprints do not match the evaluated row count.")
        if set(report.per_question) != set(self.template.questions):
            raise ValueError("Evaluation question IDs differ from the saved design.")
        answered_ids: set[str] | None = None
        for name, metrics in report.per_question.items():
            observations = metrics.observations
            ids = [item.case_id for item in observations]
            if len(ids) != len(set(ids)) or set(ids) - self.case_hashes.keys():
                raise ValueError("Evaluation has duplicate or unknown case IDs.")
            if answered_ids is not None and set(ids) != answered_ids:
                raise ValueError("Questions must describe the same successful cases.")
            answered_ids = set(ids)
            if (
                metrics.primitive != self.template.questions[name].type
                or metrics.total != report.rows
                or metrics.answered != len(observations)
                or metrics.answered != report.succeeded_runs
                or metrics.failed != metrics.total - metrics.answered
                or metrics.correct != sum(item.correct for item in observations)
                or abs(metrics.accuracy - metrics.correct / metrics.total) > 1e-12
            ):
                raise ValueError("Evaluation counts or accuracy differ from saved observations.")
            for item in observations:
                if not _valid_observation(self.template, name, item):
                    raise ValueError(f"Observation for {name} has invalid labels or values.")
                if item.correct != (item.predicted == item.expected):
                    raise ValueError("Correctness differs from the recorded prediction and label.")
                if not 0 <= item.probability <= 1 or (
                    item.confidence is not None and not 0 <= item.confidence <= 1
                ):
                    raise ValueError("Saved probabilities and confidence must be between 0 and 1.")
        return self


def snapshot(service: BatchService, job_id: str) -> EvalSnapshot:
    """Freeze a finished evaluation, checking the source file is still identical."""
    job = service.get(job_id)
    if job.kind != "eval" or job.evaluation is None or job.status in {"pending", "running"}:
        raise JevError(
            "not_evaluated", "This job has no finished evaluation.", "Finish an eval first."
        )
    template = service.template(job.id)
    path = Path(job.dataset.path)
    before = inspect_dataset(path, template, require_labels=True)
    if before.sha256 != job.dataset.sha256:
        raise JevError(
            "dataset_changed",
            "The evaluated dataset has changed.",
            "Restore the original file or run a new evaluation before saving evidence.",
        )
    rows = list(iter_dataset(path, template, require_labels=True))
    after = inspect_dataset(path, template, require_labels=True)
    if after.sha256 != before.sha256:
        raise JevError(
            "dataset_changed",
            "The dataset changed while being read.",
            "Stop editing the file and retry; no API call was made.",
        )
    return EvalSnapshot(
        job_id=job.id,
        started_at=job.started_at,
        finished_at=job.finished_at or job.started_at,
        status=cast(Literal["completed", "failed", "interrupted"], job.status),
        template=template,
        template_hash=job.template_hash,
        dataset_sha256=before.sha256,
        case_hashes={row.id: _hash({"state": row.state, "expected": row.expected}) for row in rows},
        state_hashes={row.id: _hash(row.state) for row in rows},
        evaluation=job.evaluation,
    )


def write_artifact(path: Path, artifact: StrictModel) -> None:
    """Never overwrite evidence (or an unrelated file) implicitly."""
    target = path.expanduser()
    content = artifact.model_dump_json(indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(content)
    except FileExistsError:
        raise JevError(
            "artifact_exists",
            "The output file already exists.",
            "Choose a new filename to preserve the earlier evidence.",
        ) from None


def read_snapshot(path: Path) -> EvalSnapshot:
    return EvalSnapshot.model_validate_json(read_text(path.expanduser(), limit=100_000_000))


class CaseChange(StrictModel):
    question: str
    case_id: str
    change: Literal["improved", "regressed", "changed", "unanswered"]
    baseline: Observation | None
    candidate: Observation | None


class QuestionChange(StrictModel):
    baseline_accuracy: float
    candidate_accuracy: float
    accuracy_delta: float
    improved: int = 0
    regressed: int = 0
    unanswered: int = 0


class RegressionReport(StrictModel):
    baseline_job: str
    candidate_job: str
    baseline_template_hash: str
    candidate_template_hash: str
    dataset_sha256: str
    baseline_models: dict[str, int]
    candidate_models: dict[str, int]
    per_question: dict[str, QuestionChange]
    changes: list[CaseChange]
    passed: bool
    failures: list[str]
    max_regressions: int
    min_accuracy: float | None
    note: str = (
        "Paired cases use identical states and labels. Failed/missing answers count as incorrect. "
        "Question wording and models may differ; you must preserve label meaning. "
        "This is empirical regression evidence, not a statistical guarantee. No API calls."
    )


def compare_snapshots(
    baseline: EvalSnapshot,
    candidate: EvalSnapshot,
    *,
    max_regressions: int = 0,
    min_accuracy: float | None = None,
) -> RegressionReport:
    if max_regressions < 0 or (min_accuracy is not None and not 0 <= min_accuracy <= 1):
        raise ValueError("Quality limits require nonnegative regressions and accuracy from 0 to 1.")
    if baseline.case_hashes != candidate.case_hashes:
        raise JevError(
            "unpaired_dataset",
            "The evaluations do not contain identical case IDs, states and labels.",
            "Evaluate both designs on the same cases; do not compare unrelated datasets.",
        )
    a, b = baseline.template.questions, candidate.template.questions
    compatible = set(a) == set(b)
    for name in a.keys() & b.keys():
        left_question, right_question = a[name], b[name]
        compatible &= left_question.type == right_question.type
        if isinstance(left_question, Choice) and isinstance(right_question, Choice):
            compatible &= set(left_question.criteria) == set(right_question.criteria)
        if isinstance(left_question, Score) and isinstance(right_question, Score):
            compatible &= len(left_question.criteria) == len(right_question.criteria)
    if not compatible:
        raise JevError(
            "incomparable_questions",
            "Question IDs, types or label spaces changed.",
            "Keep matching question IDs, Choice labels and Score level counts "
            "for a paired comparison.",
        )
    changes: list[CaseChange] = []
    questions: dict[str, QuestionChange] = {}
    failures: list[str] = []
    for name in a:
        left, right = (s.evaluation.per_question[name] for s in (baseline, candidate))
        before = {item.case_id: item for item in left.observations}
        after = {item.case_id: item for item in right.observations}
        stats = QuestionChange(
            baseline_accuracy=left.accuracy,
            candidate_accuracy=right.accuracy,
            accuracy_delta=right.accuracy - left.accuracy,
        )
        for case_id in baseline.case_hashes:
            old, new = before.get(case_id), after.get(case_id)
            if old is not None and new is not None and old.expected != new.expected:
                raise JevError(
                    "unpaired_dataset",
                    "Saved observations disagree about a case's label.",
                    "Recreate the evidence from evaluations of the same labeled cases.",
                )
            was_correct, is_correct = bool(old and old.correct), bool(new and new.correct)
            change: Literal["improved", "regressed", "changed", "unanswered"]
            if was_correct and not is_correct:
                change = "regressed"
                stats.regressed += 1
            elif is_correct and not was_correct:
                change = "improved"
                stats.improved += 1
            elif new is None:
                change = "unanswered"
            elif (
                old is None
                or old.value != new.value
                or old.predicted != new.predicted
                or old.confidence != new.confidence
                or old.probability != new.probability
            ):
                change = "changed"
            else:
                continue
            stats.unanswered += new is None
            changes.append(
                CaseChange(
                    question=name,
                    case_id=case_id,
                    change=change,
                    baseline=old,
                    candidate=new,
                )
            )
        if min_accuracy is not None and right.accuracy < min_accuracy:
            failures.append(f"{name}: accuracy {right.accuracy:.2%} is below {min_accuracy:.2%}.")
        questions[name] = stats
    regressions = sum(item.regressed for item in questions.values())
    if regressions > max_regressions:
        failures.append(
            f"{regressions} regressed case/question pairs exceed {max_regressions} allowed."
        )
    for label, evidence in (("Baseline", baseline), ("Candidate", candidate)):
        if (
            evidence.status != "completed"
            or evidence.evaluation.succeeded_runs != evidence.evaluation.rows
        ):
            failures.append(
                f"{label} evaluation has incomplete or failed calls; resolve them first."
            )
    return RegressionReport(
        baseline_job=baseline.job_id,
        candidate_job=candidate.job_id,
        baseline_template_hash=baseline.template_hash,
        candidate_template_hash=candidate.template_hash,
        dataset_sha256=baseline.dataset_sha256,
        baseline_models=baseline.evaluation.resolved_models,
        candidate_models=candidate.evaluation.resolved_models,
        per_question=questions,
        changes=changes,
        passed=not failures,
        failures=failures,
        max_regressions=max_regressions,
        min_accuracy=min_accuracy,
    )


class FrozenPolicy(StrictModel):
    schema_version: Literal[1] = 1
    kind: Literal["jevlab_threshold_policy"] = "jevlab_threshold_policy"
    frozen_at: str = Field(default_factory=now)
    template: Template
    template_hash: str
    tuning: EvalSnapshot

    @model_validator(mode="after")
    def matching_design(self) -> Self:
        if _timestamp(self.frozen_at) < _timestamp(self.tuning.finished_at):
            raise ValueError("Freeze the policy after tuning has finished.")
        if self.template_hash != revision_hash(self.template):
            raise ValueError("Policy fingerprint differs from the saved template.")
        if self.template.model_dump(exclude={"thresholds"}) != self.tuning.template.model_dump(
            exclude={"thresholds"}
        ):
            raise ValueError("Only thresholds may change between tuning and the frozen policy.")
        if not self.template.thresholds:
            raise ValueError("Set at least one threshold before freezing a policy.")
        if self.tuning.status != "completed":
            raise ValueError("Complete the tuning evaluation before freezing a policy.")
        return self


class VerificationReport(StrictModel):
    purpose: Literal["held_out_verification"] = "held_out_verification"
    policy_hash: str
    tuning_job: str
    verification: EvalSnapshot
    per_question: dict[str, ThresholdStats]
    passed: bool
    failures: list[str]
    min_accuracy: float
    min_coverage: float
    note: str = (
        "Thresholds were frozen before this separate evaluation. Exact duplicate states are "
        "rejected; semantic duplicates and independence cannot be established by hashes. "
        "Do not retune against this holdout or treat these sample results as a guarantee. "
        "No API calls."
    )


def verify_policy(
    policy: FrozenPolicy,
    candidate: EvalSnapshot,
    *,
    min_accuracy: float = 0.95,
    min_coverage: float = 0.0,
) -> VerificationReport:
    if not 0 <= min_accuracy <= 1 or not 0 <= min_coverage <= 1:
        raise ValueError("Accuracy and coverage limits must be between 0 and 1.")
    if candidate.template_hash != policy.template_hash:
        raise JevError(
            "policy_changed",
            "The verification did not use the frozen design and thresholds.",
            "Evaluate the unchanged frozen template on the holdout, then verify that job.",
        )
    if _timestamp(candidate.started_at) <= _timestamp(policy.frozen_at):
        raise JevError(
            "verification_order",
            "Verification must follow the tuning evaluation.",
            "Run a new evaluation after choosing and freezing thresholds.",
        )
    tuning_models = policy.tuning.evaluation.resolved_models
    verification_models = candidate.evaluation.resolved_models
    if (
        len(tuning_models) != 1
        or set(tuning_models) != set(verification_models)
        or sum(tuning_models.values()) != policy.tuning.evaluation.succeeded_runs
        or sum(verification_models.values()) != candidate.evaluation.succeeded_runs
    ):
        raise JevError(
            "model_drift",
            "Tuning and verification did not resolve every successful row "
            "to the same single model.",
            "Use a pinned model and repeat tuning and verification; aliases may change over time.",
        )
    overlap = set(policy.tuning.state_hashes.values()) & set(candidate.state_hashes.values())
    if overlap:
        raise JevError(
            "holdout_overlap",
            f"The holdout reuses {len(overlap)} state(s) from tuning.",
            "Use genuinely separate cases; changing case IDs or labels does not remove overlap.",
        )
    stats = {
        name: threshold_stats(candidate.evaluation.per_question[name], gate)
        for name, gate in policy.template.thresholds.items()
    }
    failures = []
    if candidate.status != "completed":
        failures.append("Verification contains incomplete or failed calls.")
    for name, item in stats.items():
        if item.accuracy is None or item.accuracy < min_accuracy:
            failures.append(f"{name}: automated accuracy is absent or below {min_accuracy:.2%}.")
        if item.coverage < min_coverage:
            failures.append(f"{name}: coverage {item.coverage:.2%} is below {min_coverage:.2%}.")
    return VerificationReport(
        policy_hash=policy.template_hash,
        tuning_job=policy.tuning.job_id,
        verification=candidate,
        per_question=stats,
        passed=not failures,
        failures=failures,
        min_accuracy=min_accuracy,
        min_coverage=min_coverage,
    )
