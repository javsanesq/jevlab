"""Generate portable SDK modules without executing template text."""

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from jev.core.errors import JevError
from jev.core.models import Template

ExportLanguage = Literal["python", "langchain", "pydantic-ai"]

# Deliberately self-contained: exported modules need no workbench installation,
# private database, credentials store, or runtime code generation.
_MODULE = '''"""Portable Jev decision exported by jev.

Requires Python 3.12+ and typesafe-sdk==0.7.0.
__REQUIREMENTS__
Supply TYPESAFE_API_KEY in the process environment. Never put a key in this file.
Call evaluate(state) or await aevaluate(state); importing performs no API calls.
Thresholds route judgments for application code; they never authorize side effects.
SDK errors propagate to your caller. Timeouts may leave remote billing unknown.
The SDK owns retries (two by default); avoid stacking automatic retry loops.
No local history is written. Framework tracing, if enabled by the host application,
may record state and results; configure its privacy and retention separately.
"""

import json
import logging
import math
from typing import Annotated, Literal

import httpx2
__LANGCHAIN_IMPORTS__\
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
__PYDANTIC_AI_IMPORTS__\
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    JSONContent,
    Noul,
    NoulAnswer,
    RetryPolicy,
    Score,
    ScoreAnswer,
    SystemOneResponse,
    TypeSafeClient,
)

Question = Annotated[Choice | Score | Noul, Field(discriminator="type")]


class ConfidenceGate(BaseModel):
    kind: Literal["confidence"]
    automate_at_or_above: float = Field(ge=0, le=1)


class NoulGate(BaseModel):
    kind: Literal["noul_probability"]
    no_at_or_below: float = Field(ge=0, le=1)
    yes_at_or_above: float = Field(ge=0, le=1)


Gate = Annotated[ConfidenceGate | NoulGate, Field(discriminator="kind")]


class ExportedDesign(BaseModel):
    name: str
    description: str
    model: str
    questions: dict[str, Question]
    thresholds: dict[str, Gate]


class RoutingDecision(BaseModel):
    disposition: Literal["automate", "review"]
    value: str | float | bool | None
    gate: Gate | None


class DecisionResult(BaseModel):
    """Typed SDK answers, usage, returned model, and explicit per-question routing."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    response: SystemOneResponse
    routing: dict[str, RoutingDecision]


# JSON is embedded as a quoted Python string, never interpolated into executable code.
# It includes the full state description, rubric, notes, version, and thresholds.
TEMPLATE_JSON = __TEMPLATE_JSON__
DESIGN = ExportedDesign.model_validate_json(TEMPLATE_JSON)
_STATE = TypeAdapter[JSONContent](JSONContent)
_PROBABILITY_ROUNDING_ALLOWANCE = 0.015


def _state(state: JSONContent) -> JSONContent:
    if not isinstance(state, (str, list, dict)) or (isinstance(state, str) and not state.strip()):
        raise ValueError("State must be nonempty text or a JSON object/array.")
    # Reject non-JSON objects and NaN/infinity before opening an HTTP client.
    return _STATE.validate_json(json.dumps(state, allow_nan=False))


def _quiet_wire_logs() -> None:
    for name in ("typesafe_sdk", "httpx2", "httpcore2", "httpx", "httpcore"):
        logging.getLogger(name).disabled = True


def _verify(response: SystemOneResponse) -> None:
    def invalid() -> None:
        raise ValueError("TypeSafe returned answers inconsistent with the exported template.")

    if set(response.answers) != set(DESIGN.questions):
        invalid()
    for name, question in DESIGN.questions.items():
        answer = response.answers[name]
        if answer.type != question.type:
            invalid()
        if isinstance(question, Noul) and isinstance(answer, NoulAnswer):
            if not math.isfinite(answer.noul) or not 0 <= answer.noul <= 1:
                invalid()
        elif isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
            values = list(answer.probabilities.values())
            if (
                not math.isfinite(answer.confidence)
                or not 0 <= answer.confidence <= 1
                or not values
                or any(not math.isfinite(x) or not 0 <= x <= 1 for x in values)
                or not math.isclose(sum(values), 1, abs_tol=_PROBABILITY_ROUNDING_ALLOWANCE)
            ):
                invalid()
            if isinstance(question, Choice) and isinstance(answer, ChoiceAnswer):
                if (
                    set(question.criteria) != set(answer.probabilities)
                    or answer.choice not in question.criteria
                ):
                    invalid()
                if answer.probabilities[answer.choice] < max(values) - 0.001:
                    invalid()
            if isinstance(question, Score) and isinstance(answer, ScoreAnswer):
                if (
                    set(answer.probabilities) != set(range(len(question.criteria)))
                    or set(answer.legend) != set(answer.probabilities)
                    or not math.isfinite(answer.score)
                    or not 0 <= answer.score <= len(question.criteria) - 1
                ):
                    invalid()
                expected_score = math.fsum(
                    level * probability for level, probability in answer.probabilities.items()
                )
                # Local defensive rounding allowance, scaled to the level range;
                # TypeSafe defines the weighted mean but does not specify precision.
                if not math.isclose(
                    answer.score,
                    expected_score,
                    rel_tol=0,
                    abs_tol=_PROBABILITY_ROUNDING_ALLOWANCE * (len(question.criteria) - 1),
                ):
                    raise ValueError(
                        f"TypeSafe returned an inconsistent score for {name}: "
                        f"{answer.score:g} contradicts its probability-weighted mean "
                        f"{expected_score:g}. Do not use this result as a decision."
                    )
    for count in (response.usage.input_tokens, response.usage.output_tokens):
        if count is not None and count < 0:
            invalid()


def _result(response: SystemOneResponse) -> DecisionResult:
    _verify(response)
    routing: dict[str, RoutingDecision] = {}
    for name, answer in response.answers.items():
        gate = DESIGN.thresholds.get(name)
        value: str | float | bool | None = None
        disposition: Literal["automate", "review"] = "review"
        if isinstance(answer, NoulAnswer) and isinstance(gate, NoulGate):
            if answer.noul <= gate.no_at_or_below:
                disposition, value = "automate", False
            elif answer.noul >= gate.yes_at_or_above:
                disposition, value = "automate", True
        elif isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
            value = answer.choice if isinstance(answer, ChoiceAnswer) else answer.score
            if isinstance(gate, ConfidenceGate) and answer.confidence >= gate.automate_at_or_above:
                disposition = "automate"
        routing[name] = RoutingDecision(disposition=disposition, value=value, gate=gate)
    return DecisionResult(response=response, routing=routing)


def evaluate(
    state: JSONContent,
    *,
    model: str | None = None,
    timeout: float = 10,
    max_retries: int = 2,
    transport: httpx2.BaseTransport | None = None,
) -> DecisionResult:
    """Call Jev synchronously; transport is available for offline testing.

    The saved model is the default. Explicit model overrides need fresh calibration.
    HTTP operation timeout is not a total deadline across retries.
    """
    state = _state(state)
    _quiet_wire_logs()
    with TypeSafeClient(
        base_url="https://api.typesafe.ai",
        model=DESIGN.model if model is None else model,
        timeout=timeout,
        retry=RetryPolicy(max_retries=max_retries),
        transport=transport,
    ) as client:
        return _result(client.system_one(state=state, questions=DESIGN.questions))


async def aevaluate(
    state: JSONContent,
    *,
    model: str | None = None,
    timeout: float = 10,
    max_retries: int = 2,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> DecisionResult:
    """Call Jev asynchronously; use asyncio.timeout externally for a total deadline."""
    state = _state(state)
    _quiet_wire_logs()
    async with AsyncTypeSafeClient(
        base_url="https://api.typesafe.ai",
        model=DESIGN.model if model is None else model,
        timeout=timeout,
        retry=RetryPolicy(max_retries=max_retries),
        transport=transport,
    ) as client:
        return _result(await client.system_one(state=state, questions=DESIGN.questions))
'''

