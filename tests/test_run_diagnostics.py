"""Provider evidence must survive SDK, persistence, and redaction without paid calls."""

import copy
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx2
import pytest
from conftest import RESPONSE, MockEvaluator
from typesafe_sdk import TypeSafeAPIConnectionError, TypeSafeAPIError, TypeSafeAPITimeoutError

from jev.core.client import SDKClient, translate_error
from jev.core.errors import JevError
from jev.core.models import Settings, Template
from jev.core.service import Workbench


@pytest.mark.parametrize(
    ("status", "message", "provider_code", "code", "fix"),
    [
        (400, "Unknown model: jev", "api_usage_error", "model_not_found", "jev-latest"),
        (
            400,
            "Question route contains unsupported criteria.",
            "invalid_question",
            "bad_request",
            "field",
        ),
        (401, "This API key has expired.", "invalid_api_key", "authentication", "jev config"),
        (403, "Project cannot use this model.", "permission_denied", "permission", "project"),
        (429, "Account has insufficient_quota.", "insufficient_quota", "quota", "billing"),
        (404, "Model not found: retired-test", "model_not_found", "model_not_found", "Model"),
        (404, "Resource not found.", "not_found", "not_found", "doctor --online"),
        (413, "Request too large.", "payload_too_large", "context_limit", "Shorten"),
        (
            400,
            "Maximum context length exceeded.",
            "context_length_exceeded",
            "context_limit",
            "Shorten",
        ),
        (
            422,
            "Question impact must have at least two criteria.",
            "validation_error",
            "api_validation",
            "constraint",
        ),
        (429, "Slow down: per-minute request limit exceeded.", "rate_limit", "rate_limit", "Wait"),
        (503, "Inference worker is temporarily offline.", "unavailable", "api_error", "later"),
    ],
)
async def test_provider_reason_and_metadata_survive_run_storage(
    wb: Workbench,
    design: Template,
    status: int,
    message: str,
    provider_code: str,
    code: str,
    fix: str,
) -> None:
    body = {"detail": {"error_type": provider_code, "message": message}}
    with pytest.raises(JevError) as caught:
        await wb.run(design, "Synthetic state", evaluator=MockEvaluator(body, [status]))
    error = caught.value
    assert error.message == message and error.code == code and fix in error.fix
    assert error.http_status == status and error.provider_code == provider_code
    assert error.request_id == "test-request"
    assert error.details and error.details["response_body"] == body
    saved = wb.storage.get(error.run_id or "")
    assert saved.request_id == error.request_id and saved.error == error.as_dict()
    assert saved.response == body
    assert saved.error is not None
    restored = JevError.from_dict(saved.error, exit_code=4)
    assert restored.as_dict() == error.as_dict()
    assert restored.exit_code == 4
    assert error.retryable == (status >= 500 or code == "rate_limit")


@pytest.mark.parametrize("as_text", [False, True])
async def test_sdk_redacts_exact_keys_encoded_variants_and_nested_credentials(
    wb: Workbench, design: Template, as_text: bool
) -> None:
    key = 'synthetic-key-with-slash/"\\'
    encoded = json.dumps(key)[1:-1]
    body = {
        "detail": {"message": "Rejected " + key, "error_type": "invalid_input"},
        "echo": [key, quote(key, safe=""), encoded, json.dumps(encoded)[1:-1]],
        "nested": {"Authorization": "Basic another-secret", "api_key": "another-secret"},
        "other": "Bearer synthetic-token; sk-test-hidden",
        "safe": "Preserve this explanation and all 6000 characters: " + "x" * 6000,
    }

    def respond(request: httpx2.Request) -> httpx2.Response:
        if as_text:
            return httpx2.Response(
                400, text=json.dumps(body), headers={"x-typesafe-request-id": "safe-request"}
            )
        return httpx2.Response(
            400,
            json=body,
            headers={"x-typesafe-request-id": "safe-request"},
        )

    evaluator = SDKClient(key, Settings(max_retries=0), transport=httpx2.MockTransport(respond))
    with pytest.raises(JevError) as caught:
        await wb.run(design, "Synthetic state", evaluator=evaluator)
    saved = wb.storage.get(caught.value.run_id or "")
    rendered = json.dumps(saved.model_dump(), ensure_ascii=False)
    for secret in (key, quote(key, safe=""), "another-secret", "synthetic-token", "sk-test-hidden"):
        assert secret not in rendered
    assert "x" * 6000 in rendered and "[redacted]" in rendered
    assert saved.error and saved.error["details"]
    assert not any(
        secret in wb.storage.path.read_bytes() for secret in (key.encode(), b"another-secret")
    )


