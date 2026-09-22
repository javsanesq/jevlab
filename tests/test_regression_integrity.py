"""Reject inconsistent portable evidence before it can pass an offline quality gate."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import RESPONSE

from jevlab.core.datasets import DatasetRow
from jevlab.core.errors import JevError
from jevlab.core.evaluation import evaluate_runs
from jevlab.core.models import ConfidenceGate, Run, Template
from jevlab.core.regression import EvalSnapshot, FrozenPolicy, read_snapshot, verify_policy
from jevlab.core.templates import revision_hash


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def evidence(
    template: Template,
    *,
    name: str = "tuning",
    started: str = "2026-09-22T10:00:00+00:00",
    finished: str = "2026-09-22T10:01:00+00:00",
    resolved_model: str = "jev-1.13.0",
) -> dict[str, Any]:
    """Generate valid metrics through the actual evaluator, with synthetic saved runs."""
    pairs: list[tuple[DatasetRow, Run | None]] = []
    for index in range(2):
        row = DatasetRow(
            id=f"{name}-{index}",
            index=index,
            state={"ticket": {"message": f"Synthetic {name} ticket {index}."}},
            expected={"route": "billing", "impact": 0, "refund_requested": True},
        )
        body = copy.deepcopy(RESPONSE)
        body["model"] = resolved_model
        run = Run(
            id=f"{name}-run-{index}",
            template_hash=revision_hash(template),
            template_name=template.name,
            started_at=started,
            status="succeeded",
            requested_model=template.model,
            resolved_model=resolved_model,
            request={"state": row.state},
            response=body,
            sdk_version="0.7.1",
        )
        pairs.append((row, run))
    return {
        "job_id": name,
        "started_at": started,
        "finished_at": finished,
        "status": "completed",
        "template": template.model_dump(mode="json"),
        "template_hash": revision_hash(template),
        "dataset_sha256": digest(name),
        "case_hashes": {
            row.id: digest({"state": row.state, "expected": row.expected}) for row, _ in pairs
        },
        "state_hashes": {row.id: digest(row.state) for row, _ in pairs},
        "evaluation": evaluate_runs(template, pairs).model_dump(mode="json"),
    }


@pytest.fixture
def policy_design(design: Template) -> Template:
    template = design.model_copy(deep=True)
    template.model = "jev-latest"
    template.thresholds = {"route": ConfidenceGate(automate_at_or_above=0.8)}
    return template


@pytest.fixture
def policy(policy_design: Template) -> FrozenPolicy:
    return FrozenPolicy(
        frozen_at="2026-09-22T10:02:00+00:00",
        template=policy_design,
        template_hash=revision_hash(policy_design),
        tuning=EvalSnapshot.model_validate(evidence(policy_design)),
    )


def test_complete_separate_evidence_can_pass(policy: FrozenPolicy) -> None:
    candidate = EvalSnapshot.model_validate(
        evidence(
            policy.template,
            name="holdout",
            started="2026-09-22T10:03:00+00:00",
            finished="2026-09-22T10:04:00+00:00",
        )
    )
    report = verify_policy(policy, candidate, min_accuracy=1.0, min_coverage=1.0)
    assert report.passed
    assert report.per_question["route"].automated == 2


def test_success_count_cannot_hide_a_missing_answer(
    policy_design: Template, tmp_path: Path
) -> None:
    payload = evidence(policy_design)
    metrics = payload["evaluation"]["per_question"]["route"]
    metrics["observations"].pop()
    metrics.update(answered=1, failed=1, correct=1, accuracy=0.5, answered_accuracy=1.0)
    source = tmp_path / "inconsistent.json"
    source.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        read_snapshot(source)


@pytest.mark.parametrize(
    ("question", "changes"),
    [
        ("route", {"predicted": "absent", "expected": "absent", "value": "absent"}),
        ("impact", {"predicted": 2, "expected": 2, "value": 0.3}),
        ("impact", {"predicted": True, "expected": True, "value": 1.0}),
        ("refund_requested", {"value": 1.2}),
        ("refund_requested", {"predicted": True, "expected": True, "value": 0.1}),
    ],
    ids=["unknown-choice", "score-rounding", "bool-is-not-score", "noul-range", "noul-label"],
)
def test_primitive_evidence_must_match_its_value_and_label_space(
    policy_design: Template, question: str, changes: dict[str, object]
) -> None:
    payload = evidence(policy_design)
    payload["evaluation"]["per_question"][question]["observations"][0].update(changes)
    with pytest.raises(ValueError):
        EvalSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"failed_runs": 1},
        {"succeeded_runs": -1},
        {"missing_runs": 1},
    ],
)
def test_run_totals_must_agree(policy_design: Template, changes: dict[str, int]) -> None:
    payload = evidence(policy_design)
    payload["evaluation"].update(changes)
    with pytest.raises(ValueError):
        EvalSnapshot.model_validate(payload)


@pytest.mark.parametrize("resolved_models", [{}, {"jev-1.14.0": 2}, {"jev-1.13.0": 1}])
def test_alias_cannot_hide_unknown_or_changed_resolved_model(
    policy: FrozenPolicy, resolved_models: dict[str, int]
) -> None:
    payload = evidence(
        policy.template,
        name="holdout",
        started="2026-09-22T10:03:00+00:00",
        finished="2026-09-22T10:04:00+00:00",
    )
    payload["evaluation"]["resolved_models"] = resolved_models
    candidate = EvalSnapshot.model_validate(payload)
    with pytest.raises(JevError) as raised:
        verify_policy(policy, candidate)
    assert raised.value.code == "model_drift"


def test_freeze_cannot_predate_tuning_completion(policy_design: Template) -> None:
    with pytest.raises(ValueError):
        FrozenPolicy(
            frozen_at="2026-09-22T10:00:30+00:00",
            template=policy_design,
            template_hash=revision_hash(policy_design),
            tuning=EvalSnapshot.model_validate(evidence(policy_design)),
        )


def test_verification_order_compares_instants_not_timestamp_text(policy: FrozenPolicy) -> None:
    candidate = EvalSnapshot.model_validate(
        evidence(
            policy.template,
            name="holdout",
            started="2026-09-22T11:01:00+01:00",
            finished="2026-09-22T11:01:30+01:00",
        )
    )
    with pytest.raises(JevError) as raised:
        verify_policy(policy, candidate)
    assert raised.value.code == "verification_order"


@pytest.mark.parametrize("timestamp", ["not-a-time", "2026-09-22T10:00:00"])
def test_evidence_timestamps_require_aware_instants(
    policy_design: Template, timestamp: str
) -> None:
    payload = evidence(policy_design)
    payload["started_at"] = timestamp
    with pytest.raises(ValueError):
        EvalSnapshot.model_validate(payload)


def test_holdout_case_ids_do_not_hide_reused_state(policy: FrozenPolicy) -> None:
    payload = evidence(
        policy.template,
        name="different-ids",
        started="2026-09-22T10:03:00+00:00",
        finished="2026-09-22T10:04:00+00:00",
    )
    first = next(iter(payload["state_hashes"]))
    payload["state_hashes"][first] = next(iter(policy.tuning.state_hashes.values()))
    candidate = EvalSnapshot.model_validate(payload)
    with pytest.raises(JevError) as raised:
        verify_policy(policy, candidate)
    assert raised.value.code == "holdout_overlap"
