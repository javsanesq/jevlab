"""Offline durability, request budgeting, and batch execution boundary tests."""

import asyncio
import json
import sqlite3
from pathlib import Path
from time import monotonic

import pytest
from conftest import MockEvaluator
from typesafe_sdk import JSONContent

from jev.core.client import Evaluation
from jev.core.errors import JevError
from jev.core.jobs import BatchService
from jev.core.models import ConfidenceGate, Template
from jev.core.service import Workbench
from jev.core.storage import Storage


def dataset(tmp_path: Path, count: int = 3, *, labels: bool = True) -> Path:
    path = tmp_path / "cases.jsonl"
    with path.open("w") as stream:
        for index in range(count):
            row: dict[str, object] = {"id": f"case-{index}", "state": f"Refund ticket {index}"}
            if labels:
                row["expected"] = {"route": "billing", "impact": 0, "refund_requested": True}
            stream.write(json.dumps(row) + "\n")
    return path


async def test_batch_output_registry_and_no_op_resume(
    wb: Workbench, design: Template, evaluator: MockEvaluator, tmp_path: Path
) -> None:
    source, output = dataset(tmp_path), tmp_path / "results.jsonl"
    service = BatchService(wb)
    assert service.register_dataset(source, design).rows == 3
    assert len(service.datasets()) == 1
    progress: list[tuple[int, int]] = []
    report = await service.run(
        design,
        source,
        output=output,
        evaluator=evaluator,
        requests_per_second=1000,
        progress=lambda done, total: progress.append((done, total)),
    )
    assert report.status == "completed" and report.succeeded == 3
    assert report.known_cost_nanousd == 3 * 1000 * 42
    assert report.unknown_cost_runs == 0 and report.input_tokens == 3000
    assert progress[-1] == (3, 3)
    exported = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(exported) == 3 and exported[0]["run"]["response"]["future_metadata"]
    assert all(row["job_id"] == report.id for row in exported)
    assert service.get(report.id[:8]) == report
    assert service.list()[0].id == report.id
    assert service.template(report.id) == design
    report = await service.run(
        design, source, evaluator=evaluator, resume_id=report.id, requests_per_second=1000
    )
    assert report.status == "completed" and report.plan.remaining_calls == 0
    assert len(evaluator.requests) == 3
    assert [item.attempts for item in service.rows(report.id)] == [1, 1, 1]


async def test_eval_metrics_and_threshold_save_does_not_clobber_changes(
    wb: Workbench, design: Template, evaluator: MockEvaluator, tmp_path: Path
) -> None:
    service = BatchService(wb)
    report = await service.run(
        design, dataset(tmp_path), kind="eval", evaluator=evaluator, requests_per_second=1000
    )
    assert report.evaluation
    assert report.evaluation.per_question["route"].accuracy == 1
    assert report.evaluation.known_cost_nanousd == report.known_cost_nanousd
    changed = service.save_thresholds(
        report.id, {"route": ConfidenceGate(automate_at_or_above=0.91)}
    )
    assert changed.thresholds["route"].automate_at_or_above == 0.91  # type: ignore[union-attr]
    assert service.template(report.id) == design
    changed.notes = "A design edit must be preserved"
    wb.templates.save(changed, overwrite=True)
    with pytest.raises(JevError, match="changed"):
        service.save_thresholds(report.id, {"route": ConfidenceGate(automate_at_or_above=0.92)})
    assert wb.templates.load(design.name).notes == changed.notes


async def test_cost_and_input_gates_make_no_api_calls(
    wb: Workbench, design: Template, evaluator: MockEvaluator, tmp_path: Path
) -> None:
    source = dataset(tmp_path, labels=False)
    service = BatchService(wb)
    with pytest.raises(JevError, match="every question label"):
        await service.run(design, source, kind="eval", evaluator=evaluator)
    design.model = "jev-latest"
    assert service.plan(design, source).requires_confirmation
    with pytest.raises(JevError, match="confirmation"):
        await service.run(design, source, evaluator=evaluator)
    with pytest.raises(JevError, match="Retry flags"):
        service.plan(design, source, retry_failed=True)
    with pytest.raises(JevError, match="Invalid concurrency"):
        await service.run(design, source, evaluator=evaluator, concurrency=33)
    assert not evaluator.requests and not wb.storage.history() and not service.list()


