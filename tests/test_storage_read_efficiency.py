"""Indexed reads and bounded reporting must preserve missing/retried-run accounting."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import MockEvaluator

from jevlab.core.datasets import DatasetInfo
from jevlab.core.errors import JevError
from jevlab.core.evaluation import EvalReport
from jevlab.core.jobs import BatchService, JobItem, JobPlan, JobReport
from jevlab.core.models import Run, Template
from jevlab.core.service import Workbench
from jevlab.core.storage import Storage
from jevlab.core.templates import revision_hash


def _seed(wb: Workbench, design: Template, ids: list[str]) -> list[Run]:
    runs = [
        Run(
            id=identifier,
            template_hash=revision_hash(design),
            template_name=design.name,
            started_at="2026-09-22T00:00:00Z",
            status="succeeded",
            requested_model=design.model,
            request={"state": f"Synthetic ticket {index}"},
            latency_ms=10,
            input_tokens=100,
            output_tokens=5,
            cost_nanousd=4200,
            sdk_version="offline-test",
        )
        for index, identifier in enumerate(ids)
    ]
    wb.storage.create_run(runs[0], design)
    data = [Storage._encode(run) for run in runs[1:]]
    if data:
        with wb.storage.connect() as connection:
            connection.executemany(
                f"INSERT INTO runs ({','.join(data[0])}) VALUES ({','.join('?' for _ in data[0])})",
                (list(row.values()) for row in data),
            )
    return runs


def _trace(storage: Storage, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    statements: list[str] = []
    connect = storage._connect

    @contextmanager
    def traced() -> Iterator[sqlite3.Connection]:
        with connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(storage, "_connect", traced)
    return statements


def _run_selects(statements: list[str]) -> list[str]:
    return [statement for statement in statements if statement.startswith("SELECT * FROM runs")]


def _report(design: Template, rows: int) -> JobReport:
    return JobReport(
        id="synthetic-report",
        kind="eval",
        template_name=design.name,
        template_hash=revision_hash(design),
        dataset=DatasetInfo(
            path="synthetic.jsonl", sha256="synthetic", format="jsonl", rows=rows, labeled_rows=rows
        ),
        plan=JobPlan(
            calls=rows,
            remaining_calls=0,
            estimated_input_tokens=100 * rows,
            estimated_cost_nanousd=4200 * rows,
            requires_confirmation=False,
        ),
        started_at="2026-09-22T00:00:00Z",
        total=rows,
        evaluation=EvalReport(template_name=design.name, rows=rows, per_question={}),
    )


def test_exact_run_lookup_uses_one_indexed_query(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = _seed(wb, design, [f"{index:08x}-0000-0000-0000-000000000000" for index in range(1, 30)])
    statements = _trace(wb.storage, monkeypatch)
    assert wb.storage.get(runs[-1].id) == runs[-1]
    selects = _run_selects(statements)
    assert len(selects) == 1
    with wb.storage.connect() as connection:
        plan = connection.execute("EXPLAIN QUERY PLAN " + selects[0]).fetchall()
    assert any("SEARCH runs USING INDEX" in row[3] for row in plan)
    assert not any("SCAN runs" in row[3] for row in plan)


def test_prefix_lookup_stays_literal_and_rejects_ambiguity(wb: Workbench, design: Template) -> None:
    first, second, third = _seed(wb, design, ["literal%_one", "literal%_two", "literalXY"])
    assert wb.storage.get(first.id) == first
    assert wb.storage.get("literal%_o") == first
    assert wb.storage.get("literal%_t") == second
    assert wb.storage.get("literalX") == third
    for identifier in ("literal%_", "", "missing", "%' OR 1=1 --"):
        with pytest.raises(JevError) as caught:
            wb.storage.get(identifier)
        assert caught.value.code == "run_not_found"


def test_bulk_reads_are_exact_deduplicated_and_batched_below_parameter_limit(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = _seed(wb, design, [f"{index:08x}-0000-0000-0000-000000000000" for index in range(1100)])
    statements = _trace(wb.storage, monkeypatch)
    ids = [run.id for run in reversed(runs)]
    results = wb.storage.get_many(iter([*ids, ids[0], ids[0][:8], "deleted-run"]))
    assert results == {run.id: run for run in runs}
    assert ids[0][:8] not in results and "deleted-run" not in results
    selects = _run_selects(statements)
    assert len(selects) == 3
    with wb.storage.connect() as connection:
        for statement in selects:
            plan = connection.execute("EXPLAIN QUERY PLAN " + statement).fetchall()
            assert any("SEARCH runs USING INDEX" in row[3] for row in plan)
            assert not any("SCAN runs" in row[3] for row in plan)
    statements.clear()
    assert wb.storage.get_many([]) == {} and statements == []


def test_summary_and_latency_metrics_share_bounded_reads(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = _seed(wb, design, [f"summary-{index}" for index in range(1001)])
    items = {
        index: JobItem(
            index=index, case_id=str(index), status="succeeded", run_id=run.id, run_ids=[run.id]
        )
        for index, run in enumerate(runs)
    }
    statements = _trace(wb.storage, monkeypatch)
    service, report = BatchService(wb), _report(design, len(items))
    latencies = service._summarize(report, items)
    service._evaluation_totals(report, latencies)
    assert len(_run_selects(statements)) == 3
    assert report.succeeded == 1001 and report.known_cost_nanousd == 1001 * 4200
    assert report.input_tokens == 100100 and report.latency_ms == 10010
    assert report.evaluation and report.evaluation.latency_p95_ms == 10
    assert report.evaluation.latency_total_ms == report.latency_ms


def test_summary_keeps_unknown_costs_and_every_retry_latency(
    wb: Workbench, design: Template
) -> None:
    failed, succeeded = _seed(wb, design, ["failed-attempt", "successful-retry"])
    failed.status = "failed"
    failed.cost_nanousd = failed.input_tokens = failed.output_tokens = None
    failed.latency_ms = 30
    wb.storage.finish_run(failed)
    items = {
        0: JobItem(
            index=0,
            case_id="retry",
            status="succeeded",
            run_id=succeeded.id,
            run_ids=[failed.id, succeeded.id],
            attempts=2,
        ),
        1: JobItem(
            index=1, case_id="missing", status="unknown", run_id="deleted", run_ids=["deleted"]
        ),
    }
    service, report = BatchService(wb), _report(design, 2)
    latencies = service._summarize(report, items)
    service._evaluation_totals(report, latencies)
    assert (report.succeeded, report.unknown, report.pending) == (1, 1, 0)
    assert report.known_cost_nanousd == 4200 and report.unknown_cost_runs == 2
    assert report.input_tokens == 100 and report.output_tokens == 5
    assert report.latency_ms == 40
    assert report.evaluation and report.evaluation.latency_mean_ms == 20
    assert report.evaluation.latency_p50_ms == 20 and report.evaluation.latency_p95_ms == 29


async def test_eval_and_export_do_not_fall_back_to_per_row_get(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, output = tmp_path / "cases.jsonl", tmp_path / "results.jsonl"
    source.write_text(
        "".join(
            json.dumps(
                {
                    "id": f"case-{index}",
                    "state": f"Synthetic refund ticket {index}",
                    "expected": {"route": "billing", "impact": 0, "refund_requested": True},
                }
            )
            + "\n"
            for index in range(3)
        )
    )

    def no_single_get(run_id: str) -> Run:
        pytest.fail("Completed job reporting should use bounded bulk reads.")

    monkeypatch.setattr(wb.storage, "get", no_single_get)
    statements = _trace(wb.storage, monkeypatch)
    report = await BatchService(wb).run(
        design,
        source,
        kind="eval",
        output=output,
        evaluator=MockEvaluator(),
        requests_per_second=1000,
    )
    assert report.status == "completed" and report.evaluation
    assert report.evaluation.per_question["route"].accuracy == 1
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [record["case"]["id"] for record in records] == [f"case-{index}" for index in range(3)]
    assert all(record["run"]["status"] == "succeeded" for record in records)
    assert len(_run_selects(statements)) == 3  # Billing, row analysis, streaming export.
