"""History retention exercises real SQLite, locks and file compaction offline."""

import fcntl
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jev.core.models import Run, Template
from jev.core.retention import cleanup, database_bytes
from jev.core.service import Workbench
from jev.core.storage import Storage, maintenance_lock
from jev.core.templates import revision_hash

AT = datetime(2026, 9, 20, tzinfo=UTC)


def stamp(days_ago: int) -> str:
    return (AT - timedelta(days=days_ago)).isoformat()


def add_run(
    wb: Workbench,
    design: Template,
    run_id: str,
    days_ago: int = 100,
    *,
    size: int = 20,
    pending: bool = False,
    parent: str | None = None,
) -> Run:
    run = Run(
        id=run_id,
        template_hash=revision_hash(design),
        template_name=design.name,
        parent_run_id=parent,
        started_at=stamp(days_ago),
        finished_at=None if pending else stamp(days_ago),
        requested_model=design.model,
        sdk_version="test",
        request={"state": "x" * size},
        status="pending" if pending else "succeeded",
    )
    wb.storage.create_run(run, design)
    return run


def add_job(
    wb: Workbench,
    design: Template,
    job_id: str,
    run_ids: list[str],
    *,
    days_ago: int = 100,
    status: str = "completed",
    output: Path | None = None,
) -> None:
    wb.storage.save_job(
        {
            "id": job_id,
            "kind": "eval",
            "status": status,
            "started_at": stamp(days_ago),
            "finished_at": stamp(days_ago) if status == "completed" else None,
            "output_path": str(output) if output else None,
        },
        design,
    )
    wb.storage.save_job_items(
        job_id,
        iter(
            [
                {"index": i, "run_id": run_id, "run_ids": [run_id], "status": "succeeded"}
                for i, run_id in enumerate(run_ids)
            ]
        ),
    )


def add_attempt(
    wb: Workbench,
    attempt_id: str,
    run_ids: list[str],
    *,
    days_ago: int = 100,
    status: str = "completed",
    fraction: float = 1.0,
) -> None:
    wb.storage.save_attempt(
        {
            "id": attempt_id,
            "lesson_id": "01-system-one",
            "content_version": 1,
            "started_at": stamp(days_ago),
            "finished_at": stamp(days_ago) if status != "pending" else None,
            "status": status,
            "passed": fraction >= 0.8,
            "fraction_correct": fraction,
            "cases": [{"run_id": run_id} for run_id in run_ids],
        },
        final=status != "pending",
    )


def ids(wb: Workbench) -> set[str]:
    return {run.id for run in wb.storage.history(limit=100000)}


def test_dry_run_has_no_history_or_file_changes(wb: Workbench, design: Template) -> None:
    add_run(wb, design, "expired")
    add_job(wb, design, "old-eval", ["expired"])
    before = hashlib.sha256(wb.storage.path.read_bytes()).hexdigest()
    result = cleanup(wb.storage, wb.settings, at=AT)
    assert result.dry_run and not result.applied
    assert result.plan.delete.runs == result.plan.delete.jobs == result.plan.delete.job_items == 1
    assert all(target.reason in {"age", "unreferenced_revision"} for target in result.plan.targets)
    assert result.deleted.runs == 0 and result.reclaimed_bytes == 0
    assert result.bytes_after == result.plan.bytes_before
    assert hashlib.sha256(wb.storage.path.read_bytes()).hexdigest() == before
    assert ids(wb) == {"expired"}


def test_age_deletes_related_history_preserves_external_files(
    wb: Workbench,
    design: Template,
    tmp_path: Path,
) -> None:
    old_design = design.model_copy(update={"name": "old-design"})
    add_run(wb, old_design, "expired")
    add_run(wb, design, "recent", days_ago=1)
    source = tmp_path / "source.jsonl"
    output = tmp_path / "results.jsonl"
    source.write_text('{"state":"keep"}\n')
    output.write_text("export is user owned")
    wb.storage.register_dataset({"path": str(source), "sha256": "synthetic", "rows": 1})
    add_job(wb, old_design, "old-eval", ["expired"], output=output)
    add_attempt(wb, "old-lesson", ["expired"])
    templates_before = {path.name: path.read_bytes() for path in wb.templates.root.glob("*.yaml")}
    config_before = (wb.root / "config.toml").read_bytes()
    result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    assert result.applied and result.vacuumed and result.within_budget
    assert result.deleted.runs == result.deleted.jobs == result.deleted.learn_attempts == 1
    assert result.deleted.template_revisions == 1
    assert ids(wb) == {"recent"}
    assert wb.storage.jobs() == [] and wb.storage.health() == "ok"
    with wb.storage.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM template_revisions").fetchone()[0] == 1
    progress = wb.storage.learning_progress()[0]
    assert progress["completed"] == 1 and progress["best_fraction"] == 1.0
    assert progress["attempts"] == 1 and progress["last_attempt_id"] is None
    assert source.read_text() == '{"state":"keep"}\n'
    assert output.read_text() == "export is user owned"
    assert wb.storage.datasets()[0]["path"] == str(source)
    assert (wb.root / "config.toml").read_bytes() == config_before
    assert {
        path.name: path.read_bytes() for path in wb.templates.root.glob("*.yaml")
    } == templates_before


