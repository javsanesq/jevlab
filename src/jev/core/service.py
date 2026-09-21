"""Shared run lifecycle, used identically by CLI and TUI."""

import asyncio
import json
import sqlite3
from pathlib import Path
from time import monotonic, perf_counter
from typing import cast
from uuid import uuid4

from typesafe_sdk import JSONContent
from typesafe_sdk import __version__ as sdk_version

from jev.core.client import Evaluator, SDKClient, raw_error_body, translate_error, verify_response
from jev.core.config import load_settings
from jev.core.credentials import Credentials
from jev.core.errors import JevError
from jev.core.models import Run, Settings, Template
from jev.core.pricing import price
from jev.core.storage import Storage, now
from jev.core.templates import Templates, dump_template, parse_template, revision_hash
from jev.core.thresholds import route


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


class Workbench:
    def __init__(self, root: Path, *, seed: bool = True, maintain: bool = True) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.settings = load_settings(root)
        self.templates = Templates(root / "templates")
        if seed:
            self.templates.seed()
        self.storage = Storage(root / "jev.db")
        self.last_maintenance = 0.0
        self.maintenance_error: str | None = None
        if maintain:
            self.maintain_history(force=True)

    def maintain_history(self, *, force: bool = False, protect: set[str] | None = None) -> None:
        """Retention failures must never turn a successful inference into a retry."""
        from jev.core.retention import cleanup

        if not force and monotonic() - self.last_maintenance < 60:
            return
        self.last_maintenance = monotonic()
        try:
            cleanup(self.storage, self.settings, dry_run=False, protect_run_ids=protect)
            self.maintenance_error = None
        except (OSError, ValueError, RuntimeError, sqlite3.Error, JevError):
            self.maintenance_error = "History maintenance could not finish; run jev clean."

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
            if evaluator is None:
                credentials = Credentials(self.settings.credential_mode)
                key = await asyncio.to_thread(credentials.require)
                evaluator = SDKClient(key, self.settings)
            started = perf_counter()
            async with asyncio.timeout(self.settings.deadline_seconds):
                evaluation = await evaluator.evaluate(template, state)
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
                "message": "Cancelled locally; remote completion and billing may be unknown.",
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
        from jev.core.config import save_settings

        save_settings(self.root, settings)
        self.settings = settings
