"""Official SDK adapter. No gateway, generated decisions, or payload logging."""

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, cast

import httpx2
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
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAPITimeoutError,
    TypeSafeError,
)

from jevlab.core.diagnostics import provider_fields, redact_body, redact_text
from jevlab.core.errors import JevError
from jevlab.core.models import Settings, Template

PROBABILITY_ROUNDING_ALLOWANCE = 0.015


def suppress_wire_logs() -> None:
    # Environment DEBUG settings must not expose state, responses, or credentials.
    for name in ("typesafe_sdk", "httpx2", "httpcore2", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.disabled = True


@dataclass
class Evaluation:
    response: SystemOneResponse
    raw: dict[str, object]
    request_id: str | None


class Evaluator(Protocol):
    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation: ...


class SDKClient:
    def __init__(
        self, key: str, settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        suppress_wire_logs()
        self._key = key
        try:
            self.client = AsyncTypeSafeClient(
                api_key=key,
                base_url="https://api.typesafe.ai",
                model=settings.model,
                timeout=settings.timeout_seconds,
                retry=RetryPolicy(max_retries=settings.max_retries),
                transport=transport,
            )
        except TypeSafeError as error:
            # SDK validation happens before evaluate(), while this boundary still
            # knows the credential needed to redact any echoed setup diagnostics.
            reason = redact_text(str(error), (key,))
            raise JevError(
                "client_configuration",
                f"TypeSafe client setup failed: {reason} No API request was sent.",
                "Review the setting named above in jevlab config. For an API key, re-copy "
                "the complete key without whitespace into Keychain or TYPESAFE_API_KEY.",
                3,
                details={"exception_type": type(error).__name__, "request_sent": False},
            ) from None

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        try:
            async with self.client as client:
                response = await client.system_one(
                    state=state, questions=template.questions, model=template.model
                )
                raw_http = response.raw_http_response
                return Evaluation(
                    response,
                    cast(dict[str, object], redact_body(raw_http.json(), (self._key,))),
                    redact_text(raw_http.headers.get("x-typesafe-request-id", ""), (self._key,))
                    or None,
                )
        except Exception as error:
            # This is the last boundary that knows the actual credential. Redact here,
            # before a provider can echo it into persistence, CLI JSON, or UI details.
            raise translate_error(error, model=template.model, secrets=(self._key,)) from None


def verify_response(template: Template, response: SystemOneResponse) -> None:
    def invalid(field: str, reason: str) -> None:
        raise JevError(
            "invalid_response",
            f"TypeSafe returned invalid data at {field}: {reason}",
            f"Inspect {field} in the saved raw response and report the request ID to TypeSafe; "
            "do not use this incomplete result as a decision.",
            4,
            details={"field_path": field, "reason": reason},
        )

    missing = set(template.questions) - set(response.answers)
    if missing:
        invalid("answers", f"Missing answer(s): {', '.join(sorted(missing))}.")
    if set(response.answers) - set(template.questions):
        invalid("answers", "The response contains answers for questions that were not requested.")
    for name, question in template.questions.items():
        answer = response.answers[name]
        path = f"answers.{name}"
        if answer.type != question.type:
            invalid(f"{path}.type", f"Expected a {question.type} answer.")
        if isinstance(question, Noul) and isinstance(answer, NoulAnswer):
            if not math.isfinite(answer.noul) or not 0 <= answer.noul <= 1:
                invalid(f"{path}.noul", "The yes probability must be a finite number from 0 to 1.")
        elif isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
            values = list(answer.probabilities.values())
            if not math.isfinite(answer.confidence) or not 0 <= answer.confidence <= 1:
                invalid(f"{path}.confidence", "Confidence must be a finite number from 0 to 1.")
            if not values:
                invalid(f"{path}.probabilities", "At least one probability is required.")
            if any(not math.isfinite(x) or not 0 <= x <= 1 for x in values):
                invalid(f"{path}.probabilities", "Each probability must be finite and from 0 to 1.")
            if not math.isclose(sum(values), 1, abs_tol=PROBABILITY_ROUNDING_ALLOWANCE):
                invalid(f"{path}.probabilities", "The probabilities must add up to 1.")
            if isinstance(question, Choice) and isinstance(answer, ChoiceAnswer):
                if set(question.criteria) != set(answer.probabilities):
                    invalid(
                        f"{path}.probabilities", "Option names do not match the template's choices."
                    )
                if answer.choice not in question.criteria:
                    invalid(
                        f"{path}.choice",
                        "The selected option is not one of the template's choices.",
                    )
                if answer.probabilities[answer.choice] < max(values) - 0.001:
                    invalid(
                        f"{path}.choice",
                        "The selected option does not have the highest probability.",
                    )
            if isinstance(question, Score) and isinstance(answer, ScoreAnswer):
                if set(answer.probabilities) != set(range(len(question.criteria))):
                    invalid(
                        f"{path}.probabilities",
                        "Level indices do not match the template's ordered levels.",
                    )
                if set(answer.legend) != set(answer.probabilities):
                    invalid(
                        f"{path}.legend", "The legend does not describe every probability level."
                    )
                if (
                    not math.isfinite(answer.score)
                    or not 0 <= answer.score <= len(question.criteria) - 1
                ):
                    invalid(
                        f"{path}.score",
                        "The score must be finite and within the template's level range.",
                    )
                expected_score = math.fsum(
                    level * probability for level, probability in answer.probabilities.items()
                )
                # TypeSafe defines score as the weighted mean but does not promise a
                # rounding precision. Reuse our conservative probability allowance,
                # scaled to the level range; this is a local defensive policy.
                if not math.isclose(
                    answer.score,
                    expected_score,
                    rel_tol=0,
                    abs_tol=PROBABILITY_ROUNDING_ALLOWANCE * (len(question.criteria) - 1),
                ):
                    invalid(
                        f"{path}.score",
                        f"The score {answer.score:g} contradicts the probability-weighted "
                        f"mean {expected_score:g} of its levels.",
                    )
    for name, count in [
        ("input_tokens", response.usage.input_tokens),
        ("output_tokens", response.usage.output_tokens),
    ]:
        if count is not None and count < 0:
            invalid(f"usage.{name}", "Token usage cannot be negative.")


def translate_error(
    error: Exception, *, model: str | None = None, secrets: Iterable[str] = ()
) -> JevError:
    """Preserve safe provider evidence and classify the reason, not only the status."""
    secrets = tuple(secrets)
    if isinstance(error, JevError):
        if secrets:
            safe = JevError.from_dict(
                cast(dict[str, object], redact_body(error.as_dict(), secrets)),
                exit_code=error.exit_code,
            )
            return safe
        return error
    details: dict[str, object] = {"exception_type": type(error).__name__}
    if isinstance(error, (TypeSafeAPITimeoutError, TimeoutError)):
        return JevError(
            "timeout",
            "TypeSafe did not respond before the time limit; billing may be unknown.",
            "Check your internet connection, then check history before deliberately retrying.",
            4,
            True,
            details=details,
        )
    if isinstance(error, TypeSafeAPIConnectionError):
        return JevError(
            "connection",
            "A network connection to TypeSafe could not be established.",
            "Check your internet connection and any proxy or firewall access to api.typesafe.ai.",
            4,
            True,
            details=details,
        )
    if isinstance(error, TypeSafeAPIError):
        body = redact_body(error.body, secrets)
        details["response_body"] = body
        message, provider_code = provider_fields(body)
        status = error.status
        evidence = f"{provider_code or ''} {message}".lower()
        code, fix, retryable = (
            "api_error",
            ("Open technical details and report the HTTP status and request ID to TypeSafe."),
            False,
        )
        if isinstance(error, TypeSafeAPIResponseValidationError):
            code = "invalid_response"
            field = redact_text(error.field_path, secrets)
            details["field_path"] = field
            message = f"TypeSafe returned an invalid response at {field}."
            fix = (
                f"Inspect {field} in the saved response. Check the installed SDK version with "
                "jevlab doctor and report this response to TypeSafe before retrying."
            )
        elif status == 401 or any(
            x in evidence for x in ("invalid_api_key", "authentication_error")
        ):
            code, fix = (
                "authentication",
                "Replace the TypeSafe API key in jevlab config, then retry.",
            )
        elif (
            any(
                x in evidence
                for x in (
                    "insufficient_quota",
                    "quota exceeded",
                    "quota_exceeded",
                    "billing_error",
                    "billing limit",
                    "insufficient credit",
                    "payment required",
                    "credit balance",
                    "out of credits",
                )
            )
            or status == 402
        ):
            code, fix = (
                "quota",
                (
                    "Check credits and billing in the TypeSafe console; add credit or resolve the "
                    "account limit before retrying."
                ),
            )
        elif any(
            x in evidence
            for x in (
                "unknown model",
                "model_not_found",
                "model not found",
                "invalid model",
                "model does not exist",
            )
        ) or (
            "model" in evidence
            and any(x in evidence for x in ("retired", "unsupported", "not available"))
        ):
            code = "model_not_found"
            selected = f" '{redact_text(model, secrets)}'" if model else ""
            fix = (
                f"Change this template's Model{selected} to jev-latest or a supported version "
                "in the template editor. Run jevlab doctor --online to list available models."
            )
        elif (
            any(
                x in evidence
                for x in (
                    "context_length",
                    "context length",
                    "context limit",
                    "too many tokens",
                    "token limit",
                    "maximum context",
                    "input too long",
                    "context window",
                )
            )
            or status == 413
        ):
            code, fix = (
                "context_limit",
                (
                    "Shorten the state and questions, remove irrelevant context, or split "
                    "the request into smaller calls. Inspect the provider's stated token limit."
                ),
            )
        elif status == 403:
            code, fix = (
                "permission",
                (
                    "Check this key's project and model access in the TypeSafe console; use a key "
                    "with permission for the selected model."
                ),
            )
        elif status == 404:
            code, fix = (
                "not_found",
                (
                    "Run jevlab doctor --online to check available models and service access; "
                    "report "
                    "the request ID to TypeSafe if the resource should exist."
                ),
            )
        elif status == 429:
            code, fix, retryable = (
                "rate_limit",
                ("Wait before retrying; lower batch concurrency or request rate if this repeats."),
                True,
            )
            retry_after = getattr(error, "retry_after_ms", None)
            if isinstance(retry_after, (int, float)) and math.isfinite(retry_after):
                details["retry_after_ms"] = retry_after
                fix = f"Wait at least {retry_after / 1000:g} seconds before retrying. " + fix
        elif status >= 500:
            code, fix, retryable = (
                "api_error",
                (
                    "TypeSafe's service failed. Check its service status and retry later; use the "
                    "request ID when contacting support."
                ),
                True,
            )
        elif status in (400, 422):
            code = "bad_request" if status == 400 else "api_validation"
            fix = (
                (
                    "Correct the field or constraint named in the provider message in the template "
                    "editor. Open technical details for the response body and any field locations."
                )
                if message
                else (
                    "The provider gave no rejection reason. Open the saved response and send its "
                    "request ID to TypeSafe support; do not repeatedly retry the unchanged request."
                )
            )
        if not message:
            message = (
                f"TypeSafe returned HTTP {status} without an explanatory message. "
                "The cause is unknown."
            )
        return JevError(
            code,
            message,
            fix,
            4,
            retryable,
            redact_text(error.request_id, secrets) if error.request_id else None,
            http_status=status,
            provider_code=provider_code,
            details=details,
        )
    return JevError(
        "execution_error",
        "An unexpected error stopped the call; its cause is unknown.",
        "Open technical details for the exception type, run jevlab doctor, and report the run ID.",
        4,
        details=details,
    )


def raw_error_body(error: Exception) -> dict[str, object] | None:
    """Return only sanitized object bodies; text and arrays remain in error.details."""
    safe = translate_error(error)
    body = safe.details.get("response_body") if safe.details else None
    return cast(dict[str, object], body) if isinstance(body, dict) else None
