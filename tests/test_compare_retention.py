"""Comparison groups survive retention while either paid call is unfinished."""

import asyncio
import sqlite3

import pytest
from conftest import MockEvaluator
from typesafe_sdk import JSONContent

from jev.core.client import Evaluation, Evaluator
from jev.core.compare import compare
from jev.core.models import Run, Settings, Template
from jev.core.retention import cleanup
from jev.core.service import Workbench
from jev.core.templates import fork_template


async def test_cleanup_cannot_separate_a_completed_left_from_a_pending_right(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    wb.settings.retention_bytes = 1_000_000
    left_finished = asyncio.Event()
    original_run = wb.run

    async def tracked_run(
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        result = await original_run(
            template,
            state,
            evaluator=evaluator,
            parent_run_id=parent_run_id,
            run_id=run_id,
        )
        if template.name != "variant":
            left_finished.set()
        return result

    monkeypatch.setattr(wb, "run", tracked_run)
    mock = MockEvaluator()

    class CleaningEvaluator:
        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            if template.name == "variant":
                await left_finished.wait()
                result = cleanup(wb.storage, wb.settings, dry_run=False)
                assert result.deleted.runs == 0
                assert result.plan.protected.runs == 2
                assert not result.within_budget
                rows = wb.storage.history()
                right = next(row for row in rows if row.template_name == "variant")
                assert right.status == "pending" and right.parent_run_id
                assert wb.storage.get(right.parent_run_id).status == "succeeded"
            return await mock.evaluate(template, state)

    async with asyncio.timeout(3):
        report = await compare(
            wb,
            design,
            fork_template(design, "variant"),
            {"ticket": "x" * 700_000},
            evaluator=CleaningEvaluator(),
        )
    assert report.status == "completed" and len(mock.requests) == 2
    assert report.right.parent_run_id == report.left.id
    assert wb.storage.get(report.right.id).parent_run_id == report.left.id
    # Once the comparison completes, its whole group is eligible under the cap.
    result = cleanup(wb.storage, wb.settings, dry_run=False)
    assert result.deleted.runs == 2 and wb.storage.history() == []


async def test_pair_registration_error_cancels_waiting_peer_without_an_api_call(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_create = wb.storage.create_run

    def broken_create(run: Run, template: Template) -> None:
        if template.name == "variant":
            raise sqlite3.OperationalError("Synthetic database failure")
        original_create(run, template)

    monkeypatch.setattr(wb.storage, "create_run", broken_create)
    evaluator = MockEvaluator()
    async with asyncio.timeout(2):
        with pytest.raises(sqlite3.OperationalError, match="Synthetic"):
            await compare(
                wb, design, fork_template(design, "variant"), "ticket", evaluator=evaluator
            )
    assert evaluator.requests == []
    remaining = wb.storage.history()
    assert len(remaining) == 1 and remaining[0].status == "interrupted"


async def test_no_credentials_fail_both_linked_runs_without_network(
    wb: Workbench, design: Template
) -> None:
    report = await compare(wb, design, fork_template(design, "variant"), "ticket")
    assert report.status == "failed"
    assert report.left.error and report.left.error["code"] == "missing_key"
    assert report.right.error and report.right.error["code"] == "missing_key"
    assert report.left.error["run_id"] == report.left.id
    assert report.right.error["run_id"] == report.right.id
    assert report.right.parent_run_id == report.left.id


async def test_credentials_resolve_once_before_pair_timers_start(
    wb: Workbench, design: Template, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = 0
    evaluator = MockEvaluator()

    def require(*args: object) -> str:
        nonlocal lookups
        lookups += 1
        assert wb.storage.history() == []
        return "offline-comparison-key"

    def sdk_client(key: str, settings: Settings) -> Evaluator:
        assert key == "offline-comparison-key"
        assert len(wb.storage.history()) == 2
        return evaluator

    monkeypatch.setattr("jev.core.compare.Credentials.require", require)
    monkeypatch.setattr("jev.core.compare.SDKClient", sdk_client)
    report = await compare(wb, design, fork_template(design, "variant"), "ticket")
    assert lookups == 1 and report.status == "completed" and len(evaluator.requests) == 2
