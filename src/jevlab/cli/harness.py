"""Portable SDK exports, a loopback API, and explicit history maintenance."""

import socket
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
import uvicorn
from rich.text import Text

from jevlab.cli.common import JsonFlag, console, emit, guarded, workbench
from jevlab.cli.spending import confirm_spend
from jevlab.core.config import data_directory
from jevlab.core.errors import JevError
from jevlab.core.exporting import export_template, write_export
from jevlab.core.retention import cleanup
from jevlab.core.service import Workbench
from jevlab.core.spending import SpendEstimate
from jevlab.server.app import create_app, server_token, server_token_source


class ExportLanguage(StrEnum):
    python = "python"
    langchain = "langchain"
    pydantic_ai = "pydantic-ai"


@guarded
def export(
    template: str,
    lang: Annotated[
        ExportLanguage, typer.Option(help="Standalone SDK code or framework adapter.")
    ] = ExportLanguage.python,
    output: Annotated[
        Path | None, typer.Option(help="New .py file; existing paths are not overwritten.")
    ] = None,
    json_output: JsonFlag = False,
) -> None:
    design = workbench().templates.load(template)
    target = cast(Literal["python", "langchain", "pydantic-ai"], lang.value)
    source = export_template(design, lang=target)
    path = write_export(design, output, lang=target) if output else None
    if json_output:
        emit(
            {
                "template": template,
                "lang": target,
                "path": str(path) if path else None,
                "code": source,
            }
        )
    elif path:
        console.print(Text(f"Exported {template} to {path}. No API call was made."))
    else:
        typer.echo(source)


@guarded
def clean(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview eligible history; delete nothing.")
    ] = False,
    json_output: JsonFlag = False,
) -> None:
    wb = Workbench(data_directory(), maintain=False)
    report = cleanup(wb.storage, wb.settings, dry_run=dry_run)
    if json_output:
        emit(report.model_dump(mode="json"))
    else:
        counts = report.plan.delete if dry_run else report.deleted
        console.print(
            Text(
                f"{'Would prune' if dry_run else 'Pruned'} {counts.runs:,} runs, "
                f"{counts.jobs:,} jobs, {counts.learn_attempts:,} lesson attempts.\n"
                f"Database: {report.plan.bytes_before:,} → {report.bytes_after:,} bytes.\n"
                + "\n".join(report.notes)
            )
        )


@guarded
def serve(
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8766,
    concurrency: Annotated[int, typer.Option(min=1, max=32)] = 4,
    rate: Annotated[int, typer.Option(min=1, max=100, help="Maximum new requests per second.")] = 2,
    check: Annotated[
        bool, typer.Option("--check", help="Validate startup settings without listening.")
    ] = False,
    json_output: JsonFlag = False,
) -> None:
    token = server_token()
    wb = workbench()
    application = create_app(
        wb, token, port=port, concurrency=concurrency, requests_per_second=rate
    )
    metadata = {
        "url": f"http://127.0.0.1:{port}",
        "token_source": server_token_source(),
        "concurrency": concurrency,
        "requests_per_second": rate,
        "check_only": check,
        "network_checked": False,
        "routes": ["GET /health", "GET /templates", "POST /templates/<name>/run"],
    }
    if check:
        if json_output:
            emit(metadata)
        else:
            console.print(Text(f"Local API configuration valid: {metadata['url']}"))
        return
    confirm_spend(
        SpendEstimate(
            1,
            None,
            "Start the local API so your connected programs can request paid Jev decisions.",
            "Starting the server is free. Each accepted decision request can cost money. "
            "The total depends on the requests your programs send, so it is unknown. "
            "Requests run without individual prompts until you stop the server with Ctrl+C.",
        ),
        machine=json_output,
    )
    # Explicit flags prevent environment overrides from exposing the service externally.
    # Access logs are off: request URLs and local bearer tokens should not enter logs.
    config = uvicorn.Config(
        application,
        host="127.0.0.1",
        port=port,
        workers=1,
        reload=False,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        ws="none",
        log_level="warning",
        timeout_graceful_shutdown=5,
        limit_concurrency=max(16, concurrency * 4),
        h11_max_incomplete_event_size=16_384,
    )
    # Bind before emitting success so occupied ports get the usual CLI error envelope.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise JevError(
                "server_bind_failed",
                f"Could not bind the local API to port {port}.",
                "Stop the other listener or choose another --port.",
            ) from exc
        metadata["stage"] = "bound"
        if json_output:
            emit(metadata)
        else:
            console.print(
                Text(
                    f"Starting local API at {metadata['url']}\n"
                    "Requires your separate JEVLAB_SERVER_TOKEN. Ctrl+C stops the server."
                )
            )
        uvicorn.Server(config).run(sockets=[listener])
