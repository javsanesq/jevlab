"""A bounded, authenticated local API around saved Jev templates."""

import asyncio
import hmac
import json
import math
import os
import time
from collections import deque
from typing import cast

from pydantic import StrictBool, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typesafe_sdk import JSONContent

from jev import __version__
from jev.core.client import Evaluator
from jev.core.errors import JevError
from jev.core.models import StrictModel
from jev.core.pricing import price
from jev.core.service import Workbench, parse_state
from jev.core.templates import context_estimate

MAX_BODY_BYTES = 2_000_000
BODY_READ_TIMEOUT_SECONDS = 10.0


class RunRequest(StrictModel):
    state: JSONContent
    authorize_cost: StrictBool = False


def server_token() -> str:
    token = os.environ.get("JEV_SERVER_TOKEN", "")
    if (
        not 32 <= len(token) <= 512
        or not token.isascii()
        or not token.isprintable()
        or any(c.isspace() for c in token)
    ):
        raise JevError(
            "server_token_missing",
            "JEV_SERVER_TOKEN needs 32–512 printable ASCII characters without whitespace.",
            "Set a separate random local access token in your environment; see README setup.",
            3,
        )
    return token


def envelope(data: object, *, status: int = 200) -> JSONResponse:
    return JSONResponse({"schema_version": 1, "ok": True, "data": data}, status_code=status)


def failure(code: str, message: str, fix: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"schema_version": 1, "ok": False, "error": JevError(code, message, fix).as_dict()},
        status_code=status,
    )


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys")
        result[key] = value
    return result


