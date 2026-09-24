"""Invalid job drafts stop before planning; valid output and resume paths stay usable."""

import json
from pathlib import Path

import pytest
from conftest import MockEvaluator
from test_phase3_tui import dataset, finish_job, mock_runs
from textual.widgets import Input, Static

from jevlab.core.jobs import BatchService
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.dialogs import Confirm
from jevlab.tui.evaluation import EvalScreen, JobScreen
from jevlab.tui.fields import Field, output_file


@pytest.mark.parametrize(
    ("field_id", "value", "message"),
    [
        ("job-concurrency", "4.0", "omit decimal points"),
        ("job-concurrency", "4e0", "exponents"),
        ("job-rate", "0", "greater than 0"),
        ("job-rate", "nan", "number"),
    ],
)
async def test_invalid_job_values_never_plan_prompt_or_dispatch(
    wb: Workbench,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_id: str,
    value: str,
    message: str,
) -> None:
    evaluator = mock_runs(wb, monkeypatch)
    path = dataset(tmp_path)
    app = JevApp(wb, start="batch")
    async with app.run_test(size=(120, 52)) as pilot:
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(path)
        screen.query_one("#output-path", Input).value = str(tmp_path / "output.jsonl")

        def forbidden(*_args: object, **_kwargs: object) -> None:
            pytest.fail("An invalid local draft must stop before planning or provider dispatch.")

        monkeypatch.setattr(screen.service, "plan", forbidden)
        screen.query_one(f"#{field_id}", Input).value = value
        await pilot.pause()
        assert message in screen.query_one(f"#field-{field_id}", Field).error
        await screen.prepare(run=True).wait()
        await app.workers.wait_for_complete()
        assert app.screen is screen
        await screen.execute().wait()  # Direct execution has the same guard.
        assert "No request was sent" in str(screen.query_one("#job-status", Static).content)
        assert not evaluator.requests and not wb.storage.history()
        assert not (tmp_path / "output.jsonl").exists()


async def test_batch_preview_needs_no_output_path(wb: Workbench, tmp_path: Path) -> None:
    path = dataset(tmp_path)
    app = JevApp(wb, start="batch")
    async with app.run_test(size=(120, 52)) as pilot:
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(path)
        await screen.prepare().wait()
        await pilot.pause()
        assert "estimated" in str(screen.query_one("#job-status", Static).content)
        assert screen.query_one("#output-path", Input).value == ""
        assert len(BatchService(wb).datasets()) == 1
        assert not wb.storage.history()


async def test_new_output_parent_and_suffix_work_only_after_confirmation(
    wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluator = mock_runs(wb, monkeypatch)
    wb.update_settings(wb.settings.with_updates({"confirm_cost_usd": 0}))
    path = dataset(tmp_path)
    destination = tmp_path / "new-folder" / "results.txt"
    app = JevApp(wb, start="batch")
    async with app.run_test(size=(120, 52)) as pilot:
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(path)
        screen.query_one("#output-path", Input).value = str(destination)
        await pilot.pause()
        assert not screen.query_one("#field-output-path", Field).error
        await finish_job(app, pilot, screen.prepare(run=True), approve=False)
        assert isinstance(app.screen, Confirm)
        assert not destination.parent.exists() and not evaluator.requests
        await pilot.click("#keep")
        await app.workers.wait_for_complete()
        assert not destination.parent.exists() and not evaluator.requests
        await finish_job(app, pilot, screen.prepare(run=True))
        assert isinstance(app.screen, EvalScreen)
        assert len(evaluator.requests) == 1
        assert json.loads(destination.read_text().splitlines()[0])


async def test_existing_output_requires_its_matching_saved_job(
    wb: Workbench, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, output = dataset(tmp_path), tmp_path / "results.jsonl"
    report = await BatchService(wb).run(
        wb.templates.load("support-triage"), path, output=output, evaluator=MockEvaluator()
    )
    original = output.read_bytes()
    evaluator = mock_runs(wb, monkeypatch)
    app = JevApp(wb, start="batch")
    async with app.run_test(size=(120, 52)) as pilot:
        screen = app.screen
        assert isinstance(screen, JobScreen)
        screen.query_one("#dataset-path", Input).value = str(path)
        screen.query_one("#output-path", Input).value = str(output)
        await pilot.pause()
        assert "already exists" in screen.query_one("#field-output-path", Field).error
        await screen.prepare(run=True).wait()
        assert app.screen is screen and not evaluator.requests
        assert output.read_bytes() == original

        screen.query_one("#resume-id", Input).value = report.id
        await pilot.pause()
        assert not screen.query_one("#field-output-path", Field).error
        await finish_job(app, pilot, screen.prepare(run=True))
        assert isinstance(app.screen, EvalScreen)
        assert app.screen.report.id == report.id
        assert not evaluator.requests  # Completed resume has nothing to rerun.
        assert output.read_bytes() == original


def test_output_validator_matches_existing_paths_and_creates_nothing(tmp_path: Path) -> None:
    new = tmp_path / "new-folder" / "results.txt"
    assert output_file(str(new), require_jsonl=False) is None
    assert not new.parent.exists()
    assert "ending in .jsonl" in (output_file(str(new)) or "")
    existing = tmp_path / "results.jsonl"
    existing.write_text("preserve this")
    assert "already exists" in (output_file(str(existing)) or "")
    assert output_file(str(existing), resume_path=existing) is None
    link = tmp_path / "linked.jsonl"
    link.symlink_to(existing)
    assert "symbolic link" in (output_file(str(link), resume_path=existing) or "")
    assert "folder path is a file" in (output_file(str(existing / "child.jsonl")) or "")
    assert existing.read_text() == "preserve this"
