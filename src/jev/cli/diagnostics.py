"""Explicit, cost-confirmed live coach checks, separate from offline doctor."""

import asyncio
import json
import sys
from typing import cast

import typer
from rich.text import Text

from jev.cli.common import console, emit, stderr, verbose_errors
from jev.coach.diagnostics import check_coaches, probe_estimates
from jev.core.errors import JevError
from jev.core.models import Settings
from jev.presentation import human_error


def check_error(detail: dict[str, object]) -> str:
    return human_error(JevError.from_dict(detail), verbose=verbose_errors())


def display_checks(rows: list[dict[str, object]]) -> None:
    for row in rows:
        provider = str(row["provider"])
        sdk = row.get("sdk_version") or "not installed"
        key = f"found in {row['key_source']}" if row.get("key_found") else "not found"
        console.print(Text(f"{provider}: SDK {sdk}; key {key}; model {row['model']}"))
        blockers = cast(list[dict[str, object]], row.get("blockers", []))
        for blocker in blockers:
            console.print(Text(check_error(blocker)))
        if error := row.get("error"):
            detail = cast(dict[str, object], error)
            console.print(Text(check_error(detail)))
        if row.get("status") == "succeeded":
            console.print(Text("  Live call succeeded; returned advice passed validation."))
            result = cast(dict[str, object], row.get("result", {}))
            advice = cast(dict[str, object], result.get("advice", {}))
            if summary := advice.get("summary"):
                console.print(Text(f"  Coach: {summary}"))
        elif not blockers and not error:
            console.print(Text("  Ready for a live check. No provider call was made."))


def coach_diagnostics(settings: Settings, *, machine: bool, offline: bool, yes: bool) -> None:
    rows = asyncio.run(check_coaches(settings, live=False))
    estimates = probe_estimates(settings)
    eligible = {str(row["provider"]) for row in rows if row.get("ready")}
    if not machine:
        display_checks(rows)
    if not offline and eligible:
        if not machine:
            for estimate in estimates:
                if estimate["provider"] in eligible:
                    cost = estimate.get("estimated_cost_nanousd")
                    price = f"about ${cost / 1e9:.5f}" if isinstance(cost, int) else "cost unknown"
                    console.print(Text(f"  {estimate['provider']} / {estimate['model']}: {price}."))
            console.print(
                Text(
                    f"This makes one small live coach request for each of {len(eligible)} ready "
                    "providers, with no automatic retries. Estimates are not spending caps. "
                    "Only a built-in synthetic example is sent."
                )
            )
        if not yes:
            if machine or not sys.stdin.isatty():
                raise JevError(
                    "cost_confirmation",
                    "Live coach checks need explicit cost authorization.",
                    "Use jev doctor --coach --offline --json to inspect estimates, then add "
                    "--yes to authorize one request per ready provider.",
                    3,
                )
            if not typer.confirm("Run these live checks?", default=False):
                raise JevError(
                    "cancelled", "No live coach request was made.", "Run the check when ready.", 2
                )
        rows = asyncio.run(check_coaches(settings, live=True))
        if not machine:
            display_checks(rows)
    ready = all(bool(row.get("ready")) for row in rows)
    success = ready if offline else all(row.get("status") == "succeeded" for row in rows)
    data = {"live_requested": not offline, "providers": rows, "estimates": estimates}
    if success:
        if machine:
            emit(data)
        return
    code = 3 if not any(row.get("live_attempted") for row in rows) else 4
    error = JevError(
        "coach_check_failed",
        "One or more coaches are not ready or failed their live check.",
        "Follow each provider's message and suggested fix above before retrying.",
        code,
    )
    if machine:
        typer.echo(
            json.dumps({"schema_version": 1, "ok": False, "error": error.as_dict(), "data": data})
        )
    else:
        stderr.print(Text(human_error(error)))
    raise typer.Exit(code)
