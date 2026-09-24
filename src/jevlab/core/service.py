"""Shared run lifecycle, used identically by CLI and TUI."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from time import monotonic, perf_counter
from typing import cast
from uuid import uuid4

from typesafe_sdk import JSONContent
from typesafe_sdk import __version__ as sdk_version

from jevlab.core.client import (
    Evaluation,
    Evaluator,
    SDKClient,
    raw_error_body,
    translate_error,
    verify_response,
)
from jevlab.core.config import database_path, load_settings, profile_notice
from jevlab.core.credentials import Credentials, require_credentials
from jevlab.core.errors import JevError
from jevlab.core.models import Run, Settings, Template
from jevlab.core.pricing import price
from jevlab.core.storage import Storage, now
from jevlab.core.templates import Templates, dump_template, parse_template, revision_hash
from jevlab.core.thresholds import route


def parse_state(text: str, format: str) -> JSONContent:
    try:
        value = json.loads(text) if format == "json" else text
        if not isinstance(value, (str, list, dict)) or (
            isinstance(value, str) and not value.strip()
        ):
            raise ValueError("invalid state")
        json.dumps(value, allow_nan=False)
        return cast(JSONContent, value)
    except (ValueError, RecursionError):
        raise JevError(
            "invalid_state",
            "State must be nonempty text or valid JSON text/object/array.",
            "Correct the state or select the matching input format.",
        ) from None


class SharedEvaluator:
    """One credential lookup and one pooled SDK client shared by many calls.

    Jobs and the local server reuse connections instead of repeating a key lookup and
    TLS handshake per call. Workbench.run connects before timing a call, so a slow key
    lookup is reported as a credential problem with no request sent.
    """

    def __init__(self, settings: Settings, *, remember_failure: bool = True) -> None:
        self.settings = settings
        self.remember_failure = remember_failure
        self._client: SDKClient | None = None
        self._failure: JevError | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> SDKClient:
        """Resolve the key once (bounded like a single run) and return the pooled client."""
        async with self._lock:
            if self._client is not None:
                return self._client
            if self._failure is not None:
                # Each run receives its own copy; callers attach their run ID to it.
                raise replace(self._failure)
            try:
                key = await require_credentials(
                    Credentials(self.settings.credential_mode),
                    timeout_seconds=min(5.0, self.settings.deadline_seconds),
                )
                self._client = SDKClient(key, self.settings)
            except JevError as error:
                if self.remember_failure:
                    self._failure = replace(error)
                raise
            return self._client

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        return await (await self.connect()).evaluate(template, state)

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()


class Workbench:
    def __init__(self, root: Path, *, seed: bool = True, maintain: bool = True) -> None:
        self.root = root
        self.profile_notice = profile_notice(root)
        history = database_path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.settings = load_settings(root)
        self.templates = Templates(root / "templates")
        if seed:
            self.templates.seed()
        self.storage = Storage(history)
        self.last_maintenance = 0.0
        self.maintenance_error: str | None = None
        if maintain:
            self.maintain_history(force=True)

    def maintain_history(self, *, force: bool = False, protect: set[str] | None = None) -> None:
        """Retention failures must never turn a successful inference into a retry."""
        from jevlab.core.retention import cleanup

        if not force and monotonic() - self.last_maintenance < 60:
            return
        self.last_maintenance = monotonic()
        try:
            cleanup(self.storage, self.settings, dry_run=False, protect_run_ids=protect)
            self.maintenance_error = None
        except (OSError, ValueError, RuntimeError, sqlite3.Error, JevError):
            self.maintenance_error = "History maintenance could not finish; run jevlab clean."

    async def run(
        self,
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        template = parse_template(dump_template(template))
        # Validate state even for callers using the Python service directly.
        state = parse_state(json.dumps(state, allow_nan=False), "json")
        run = Run(
            id=run_id or str(uuid4()),
            template_hash=revision_hash(template),
            template_name=template.name,
            parent_run_id=parent_run_id,
            started_at=now(),
            requested_model=template.model,
            request={
                "state": state,
                "model": template.model,
                "questions": {k: q.model_dump(mode="json") for k, q in template.questions.items()},
            },
            sdk_version=sdk_version,
        )
        self.storage.create_run(run, template)
        started: float | None = None
        try:
            deadline = asyncio.get_running_loop().time() + self.settings.deadline_seconds
            owned: SDKClient | None = None
            if isinstance(evaluator, SharedEvaluator):
                evaluator = await evaluator.connect()
            elif evaluator is None:
                credentials = Credentials(self.settings.credential_mode)
                key = await require_credentials(
                    credentials, timeout_seconds=min(5.0, self.settings.deadline_seconds)
                )
                evaluator = owned = SDKClient(key, self.settings)
            started = perf_counter()
            try:
                async with asyncio.timeout_at(deadline):
                    evaluation = await evaluator.evaluate(template, state)
            finally:
                if owned is not None:
                    await owned.aclose()
            response = evaluation.response
            run.response = evaluation.raw
            run.request_id = evaluation.request_id
            run.resolved_model = response.model
            run.input_tokens, run.output_tokens = (
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            run.cost_nanousd, run.price_snapshot = price(response.model, run.input_tokens)
            if run.input_tokens is not None and run.input_tokens < 0:
                run.input_tokens, run.cost_nanousd = None, None
            if run.output_tokens is not None and run.output_tokens < 0:
                run.output_tokens = None
            verify_response(template, response)
            run.routing = route(template, response)
            run.status = "succeeded"
        except asyncio.CancelledError:
            run.status = "interrupted"
            run.error = {
                "code": "interrupted",
                "message": (
                    "Cancelled locally; remote completion and billing may be unknown."
                    if started is not None
                    else "Cancelled while reading credentials. No API request was sent."
                ),
            }
            raise
        except Exception as error:
            safe = translate_error(error, model=template.model)
            safe.run_id = run.id
            if safe.request_id:
                run.request_id = safe.request_id
            elif run.request_id:
                safe.request_id = run.request_id
            if run.response is not None:
                # Semantic validation can fail after a valid HTTP response was decoded.
                safe.http_status = safe.http_status or 200
                safe.details = {**(safe.details or {}), "response_body": run.response}
            run.status = "failed"
            run.error = safe.as_dict()
            if run.response is None:
                run.response = raw_error_body(safe)
            raise safe from None
        finally:
            run.finished_at = now()
            run.latency_ms = round((perf_counter() - started) * 1000) if started else None
            self.storage.finish_run(run)
            self.maintain_history(protect={run.id})
        return run

    async def rerun(self, run_id: str, *, evaluator: Evaluator | None = None) -> Run:
        previous = self.storage.get(run_id)
        template = self.storage.template_for(previous)
        return await self.run(
            template,
            cast(JSONContent, previous.request["state"]),
            evaluator=evaluator,
            parent_run_id=previous.id,
        )

    def update_settings(self, settings: Settings) -> None:
        from jevlab.core.config import save_settings

        save_settings(self.root, settings)
        self.settings = settings
