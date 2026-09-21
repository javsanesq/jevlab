import copy
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typesafe_sdk import SystemOneResponse

from jev.core import datasets
from jev.core.datasets import DatasetRow, inspect_dataset, iter_dataset
from jev.core.errors import JevError
from jev.core.evaluation import EvalReport, evaluate_runs, threshold_curve, threshold_stats
from jev.core.models import ConfidenceGate, NoulGate, Run, Template
from jev.core.thresholds import route

LABELS: dict[str, str | bool | int] = {
    "route": "billing",
    "impact": 0,
    "refund_requested": True,
}


def write_jsonl(path: Path, records: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    return path


def row(index: int, expected: dict[str, str | bool | int] | None = None) -> DatasetRow:
    return DatasetRow(
        id=f"case-{index}",
        index=index,
        state={"ticket": "Synthetic test"},
        expected=expected or LABELS,
    )


def run(index: int, body: dict[str, Any] | None = None) -> Run:
    from conftest import RESPONSE

    return Run(
        id=f"run-{index}",
        template_hash="hash",
        template_name="support-triage",
        started_at="2026-09-20T10:00:00+00:00",
        status="succeeded",
        requested_model="jev-1.13.0",
        resolved_model="jev-1.13.0",
        request={},
        response=copy.deepcopy(body or RESPONSE),
        input_tokens=100,
        output_tokens=20,
        latency_ms=100 + index * 100,
        cost_nanousd=4200,
        sdk_version="0.7.0",
    )


def test_jsonl_inspection_fingerprint_and_auto_ids(tmp_path: Path, design: Template) -> None:
    path = write_jsonl(
        tmp_path / "examples.jsonl",
        [
            {"id": "first", "state": {"ticket": "Help"}, "expected": LABELS},
            {"state": ["Another ticket"]},
        ],
    )
    info = inspect_dataset(path, design)
    assert info.rows == 2 and info.labeled_rows == 1
    assert info.path == str(path.resolve()) and info.format == "jsonl"
    assert info.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    imported = list(iter_dataset(path, design))
    assert [(item.id, item.index) for item in imported] == [("first", 0), ("row-2", 1)]
    assert imported[0].expected == LABELS and imported[1].expected == {}
    with pytest.raises(JevError, match="missing"):
        inspect_dataset(path, design, require_labels=True)


def test_csv_multiline_json_and_typed_labels(tmp_path: Path, design: Template) -> None:
    path = tmp_path / "cases.csv"
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["id", "state", "expected.route", "expected.impact", "expected.refund_requested"]
        )
        writer.writerow(
            ["one", json.dumps({"message": "Line one\nLine two"}), "billing", "0", "false"]
        )
    imported = list(iter_dataset(path, design, require_labels=True))
    assert imported[0].state == {"message": "Line one\nLine two"}
    assert type(imported[0].expected["impact"]) is int
    assert imported[0].expected["refund_requested"] is False
    assert inspect_dataset(path, design).format == "csv"
    design.state.format = "text"
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["state"])
        writer.writerow(["Quoted multiline\nstate"])
    assert list(iter_dataset(path, design))[0].state == "Quoted multiline\nstate"


@pytest.mark.parametrize(
    "record",
    [
        {"state": "ticket", "extra": "bad"},
        {"state": "ticket", "expected": {"unknown": True}},
        {"state": "ticket", "expected": {"refund_requested": 1}},
        {"state": "ticket", "expected": {"refund_requested": "true"}},
        {"state": "ticket", "expected": {"impact": True}},
        {"state": "ticket", "expected": {"impact": 0.0}},
        {"state": "ticket", "expected": {"impact": 3}},
        {"state": "ticket", "expected": {"route": "not-an-option"}},
        {"state": "ticket", "expected": []},
        {"state": " "},
        {"state": {}},
        {"state": []},
        {"state": False},
        {"state": {"value": float("nan")}},
        {"id": "", "state": "ticket"},
        {"id": 1, "state": "ticket"},
        {"id": "x" * 129, "state": "ticket"},
    ],
)
def test_reject_ambiguous_or_invalid_jsonl(
    tmp_path: Path, design: Template, record: dict[str, object]
) -> None:
    path = write_jsonl(tmp_path / "invalid.jsonl", [record])
    with pytest.raises(JevError) as error:
        list(iter_dataset(path, design))
    assert error.value.code == "invalid_dataset" and error.value.fix


