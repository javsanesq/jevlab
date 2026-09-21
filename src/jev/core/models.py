"""Versioned domain types; SDK questions stay faithful to the wire contract."""

import json
import re
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from typesafe_sdk import Choice, JSONContent, Noul, Score

from jev.core.coach_models import (
    DEFAULT_COACH_MODELS,
    CoachProvider,
    normalize_coach_model,
    recognized_coach_provider,
)

QuestionSpec = Annotated[Choice | Score | Noul, Field(discriminator="type")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def validate_jev_model(value: str) -> str:
    """Reject obvious input mistakes without freezing the list of provider models."""
    if value.strip().lower() == "jev":
        raise ValueError(
            "Jev is the model family, not an API model ID. "
            "Use jev-latest or a pinned version such as jev-1.13.0."
        )
    if value.startswith(("sk-", "sk_", "Bearer ")) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", value
    ):
        raise ValueError(
            "Enter one API model ID, without spaces, line breaks, code fences, or credentials. "
            "Use jev-latest or a pinned version such as jev-1.13.0."
        )
    return value


class StateSpec(StrictModel):
    description: str = Field(min_length=1)
    format: Literal["text", "json"] = "text"
    example: JSONContent | None = None


class ConfidenceGate(StrictModel):
    kind: Literal["confidence"] = "confidence"
    automate_at_or_above: float = Field(ge=0, le=1)


class NoulGate(StrictModel):
    kind: Literal["noul_probability"] = "noul_probability"
    no_at_or_below: float = Field(ge=0, le=1)
    yes_at_or_above: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.no_at_or_below >= self.yes_at_or_above:
            raise ValueError("Noul boundaries must satisfy no < yes.")
        return self


Gate = Annotated[ConfidenceGate | NoulGate, Field(discriminator="kind")]


class Template(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str = Field(min_length=1)
    state: StateSpec
    model: str = "jev-1.13.0"
    questions: dict[str, QuestionSpec] = Field(min_length=1)
    thresholds: dict[str, Gate] = Field(default_factory=dict)
    notes: str = ""

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str, info: ValidationInfo) -> str:
        # History must retain the exact failed request. Running or saving it always
        # revalidates without this explicitly read-only context.
        if isinstance(info.context, dict) and info.context.get("historical"):
            return value
        return validate_jev_model(value)

    @model_validator(mode="after")
    def check_design(self, info: ValidationInfo) -> Self:
        historical = isinstance(info.context, dict) and bool(info.context.get("historical"))
        for name, question in self.questions.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{0,63}", name):
                raise ValueError("Question IDs must be simple names of 1–64 characters.")
            if not question.instructions or (
                isinstance(question.instructions, str) and not question.instructions.strip()
            ):
                raise ValueError(f"Question {name} needs explicit instructions.")
            if isinstance(question, Choice) and not 2 <= len(question.criteria) <= 255:
                raise ValueError(f"Choice {name} needs 2–255 options.")
            if isinstance(question, Choice) and any(not x.strip() for x in question.criteria):
                raise ValueError(f"Choice {name} has an empty option label.")
            if isinstance(question, Score) and not 2 <= len(question.criteria) <= 10:
                raise ValueError(f"Score {name} needs 2–10 levels.")
            if isinstance(question, Score):
                for index, level in enumerate(question.criteria):
                    if not level or (
                        not historical and isinstance(level, str) and not level.strip()
                    ):
                        raise ValueError(
                            f"Score {name} level {index} has no description. "
                            "Describe when that level applies."
                        )
        for name, gate in self.thresholds.items():
            if name not in self.questions:
                raise ValueError(f"Threshold {name} has no matching question.")
            is_noul = isinstance(self.questions[name], Noul)
            if is_noul != isinstance(gate, NoulGate):
                raise ValueError(f"Threshold type does not match question {name}.")
        # Pydantic's SDK submodels permit NaN nested in JSON. Reject it before persistence.
        json.dumps(self.model_dump(mode="python"), allow_nan=False)
        return self


class Settings(StrictModel):
    schema_version: Literal[1] = 1
    ui_mode: Literal["simple", "expert"] = "simple"
    tour_completed: bool = False
    model: str = "jev-1.13.0"
    credential_mode: Literal["keychain", "environment"] = "keychain"
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    deadline_seconds: float = Field(default=45.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    retention_days: int = Field(default=90, ge=1)
    retention_bytes: int = Field(default=100_000_000, ge=1_000_000)
    confirm_cost_usd: float = Field(default=1.0, ge=0)
    # Human-terminal preferences only. Scripted jobs retain the cost budget gate.
    confirm_batch_cost: bool = True
    confirm_eval_cost: bool = True
    coach_provider: Literal["disabled", "anthropic", "openai"] = "disabled"
    # Retained as the selected-provider mirror for existing config/JSON consumers.
    coach_model: str = ""
    anthropic_model: str = DEFAULT_COACH_MODELS["anthropic"]
    openai_model: str = DEFAULT_COACH_MODELS["openai"]
    coach_max_output_tokens: int = Field(default=4096, ge=256, le=16384)
    coach_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str) -> str:
        return validate_jev_model(value)

    @model_validator(mode="before")
    @classmethod
    def migrate_coach_model(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        provider = data.get("coach_provider", "disabled")
        legacy = data.get("coach_model")
        if provider in ("anthropic", "openai") and isinstance(legacy, str) and legacy.strip():
            target = provider
            if "anthropic_model" not in data and "openai_model" not in data:
                # The old provider switch retained one shared model field. Recover only
                # recognizable ownership in legacy files; explicit modern settings stay strict.
                target = recognized_coach_provider(legacy) or provider
            data.setdefault(f"{target}_model", legacy)
        return data

    @model_validator(mode="after")
    def validate_coach_models(self) -> Self:
        self.anthropic_model = normalize_coach_model(self.anthropic_model, "anthropic")
        self.openai_model = normalize_coach_model(self.openai_model, "openai")
        self.coach_model = self.coach_model_for() if self.coach_provider != "disabled" else ""
        return self

    def coach_model_for(self, provider: CoachProvider | None = None) -> str:
        selected = provider or self.coach_provider
        if selected == "disabled":
            return ""
        return self.anthropic_model if selected == "anthropic" else self.openai_model

    def with_updates(self, updates: dict[str, object]) -> Self:
        """Apply legacy coach_model only to the selected provider, never its neighbor."""
        data = self.model_dump()
        selected = updates.get("coach_provider", self.coach_provider)
        if "coach_model" in updates:
            if selected == "disabled" and updates["coach_model"]:
                raise ValueError("Select a coach provider before setting its active model.")
            if selected in ("anthropic", "openai"):
                field = f"{selected}_model"
                if field in updates and updates[field] != updates["coach_model"]:
                    raise ValueError("Specify the selected provider model only once.")
                data[field] = updates["coach_model"]
        data.update(updates)
        return cast(Self, type(self).model_validate(data))


RunStatus = Literal["pending", "succeeded", "failed", "interrupted"]


class Run(StrictModel):
    id: str
    template_hash: str
    template_name: str
    parent_run_id: str | None = None
    started_at: str
    finished_at: str | None = None
    status: RunStatus = "pending"
    requested_model: str
    resolved_model: str | None = None
    request: dict[str, object]
    response: dict[str, object] | None = None
    routing: dict[str, object] | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_nanousd: int | None = None
    price_snapshot: dict[str, object] = Field(default_factory=dict)
    sdk_version: str
    request_id: str | None = None
    error: dict[str, object] | None = None