class LocalAPI:
    def __init__(
        self,
        wb: Workbench,
        token: str,
        *,
        port: int,
        concurrency: int,
        requests_per_second: int,
        evaluator: Evaluator | None,
    ) -> None:
        if (
            not 32 <= len(token) <= 512
            or not token.isascii()
            or not token.isprintable()
            or any(c.isspace() for c in token)
        ):
            raise ValueError(
                "Use a 32–512 character printable ASCII server token without whitespace."
            )
        if (
            not 1 <= port <= 65535
            or not 1 <= concurrency <= 32
            or not 1 <= requests_per_second <= 100
        ):
            raise ValueError("Invalid server limits.")
        self.wb, self.token, self.evaluator = wb, token, evaluator
        self.concurrency, self.rate = concurrency, requests_per_second
        self.active = 0
        self.starts: deque[float] = deque()
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def guard(self, request: Request, *, authenticated: bool = True) -> Response | None:
        hosts = request.headers.getlist("host")
        if len(hosts) != 1 or hosts[0].lower() not in self.allowed_hosts:
            return failure("invalid_host", "Invalid local API host.", "Use its loopback URL.", 400)
        if "origin" in request.headers:
            return failure(
                "browser_origin",
                "Browser-origin requests are disabled.",
                "Call this API from your local application backend.",
                403,
            )
        if authenticated:
            headers = request.headers.getlist("authorization")
            expected = "Bearer " + self.token
            if len(headers) != 1 or not hmac.compare_digest(headers[0].encode(), expected.encode()):
                return failure(
                    "unauthorized",
                    "A local API bearer token is required.",
                    "Send Authorization: Bearer with your JEV_SERVER_TOKEN.",
                    401,
                )
        return None

    async def health(self, request: Request) -> Response:
        if blocked := self.guard(request, authenticated=False):
            return blocked
        return envelope({"status": "ok", "version": __version__, "inference": "not_checked"})

    async def templates(self, request: Request) -> Response:
        if blocked := self.guard(request):
            return blocked
        rows: list[dict[str, object]] = []
        for name in self.wb.templates.names():
            try:
                template = self.wb.templates.load(name)
                rows.append(
                    {
                        "name": name,
                        "model": template.model,
                        "description": template.description,
                        "questions": list(template.questions),
                        "valid": True,
                    }
                )
            except JevError:
                rows.append({"name": name, "valid": False})
        return envelope(rows)

    async def run(self, request: Request) -> Response:
        if blocked := self.guard(request):
            return blocked
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return failure(
                "content_type", "Send application/json.", "Set the Content-Type header.", 415
            )
        try:
            if len(request.headers.getlist("content-length")) > 1:
                raise ValueError("Duplicate Content-Length headers")
            size = int(request.headers.get("content-length", "0"))
            if size < 0 or size > MAX_BODY_BYTES:
                return failure(
                    "body_too_large", "Request body exceeds 2 MB.", "Trim the state.", 413
                )
            body = bytearray()
            async with asyncio.timeout(BODY_READ_TIMEOUT_SECONDS):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        return failure(
                            "body_too_large", "Request body exceeds 2 MB.", "Trim the state.", 413
                        )
                    body.extend(chunk)
            payload = RunRequest.model_validate(json.loads(body, object_pairs_hook=unique_object))
            state = parse_state(json.dumps(payload.state, allow_nan=False), "json")
            template = self.wb.templates.load(request.path_params["name"])
        except (ValueError, ValidationError, UnicodeError, RecursionError):
            return failure(
                "invalid_request",
                "Invalid JSON request or state.",
                "Send {state: text/object/array, authorize_cost?: boolean} with unique keys.",
                422,
            )
        except ClientDisconnect:
            return failure(
                "client_disconnect", "Request body was interrupted.", "Retry deliberately.", 400
            )
        except TimeoutError:
            return failure(
                "body_timeout",
                "The request body took too long to arrive.",
                "Send the complete JSON body within 10 seconds; no Jev call was started.",
                408,
            )
        except JevError as error:
            status = 404 if error.code == "not_found" else 422
            return JSONResponse(
                {"schema_version": 1, "ok": False, "error": error.as_dict()}, status_code=status
            )
        estimate = context_estimate(template, state)
        cost, _ = price(template.model, cast(int, estimate["estimated_total_tokens"]))
        if (
            cost is None or cost / 1e9 > self.wb.settings.confirm_cost_usd
        ) and not payload.authorize_cost:
            return JSONResponse(
                {
                    "schema_version": 1,
                    "ok": False,
                    "error": {
                        "code": "cost_confirmation",
                        "message": "This request needs cost authorization.",
                        "fix": "Review the estimate and set authorize_cost:true to permit it.",
                    },
                    "data": {"estimated_cost_nanousd": cost, "estimate": estimate},
                },
                status_code=409,
            )
        now = time.monotonic()
        while self.starts and self.starts[0] <= now - 1:
            self.starts.popleft()
        if len(self.starts) >= self.rate:
            response = failure(
                "local_rate_limit", "Local request limit reached.", "Wait before retrying.", 429
            )
            response.headers["Retry-After"] = str(max(1, math.ceil(self.starts[0] + 1 - now)))
            return response
        if self.active >= self.concurrency:
            return failure(
                "local_busy",
                "All local workers are busy.",
                "Retry after a running call finishes.",
                503,
            )
        self.starts.append(now)
        self.active += 1
        try:
            run = await self.wb.run(template, state, evaluator=self.evaluator)
            return envelope(run.model_dump(mode="json"))
        except JevError as error:
            status = 429 if error.code == "rate_limit" else 504 if error.code == "timeout" else 502
            return JSONResponse(
                {"schema_version": 1, "ok": False, "error": error.as_dict()}, status_code=status
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return failure(
                "local_error",
                "The request could not be completed.",
                "Check jev doctor and saved history before retrying.",
                500,
            )
        finally:
            self.active -= 1


class APIBoundary:
    """Guard every HTTP path and keep unexpected failures out of server tracebacks."""

    def __init__(self, app: ASGIApp, api: LocalAPI) -> None:
        self.app, self.api = app, api

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        is_health = scope["path"] == "/health" and scope["method"] in {"GET", "HEAD"}
        if blocked := self.api.guard(request, authenticated=not is_health):
            await blocked(scope, receive, send)
            return
        started = False

        async def record_start(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, record_start)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not stringify exceptions: upstream/file errors can contain request data.
            # If a disconnect happened during send, a second response is impossible.
            if not started:
                response = failure(
                    "local_error",
                    "The local request could not be completed.",
                    "Check jev doctor and saved history before retrying.",
                    500,
                )
                await response(scope, receive, send)


async def http_error(request: Request, error: Exception) -> Response:
    """Starlette routing errors use the same envelope as inference errors."""
    status = error.status_code if isinstance(error, HTTPException) else 500
    code, message = {
        404: ("not_found", "This local API route does not exist."),
        405: ("method_not_allowed", "This HTTP method is not supported for this route."),
    }.get(status, ("http_error", "The HTTP request could not be completed."))
    response = failure(code, message, "Check the local API routes and HTTP method.", status)
    if isinstance(error, HTTPException) and error.headers and "Allow" in error.headers:
        response.headers["Allow"] = error.headers["Allow"]
    return response


def create_app(
    wb: Workbench,
    token: str,
    *,
    port: int = 8766,
    concurrency: int = 4,
    requests_per_second: int = 2,
    evaluator: Evaluator | None = None,
) -> Starlette:
    api = LocalAPI(
        wb,
        token,
        port=port,
        concurrency=concurrency,
        requests_per_second=requests_per_second,
        evaluator=evaluator,
    )
    application = Starlette(
        routes=[
            Route("/health", api.health, methods=["GET"]),
            Route("/templates", api.templates, methods=["GET"]),
            Route("/templates/{name:str}/run", api.run, methods=["POST"]),
        ],
        middleware=[Middleware(APIBoundary, api=api)],
        exception_handlers={HTTPException: http_error},
    )
    application.router.redirect_slashes = False
    application.state.api = api
    return application
