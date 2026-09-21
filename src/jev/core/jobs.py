"""Durable bounded-concurrency batches and evaluations, independent of either UI."""

import asyncio
import builtins
import fcntl
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import Field, PrivateAttr

from jev.core.client import Evaluator
from jev.core.datasets import DatasetInfo, DatasetRow, inspect_dataset, iter_dataset
from jev.core.errors import JevError
from jev.core.evaluation import EvalReport, evaluate_runs
from jev.core.models import Gate, Run, StrictModel, Template
from jev.core.pricing import price
from jev.core.service import Workbench
from jev.core.storage import now
from jev.core.templates import context_estimate, dump_template, parse_template, revision_hash

JobKind = Literal["batch", "eval"]
ItemStatus = Literal["pending", "running", "succeeded", "failed", "unknown"]


class JobPlan(StrictModel):
    # In-memory consent binding, deliberately absent from persisted reports and
    # JSON output so existing scripts keep the same public schema.
    _approval_fingerprint: str | None = PrivateAttr(default=None)
    calls: int
    remaining_calls: int
    estimated_input_tokens: int
    estimated_cost_nanousd: int | None
    requires_confirmation: bool
    estimate_note: str = (
        "Characters/4 approximation for remaining calls; not a spending cap. "
        "Server overhead and SDK retries can increase cost. "
        "Rate limits apply to logical calls; SDK retry requests may add traffic."
    )


class JobItem(StrictModel):
    index: int
    case_id: str
    row_sha256: str | None = None
    status: ItemStatus = "pending"
    run_id: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    attempts: int = 0
    error: dict[str, object] | None = None


class JobReport(StrictModel):
    id: str
    kind: JobKind
    status: Literal["pending", "running", "completed", "failed", "interrupted"] = "pending"
    template_name: str
    template_hash: str
    dataset: DatasetInfo
    plan: JobPlan
    started_at: str
    finished_at: str | None = None
    output_path: str | None = None
    output_sha256: str | None = None
    total: int
    succeeded: int = 0
    failed: int = 0
    unknown: int = 0
    pending: int = 0
    known_cost_nanousd: int = 0
    unknown_cost_runs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    evaluation: EvalReport | None = None
    error: dict[str, object] | None = None


class _RateLimiter:
    """Evenly spaced logical call starts, with no initial burst."""

    def __init__(self, rate: float) -> None:
        self.interval = 1 / rate
        self.next_start = 0.0
        self.lock = asyncio.Lock()

    async def wait(self, stop: asyncio.Event) -> bool:
        async with self.lock:
            if stop.is_set():
                return False
            loop = asyncio.get_running_loop()
            delay = max(0, self.next_start - loop.time())
            if delay:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    return False
                except TimeoutError:
                    pass
            else:
                await asyncio.sleep(0)
            if stop.is_set():
                return False
            self.next_start = loop.time() + self.interval
            return True