def test_old_jobs_and_ancestors_survive_when_related_runs_are_recent(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "old-parent")
    add_run(wb, design, "new-child", days_ago=1, parent="old-parent")
    add_job(wb, design, "old-job", ["old-parent"])
    add_attempt(wb, "old-lesson", ["new-child"])
    result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    assert not result.plan.targets and ids(wb) == {"old-parent", "new-child"}
    assert wb.storage.job("old-job") and wb.storage.attempt("old-lesson")


def test_expired_parent_child_chain_deletes_without_foreign_key_failure(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "parent", days_ago=150)
    add_run(wb, design, "child", days_ago=100, parent="parent")
    result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    assert result.deleted.runs == 2 and ids(wb) == set()


def test_active_job_protects_completed_runs_and_shared_lesson(
    wb: Workbench, design: Template
) -> None:
    add_run(wb, design, "completed-item")
    add_job(wb, design, "running-job", ["completed-item"], status="running")
    add_attempt(wb, "linked-lesson", ["completed-item"])
    result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    assert not result.plan.targets
    assert result.plan.protected.runs == result.plan.protected.jobs == 1
    assert result.plan.protected.learn_attempts == 1
    assert result.plan.protected_reasons["active_job"] == 1


def test_locked_completed_job_is_protected_until_export_finishes(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "exporting-run")
    add_job(wb, design, "exporting-job", ["exporting-run"])
    lock_path = wb.root / "locks" / "job-exporting-job.lock"
    with lock_path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
        assert result.plan.protected_reasons["locked_job"] == 1
        assert ids(wb) == {"exporting-run"}
    assert cleanup(wb.storage, wb.settings, dry_run=False, at=AT).deleted.jobs == 1


def test_pending_lesson_protects_run_before_report_append(wb: Workbench, design: Template) -> None:
    add_attempt(wb, "pending-lesson", [], days_ago=120, status="pending")
    add_run(wb, design, "recently-returned-to-lesson", days_ago=100)
    add_run(wb, design, "unrelated-earlier-run", days_ago=150)
    result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    assert ids(wb) == {"recently-returned-to-lesson"}
    assert result.plan.protected.learn_attempts == result.plan.protected.runs == 1
    assert result.plan.protected_reasons["pending_lesson_window"] == 1


def test_pending_and_explicitly_protected_runs_can_exceed_size_budget(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "pending", size=1_100_000, pending=True)
    add_run(wb, design, "returned", size=1_100_000)
    settings = wb.settings.model_copy(update={"retention_bytes": 1_000_000})
    result = cleanup(wb.storage, settings, dry_run=False, at=AT, protect_run_ids={"returned"})
    assert result.plan.protected.runs == 2 and result.deleted.runs == 0
    assert not result.within_budget and result.bytes_after > settings.retention_bytes
    assert result.notes and ids(wb) == {"pending", "returned"}


def test_size_budget_removes_oldest_recent_history_and_reclaims_space(
    wb: Workbench,
    design: Template,
) -> None:
    for index in range(6):
        add_run(wb, design, f"large-{index}", days_ago=6 - index, size=350_000)
    settings = wb.settings.model_copy(update={"retention_bytes": 1_000_000})
    result = cleanup(wb.storage, settings, dry_run=False, at=AT)
    assert result.within_budget and result.bytes_after <= 1_000_000
    assert result.reclaimed_bytes > 1_000_000 and result.vacuumed
    assert "large-5" in ids(wb) and "large-0" not in ids(wb)
    assert result.deleted.runs >= 4
    assert all(target.reason == "size" for target in result.plan.targets if target.kind == "run")


def test_existing_free_pages_compact_without_deleting_valid_history(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "removed-before-cleanup", days_ago=1, size=2_000_000)
    add_run(wb, design, "keep", days_ago=1, size=100_000)
    with wb.storage.connect() as connection:
        connection.execute("DELETE FROM runs WHERE id='removed-before-cleanup'")
    assert database_bytes(wb.storage.path) > 1_000_000
    result = cleanup(
        wb.storage,
        wb.settings.model_copy(update={"retention_bytes": 1_000_000}),
        dry_run=False,
        at=AT,
    )
    assert result.deleted.runs == 0 and result.vacuumed and result.within_budget
    assert ids(wb) == {"keep"}


def test_maintenance_skips_nonblocking_while_database_operation_active(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "expired")
    with maintenance_lock(wb.storage.path):
        result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    assert result.skipped == "database_busy" and not result.applied
    assert ids(wb) == {"expired"}


def test_sqlite_external_writer_skips_without_deletion(wb: Workbench, design: Template) -> None:
    add_run(wb, design, "expired")
    with closing(sqlite3.connect(wb.storage.path)) as external, external:
        external.execute("BEGIN IMMEDIATE")
        result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
        external.rollback()
    assert result.skipped == "database_busy" and result.deleted.runs == 0
    assert ids(wb) == {"expired"}