@pytest.mark.parametrize("body", ["Gateway could not reach inference.", ["diagnostic", 7], None])
async def test_nonobject_error_bodies_are_retained_in_details(
    wb: Workbench, design: Template, body: object
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if isinstance(body, str):
            return httpx2.Response(502, text=body)
        return httpx2.Response(502, json=body)

    evaluator = SDKClient(
        "offline-secret", Settings(max_retries=0), transport=httpx2.MockTransport(respond)
    )
    with pytest.raises(JevError) as caught:
        await wb.run(design, "Synthetic state", evaluator=evaluator)
    error = caught.value
    saved = wb.storage.get(error.run_id or "")
    assert saved.response is None
    assert error.details and error.details["response_body"] == body
    if isinstance(body, str):
        assert error.message == body
    else:
        assert "cause is unknown" in error.message


def test_field_locations_and_unknown_diagnostics_are_honest() -> None:
    body = {
        "detail": [{"loc": ["body", "questions", "urgency", "criteria"], "msg": "Too few levels"}]
    }
    error = translate_error(TypeSafeAPIError(422, body, httpx2.Headers()))
    assert error.message == "questions.urgency.criteria: Too few levels"
    assert error.details and error.details["response_body"] == body
    unknown = translate_error(RuntimeError("private exception contents"))
    assert "cause is unknown" in unknown.message
    assert unknown.details == {"exception_type": "RuntimeError"}
    assert "private exception contents" not in json.dumps(unknown.as_dict())


@pytest.mark.parametrize(
    ("failure", "code", "phrase"),
    [
        (TypeSafeAPIConnectionError("private request headers"), "connection", "network connection"),
        (TypeSafeAPITimeoutError(10), "timeout", "time limit"),
        (TimeoutError("private timer message"), "timeout", "time limit"),
    ],
)
def test_network_errors_keep_distinct_safe_explanation(
    failure: Exception, code: str, phrase: str
) -> None:
    error = translate_error(failure)
    assert error.code == code and phrase in error.message
    assert error.retryable and error.http_status is None
    assert "private" not in json.dumps(error.as_dict())


def test_legacy_error_envelope_does_not_gain_null_optional_keys() -> None:
    error = JevError("missing_key", "No key found.", "Use jev config.", 3)
    assert set(error.as_dict()) == {"code", "message", "fix", "retryable", "request_id", "run_id"}
    assert JevError.from_dict(error.as_dict()).as_dict() == error.as_dict()


@pytest.mark.parametrize(
    ("mutation", "path", "reason"),
    [
        (lambda body: body["answers"].pop("impact"), "answers", "Missing answer(s): impact"),
        (
            lambda body: body["answers"].update(extra={"type": "noul", "noul": 0.5}),
            "answers",
            "not requested",
        ),
        (
            lambda body: body["answers"].update(route={"type": "noul", "noul": 0.5}),
            "answers.route.type",
            "Expected a choice",
        ),
        (
            lambda body: body["answers"]["route"].update(confidence=1.5),
            "answers.route.confidence",
            "Confidence must",
        ),
        (
            lambda body: body["answers"]["refund_requested"].update(noul=1.5),
            "answers.refund_requested.noul",
            "yes probability must",
        ),
        (
            lambda body: body["answers"]["route"].update(probabilities={}),
            "answers.route.probabilities",
            "At least one",
        ),
        (
            lambda body: body["answers"]["route"]["probabilities"].update(billing=-0.3),
            "answers.route.probabilities",
            "Each probability",
        ),
        (
            lambda body: body["answers"]["route"]["probabilities"].update(billing=0.1),
            "answers.route.probabilities",
            "add up to 1",
        ),
        (
            lambda body: body["answers"]["route"].update(probabilities={"billing": 1.0}),
            "answers.route.probabilities",
            "Option names",
        ),
        (
            lambda body: body["answers"]["route"].update(choice="unknown"),
            "answers.route.choice",
            "not one of",
        ),
        (
            lambda body: body["answers"]["route"].update(choice="technical"),
            "answers.route.choice",
            "highest probability",
        ),
        (
            lambda body: body["answers"]["impact"].update(probabilities={"0": 1.0}),
            "answers.impact.probabilities",
            "Level indices",
        ),
        (
            lambda body: body["answers"]["impact"]["legend"].pop("2"),
            "answers.impact.legend",
            "every probability level",
        ),
        (
            lambda body: body["answers"]["impact"].update(score=3.5),
            "answers.impact.score",
            "level range",
        ),
        (
            lambda body: body["usage"].update(input_tokens=-3),
            "usage.input_tokens",
            "cannot be negative",
        ),
    ],
)
async def test_semantic_failures_preserve_specific_reason_and_response(
    wb: Workbench,
    design: Template,
    mutation: Callable[[dict[str, Any]], None],
    path: str,
    reason: str,
) -> None:
    body = copy.deepcopy(RESPONSE)
    mutation(body)
    with pytest.raises(JevError) as caught:
        await wb.run(design, "Synthetic state", evaluator=MockEvaluator(body))
    error = caught.value
    assert error.code == "invalid_response" and path in error.message and reason in error.message
    assert error.http_status == 200 and error.request_id == "test-request"
    assert error.details and error.details["field_path"] == path
    assert error.details["response_body"] == body
    saved = wb.storage.get(error.run_id or "")
    assert saved.response == body and saved.error == error.as_dict()
    if path == "usage.input_tokens":
        assert saved.input_tokens is None and saved.cost_nanousd is None


async def test_sdk_missing_response_field_retains_field_path_and_redacted_body(
    wb: Workbench, design: Template
) -> None:
    body = copy.deepcopy(RESPONSE)
    body["answers"]["route"].pop("confidence")
    body["future_metadata"] = {"echo": "offline-secret", "safe": "Useful diagnostic"}
    with pytest.raises(JevError) as caught:
        await wb.run(design, "Synthetic state", evaluator=MockEvaluator(body))
    error = caught.value
    assert "answers.route.confidence" in error.message
    assert error.http_status == 200 and error.request_id == "test-request"
    assert error.details and error.details["field_path"] == "answers.route.confidence"
    saved = wb.storage.get(error.run_id or "")
    assert saved.response and saved.response["future_metadata"] == {
        "echo": "[redacted]",
        "safe": "Useful diagnostic",
    }
    assert "offline-secret" not in json.dumps(saved.model_dump())


def test_billing_option_validation_is_not_misclassified_as_account_quota() -> None:
    error = translate_error(
        TypeSafeAPIError(
            400,
            {"message": "Choice route billing option has an invalid description."},
            httpx2.Headers(),
        )
    )
    assert error.code == "bad_request"
