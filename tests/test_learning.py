import asyncio
import json
import sqlite3
from contextlib import closing
from typing import Any

import httpx2
import pytest
from typesafe_sdk import Choice, JSONContent, Noul, Score

from jevlab.core.client import Evaluation, SDKClient
from jevlab.core.content import LabeledCase, lesson, lessons, pattern, patterns, starter
from jevlab.core.errors import JevError
from jevlab.core.learning import Learning, exercise_plan
from jevlab.core.models import Settings, Template
from jevlab.core.service import Workbench
from jevlab.core.storage import Storage
from jevlab.core.templates import dump_template, parse_template


class LabeledEvaluator:
    """Return controlled labels through the official SDK, with one deliberate miss."""

    def __init__(self, cases: list[LabeledCase], *, miss: bool = False, fail_at: int = -1) -> None:
        self.cases, self.miss, self.fail_at = cases, miss, fail_at
        self.requests: list[dict[str, Any]] = []

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        index = len(self.requests)
        case = self.cases[index]
        answers: dict[str, object] = {}
        for name, question in template.questions.items():
            label = case.expected[name]
            if isinstance(question, Choice):
                selected = str(label)
                answers[name] = {
                    "type": "choice",
                    "choice": selected,
                    "confidence": 1.0,
                    "probabilities": {k: float(k == selected) for k in question.criteria},
                }
            elif isinstance(question, Noul):
                yes = bool(label) ^ (self.miss and index == 0)
                answers[name] = {"type": "noul", "noul": 0.9 if yes else 0.1}
            else:
                selected = int(label)
                answers[name] = {
                    "type": "score",
                    "score": float(selected),
                    "confidence": 1.0,
                    "legend": {str(i): c for i, c in enumerate(question.criteria)},
                    "probabilities": {
                        str(i): float(i == selected) for i in range(len(question.criteria))
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
            "fake-key", Settings(max_retries=0), transport=httpx2.MockTransport(respond)
        ).evaluate(template, state)


def test_library_is_valid_and_labels_match() -> None:
    assert len(patterns()) == 7 and len(lessons()) == 10
    assert sum(len(p.cases) for p in patterns()) == 29
    for item in patterns():
        parse_template(dump_template(item.template))
        assert len({c.id for c in item.cases}) == len(item.cases)
        for case in item.cases:
            assert set(case.expected) == set(item.template.questions)
            for name, question in item.template.questions.items():
                label = case.expected[name]
                if isinstance(question, Choice):
                    assert label in question.criteria
                elif isinstance(question, Score):
                    assert type(label) is int and 0 <= label < len(question.criteria)
                else:
                    assert type(label) is bool


async def test_grading_real_sdk_projection_metrics_and_progress(wb: Workbench) -> None:
    item = lesson("2")
    design = starter(item, "exercise", wb.settings.model)
    evaluator = LabeledEvaluator(pattern(item.pattern).cases, miss=True)
    report = await Learning(wb).grade(item, design, evaluator=evaluator)
    assert report.status == "completed" and report.passed
    assert report.fraction_correct == 14 / 15
    assert report.per_question["refund_requested"].accuracy == 0.8
    assert report.per_question["impact"].mean_absolute_error == 0
    assert report.known_cost_nanousd == 5 * 100 * 42
    assert all(set(r["state"]) == {"ticket"} for r in evaluator.requests)
    assert all("expected" not in r for r in evaluator.requests)
    assert all(
        row.run_id and wb.storage.get(row.run_id).status == "succeeded" for row in report.cases
    )
    # Adding advice later must not double-count attempts or change the Jev grade.
    report.feedback = {"summary": "Test clearer wording."}
    wb.storage.save_attempt(report.model_dump())
    saved = Storage(wb.storage.path)
    assert saved.learning_progress()[0]["attempts"] == 1
    assert saved.attempt(report.id[:8])["fraction_correct"] == 14 / 15


async def test_selected_state_fields_are_the_actual_sdk_inputs(wb: Workbench) -> None:
    item = lesson("3")
    design = starter(item, "state-exercise", wb.settings.model)
    evaluator = LabeledEvaluator(pattern(item.pattern).cases)
    report = await Learning(wb).grade(item, design, ["query", "chunk"], evaluator=evaluator)
    assert report.passed
    assert all(set(request["state"]) == {"query", "chunk"} for request in evaluator.requests)
    assert report.plan.design_feedback == []


async def test_failed_calls_do_not_inflate_accuracy(wb: Workbench) -> None:
    item = lesson("1")
    design = starter(item, "failed-exercise", wb.settings.model)
    evaluator = LabeledEvaluator(pattern(item.pattern).cases, fail_at=1)
    report = await Learning(wb).grade(item, design, evaluator=evaluator)
    assert len(evaluator.requests) == 2
    assert report.status == "failed" and not report.passed
    assert report.fraction_correct == 1 / 5
    assert report.unknown_cost_runs == 1
    assert report.cases[1].error and report.cases[1].run_id
    assert wb.storage.learning_progress()[0]["completed"] == 0


async def test_cost_gate_before_any_paid_call(wb: Workbench) -> None:
    item = lesson("1")
    design = starter(item, "cost-exercise", "jev-latest")
    evaluator = LabeledEvaluator(pattern(item.pattern).cases)
    assert exercise_plan(wb, item, design).requires_confirmation
    with pytest.raises(JevError, match="confirmation"):
        await Learning(wb).grade(item, design, evaluator=evaluator)
    assert not evaluator.requests and not wb.storage.history()
    report = await Learning(wb).grade(item, design, authorize_cost=True, evaluator=evaluator)
    assert report.passed


async def test_cancel_persists_partial_attempt(wb: Workbench) -> None:
    started = asyncio.Event()

    class WaitingEvaluator:
        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    item = lesson("1")
    task = asyncio.create_task(
        Learning(wb).grade(
            item, starter(item, "cancel-exercise", wb.settings.model), evaluator=WaitingEvaluator()
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    progress = wb.storage.learning_progress()[0]
    report = wb.storage.attempt(str(progress["last_attempt_id"]))
    assert report["status"] == "interrupted" and report["passed"] is False
    assert wb.storage.history()[0].status == "interrupted"


async def test_v1_migration_preserves_history(wb: Workbench) -> None:
    item = pattern("support-routing")
    run = await wb.run(
        item.template,
        {"ticket": {"message": "Synthetic test"}},
        evaluator=LabeledEvaluator(item.cases),
    )
    with closing(sqlite3.connect(wb.storage.path)) as connection, connection:
        connection.executescript(
            "DROP TABLE learn_progress; DROP TABLE learn_attempts; "
            "DELETE FROM schema_migrations WHERE version=2; PRAGMA user_version=1;"
        )
    migrated = Storage(wb.storage.path)
    assert migrated.health() == "ok" and migrated.learning_progress() == []
    assert migrated.get(run.id) == run
    assert migrated.template_for(run) == item.template
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 4


@pytest.mark.parametrize("fields", [[], ["wrong"], ["ticket", "ticket"]])
def test_invalid_exercise_fields_do_not_call_api(wb: Workbench, fields: list[str]) -> None:
    item = lesson("1")
    with pytest.raises(JevError):
        exercise_plan(wb, item, starter(item, "exercise", wb.settings.model), fields)
