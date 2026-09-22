"""Two real Jev calls over identical state, with transparent answer differences."""

import asyncio
import json
from dataclasses import replace
from typing import Literal, cast
from uuid import uuid4

from pydantic import Field
from typesafe_sdk import ChoiceAnswer, JSONContent, NoulAnswer, ScoreAnswer, SystemOneResponse

from jevlab.core.client import Evaluation, Evaluator, SDKClient, translate_error
from jevlab.core.credentials import Credentials, require_credentials
from jevlab.core.errors import JevError
from jevlab.core.models import Run, StrictModel, Template
from jevlab.core.pricing import price
from jevlab.core.service import Workbench, parse_state
from jevlab.core.templates import context_estimate, dump_template, parse_template


class ComparisonPlan(StrictModel):
    calls: int = 2
    estimated_input_tokens: int
    estimated_cost_nanousd: int | None
    requires_confirmation: bool
    estimate_note: str = "Characters/4 approximation; overhead and retries can increase cost."


class AnswerDifference(StrictModel):
    question_id: str
    left_type: str | None = None
    right_type: str | None = None
    left_value: str | float | None = None
    right_value: str | float | None = None
    left_confidence: float | None = None
    right_confidence: float | None = None
    value_delta: float | None = None
    probability_deltas: dict[str, float] = Field(default_factory=dict)
    comparable: bool = False
    note: str = ""


class ComparisonReport(StrictModel):
    id: str
    started_at: str
    left: Run
    right: Run
    differences: list[AnswerDifference]
    known_cost_nanousd: int
    unknown_cost_runs: int
    status: Literal["completed", "partial", "failed"]


class _PairStart:
    """Hold inference until both pending runs exist and form one retention unit."""

    def __init__(self) -> None:
        self.right_registered = asyncio.Event()
        self.left_started = asyncio.Event()


class _PairEvaluator:
    def __init__(
        self,
        wb: Workbench,
        evaluator: Evaluator | None,
        start: _PairStart,
        *,
        left: bool,
        api_key: str | None,
        credential_error: JevError | None,
    ) -> None:
        self.wb, self.evaluator, self.start, self.left = wb, evaluator, start, left
        self.api_key, self.credential_error = api_key, credential_error

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        # Workbench creates a pending row before invoking this method. The right
        # row already references the left, so retention protects both while either
        # is pending. Releasing the left first also makes fixture ordering stable.
        if self.left:
            await self.start.right_registered.wait()
            self.start.left_started.set()
        else:
            self.start.right_registered.set()
            await self.start.left_started.wait()
        if self.credential_error:
            # Workbench attaches the individual run ID, so give each side its own error.
            raise replace(self.credential_error)
        evaluator = self.evaluator
        if evaluator is None:
            assert self.api_key is not None
            # Each side owns its HTTP client; concurrent requests must not share a
            # context manager that one side could close while the other is running.
            evaluator = SDKClient(self.api_key, self.wb.settings)
        return await evaluator.evaluate(template, state)


def comparison_plan(
    wb: Workbench, left: Template, right: Template, state: JSONContent
) -> ComparisonPlan:
    tokens = [
        cast(int, context_estimate(template, state)["estimated_total_tokens"])
        for template in (left, right)
    ]
    costs = [
        price(template.model, count)[0]
        for template, count in zip((left, right), tokens, strict=True)
    ]
    total = sum(cast(list[int], costs)) if all(c is not None for c in costs) else None
    return ComparisonPlan(
        estimated_input_tokens=sum(tokens),
        estimated_cost_nanousd=total,
        requires_confirmation=total is None or total / 1e9 > wb.settings.confirm_cost_usd,
    )


