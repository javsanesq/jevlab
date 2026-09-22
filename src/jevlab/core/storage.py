"""One SQLite database with immutable template revisions and complete runs."""

import fcntl
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from jevlab.core.errors import JevError
from jevlab.core.models import Run, Template
from jevlab.core.templates import dump_template, parse_template, revision_hash


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS template_revisions (
    hash TEXT PRIMARY KEY, name TEXT NOT NULL, yaml_text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    template_hash TEXT NOT NULL REFERENCES template_revisions(hash),
    template_name TEXT NOT NULL,
    parent_run_id TEXT REFERENCES runs(id),
    started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed','interrupted')),
    requested_model TEXT NOT NULL, resolved_model TEXT,
    request_json TEXT NOT NULL, response_json TEXT, routing_json TEXT,
    latency_ms INTEGER, input_tokens INTEGER, output_tokens INTEGER, cost_nanousd INTEGER,
    price_snapshot_json TEXT NOT NULL, sdk_version TEXT NOT NULL, request_id TEXT, error_json TEXT
);
CREATE INDEX IF NOT EXISTS runs_time ON runs(started_at);
CREATE INDEX IF NOT EXISTS runs_template ON runs(template_name, started_at);
CREATE INDEX IF NOT EXISTS runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS runs_model ON runs(resolved_model);
"""
JSON_FIELDS = {"request", "response", "routing", "price_snapshot", "error"}

LEARNING_SCHEMA = """
CREATE TABLE IF NOT EXISTS learn_attempts (
    id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL, content_version INTEGER NOT NULL,
    started_at TEXT NOT NULL, report_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS learn_attempts_lesson ON learn_attempts(lesson_id, started_at);
CREATE TABLE IF NOT EXISTS learn_progress (
    lesson_id TEXT NOT NULL, content_version INTEGER NOT NULL, attempts INTEGER NOT NULL,
    completed INTEGER NOT NULL, best_fraction REAL NOT NULL, last_attempt_id TEXT NOT NULL
    REFERENCES learn_attempts(id), updated_at TEXT NOT NULL,
    PRIMARY KEY(lesson_id, content_version)
);
"""

PROGRESS_RETENTION_MIGRATION = """
ALTER TABLE learn_progress RENAME TO learn_progress_before_retention;
CREATE TABLE learn_progress (
    lesson_id TEXT NOT NULL, content_version INTEGER NOT NULL, attempts INTEGER NOT NULL,
    completed INTEGER NOT NULL, best_fraction REAL NOT NULL,
    last_attempt_id TEXT REFERENCES learn_attempts(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY(lesson_id, content_version)
);
INSERT INTO learn_progress SELECT * FROM learn_progress_before_retention;
DROP TABLE learn_progress_before_retention;
"""


JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, info_json TEXT NOT NULL,
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('batch','eval')),
    started_at TEXT NOT NULL, template_yaml TEXT NOT NULL, report_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_kind_time ON jobs(kind, started_at);
CREATE TABLE IF NOT EXISTS job_items (
    job_id TEXT NOT NULL REFERENCES jobs(id), row_index INTEGER NOT NULL,
    item_json TEXT NOT NULL, PRIMARY KEY(job_id, row_index)
);
"""


@contextmanager
def maintenance_lock(
    path: Path, *, exclusive: bool = False, blocking: bool = False
) -> Iterator[bool]:
    """Serialize maintenance with short DB operations across threads and processes.

    Maintenance never waits on an active operation. Ordinary operations wait for an
    already-started cleanup to finish; they do not hold this lock during API calls.
    Keep the lock file: unlinking it could create two independently locked inodes.
    """
    directory = path.parent / "locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    lock_path = directory / "maintenance.lock"
    with lock_path.open("a") as handle:
        lock_path.chmod(0o600)
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if exclusive and not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle, operation)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Startup migration is the one exclusive operation that waits: simultaneous
        # app launches must not both migrate the same older schema.
        with maintenance_lock(path, exclusive=True, blocking=True), self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 4:
                raise JevError(
                    "database_version", "This database is newer than the app.", "Upgrade jevlab."
                )
            if version < 4:
                connection.executescript(SCHEMA)
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (1, ?)", (now(),)
                )
                connection.executescript(LEARNING_SCHEMA)
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (2, ?)", (now(),)
                )
                connection.executescript(JOBS_SCHEMA)
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (3, ?)", (now(),)
                )
                # Keep lifetime learning aggregates when their detailed history expires.
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + PROGRESS_RETENTION_MIGRATION
                    + "\nPRAGMA user_version=4; COMMIT;"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (4, ?)", (now(),)
                )
        path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with maintenance_lock(self.path), self._connect() as connection:
            yield connection

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_run(self, run: Run, template: Template) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO template_revisions VALUES (?, ?, ?, ?)",
                (revision_hash(template), template.name, dump_template(template), now()),
            )
            data = self._encode(run)
            columns = ",".join(data)
            placeholders = ",".join("?" for _ in data)
            connection.execute(
                f"INSERT INTO runs ({columns}) VALUES ({placeholders})", list(data.values())
            )

    def finish_run(self, run: Run) -> None:
        data = self._encode(run)
        run_id = data.pop("id")
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET " + ",".join(f"{key}=?" for key in data) + " WHERE id=?",
                [*data.values(), run_id],
            )

    @staticmethod
    def _encode(run: Run) -> dict[str, object]:
        return {
            f"{key}_json" if key in JSON_FIELDS else key: json.dumps(
                value, ensure_ascii=False, allow_nan=False
            )
            if key in JSON_FIELDS and value is not None
            else value
            for key, value in run.model_dump().items()
        }

    @staticmethod
    def _decode(row: sqlite3.Row) -> Run:
        values = dict(row)
        for key in JSON_FIELDS:
            value = values.pop(f"{key}_json")
            values[key] = json.loads(value) if value is not None else None
        return Run.model_validate(values)

    def get(self, run_id: str) -> Run:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE id=? OR substr(id,1,?)=? LIMIT 2",
                (run_id, len(run_id), run_id),
            ).fetchall()
        if len(rows) != 1:
            raise JevError(
                "run_not_found",
                "Run ID is missing or ambiguous.",
                "Use jevlab history to find its full ID.",
            )
        return self._decode(rows[0])

    def template_for(self, run: Run) -> Template:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT yaml_text FROM template_revisions WHERE hash=?",
                (run.template_hash,),
            ).fetchone()
        if row is None:
            raise JevError(
                "missing_revision",
                "The run's template revision is unavailable.",
                "Check database integrity.",
            )
        return parse_template(row[0], historical=True)

    def history(
        self,
        *,
        search: str = "",
        template: str = "",
        status: str = "",
        model: str = "",
        limit: int = 100,
    ) -> list[Run]:
        conditions = ["1=1"]
        params: list[object] = []
        for column, value in [
            ("template_name", template),
            ("status", status),
            ("resolved_model", model),
        ]:
            if value:
                conditions.append(f"r.{column}=?")
                params.append(value)
        if search:
            conditions.append(
                "(instr(r.request_json,?)>0 OR instr(coalesce(r.response_json,''),?)>0 "
                "OR instr(t.yaml_text,?)>0)"
            )
            params.extend([search] * 3)
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT r.* FROM runs r JOIN template_revisions t ON r.template_hash=t.hash WHERE "
                + " AND ".join(conditions)
                + " ORDER BY r.started_at DESC, r.rowid DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._decode(row) for row in rows]

    def totals(self, group: str = "day") -> list[dict[str, object]]:
        column = "substr(started_at,1,10)" if group == "day" else "template_name"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT {column} AS bucket, count(*) AS runs, sum(cost_nanousd) AS cost_nanousd, "
                "sum(cost_nanousd IS NULL) AS unknown_cost_runs, sum(latency_ms) AS latency_ms "
                f"FROM runs GROUP BY {column} ORDER BY bucket DESC",
            ).fetchall()
        return [cast(dict[str, object], dict(row)) for row in rows]

    def health(self) -> str:
        with self.connect() as connection:
            return str(connection.execute("PRAGMA quick_check").fetchone()[0])

    def save_attempt(self, report: dict[str, object], *, final: bool = False) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO learn_attempts VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET report_json=excluded.report_json",
                (
                    report["id"],
                    report["lesson_id"],
                    report["content_version"],
                    report["started_at"],
                    json.dumps(report, allow_nan=False),
                ),
            )
            if final:
                connection.execute(
                    "INSERT INTO learn_progress VALUES (?, ?, 1, ?, ?, ?, ?) "
                    "ON CONFLICT(lesson_id, content_version) DO UPDATE SET "
                    "attempts=attempts+1, completed=max(completed,excluded.completed), "
                    "best_fraction=max(best_fraction,excluded.best_fraction), "
                    "last_attempt_id=excluded.last_attempt_id, updated_at=excluded.updated_at",
                    (
                        report["lesson_id"],
                        report["content_version"],
                        bool(report["passed"]),
                        report["fraction_correct"],
                        report["id"],
                        now(),
                    ),
                )

    def learning_progress(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM learn_progress ORDER BY lesson_id").fetchall()
        return [dict(row) for row in rows]

    def attempt(self, attempt_id: str) -> dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT report_json FROM learn_attempts WHERE id=? OR substr(id,1,?)=? LIMIT 2",
                (attempt_id, len(attempt_id), attempt_id),
            ).fetchall()
        if len(rows) != 1:
            raise JevError(
                "attempt_not_found",
                "Attempt ID missing or ambiguous.",
                "Use jevlab learn progress --json to find the full ID.",
            )
        return json.loads(rows[0][0])

    def register_dataset(self, info: dict[str, object]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO datasets VALUES (?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
                "sha256=excluded.sha256, info_json=excluded.info_json, "
                "registered_at=excluded.registered_at",
                (info["path"], info["sha256"], json.dumps(info, allow_nan=False), now()),
            )

    def datasets(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT info_json FROM datasets ORDER BY path").fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_job(self, report: dict[str, object], template: Template) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "report_json=excluded.report_json",
                (
                    report["id"],
                    report["kind"],
                    report["started_at"],
                    dump_template(template),
                    json.dumps(report, allow_nan=False),
                ),
            )

    def job(self, job_id: str) -> dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT report_json FROM jobs WHERE id=? OR substr(id,1,?)=? LIMIT 2",
                (job_id, len(job_id), job_id),
            ).fetchall()
        if len(rows) != 1:
            raise JevError(
                "job_not_found",
                "Job ID is missing or ambiguous.",
                "List batch or eval jobs to find its full ID.",
            )
        return json.loads(rows[0][0])

    def job_template(self, job_id: str) -> Template:
        report = self.job(job_id)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT template_yaml FROM jobs WHERE id=?", (report["id"],)
            ).fetchone()
        assert row is not None
        return parse_template(row[0])

    def jobs(self, kind: str = "") -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT report_json FROM jobs WHERE (?='' OR kind=?) "
                "ORDER BY started_at DESC, rowid DESC",
                (kind, kind),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_job_item(self, job_id: str, item: dict[str, object]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO job_items VALUES (?, ?, ?) "
                "ON CONFLICT(job_id, row_index) DO UPDATE SET item_json=excluded.item_json",
                (job_id, item["index"], json.dumps(item, allow_nan=False)),
            )

    def save_job_items(self, job_id: str, items: Iterator[dict[str, object]]) -> None:
        """Checkpoint initialization in one transaction rather than one fsync per row."""
        with self.connect() as connection:
            connection.executemany(
                "INSERT INTO job_items VALUES (?, ?, ?) "
                "ON CONFLICT(job_id, row_index) DO UPDATE SET item_json=excluded.item_json",
                ((job_id, item["index"], json.dumps(item, allow_nan=False)) for item in items),
            )

    def job_items(self, job_id: str) -> Iterator[dict[str, object]]:
        # Cursor iteration keeps export memory bounded by one row, even for large jobs.
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT item_json FROM job_items WHERE job_id=? ORDER BY row_index", (job_id,)
            )
            for row in rows:
                yield json.loads(row[0])
