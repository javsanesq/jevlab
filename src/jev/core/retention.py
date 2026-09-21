"""Age and physical-size retention for SQLite history, with auditable previews.

A job, a lesson attempt, and all referenced runs are one retention unit. Rerun
ancestry and shared runs also connect units. This preserves inspectable reports
without dangling references. Active units are exempt until they finish.
"""

import fcntl
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field

from jev.core.models import Settings, StrictModel
from jev.core.storage import Storage, maintenance_lock

Kind = Literal["run", "job", "learn_attempt", "template_revision"]
Key = tuple[str, str]


class CleanupCounts(StrictModel):
    runs: int = 0
    jobs: int = 0
    job_items: int = 0
    learn_attempts: int = 0
    template_revisions: int = 0


class CleanupTarget(StrictModel):
    kind: Kind
    id: str
    last_activity: str
    reason: Literal["age", "size", "unreferenced_revision"]


class CleanupPlan(StrictModel):
    cutoff: str
    limit_bytes: int
    bytes_before: int
    estimated_bytes_after: int
    delete: CleanupCounts = Field(default_factory=CleanupCounts)
    protected: CleanupCounts = Field(default_factory=CleanupCounts)
    protected_reasons: dict[str, int] = Field(default_factory=dict)
    targets: list[CleanupTarget] = Field(default_factory=list)
    estimate_note: str = (
        "Database and WAL only. Preview size is approximate; apply vacuums and checks actual size. "
        "Related reports/runs expire together using their newest activity. Active records, "
        "learning progress, dataset references and schema metadata are retained."
    )


class CleanupReport(StrictModel):
    dry_run: bool
    applied: bool = False
    skipped: str | None = None
    plan: CleanupPlan
    deleted: CleanupCounts = Field(default_factory=CleanupCounts)
    bytes_after: int
    reclaimed_bytes: int = 0
    within_budget: bool
    vacuumed: bool = False
    notes: list[str] = Field(default_factory=list)


@dataclass
class _Node:
    kind: Literal["run", "job", "learn_attempt"]
    id: str
    activity: datetime
    size: int
    references: list[str] = field(default_factory=list)
    protection: set[str] = field(default_factory=set)
    items: int = 0
    template_hash: str | None = None

    @property
    def key(self) -> Key:
        return (self.kind, self.id)


@dataclass
class _Group:
    nodes: list[_Node] = field(default_factory=list)

    @property
    def activity(self) -> datetime:
        return max(node.activity for node in self.nodes)

    @property
    def size(self) -> int:
        return sum(node.size for node in self.nodes)

    @property
    def protected(self) -> bool:
        return any(node.protection for node in self.nodes)


class _Components:
    """Union/find avoids repeated scans of large batch histories."""

    def __init__(self, nodes: list[_Node]) -> None:
        self.parent = {node.key: node.key for node in nodes}
        self.rank = dict.fromkeys(self.parent, 0)

    def find(self, key: Key) -> Key:
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def join(self, left: Key, right: Key) -> None:
        if right not in self.parent:
            return
        left, right = self.find(left), self.find(right)
        if left == right:
            return
        if self.rank[left] < self.rank[right]:
            left, right = right, left
        self.parent[right] = left
        if self.rank[left] == self.rank[right]:
            self.rank[left] += 1


def database_bytes(path: Path) -> int:
    """Physical history footprint; never follows dataset or output paths."""
    total = 0
    for candidate in (path, Path(f"{path}-wal")):
        try:
            total += candidate.stat().st_size
        except FileNotFoundError:
            pass
    return total


def _stamp(value: object, fallback: datetime) -> tuple[datetime, bool]:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            raise ValueError("naive timestamp")
        return parsed.astimezone(UTC), True
    except (ValueError, TypeError):
        return fallback, False


def _activity(started: object, finished: object, at: datetime) -> tuple[datetime, set[str]]:
    start, valid_start = _stamp(started, at)
    end, valid_end = _stamp(finished, at) if finished else (start, True)
    return max(start, end), set() if valid_start and valid_end else {"invalid_timestamp"}