def differences(left: Run, right: Run, a: Template, b: Template) -> list[AnswerDifference]:
    responses = [
        SystemOneResponse.model_validate_json(json.dumps(run.response))
        if run.status == "succeeded"
        else None
        for run in (left, right)
    ]
    result: list[AnswerDifference] = []
    for name in dict.fromkeys([*a.questions, *b.questions]):
        item = AnswerDifference(question_id=name)
        values: list[str | float | None] = []
        distributions: list[dict[str, float]] = []
        for side, response in zip(("left", "right"), responses, strict=True):
            answer = response.answers.get(name) if response else None
            setattr(item, f"{side}_type", answer.type if answer else None)
            value: str | float | None = None
            distribution: dict[str, float] = {}
            if isinstance(answer, ChoiceAnswer):
                value, distribution = answer.choice, dict(answer.probabilities)
            elif isinstance(answer, ScoreAnswer):
                value = answer.score
                distribution = {str(k): v for k, v in answer.probabilities.items()}
            elif isinstance(answer, NoulAnswer):
                value = answer.noul
                distribution = {"yes": answer.noul, "no": 1 - answer.noul}
            setattr(item, f"{side}_value", value)
            if isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
                setattr(item, f"{side}_confidence", answer.confidence)
            values.append(value)
            distributions.append(distribution)
        qa, qb = a.questions.get(name), b.questions.get(name)
        item.comparable = (
            qa is not None
            and qb is not None
            and qa.type == qb.type
            and qa.criteria == qb.criteria
            and all(value is not None for value in values)
        )
        if item.comparable:
            item.probability_deltas = {
                key: distributions[1][key] - distributions[0][key] for key in distributions[0]
            }
            if all(isinstance(value, (int, float)) for value in values):
                item.value_delta = cast(float, values[1]) - cast(float, values[0])
            item.note = "Right minus left; matching criteria do not guarantee equivalent wording."
        else:
            item.note = (
                "Missing answer or changed type/criteria; numeric deltas are not comparable."
            )
        result.append(item)
    return result


async def compare(
    wb: Workbench,
    left: Template,
    right: Template,
    state: JSONContent,
    *,
    authorize_cost: bool = False,
    evaluator: Evaluator | None = None,
) -> ComparisonReport:
    left, right = (parse_template(dump_template(t)) for t in (left, right))
    state = parse_state(json.dumps(state, allow_nan=False), "json")
    plan = comparison_plan(wb, left, right, state)
    if plan.requires_confirmation and not authorize_cost:
        raise JevError(
            "cost_confirmation",
            "Comparison cost needs confirmation.",
            "Review the estimate and explicitly authorize both calls.",
        )

    # Bound the shared credential lookup separately from the two inference
    # deadlines. A credential error still becomes two linked failed history
    # records. Comparison latency includes the brief registration barrier.
    api_key: str | None = None
    credential_error: JevError | None = None
    if evaluator is None:
        try:
            credentials = Credentials(wb.settings.credential_mode)
            api_key = await require_credentials(
                credentials, timeout_seconds=min(5.0, wb.settings.deadline_seconds)
            )
        except Exception as error:
            credential_error = translate_error(error)
    left_id, right_id = str(uuid4()), str(uuid4())
    start = _PairStart()

    async def execute(template: Template, *, left_side: bool) -> Run:
        try:
            return await wb.run(
                template,
                state,
                evaluator=_PairEvaluator(
                    wb,
                    evaluator,
                    start,
                    left=left_side,
                    api_key=api_key,
                    credential_error=credential_error,
                ),
                run_id=left_id if left_side else right_id,
                parent_run_id=None if left_side else left_id,
            )
        except JevError as error:
            if error.run_id:
                return wb.storage.get(error.run_id)
            raise

    # Register left before right; neither evaluator starts until the linked pair
    # is persisted. A startup failure or cancellation must release the peer too.
    tasks = [
        asyncio.create_task(execute(left, left_side=True)),
        asyncio.create_task(execute(right, left_side=False)),
    ]
    try:
        a, b = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    successes = sum(run.status == "succeeded" for run in (a, b))
    return ComparisonReport(
        id=a.id,
        started_at=a.started_at,
        left=a,
        right=b,
        differences=differences(a, b, left, right),
        known_cost_nanousd=sum(run.cost_nanousd or 0 for run in (a, b)),
        unknown_cost_runs=sum(run.cost_nanousd is None for run in (a, b)),
        status="completed" if successes == 2 else "partial" if successes else "failed",
    )