@pytest.mark.parametrize(
    "text",
    [
        'state,expected.refund_requested\n"{""ticket"":""case""}",TRUE\n',
        'state,expected.impact\n"{""ticket"":""case""}",0.0\n',
        'state,expected.unknown\n"{}",\n',
        'state,state\n"{}","{}"\n',
        'state,unknown\n"{}",value\n',
        'state\n"unterminated\n',
        'state\n"{}",extra\n',
    ],
)
def test_reject_invalid_csv(tmp_path: Path, design: Template, text: str) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(text)
    with pytest.raises(JevError):
        inspect_dataset(path, design)


def test_duplicate_keys_ids_and_bounded_stream(
    tmp_path: Path, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text('{"state":"first","state":"second"}\n')
    with pytest.raises(JevError):
        inspect_dataset(path, design)
    write_jsonl(path, [{"id": "same", "state": "one"}, {"id": "same", "state": "two"}])
    with pytest.raises(JevError, match="Duplicate"):
        inspect_dataset(path, design)
    write_jsonl(path, [{"state": "one"}, {"state": "two"}])
    monkeypatch.setattr(datasets, "MAX_ROWS", 1)
    stream = iter_dataset(path, design)
    assert next(stream).id == "row-1"
    with pytest.raises(JevError, match="rows"):
        next(stream)
    monkeypatch.setattr(datasets, "MAX_ROW_BYTES", 10)
    with pytest.raises(JevError, match="MiB"):
        inspect_dataset(path, design)
    monkeypatch.setattr(datasets, "MAX_FILE_BYTES", 1)
    with pytest.raises(JevError, match="MiB"):
        inspect_dataset(path, design)


@pytest.mark.parametrize(
    "filename,content", [("cases.jsonl", ""), ("cases.csv", "state\n"), ("cases.txt", "x")]
)
def test_empty_unsupported_and_missing_files(
    tmp_path: Path, design: Template, filename: str, content: str
) -> None:
    path = tmp_path / filename
    with pytest.raises(JevError):
        inspect_dataset(path, design)
    path.write_text(content)
    with pytest.raises(JevError):
        inspect_dataset(path, design)


def test_metrics_denominators_calibration_missing_and_cost(design: Template) -> None:
    successful = run(0)
    missed = run(1)
    failed = run(2).model_copy(update={"status": "failed", "response": None, "cost_nanousd": None})
    report = evaluate_runs(
        design,
        [
            (row(0), successful),
            (row(1, {**LABELS, "route": "technical"}), missed),
            (row(2), failed),
            (row(3), None),
        ],
    )
    metrics = report.per_question["route"]
    assert (metrics.total, metrics.answered, metrics.failed, metrics.correct) == (4, 2, 2, 1)
    assert metrics.accuracy == 0.25 and metrics.answered_accuracy == 0.5
    assert sum(bin.count for bin in metrics.calibration) == 2
    assert metrics.calibration[9].observed_accuracy == 0.5
    assert metrics.calibration[0].observed_accuracy is None
    assert metrics.confusion_matrix["billing"]["billing"] == 1
    assert metrics.confusion_matrix["technical"]["billing"] == 1
    assert metrics.worst_misses[0].case_id == "case-1"
    assert (report.succeeded_runs, report.failed_runs, report.missing_runs) == (2, 1, 1)
    assert report.known_cost_nanousd == 8400 and report.unknown_cost_runs == 1
    assert report.resolved_models == {"jev-1.13.0": 3}
    assert report.input_tokens == 300 and report.output_tokens == 60
    assert report.latency_total_ms == 600 and report.latency_mean_ms == 200
    assert report.latency_p50_ms == 200 and report.latency_p95_ms == 290
    assert EvalReport.model_validate_json(report.model_dump_json()) == report


def test_resolved_model_counts_make_alias_mixtures_visible(design: Template) -> None:
    first = run(0)
    second = run(1).model_copy(update={"resolved_model": "jev-future-version"})
    failed = run(2).model_copy(
        update={"status": "failed", "response": None, "resolved_model": None}
    )
    report = evaluate_runs(design, [(row(0), first), (row(1), second), (row(2), failed)])
    assert report.resolved_models == {"jev-1.13.0": 1, "jev-future-version": 1}


def test_score_nearest_probability_is_not_argmax_or_confidence(design: Template) -> None:
    from conftest import RESPONSE

    body = copy.deepcopy(RESPONSE)
    body["answers"]["impact"].update(
        score=1.5, confidence=0.91, probabilities={"0": 0.4, "1": 0.3, "2": 0.3}
    )
    report = evaluate_runs(design, [(row(0, {**LABELS, "impact": 2}), run(0, body))])
    metrics = report.per_question["impact"]
    assert metrics.accuracy == 1 and metrics.mean_absolute_error == 0.5
    assert metrics.observations[0].predicted == 2
    assert metrics.observations[0].probability == 0.3
    assert metrics.calibration[3].count == 1 and metrics.confidence_calibration[9].count == 1
    assert metrics.brier_score == pytest.approx(0.74)
    assert threshold_stats(metrics, ConfidenceGate(automate_at_or_above=0.9)).automated == 1
    assert threshold_stats(metrics, ConfidenceGate(automate_at_or_above=0.92)).accuracy is None


def test_probability_one_last_bin_and_worst_miss_order(design: Template) -> None:
    from conftest import RESPONSE

    body = copy.deepcopy(RESPONSE)
    body["answers"]["route"].update(
        confidence=1.0, probabilities={"billing": 1.0, "technical": 0.0, "other": 0.0}
    )
    report = evaluate_runs(
        design,
        [
            (row(0, {**LABELS, "route": "technical"}), run(0)),
            (row(1, {**LABELS, "route": "technical"}), run(1, body)),
        ],
    )
    metrics = report.per_question["route"]
    assert metrics.calibration[9].count == 2
    assert metrics.calibration[9].mean_probability == 0.95
    assert metrics.confidence_calibration[9].count == 1
    assert [miss.case_id for miss in metrics.worst_misses] == ["case-1", "case-0"]
    assert threshold_curve(metrics, 3)[-1].automated == 1
    assert threshold_stats(metrics, None).accuracy is None


@pytest.mark.parametrize(
    "probability,no,yes,expected",
    [
        (0.1, 0.2, 0.8, False),
        (0.2, 0.2, 0.8, False),
        (0.8, 0.2, 0.8, True),
        (0.5, 0.2, 0.8, True),
        (0.6, 0.7, 0.9, True),
        (0.4, 0.1, 0.3, False),
    ],
)
def test_noul_threshold_polarity_matches_real_routing(
    design: Template, probability: float, no: float, yes: float, expected: bool
) -> None:
    from conftest import RESPONSE

    body = copy.deepcopy(RESPONSE)
    body["answers"]["refund_requested"]["noul"] = probability
    current = run(0, body)
    report = evaluate_runs(
        design, [(row(0, {**LABELS, "refund_requested": expected}), current), (row(1), None)]
    )
    metrics = report.per_question["refund_requested"]
    assert metrics.observations[0].probability == max(probability, 1 - probability)
    assert metrics.confidence_calibration == []
    assert metrics.brier_score == pytest.approx((probability - expected) ** 2)
    gate = NoulGate(no_at_or_below=no, yes_at_or_above=yes)
    design.thresholds["refund_requested"] = gate
    routing = route(design, SystemOneResponse.model_validate_json(json.dumps(current.response)))
    disposition = routing["refund_requested"]
    assert isinstance(disposition, dict)
    stats = threshold_stats(metrics, gate)
    automated = disposition["disposition"] == "automate"
    assert stats.automated == int(automated)
    assert stats.coverage == int(automated) / 2 and stats.review == 2 - int(automated)
    assert stats.accuracy == (float(disposition["value"] == expected) if automated else None)
    assert len(threshold_curve(metrics)) == 21
    with pytest.raises(JevError):
        threshold_stats(metrics, ConfidenceGate(automate_at_or_above=0.5))


def test_empty_results_and_invalid_saved_success(design: Template) -> None:
    report = evaluate_runs(design, [(row(0), None)])
    metrics = report.per_question["route"]
    assert metrics.accuracy == 0 and metrics.answered_accuracy is None
    assert metrics.brier_score is None and not metrics.observations
    assert report.latency_mean_ms is None
    assert threshold_stats(metrics, ConfidenceGate(automate_at_or_above=0)).coverage == 0
    current = run(0).model_copy(update={"response": {"bad": "shape"}})
    with pytest.raises(JevError, match="invalid response"):
        evaluate_runs(design, [(row(0), current)])