def _job_locked(root: Path, job_id: str) -> bool:
    # IDs come from this application's UUIDs, but avoid trusting edited database paths.
    if Path(job_id).name != job_id or job_id in {".", ".."}:
        return True
    path = root / "locks" / f"job-{job_id}.lock"
    try:
        with path.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except FileNotFoundError:
        return False
    except OSError:
        # If a lock cannot be inspected, prefer keeping the job.
        return True
    return False


def _row_size(columns: str) -> str:
    return " + ".join(f"coalesce(length(CAST({name} AS BLOB)),0)" for name in columns.split())


def _nodes(
    connection: sqlite3.Connection, root: Path, at: datetime, protect_run_ids: set[str]
) -> list[_Node]:
    nodes: list[_Node] = []
    run_size = _row_size(
        "id template_hash template_name parent_run_id started_at finished_at requested_model "
        "resolved_model request_json response_json routing_json price_snapshot_json "
        "sdk_version request_id error_json"
    )
    for row in connection.execute(
        "SELECT id, parent_run_id, started_at, finished_at, status, template_hash, "
        f"({run_size}) + 256 AS size FROM runs"
    ):
        activity, protection = _activity(row["started_at"], row["finished_at"], at)
        if row["status"] == "pending":
            protection.add("pending_run")
        if row["id"] in protect_run_ids:
            protection.add("current_run")
        nodes.append(
            _Node(
                "run",
                row["id"],
                activity,
                row["size"],
                references=[row["parent_run_id"]] if row["parent_run_id"] else [],
                protection=protection,
                template_hash=row["template_hash"],
            )
        )
    jobs: dict[str, _Node] = {}
    for row in connection.execute(
        "SELECT id, started_at, json_extract(report_json,'$.finished_at') AS finished_at, "
        "json_extract(report_json,'$.status') AS status, "
        "length(CAST(report_json AS BLOB)) + length(CAST(template_yaml AS BLOB)) + 192 AS size "
        "FROM jobs"
    ):
        activity, protection = _activity(row["started_at"], row["finished_at"], at)
        if row["status"] in {"pending", "running"}:
            protection.add("active_job")
        if _job_locked(root, row["id"]):
            protection.add("locked_job")
        node = _Node("job", row["id"], activity, row["size"], protection=protection)
        nodes.append(node)
        jobs[node.id] = node
    for row in connection.execute("SELECT job_id, item_json FROM job_items"):
        node = jobs[row["job_id"]]
        item = json.loads(row["item_json"])
        node.references.extend(value for value in item.get("run_ids", []) if isinstance(value, str))
        if item.get("run_id"):
            node.references.append(item["run_id"])
        node.items += 1
        node.size += len(row["item_json"].encode()) + 96
    pending_lesson_start: datetime | None = None
    for row in connection.execute("SELECT id, started_at, report_json FROM learn_attempts"):
        report = json.loads(row["report_json"])
        activity, protection = _activity(row["started_at"], report.get("finished_at"), at)
        if report.get("status") == "pending":
            protection.add("pending_lesson")
            start, _ = _stamp(row["started_at"], at)
            pending_lesson_start = min(pending_lesson_start or start, start)
        nodes.append(
            _Node(
                "learn_attempt",
                row["id"],
                activity,
                len(row["report_json"].encode()) + 128,
                references=[
                    case["run_id"] for case in report.get("cases", []) if case.get("run_id")
                ],
                protection=protection,
            )
        )
    if pending_lesson_start:
        # A lesson only attaches a run ID after awaiting wb.run(). Protect the append gap,
        # including concurrent unrelated runs, until every pending lesson has finished.
        for node in nodes:
            if node.kind == "run" and node.activity >= pending_lesson_start:
                node.protection.add("pending_lesson_window")
    return nodes