@contextmanager
def _job_lock(root: Path, job_id: str) -> Iterator[None]:
    directory = root / "locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"job-{job_id}.lock"
    with path.open("a") as lock:
        path.chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JevError(
                "job_busy",
                "This job is already running in another process.",
                "Wait for it to finish or cancel that process before resuming.",
            ) from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _row_digest(row: DatasetRow) -> str:
    canonical = json.dumps(row.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class BatchService:
    def __init__(self, wb: Workbench) -> None:
        self.wb = wb

    def get(self, job_id: str) -> JobReport:
        return JobReport.model_validate(self.wb.storage.job(job_id))

    def list(self, kind: str = "") -> list[JobReport]:
        return [JobReport.model_validate(value) for value in self.wb.storage.jobs(kind)]

    def rows(self, job_id: str) -> Iterator[JobItem]:
        report = self.get(job_id)
        for value in self.wb.storage.job_items(report.id):
            yield JobItem.model_validate(value)

    def template(self, job_id: str) -> Template:
        return self.wb.storage.job_template(job_id)

    def register_dataset(
        self, path: Path, template: Template, *, require_labels: bool = False
    ) -> DatasetInfo:
        info = inspect_dataset(path, template, require_labels=require_labels)
        self.wb.storage.register_dataset(info.model_dump())
        return info

    def datasets(self) -> builtins.list[DatasetInfo]:
        return [DatasetInfo.model_validate(value) for value in self.wb.storage.datasets()]

    def save_thresholds(self, job_id: str, gates: dict[str, Gate]) -> Template:
        saved = self.get(job_id)
        if saved.kind != "eval" or saved.evaluation is None:
            raise JevError(
                "not_evaluated", "This job has no evaluation.", "Complete an eval first."
            )
        reference = self.template(saved.id)
        current = self.wb.templates.load(reference.name)
        if current.model_dump(exclude={"thresholds"}) != reference.model_dump(
            exclude={"thresholds"}
        ):
            raise JevError(
                "template_changed",
                "The template design has changed since this eval.",
                "Evaluate the current design before saving thresholds.",
            )
        data = current.model_dump(mode="json")
        data["thresholds"] = {
            **{name: value.model_dump(mode="json") for name, value in current.thresholds.items()},
            **{name: value.model_dump(mode="json") for name, value in gates.items()},
        }
        candidate = Template.model_validate(data)
        self.wb.templates.save(candidate, overwrite=True)
        return candidate

    def _effective_status(self, item: JobItem) -> ItemStatus:
        if item.status != "running":
            return item.status
        if item.run_id:
            try:
                run = self.wb.storage.get(item.run_id)
            except JevError:
                return "unknown"
            if run.status in ("succeeded", "failed"):
                return run.status
        return "unknown"

    @staticmethod
    def _selected(status: ItemStatus, retry_failed: bool, retry_unknown: bool) -> bool:
        return (
            status == "pending"
            or (retry_failed and status == "failed")
            or (retry_unknown and status == "unknown")
        )

    def _prepare(
        self,
        template: Template,
        path: Path,
        kind: JobKind,
        resume_id: str | None,
        retry_failed: bool,
        retry_unknown: bool,
    ) -> tuple[Template, DatasetInfo, JobReport | None, dict[int, JobItem], JobPlan]:
        template = parse_template(dump_template(template))
        if not resume_id and (retry_failed or retry_unknown):
            raise JevError(
                "invalid_retry", "Retry flags require a saved job.", "Supply a resume ID."
            )
        if kind not in ("batch", "eval"):
            raise JevError("invalid_job", "Invalid job kind.", "Use batch or eval.")
        previous = self.get(resume_id) if resume_id else None
        if previous:
            reference = self.template(previous.id)
            if previous.kind != kind or revision_hash(template) != previous.template_hash:
                raise JevError(
                    "job_design_changed",
                    "The requested design differs from this job.",
                    "Resume using the job's saved immutable template snapshot.",
                )
            template = reference
        info = inspect_dataset(path, template, require_labels=kind == "eval")
        if previous and (
            info.path != previous.dataset.path or info.sha256 != previous.dataset.sha256
        ):
            raise JevError(
                "dataset_changed",
                "The dataset path or content changed since this job began.",
                "Restore the original dataset, or start a new job.",
            )
        items = {item.index: item for item in self.rows(previous.id)} if previous else {}
        tokens = remaining = 0
        eligible: list[tuple[int, str, ItemStatus, int, str | None]] = []
        for row in iter_dataset(path, template, require_labels=kind == "eval"):
            status = self._effective_status(items[row.index]) if row.index in items else "pending"
            if self._selected(status, retry_failed, retry_unknown):
                remaining += 1
                tokens += cast(int, context_estimate(template, row.state)["estimated_total_tokens"])
                item = items.get(row.index)
                eligible.append(
                    (
                        row.index,
                        row.id,
                        status,
                        item.attempts if item else 0,
                        item.run_id if item else None,
                    )
                )
        cost, _ = price(template.model, tokens)
        # A completed/no-op resume costs zero even if an alias has no known published price.
        if remaining == 0:
            cost = 0
        plan = JobPlan(
            calls=info.rows,
            remaining_calls=remaining,
            estimated_input_tokens=tokens,
            estimated_cost_nanousd=cost,
            requires_confirmation=cost is None
            or cost / 1_000_000_000 > self.wb.settings.confirm_cost_usd,
        )
        binding = {
            "dataset": {"path": info.path, "sha256": info.sha256},
            "template": revision_hash(template),
            "kind": kind,
            "resume_id": previous.id if previous else None,
            "retry_failed": retry_failed,
            "retry_unknown": retry_unknown,
            "eligible": eligible,
            "tokens": tokens,
            "cost": cost,
            "settings": {
                field: getattr(self.wb.settings, field)
                for field in (
                    "credential_mode",
                    "max_retries",
                    "timeout_seconds",
                    "deadline_seconds",
                )
            },
        }
        plan._approval_fingerprint = hashlib.sha256(
            json.dumps(binding, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return template, info, previous, items, plan

    @staticmethod
    def _verify_approval(expected: JobPlan | None, actual: JobPlan, info: DatasetInfo) -> None:
        if expected is None:
            return
        if (
            expected._approval_fingerprint is None
            or expected._approval_fingerprint != actual._approval_fingerprint
            or _digest(Path(info.path)) != info.sha256
        ):
            raise JevError(
                "cost_plan_changed",
                "The data, design, remaining requests, or request settings changed "
                "after the price was shown. No new request was sent.",
                "Check the current inputs and start again to review and confirm a fresh estimate.",
            )

    def plan(
        self,
        template: Template,
        path: Path,
        *,
        kind: JobKind = "batch",
        resume_id: str | None = None,
        retry_failed: bool = False,
        retry_unknown: bool = False,
    ) -> JobPlan:
        return self._prepare(template, path, kind, resume_id, retry_failed, retry_unknown)[-1]

    def _save(self, report: JobReport, template: Template) -> None:
        self.wb.storage.save_job(report.model_dump(), template)

    def _item_save(self, job_id: str, item: JobItem) -> None:
        self.wb.storage.save_job_item(job_id, item.model_dump())

    def _reserve_output(self, report: JobReport, output: Path | None) -> None:
        if output is None:
            return
        path = output.expanduser().absolute()
        if path.is_symlink():
            raise JevError(
                "output_exists", "Output is a symlink.", "Choose a new JSONL output path."
            )
        path = path.resolve()
        if report.output_path == str(path):
            self._verify_output(report)
            return
        if path.exists():
            raise JevError(
                "output_exists", "Output already exists.", "Choose a new JSONL output path."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8"):
                pass
            path.chmod(0o600)
        except FileExistsError:
            raise JevError(
                "output_exists", "Output already exists.", "Choose a new JSONL output path."
            ) from None
        report.output_path, report.output_sha256 = str(path), _digest(path)

    def _verify_output(self, report: JobReport) -> None:
        if report.output_path:
            path = Path(report.output_path)
            if path.is_symlink() or (path.exists() and _digest(path) != report.output_sha256):
                raise JevError(
                    "output_changed",
                    "The saved output has been modified outside this job.",
                    "Preserve that file and choose a new output path to resume safely.",
                )

    @staticmethod
    def _verify_row(row: DatasetRow, item: JobItem, run: Run | None) -> None:
        # Fingerprints alone miss edit/undo while a streaming job consumes the file.
        # The actual API input is the durable evidence for which state was judged.
        if (
            item.case_id != row.id
            or (item.row_sha256 is not None and item.row_sha256 != _row_digest(row))
            or (run is not None and run.request.get("state") != row.state)
        ):
            raise JevError(
                "dataset_changed",
                "A saved run does not match the dataset row being evaluated.",
                "Restore the exact original states and row IDs; do not grade changed inputs.",
            )

    def _export(self, report: JobReport, template: Template) -> None:
        if not report.output_path:
            return
        self._verify_output(report)
        destination = Path(report.output_path)
        descriptor, temporary = tempfile.mkstemp(prefix=".jev-output-", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                source_rows = iter_dataset(
                    Path(report.dataset.path), template, require_labels=report.kind == "eval"
                )
                for item, row in zip(self.rows(report.id), source_rows, strict=True):
                    run: Run | None = None
                    if item.run_id:
                        try:
                            run = self.wb.storage.get(item.run_id)
                        except JevError:
                            pass
                    self._verify_row(row, item, run)
                    payload = {
                        "job_id": report.id,
                        "dataset_sha256": report.dataset.sha256,
                        "template_hash": report.template_hash,
                        "case": {"id": row.id, "index": row.index, "expected": row.expected},
                        "item": item.model_dump(mode="json"),
                        "run": run.model_dump(mode="json") if run else None,
                    }
                    stream.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._verify_output(report)
            if _digest(Path(report.dataset.path)) != report.dataset.sha256:
                raise JevError(
                    "dataset_changed",
                    "The dataset changed during export.",
                    "Restore the original file before rebuilding the saved output.",
                )
            os.replace(temporary, destination)
            report.output_sha256 = _digest(destination)
            self._save(report, template)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _summarize(self, report: JobReport, items: dict[int, JobItem]) -> None:
        report.succeeded = sum(item.status == "succeeded" for item in items.values())
        report.failed = sum(item.status == "failed" for item in items.values())
        report.unknown = sum(item.status in ("running", "unknown") for item in items.values())
        report.pending = report.total - report.succeeded - report.failed - report.unknown
        report.known_cost_nanousd = report.unknown_cost_runs = 0
        report.input_tokens = report.output_tokens = report.latency_ms = 0
        for item in items.values():
            for run_id in item.run_ids:
                try:
                    run = self.wb.storage.get(run_id)
                except JevError:
                    report.unknown_cost_runs += 1
                    continue
                report.known_cost_nanousd += run.cost_nanousd or 0
                report.unknown_cost_runs += run.cost_nanousd is None
                report.input_tokens += run.input_tokens or 0
                report.output_tokens += run.output_tokens or 0
                report.latency_ms += run.latency_ms or 0

    def _evaluation_totals(self, report: JobReport, items: dict[int, JobItem]) -> None:
        """Outcome metrics use the latest row answer; billing includes every attempted call."""
        evaluation = report.evaluation
        assert evaluation is not None
        evaluation.known_cost_nanousd = report.known_cost_nanousd
        evaluation.unknown_cost_runs = report.unknown_cost_runs
        evaluation.input_tokens = report.input_tokens
        evaluation.output_tokens = report.output_tokens
        evaluation.latency_total_ms = report.latency_ms
        latencies: list[int] = []
        for item in items.values():
            for run_id in item.run_ids:
                try:
                    latency = self.wb.storage.get(run_id).latency_ms
                except JevError:
                    continue
                if latency is not None:
                    latencies.append(latency)
        if latencies:
            latencies.sort()
            evaluation.latency_mean_ms = sum(latencies) / len(latencies)
            for fraction, name in ((0.5, "latency_p50_ms"), (0.95, "latency_p95_ms")):
                position = (len(latencies) - 1) * fraction
                low, high = math.floor(position), math.ceil(position)
                value = latencies[low] + (latencies[high] - latencies[low]) * (position - low)
                setattr(evaluation, name, value)

    async def run(
        self,
        template: Template,
        path: Path,
        *,
        kind: JobKind = "batch",
        output: Path | None = None,
        concurrency: int = 4,
        requests_per_second: float = 2.0,
        authorize_cost: bool = False,
        expected_plan: JobPlan | None = None,
        evaluator: Evaluator | None = None,
        progress: Callable[[int, int], None] | None = None,
        resume_id: str | None = None,
        retry_failed: bool = False,
        retry_unknown: bool = False,
    ) -> JobReport:
        if (
            not 1 <= concurrency <= 32
            or not math.isfinite(requests_per_second)
            or not 0 < requests_per_second <= 1000
        ):
            raise JevError(
                "invalid_rate",
                "Invalid concurrency or rate.",
                "Use 1–32 workers and 0 < requests/second <= 1000.",
            )
        prepared = self._prepare(template, path, kind, resume_id, retry_failed, retry_unknown)
        template, info, previous, items, plan = prepared
        self._verify_approval(expected_plan, plan, info)
        if plan.requires_confirmation and not authorize_cost:
            raise JevError(
                "cost_confirmation",
                "Job cost needs confirmation.",
                "Review the estimate and explicitly authorize this run.",
            )
        report = previous or JobReport(
            id=str(uuid4()),
            kind=kind,
            template_name=template.name,
            template_hash=revision_hash(template),
            dataset=info,
            plan=JobPlan.model_validate(plan.model_dump()),
            started_at=now(),
            total=info.rows,
            pending=info.rows,
        )
        with _job_lock(self.wb.root, report.id):
            # Re-read after acquiring the lock: another process may have completed during preflight.
            if previous:
                template, info, previous, items, plan = self._prepare(
                    template,
                    path,
                    kind,
                    report.id,
                    retry_failed,
                    retry_unknown,
                )
                assert previous is not None
                report = previous
                self._verify_approval(expected_plan, plan, info)
                if plan.requires_confirmation and not authorize_cost:
                    raise JevError(
                        "cost_confirmation",
                        "Job cost needs confirmation.",
                        "Review the updated estimate and explicitly authorize this run.",
                    )
            self._reserve_output(report, output)
            self._verify_output(report)
            report.plan, report.status, report.finished_at, report.error = (
                JobPlan.model_validate(plan.model_dump()),
                "running",
                None,
                None,
            )
            report.evaluation = None
            self._save(report, template)
            self.wb.storage.register_dataset(info.model_dump())
            for row in iter_dataset(path, template, require_labels=kind == "eval"):
                item = items.setdefault(
                    row.index, JobItem(index=row.index, case_id=row.id, row_sha256=_row_digest(row))
                )
                item.status = self._effective_status(item)
            self.wb.storage.save_job_items(
                report.id, (item.model_dump() for item in items.values())
            )
            iterator = iter_dataset(path, template, require_labels=kind == "eval")
            limiter, stop = _RateLimiter(requests_per_second), asyncio.Event()
            done = report.total - plan.remaining_calls

            async def worker() -> None:
                nonlocal done
                while not stop.is_set():
                    try:
                        row = next(iterator)
                    except StopIteration:
                        return
                    item = items[row.index]
                    if not self._selected(item.status, retry_failed, retry_unknown):
                        continue
                    self._verify_row(row, item, None)
                    if not await limiter.wait(stop):
                        return
                    item.status, item.error = "running", None
                    item.run_id = str(uuid4())
                    item.run_ids.append(item.run_id)
                    item.attempts += 1
                    self._item_save(report.id, item)
                    try:
                        await self.wb.run(
                            template, row.state, evaluator=evaluator, run_id=item.run_id
                        )
                        item.status = "succeeded"
                    except asyncio.CancelledError:
                        item.status = "unknown"
                        item.error = {
                            "code": "interrupted",
                            "message": (
                                "Remote completion and billing may be unknown; "
                                "retry requires explicit authorization."
                            ),
                        }
                        raise
                    except JevError as error:
                        item.status, item.error = "failed", error.as_dict()
                        if error.code in {
                            "authentication",
                            "permission",
                            "missing_key",
                            "keychain_unavailable",
                            "model_not_found",
                            "quota",
                            "not_found",
                        }:
                            report.error = error.as_dict()
                            stop.set()
                    finally:
                        self._item_save(report.id, item)
                    done += 1
                    if progress:
                        progress(done, report.total)

            tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
            try:
                await asyncio.gather(*tasks)
                report.status = (
                    "completed"
                    if all(item.status == "succeeded" for item in items.values())
                    else "failed"
                )
            except asyncio.CancelledError:
                report.status = "interrupted"
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            except JevError as error:
                report.status, report.error = "failed", error.as_dict()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                report.status = "failed"
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            finally:
                close = getattr(iterator, "close", None)
                if callable(close):
                    close()
                report.finished_at = now()
                self._summarize(report, items)
                # Save the durable outcome before secondary analysis or output can fail.
                self._save(report, template)
                try:
                    if report.error and report.error.get("code") == "dataset_changed":
                        raise JevError(
                            "dataset_changed",
                            "A dataset row changed during processing.",
                            "Restore the original file and start a new job with stable inputs.",
                        )
                    current = inspect_dataset(path, template, require_labels=kind == "eval")
                    if current.sha256 != info.sha256:
                        raise JevError(
                            "dataset_changed",
                            "The dataset changed while the job was running.",
                            "Restore the original file before resuming; saved runs are preserved.",
                        )
                    pairs: list[tuple[DatasetRow, Run | None]] = []
                    for row in iter_dataset(path, template, require_labels=kind == "eval"):
                        item = items[row.index]
                        run = None
                        if item.run_id:
                            try:
                                run = self.wb.storage.get(item.run_id)
                            except JevError:
                                pass
                        self._verify_row(row, item, run)
                        if kind == "eval":
                            pairs.append((row, run))
                    if kind == "eval":
                        report.evaluation = evaluate_runs(template, pairs)
                        self._evaluation_totals(report, items)
                    self._export(report, template)
                except (JevError, OSError) as error:
                    if isinstance(error, JevError) and error.code == "dataset_changed":
                        report.evaluation = None
                    if report.status != "interrupted":
                        report.status = "failed"
                    report.error = (
                        error.as_dict()
                        if isinstance(error, JevError)
                        else {
                            "code": "output_error",
                            "message": "Could not write output; results remain saved in SQLite.",
                            "fix": (
                                "Check output permissions and resume to rebuild the file "
                                "without repeating successes."
                            ),
                        }
                    )
                self._save(report, template)
            return report
