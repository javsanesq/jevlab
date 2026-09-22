"""Paired Jev execution, cost gates, meaningful deltas, and cancellation."""

import asyncio
import copy

import pytest
from conftest import RESPONSE, MockEvaluator
from typesafe_sdk import JSONContent

from jevlab.core.client import Evaluation
from jevlab.core.compare import compare, comparison_plan
from jevlab.core.errors import JevError
from jevlab.core.models import Template
from jevlab.core.service import Workbench
from jevlab.core.templates import fork_template


class PairedEvaluator:
    def __init__(self, left: MockEvaluator, right: MockEvaluator) -> None:
        self.left, self.right = left, right

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        selected = self.right if template.name == "variant" else self.left
        return await selected.evaluate(template, state)


async def test_comparison_identical_state_and_model_calls_persist_linked_runs(
    wb: Workbench,
    design: Template,
) -> None:
    variant = fork_template(design, "variant")
    variant.questions["route"].instructions = "Identify the department responsible for this issue."
    body = copy.deepcopy(RESPONSE)
    body["answers"]["route"]["probabilities"] = {
        "billing": 0.7,
        "technical": 0.15,
        "other": 0.15,
    }
    body["answers"]["impact"].update(score=0.7, probabilities={"0": 0.4, "1": 0.5, "2": 0.1})
    body["answers"]["refund_requested"]["noul"] = 0.25
    left, right = MockEvaluator(), MockEvaluator(body)
    state: JSONContent = {"ticket": "Refund the duplicate charge.", "irrelevant": [1, 2]}
    report = await compare(wb, design, variant, state, evaluator=PairedEvaluator(left, right))
    assert report.status == "completed"
    assert left.requests[0]["state"] == right.requests[0]["state"] == state
    assert left.requests[0]["model"] == right.requests[0]["model"] == design.model
    assert report.left.id != report.right.id
    assert wb.storage.get(report.right.id).parent_run_id == report.left.id
    assert wb.storage.template_for(report.right).name == "variant"
    assert report.known_cost_nanousd == 84_000 and report.unknown_cost_runs == 0
    changes = {item.question_id: item for item in report.differences}
    assert changes["route"].probability_deltas["billing"] == pytest.approx(-0.2)
    assert changes["impact"].value_delta == pytest.approx(0.4)
    assert changes["refund_requested"].value_delta == pytest.approx(-0.7)
    assert changes["refund_requested"].left_confidence is None
    assert "wording" in changes["route"].note


@pytest.mark.parametrize("both_fail", [False, True])
async def test_comparison_partial_and_full_failures_retain_inspectable_history(
    wb: Workbench,
    design: Template,
    both_fail: bool,
) -> None:
    left = MockEvaluator(statuses=[401] if both_fail else [200])
    right = MockEvaluator(statuses=[401])
    report = await compare(
        wb,
        design,
        fork_template(design, "variant"),
        "A ticket",
        evaluator=PairedEvaluator(left, right),
    )
    assert report.status == ("failed" if both_fail else "partial")
    assert report.right.error and report.right.error["code"] == "authentication"
    assert report.unknown_cost_runs == (2 if both_fail else 1)
    assert len(wb.storage.history()) == 2
    assert all(not item.comparable and not item.probability_deltas for item in report.differences)
    assert all(item.value_delta is None for item in report.differences)


async def test_alias_price_gate_blocks_calls_until_authorized(
    wb: Workbench,
    design: Template,
) -> None:
    alias = fork_template(design, "variant")
    alias.model = "jev-latest"
    fake = MockEvaluator()
    plan = comparison_plan(wb, design, alias, "Same input")
    assert plan.calls == 2 and plan.estimated_cost_nanousd is None
    assert plan.requires_confirmation
    with pytest.raises(JevError, match="confirmation"):
        await compare(wb, design, alias, "Same input", evaluator=fake)
    assert not fake.requests and not wb.storage.history()
    report = await compare(wb, design, alias, "Same input", authorize_cost=True, evaluator=fake)
    assert report.right.requested_model == "jev-latest"
    assert report.right.resolved_model == "jev-1.13.0"
    assert {request["model"] for request in fake.requests} == {"jev-latest", "jev-1.13.0"}
    assert report.unknown_cost_runs == 0


async def test_changed_primitive_and_score_criteria_suppress_misleading_deltas(
    wb: Workbench,
    design: Template,
) -> None:
    data = design.model_dump(mode="json")
    data["name"] = "variant"
    data["thresholds"].pop("route")
    data["questions"]["route"] = {
        "type": "noul",
        "instructions": "Judge this ticket.",
        "criteria": {"true": "This is a billing issue.", "false": "This is another kind of issue."},
    }
    data["questions"]["impact"]["criteria"] = [
        "Low financial risk",
        "Material financial risk",
        "High financial risk",
    ]
    variant = Template.model_validate(data)
    body = copy.deepcopy(RESPONSE)
    body["answers"]["route"] = {"type": "noul", "noul": 0.9}
    report = await compare(
        wb,
        design,
        variant,
        "Ticket",
        evaluator=PairedEvaluator(MockEvaluator(), MockEvaluator(body)),
    )
    changes = {item.question_id: item for item in report.differences}
    for key in ("route", "impact"):
        assert not changes[key].comparable
        assert changes[key].value_delta is None and not changes[key].probability_deltas
    assert changes["route"].left_type == "choice" and changes["route"].right_type == "noul"
    assert changes["refund_requested"].comparable


@pytest.mark.parametrize("left_completed", [False, True])
async def test_cancelled_comparison_records_inflight_runs(
    wb: Workbench,
    design: Template,
    left_completed: bool,
) -> None:
    entered = asyncio.Event()
    fake = MockEvaluator()

    class WaitingEvaluator:
        calls = 0

        async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
            self.calls += 1
            if left_completed and template.name != "variant":
                return await fake.evaluate(template, state)
            if self.calls == 2:
                entered.set()
            await asyncio.Event().wait()
            raise AssertionError("Cancelled evaluator cannot finish")

    task = asyncio.create_task(
        compare(
            wb, design, fork_template(design, "variant"), "Ticket", evaluator=WaitingEvaluator()
        )
    )
    async with asyncio.timeout(2):
        await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    runs = wb.storage.history()
    assert len(runs) == 2
    right = next(run for run in runs if run.template_name == "variant")
    left = next(run for run in runs if run.template_name != "variant")
    assert right.parent_run_id == left.id
    assert sum(run.status == "interrupted" for run in runs) == (1 if left_completed else 2)
    assert sum(run.status == "succeeded" for run in runs) == int(left_completed)
    assert all(run.finished_at is not None for run in runs)
    for run in runs:
        if run.status == "interrupted":
            assert run.cost_nanousd is None
            assert run.error and "unknown" in str(run.error["message"])