def _groups(nodes: list[_Node]) -> list[_Group]:
    components = _Components(nodes)
    for node in nodes:
        for reference in node.references:
            components.join(node.key, ("run", reference))
    groups: dict[Key, _Group] = {}
    for node in nodes:
        groups.setdefault(components.find(node.key), _Group()).nodes.append(node)
    return sorted(groups.values(), key=lambda group: (group.activity, group.nodes[0].key))


def _add_count(counts: CleanupCounts, node: _Node) -> None:
    if node.kind == "run":
        counts.runs += 1
    elif node.kind == "job":
        counts.jobs += 1
        counts.job_items += node.items
    else:
        counts.learn_attempts += 1


def _plan(
    connection: sqlite3.Connection,
    groups: list[_Group],
    settings: Settings,
    before: int,
    at: datetime,
    *,
    size_margin: int = 0,
) -> CleanupPlan:
    cutoff = at - timedelta(days=settings.retention_days)
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    free_count = connection.execute("PRAGMA freelist_count").fetchone()[0]
    # WAL frames can contain many obsolete copies of the same pages. A checkpoint
    # can reclaim those without deleting any history, so never count them twice.
    estimated_after = max(0, (page_count - free_count) * page_size)
    plan = CleanupPlan(
        cutoff=cutoff.isoformat(),
        limit_bytes=settings.retention_bytes,
        bytes_before=before,
        estimated_bytes_after=estimated_after,
    )
    protected_reasons: Counter[str] = Counter()
    selected: set[str] = set()
    for group in groups:
        if group.protected:
            for node in group.nodes:
                _add_count(plan.protected, node)
                protected_reasons.update(node.protection)
            continue
        reason: Literal["age", "size"]
        if group.activity < cutoff:
            reason = "age"
        elif estimated_after > settings.retention_bytes - size_margin:
            reason = "size"
        else:
            continue
        for node in group.nodes:
            _add_count(plan.delete, node)
            plan.targets.append(
                CleanupTarget(
                    kind=node.kind,
                    id=node.id,
                    last_activity=node.activity.isoformat(),
                    reason=reason,
                )
            )
            if node.kind == "run":
                selected.add(node.id)
        estimated_after = max(0, estimated_after - group.size)
    remaining_hashes = {
        node.template_hash
        for group in groups
        for node in group.nodes
        if node.kind == "run" and node.id not in selected
    }
    for row in connection.execute(
        "SELECT hash, created_at, length(CAST(yaml_text AS BLOB)) + 128 AS size "
        "FROM template_revisions"
    ):
        if row["hash"] not in remaining_hashes:
            plan.delete.template_revisions += 1
            plan.targets.append(
                CleanupTarget(
                    kind="template_revision",
                    id=row["hash"],
                    last_activity=row["created_at"],
                    reason="unreferenced_revision",
                )
            )
            estimated_after = max(0, estimated_after - row["size"])
    plan.estimated_bytes_after = estimated_after
    plan.protected_reasons = dict(protected_reasons)
    return plan


def _delete(connection: sqlite3.Connection, plan: CleanupPlan) -> None:
    connection.execute("CREATE TEMP TABLE IF NOT EXISTS retention_targets (kind TEXT, id TEXT)")
    connection.execute("DELETE FROM retention_targets")
    connection.executemany(
        "INSERT INTO retention_targets VALUES (?,?)", ((t.kind, t.id) for t in plan.targets)
    )
    # A temporary table keeps these statements bounded and avoids SQLite parameter limits.
    connection.execute(
        "DELETE FROM job_items WHERE job_id IN (SELECT id FROM retention_targets WHERE kind='job')"
    )
    for table, kind, key in (
        ("jobs", "job", "id"),
        ("learn_attempts", "learn_attempt", "id"),
        ("runs", "run", "id"),
        ("template_revisions", "template_revision", "hash"),
    ):
        connection.execute(
            f"DELETE FROM {table} WHERE {key} IN (SELECT id FROM retention_targets WHERE kind=?)",
            (kind,),
        )