_LANGCHAIN = """

# Usage: decision_runnable.invoke(state) / await decision_runnable.ainvoke(state).
# Use normal LangChain composition with `|`; this wrapper does not translate rubrics.
decision_runnable: Runnable[JSONContent, DecisionResult] = RunnableLambda(
    evaluate, afunc=aevaluate, name=f"jev_{DESIGN.name}"
)
"""

_PYDANTIC_AI = '''

async def jev_decision(state: JSONContent) -> DecisionResult:
    """Judge the supplied state using the saved Jev rubric.

    Args:
        state: Relevant evidence as nonempty text or a JSON object/array.
    """
    return await aevaluate(state)


# Usage: Agent(YOUR_CONFIGURED_MODEL, tools=[decision_tool]).
# Application code must still enforce review dispositions and allowed side effects.
# This exposes a tool to your chosen agent; it does not select an agent model.
decision_tool: Tool[None] = Tool(
    jev_decision,
    takes_ctx=False,
    name=f"jev_{DESIGN.name.replace('-', '_')}",
    description=DESIGN.description,
    max_retries=0,
)
'''


def export_template(template: Template, *, lang: ExportLanguage = "python") -> str:
    """Render an importable module, preserving SDK instructions and structured criteria."""
    # Revalidate copies even when callers bypassed Pydantic with model_copy(update=...).
    checked = Template.model_validate_json(template.model_dump_json())
    integrations = {
        "python": ("No other dependencies are required.", "", ""),
        "langchain": (
            "Also requires langchain-core==1.6.3 (verified 2026-09-20).",
            "from langchain_core.runnables import Runnable, RunnableLambda\n",
            _LANGCHAIN,
        ),
        "pydantic-ai": (
            "Also requires pydantic-ai-slim==2.46.0 (verified 2026-09-20).",
            "from pydantic_ai import Tool\n",
            _PYDANTIC_AI,
        ),
    }
    if lang not in integrations:
        raise JevError(
            "export_language", "Unknown export language.", "Use python, langchain, or pydantic-ai."
        )
    requirements, imports, suffix = integrations[lang]
    content = (
        _MODULE.replace("__REQUIREMENTS__", requirements)
        .replace("__LANGCHAIN_IMPORTS__", imports if lang == "langchain" else "")
        .replace("__PYDANTIC_AI_IMPORTS__", imports if lang == "pydantic-ai" else "")
    )
    # The template is inserted last so even marker-like user text stays literal data.
    serialized = json.dumps(
        checked.model_dump(mode="json"), ensure_ascii=True, allow_nan=False, indent=2
    )
    literals: list[str] = []
    for line in serialized.splitlines(keepends=True):
        while line:
            length = min(len(line), 88)
            while len(repr(line[:length])) > 94:
                length -= 1
            piece = line[:length]
            literal = repr(piece) if '"' in piece else json.dumps(piece, ensure_ascii=True)
            literals.append("    " + literal)
            line = line[length:]
    encoded = "(\n" + "\n".join(literals) + "\n)"
    return content.replace("__TEMPLATE_JSON__", encoded) + suffix


def write_export(template: Template, path: Path, *, lang: ExportLanguage = "python") -> Path:
    """Publish a complete 0600 module atomically, refusing existing paths and symlinks."""
    destination = path.expanduser().absolute()
    if destination.suffix.lower() != ".py":
        raise JevError(
            "export_extension", "Python exports need a .py filename.", "Choose OUTPUT.py."
        )
    content = export_template(template, lang=lang)
    temporary: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    except FileExistsError:
        raise JevError(
            "already_exists", "The output path already exists.", "Choose a new module path."
        ) from None
    except OSError:
        raise JevError(
            "export_write",
            "Could not write the exported module.",
            "Choose a writable local directory with available space.",
        ) from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return destination
