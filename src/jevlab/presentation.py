"""Plain-language error presentation shared by CLI and TUI, outside core."""

import json

from jevlab.core.diagnostics import redact_body, redact_text
from jevlab.core.errors import JevError
from jevlab.core.models import Run


def error_for_run(run: Run) -> JevError | None:
    """Restore diagnostics without losing identifiers stored on older run records."""
    if not run.error:
        return None
    error = JevError.from_dict(run.error)
    error.run_id = error.run_id or run.id
    error.request_id = error.request_id or run.request_id
    if run.response is not None:
        error.details = dict(error.details or {})
        error.details.setdefault("response_body", run.response)
    return error


def human_error(error: JevError, *, verbose: bool = False) -> str:
    """Show the cause immediately; keep redacted provider bodies in technical details."""
    if error.code == "invalid_response" or (
        error.http_status is not None and 200 <= error.http_status < 300
    ):
        happened = "The online service returned an unusable response."
    elif error.http_status is not None and error.http_status >= 500:
        happened = "The online service failed while processing the request."
    elif error.http_status:
        happened = "The online service rejected the request."
    elif error.code in {"internal_error", "execution_error"}:
        happened = "An unexpected problem stopped this action; its cause is not known."
    elif error.code in {"timeout", "connection"}:
        happened = "The online request could not complete."
    elif error.code == "client_configuration":
        happened = "The request could not start because the client settings are invalid."
    else:
        happened = None
    # A specific cause gets the full what/why/next shape; a local error is said once.
    text = (
        f"What happened: {happened}\nWhy: {redact_text(error.message)}\n"
        if happened
        else f"Error: {redact_text(error.message)}\n"
    ) + f"Next: {redact_text(error.fix)}"
    if error.http_status:
        text += f"\nHTTP status: {error.http_status}"
    if error.request_id:
        text += f"\nRequest ID: {redact_text(error.request_id)}"
    if error.run_id:
        text += f"\nSaved run: {redact_text(error.run_id)}"
    if verbose:
        text += f"\nDetails: code={redact_text(error.code)}; retryable={error.retryable}"
        if error.provider_code:
            text += f"; provider_code={redact_text(error.provider_code)}"
        if error.details:
            text += "\nTechnical detail (credentials redacted):\n" + redact_text(
                json.dumps(redact_body(error.details), indent=2, ensure_ascii=False)
            )
    return text