def _merge(plan: CleanupPlan, extra: CleanupPlan) -> None:
    plan.targets.extend(extra.targets)
    for field_name in type(plan.delete).model_fields:
        setattr(
            plan.delete,
            field_name,
            getattr(plan.delete, field_name) + getattr(extra.delete, field_name),
        )
    plan.estimated_bytes_after = extra.estimated_bytes_after


def cleanup(
    storage: Storage,
    settings: Settings,
    *,
    dry_run: bool = True,
    at: datetime | None = None,
    protect_run_ids: set[str] | None = None,
) -> CleanupReport:
    """Preview or prune retained history; never delete source datasets or output files.

    Nonblocking maintenance returns a skipped report if another operation owns the
    lock. Apply verifies real file size after vacuum and makes at most one further
    conservative pass, keeping work linear in the number of history records.
    """
    current = (at or datetime.now(UTC)).astimezone(UTC)
    before = database_bytes(storage.path)
    report = CleanupReport(
        dry_run=dry_run,
        bytes_after=before,
        within_budget=before <= settings.retention_bytes,
        plan=CleanupPlan(
            cutoff=(current - timedelta(days=settings.retention_days)).isoformat(),
            limit_bytes=settings.retention_bytes,
            bytes_before=before,
            estimated_bytes_after=before,
        ),
    )
    with maintenance_lock(storage.path, exclusive=True) as acquired:
        if not acquired:
            report.skipped = "database_busy"
            report.notes.append("An active database operation deferred cleanup; retry later.")
            return report
        # Other writers may have committed between the initial busy-report snapshot
        # and obtaining the lock. The actual plan starts from this locked footprint.
        before = database_bytes(storage.path)
        report.bytes_after = before
        report.within_budget = before <= settings.retention_bytes
        connection = sqlite3.connect(storage.path, timeout=0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute("BEGIN IMMEDIATE" if not dry_run else "BEGIN")
            groups = _groups(
                _nodes(connection, storage.path.parent, current, protect_run_ids or set())
            )
            report.plan = _plan(connection, groups, settings, before, current)
            if dry_run:
                connection.rollback()
                return report
            _delete(connection, report.plan)
            connection.commit()
            report.applied = True
            report.deleted = report.plan.delete.model_copy()
            if report.plan.targets or before > settings.retention_bytes:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                report.vacuumed = True
            report.bytes_after = database_bytes(storage.path)
            if report.bytes_after > settings.retention_bytes:
                # Page rounding/index overhead make preflight byte estimates inexact.
                # A 10% margin avoids vacuuming once per row when nearing the cap.
                deleted_keys = {(target.kind, target.id) for target in report.plan.targets}
                remaining = [group for group in groups if group.nodes[0].key not in deleted_keys]
                connection.execute("BEGIN IMMEDIATE")
                extra = _plan(
                    connection,
                    remaining,
                    settings,
                    report.bytes_after,
                    current,
                    size_margin=max(settings.retention_bytes // 10, 65536),
                )
                if extra.targets:
                    _delete(connection, extra)
                    connection.commit()
                    _merge(report.plan, extra)
                    report.deleted = report.plan.delete.model_copy()
                    connection.execute("VACUUM")
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    report.vacuumed = True
                else:
                    connection.rollback()
            report.deleted = report.plan.delete.model_copy()
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                raise
            report.skipped = "database_busy"
            report.notes.append("SQLite is busy; cleanup or file compaction was deferred.")
        finally:
            connection.close()
        report.bytes_after = database_bytes(storage.path)
        report.reclaimed_bytes = max(0, before - report.bytes_after)
        report.within_budget = report.bytes_after <= settings.retention_bytes
        if not report.within_budget:
            report.notes.append(
                "The size limit could not be reached. Protected active history or retained "
                "metadata may exceed the budget; cleanup retries after operations finish."
            )
        return report