def test_newer_learning_progress_survives_older_attempt_pruning(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "old")
    add_run(wb, design, "new", days_ago=1)
    add_attempt(wb, "old-attempt", ["old"])
    add_attempt(wb, "new-attempt", ["new"], days_ago=1, fraction=0.4)
    cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    progress = wb.storage.learning_progress()[0]
    assert progress["attempts"] == 2 and progress["completed"] == 1
    assert progress["best_fraction"] == 1 and progress["last_attempt_id"] == "new-attempt"
    assert wb.storage.attempt("new-attempt")["fraction_correct"] == 0.4


def test_migration_four_preserves_learning_aggregates_and_allows_pruned_last_attempt(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "old")
    add_attempt(wb, "old-attempt", ["old"])
    with closing(sqlite3.connect(wb.storage.path)) as connection, connection:
        connection.executescript("""
            ALTER TABLE learn_progress RENAME TO new_progress;
            CREATE TABLE learn_progress (
                lesson_id TEXT NOT NULL, content_version INTEGER NOT NULL,
                attempts INTEGER NOT NULL, completed INTEGER NOT NULL, best_fraction REAL NOT NULL,
                last_attempt_id TEXT NOT NULL
                REFERENCES learn_attempts(id), updated_at TEXT NOT NULL,
                PRIMARY KEY(lesson_id, content_version)
            );
            INSERT INTO learn_progress SELECT * FROM new_progress;
            DROP TABLE new_progress;
            DELETE FROM schema_migrations WHERE version=4;
            PRAGMA user_version=3;
        """)
    migrated = Storage(wb.storage.path)
    before = migrated.learning_progress()[0]
    assert before["last_attempt_id"] == "old-attempt" and before["best_fraction"] == 1
    cleanup(migrated, wb.settings, dry_run=False, at=AT)
    after = migrated.learning_progress()[0]
    assert after == {**before, "last_attempt_id": None}
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_wal_counts_toward_footprint_and_is_reclaimed(wb: Workbench, design: Template) -> None:
    with closing(sqlite3.connect(wb.storage.path)) as external, external:
        external.execute("PRAGMA journal_mode=WAL")
        add_run(wb, design, "expired", size=1_500_000)
        external.execute("PRAGMA wal_autocheckpoint=0")
        external.execute(
            "UPDATE runs SET request_json=? WHERE id='expired'",
            (json.dumps({"state": "y" * 1_500_000}),),
        )
        external.commit()
        wal = Path(f"{wb.storage.path}-wal")
        assert wal.exists() and wal.stat().st_size > 0
        assert (
            database_bytes(wb.storage.path) == wb.storage.path.stat().st_size + wal.stat().st_size
        )
        result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
        assert result.deleted.runs == 1 and result.reclaimed_bytes > 1_000_000
    assert ids(wb) == set()


def test_obsolete_wal_frames_compact_without_pruning_recent_runs(
    wb: Workbench,
    design: Template,
) -> None:
    add_run(wb, design, "keep", days_ago=1, size=100_000)
    with closing(sqlite3.connect(wb.storage.path)) as external, external:
        external.execute("PRAGMA journal_mode=WAL")
        external.execute("PRAGMA wal_autocheckpoint=0")
        for index in range(12):
            external.execute(
                "UPDATE runs SET request_json=? WHERE id='keep'",
                (json.dumps({"state": str(index % 10) * 100_000}),),
            )
            external.commit()
        assert database_bytes(wb.storage.path) > 1_000_000
        result = cleanup(
            wb.storage,
            wb.settings.model_copy(update={"retention_bytes": 1_000_000}),
            dry_run=False,
            at=AT,
        )
        assert result.deleted.runs == 0 and result.vacuumed and result.within_budget
        assert result.bytes_after < 300_000
    assert ids(wb) == {"keep"}


def test_invalid_timestamp_is_protected(wb: Workbench, design: Template) -> None:
    add_run(wb, design, "invalid")
    with wb.storage.connect() as connection:
        connection.execute("UPDATE runs SET started_at='invalid' WHERE id='invalid'")
    result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    assert result.plan.protected_reasons["invalid_timestamp"] == 1
    assert result.deleted.runs == 0


def test_many_job_rows_are_pruned_in_one_bounded_transaction(
    wb: Workbench, design: Template
) -> None:
    # More rows than SQLite's historical 999-parameter limit; no per-row history queries.
    add_job(wb, design, "large-job", [])
    with wb.storage.connect() as connection:
        connection.executemany(
            "INSERT INTO job_items VALUES (?,?,?)",
            (
                ("large-job", index, json.dumps({"index": index, "run_ids": []}))
                for index in range(1200)
            ),
        )
    result = cleanup(wb.storage, wb.settings, dry_run=False, at=AT)
    assert result.deleted.jobs == 1 and result.deleted.job_items == 1200
