"""Explicit, primitive-specific review routing."""

from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse

from jev.core.models import ConfidenceGate, NoulGate, Template


def route(template: Template, response: SystemOneResponse) -> dict[str, object]:
    decisions: dict[str, object] = {}
    for name, answer in response.answers.items():
        gate = template.thresholds.get(name)
        value: str | float | bool | None = None
        disposition = "review"
        if isinstance(answer, NoulAnswer) and isinstance(gate, NoulGate):
            if answer.noul <= gate.no_at_or_below:
                disposition, value = "automate", False
            elif answer.noul >= gate.yes_at_or_above:
                disposition, value = "automate", True
        elif isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
            value = answer.choice if isinstance(answer, ChoiceAnswer) else answer.score
            if isinstance(gate, ConfidenceGate) and answer.confidence >= gate.automate_at_or_above:
                disposition = "automate"
        decisions[name] = {
            "disposition": disposition,
            "value": value,
            "gate": gate.model_dump() if gate else None,
        }
    return decisions