async def test_resume_failures_is_explicit_and_stops_authentication_storm(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    source, service = dataset(tmp_path), BatchService(wb)
    bad = MockEvaluator(statuses=[401])
    first = await service.run(
        design, source, evaluator=bad, requests_per_second=1000, concurrency=1
    )
    assert first.failed == 1 and first.pending == 2 and len(bad.requests) == 1
    good = MockEvaluator()
    second = await service.run(
        design, source, evaluator=good, resume_id=first.id, requests_per_second=1000
    )
    assert second.failed == 1 and second.succeeded == 2 and len(good.requests) == 2
    third = await service.run(
        design,
        source,
        evaluator=good,
        resume_id=first.id,
        retry_failed=True,
        requests_per_second=1000,
    )
    assert third.status == "completed" and len(good.requests) == 3
    assert third.unknown_cost_runs == 1
    assert [item.attempts for item in service.rows(first.id)] == [2, 1, 1]


async def test_cancellation_tracks_run_and_unknown_requires_explicit_retry(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    started = asyncio.Event()

    class Blocked:
        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    source, service = dataset(tmp_path, 1), BatchService(wb)
    task = asyncio.create_task(service.run(design, source, evaluator=Blocked(), concurrency=1))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = service.list()[0]
    assert report.status == "interrupted" and report.unknown == 1
    item = next(service.rows(report.id))
    assert item.run_id and wb.storage.get(item.run_id).status == "interrupted"
    assert service.plan(design, source, resume_id=report.id).remaining_calls == 0
    evaluator = MockEvaluator()
    no_retry = await service.run(design, source, resume_id=report.id, evaluator=evaluator)
    assert no_retry.unknown == 1 and not evaluator.requests
    retried = await service.run(
        design, source, resume_id=report.id, retry_unknown=True, evaluator=evaluator
    )
    assert retried.status == "completed" and len(evaluator.requests) == 1
    assert len(next(service.rows(report.id)).run_ids) == 2


async def test_same_job_cannot_run_twice_concurrently(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    started = asyncio.Event()

    class Blocked:
        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    source, service = dataset(tmp_path), BatchService(wb)
    task = asyncio.create_task(service.run(design, source, evaluator=Blocked(), concurrency=1))
    await started.wait()
    report = service.list()[0]
    try:
        with pytest.raises(JevError, match="already running"):
            await service.run(design, source, resume_id=report.id, evaluator=MockEvaluator())
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_rate_spacing_and_worker_bound(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    starts: list[float] = []
    active = peak = 0
    delegate = MockEvaluator()

    class Measured:
        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            nonlocal active, peak
            starts.append(monotonic())
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.13)
                return await delegate.evaluate(template, state)
            finally:
                active -= 1

    report = await BatchService(wb).run(
        design, dataset(tmp_path, 5), evaluator=Measured(), concurrency=2, requests_per_second=20
    )
    assert report.status == "completed" and peak == 2
    assert all(b - a >= 0.045 for a, b in zip(starts, starts[1:], strict=False))


async def test_dataset_template_and_output_mutations_block_resume(
    wb: Workbench, design: Template, evaluator: MockEvaluator, tmp_path: Path
) -> None:
    source, output = dataset(tmp_path), tmp_path / "results.jsonl"
    service = BatchService(wb)
    output.write_text("user content")
    with pytest.raises(JevError, match="already exists"):
        await service.run(design, source, output=output, evaluator=evaluator)
    assert not evaluator.requests and output.read_text() == "user content"
    output.unlink()
    report = await service.run(
        design, source, output=output, evaluator=evaluator, requests_per_second=1000
    )
    original = source.read_text()
    source.write_text(original.replace("Refund", "Changed"))
    with pytest.raises(JevError, match="dataset path or content changed"):
        service.plan(design, source, resume_id=report.id)
    source.write_text(original)
    altered = design.model_copy(deep=True)
    altered.notes = "changed"
    with pytest.raises(JevError, match="design differs"):
        service.plan(altered, source, resume_id=report.id)
    output.write_text("user edits")
    with pytest.raises(JevError, match="modified outside"):
        await service.run(design, source, resume_id=report.id, evaluator=evaluator)
    assert len(evaluator.requests) == 3 and output.read_text() == "user edits"
    rebuilt = await service.run(
        design, source, resume_id=report.id, output=tmp_path / "rebuilt.jsonl", evaluator=evaluator
    )
    assert rebuilt.status == "completed" and len(evaluator.requests) == 3


async def test_export_failure_is_reported_and_can_rebuild_without_paid_calls(
    wb: Workbench,
    design: Template,
    evaluator: MockEvaluator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, service = dataset(tmp_path), BatchService(wb)
    output = tmp_path / "results.jsonl"

    def fail_replace(source: str, destination: Path) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr("jev.core.jobs.os.replace", fail_replace)
        report = await service.run(
            design, source, output=output, evaluator=evaluator, requests_per_second=1000
        )
    assert report.status == "failed" and report.succeeded == 3 and report.error
    assert report.error["code"] == "output_error"
    assert not list(tmp_path.glob(".jev-output-*"))
    report = await service.run(design, source, resume_id=report.id, evaluator=evaluator)
    assert report.status == "completed" and len(evaluator.requests) == 3
    assert len(output.read_text().splitlines()) == 3


async def test_schema3_migration_preserves_history_and_learning(
    wb: Workbench, design: Template, evaluator: MockEvaluator
) -> None:
    run = await wb.run(design, "Synthetic ticket", evaluator=evaluator)
    attempt: dict[str, object] = {
        "id": "lesson-attempt",
        "lesson_id": "1",
        "content_version": 1,
        "started_at": "2026-09-20",
        "passed": True,
        "fraction_correct": 1.0,
    }
    wb.storage.save_attempt(attempt, final=True)
    with sqlite3.connect(wb.storage.path) as connection:
        connection.executescript(
            "DROP TABLE job_items; DROP TABLE jobs; DROP TABLE datasets; "
            "DELETE FROM schema_migrations WHERE version=3; PRAGMA user_version=2;"
        )
    migrated = Storage(wb.storage.path)
    assert migrated.get(run.id) == run and migrated.template_for(run) == design
    assert migrated.attempt("lesson-attempt") == attempt
    assert migrated.learning_progress()[0]["completed"] == 1
    assert migrated.jobs() == [] and migrated.datasets() == [] and migrated.health() == "ok"
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4


@pytest.mark.parametrize("run_status", ["succeeded", "pending"])
async def test_crash_recovery_reconciles_run_but_never_silently_repeats_unknown(
    wb: Workbench, design: Template, evaluator: MockEvaluator, tmp_path: Path, run_status: str
) -> None:
    source, service = dataset(tmp_path, 1), BatchService(wb)
    report = await service.run(design, source, evaluator=evaluator)
    item = next(service.rows(report.id))
    assert item.run_id
    if run_status == "pending":
        run = wb.storage.get(item.run_id)
        run.status = "pending"
        wb.storage.finish_run(run)
    # A process can die after saving a Run and before checkpointing its job item.
    item.status = "running"
    wb.storage.save_job_item(report.id, item.model_dump())
    recovered = await service.run(design, source, resume_id=report.id, evaluator=evaluator)
    assert len(evaluator.requests) == 1
    if run_status == "succeeded":
        assert recovered.status == "completed" and recovered.succeeded == 1
    else:
        assert recovered.status == "failed" and recovered.unknown == 1
        assert (
            service.plan(design, source, resume_id=report.id, retry_unknown=True).remaining_calls
            == 1
        )


async def test_dataset_changed_during_resume_drops_previous_eval_metrics(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    source, service = dataset(tmp_path, 2), BatchService(wb)
    report = await service.run(
        design,
        source,
        kind="eval",
        evaluator=MockEvaluator(statuses=[401]),
        concurrency=1,
        requests_per_second=1000,
    )
    assert report.evaluation is not None
    delegate = MockEvaluator()

    class ChangeDuringRun:
        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            result = await delegate.evaluate(template, state)
            source.write_text(source.read_text().replace("Refund", "Changed"))
            return result

    resumed = await service.run(
        design,
        source,
        kind="eval",
        resume_id=report.id,
        retry_failed=True,
        evaluator=ChangeDuringRun(),
        requests_per_second=1000,
    )
    assert resumed.status == "failed" and resumed.error
    assert resumed.error["code"] == "dataset_changed" and resumed.evaluation is None
    assert service.get(resumed.id).evaluation is None


async def test_edit_undo_cannot_attach_original_labels_to_changed_api_input(
    wb: Workbench,
    design: Template,
    evaluator: MockEvaluator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Iterator

    from jev.core.datasets import DatasetRow, iter_dataset

    calls = 0

    def change_worker_pass(
        path: Path, template: Template, require_labels: bool = False
    ) -> Iterator[DatasetRow]:
        nonlocal calls
        calls += 1
        current = calls
        for row in iter_dataset(path, template, require_labels):
            # Simulate an edit only while workers consume the source, followed by undo.
            yield row.model_copy(update={"state": "Changed input"}) if current == 3 else row

    monkeypatch.setattr("jev.core.jobs.iter_dataset", change_worker_pass)
    report = await BatchService(wb).run(
        design,
        dataset(tmp_path, 1),
        kind="eval",
        evaluator=evaluator,
    )
    assert report.succeeded == 0 and report.status == "failed" and report.error
    assert report.error["code"] == "dataset_changed" and report.evaluation is None
    assert not wb.storage.history() and not evaluator.requests


async def test_authentication_failure_interrupts_long_rate_waits_promptly(
    wb: Workbench, design: Template, tmp_path: Path
) -> None:
    evaluator = MockEvaluator(statuses=[401])
    # Subsequent starts would otherwise wait 100 seconds each behind the rate lock.
    report = await asyncio.wait_for(
        BatchService(wb).run(
            design,
            dataset(tmp_path, 5),
            evaluator=evaluator,
            concurrency=4,
            requests_per_second=0.01,
        ),
        timeout=2,
    )
    assert report.failed == 1 and report.pending == 4 and report.unknown == 0
    assert len(evaluator.requests) == 1


@pytest.mark.parametrize(
    ("status", "message", "code"),
    [
        (402, "Account has no remaining credits.", "quota"),
        (404, "The evaluation endpoint is unavailable.", "not_found"),
    ],
)
async def test_account_or_service_failure_stops_pending_rows_and_keeps_diagnostics(
    wb: Workbench, design: Template, tmp_path: Path, status: int, message: str, code: str
) -> None:
    evaluator = MockEvaluator({"detail": {"message": message}}, [status])
    report = await asyncio.wait_for(
        BatchService(wb).run(
            design,
            dataset(tmp_path, 5),
            evaluator=evaluator,
            concurrency=4,
            requests_per_second=0.01,
        ),
        timeout=2,
    )
    assert report.failed == 1 and report.pending == 4
    assert len(evaluator.requests) == 1
    assert report.error is not None
    assert report.error["code"] == code and report.error["message"] == message
    assert report.error["http_status"] == status
    assert report.error["request_id"] == "test-request"
