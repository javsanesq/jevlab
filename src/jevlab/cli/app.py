"""Human and machine CLI. JSON stdout always contains one versioned envelope."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, cast

import typer
from rich.table import Table
from rich.text import Text

from jevlab import __version__
from jevlab.cli.common import (
    JsonFlag,
    configure_error_details,
    console,
    emit,
    emit_error,
    guarded,
    launch,
    parse_state_for,
    read_state_payload,
    stderr,
    verbose_errors,
    workbench,
)
from jevlab.cli.evaluation import batch, compare_command, datasets_app, eval_app
from jevlab.cli.guidance import register_guidance
from jevlab.cli.guide import guide
from jevlab.cli.harness import clean, export, serve
from jevlab.cli.learning import coach_app, learn_app, library_app
from jevlab.cli.spending import confirm_spend
from jevlab.core.config import PRIVACY, data_directory, load_settings
from jevlab.core.credentials import Credentials, Provider
from jevlab.core.doctor import inspect, online
from jevlab.core.errors import JevError
from jevlab.core.files import read_text
from jevlab.core.models import DEFAULT_BASE_URL, Settings
from jevlab.core.pricing import format_cost
from jevlab.core.service import Workbench
from jevlab.core.spending import SpendEstimate
from jevlab.core.templates import (
    context_estimate,
    fork_template,
    parse_template,
    validation_message,
)
from jevlab.presentation import human_error
from jevlab.rendering import render_run

app = typer.Typer(
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    help="Design, run, and inspect TypeSafe Jev decisions.",
)
templates_app = typer.Typer(invoke_without_command=True, help="Browse, create, and edit templates.")
history_app = typer.Typer(
    invoke_without_command=True, help="Inspect recorded runs and rerun exact designs."
)
DEVELOP, OPERATE, LEARN = "Develop and evaluate", "Run and operate", "Learn and explore"
app.add_typer(templates_app, name="templates", rich_help_panel=DEVELOP)
app.add_typer(eval_app, name="eval", rich_help_panel=DEVELOP)
app.add_typer(history_app, name="history", rich_help_panel=DEVELOP)
app.add_typer(datasets_app, name="datasets", rich_help_panel=OPERATE)
app.add_typer(learn_app, name="learn", rich_help_panel=LEARN)
app.add_typer(library_app, name="library", rich_help_panel=LEARN)
app.add_typer(coach_app, name="coach", rich_help_panel=LEARN)


@app.callback(invoke_without_command=True)
@guarded
def root(
    ctx: typer.Context,
    json_output: JsonFlag = False,
    version: Annotated[bool, typer.Option("--version", help="Show version.")] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show safe error codes and request references.")
    ] = False,
) -> None:
    configure_error_details(verbose)
    if ctx.invoked_subcommand:
        return
    if version:
        emit({"version": __version__}) if json_output else typer.echo(f"jevlab {__version__}")
    elif json_output:
        emit({"version": __version__, "data_directory": str(data_directory())})
    else:
        launch(workbench())


@app.command(help="Get Jev answers for a template and input state.", rich_help_panel=DEVELOP)
@guarded
def run(
    template: Annotated[str, typer.Argument(help="Saved template name or project YAML path.")],
    state: Annotated[str | None, typer.Option(help="File path, or - for stdin.")] = None,
    text: Annotated[str | None, typer.Option(help="Inline state text.")] = None,
    format: Annotated[str | None, typer.Option(help="Override state format: text or json.")] = None,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    design = wb.templates.load_reference(template)
    if (state is None) == (text is None):
        raise JevError(
            "state_required",
            "Supply exactly one of --state or --text.",
            "Use --state - to read stdin.",
        )
    input_format = format or design.state.format
    if input_format not in ("text", "json"):
        raise JevError("invalid_format", "Unknown state format.", "Choose text or json.")
    parsed = parse_state_for(
        design, read_state_payload(state, text, action="running"), input_format
    )
    for warning in cast(list[str], context_estimate(design, parsed)["warnings"]):
        stderr.print(Text(warning))
    result = asyncio.run(wb.run(design, parsed))
    if json_output or state == "-":
        emit(result.model_dump())
    else:
        console.print(render_run(result, verbose=verbose_errors()))
        console.print(Text(f"Run {result.id}", style="dim"))


@templates_app.callback()
@guarded
def templates(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    if ctx.invoked_subcommand:
        return
    wb = workbench()
    rows: list[dict[str, object]] = []
    for name in wb.templates.names():
        try:
            design = wb.templates.load(name)
            rows.append(
                {
                    "name": name,
                    "model": design.model,
                    "questions": len(design.questions),
                    "description": design.description,
                    "valid": True,
                }
            )
        except JevError:
            rows.append({"name": name, "valid": False})
    if json_output:
        emit(rows)
    else:
        table = Table("Template", "Model", "Questions", "Description", box=None)
        for row in rows:
            table.add_row(
                Text(str(row["name"])),
                Text(str(row.get("model", "invalid"))),
                Text(str(row.get("questions", "")), justify="right"),
                Text(str(row.get("description", ""))),
            )
        console.print(table)


@templates_app.command(
    "new", help="Create a catalog template in the editor, or import one with --from."
)
@guarded
def template_new(
    name: str,
    from_file: Annotated[
        Path | None, typer.Option("--from", help="Import a YAML template.")
    ] = None,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    if from_file:
        design = fork_template(parse_template(read_text(from_file)), name)
        path = wb.templates.save(design)
        emit({"path": str(path)}) if json_output else typer.echo(f"Saved {path}")
    elif json_output:
        raise JevError(
            "file_required", "JSON mode requires --from PATH.", "Provide a YAML design to import."
        )
    else:
        wb.templates.path(name)
        if name in wb.templates.names():
            raise JevError("already_exists", "That name already exists.", "Use templates edit.")
        launch(wb, "new", name)


@templates_app.command("edit", help="Edit a catalog template or project YAML; --from replaces it.")
@guarded
def template_edit(
    name: Annotated[str, typer.Argument(help="Saved template name or project YAML path.")],
    from_file: Annotated[Path | None, typer.Option("--from")] = None,
    json_output: JsonFlag = False,
) -> None:
    wb = workbench()
    source = wb.templates.resolve(name)
    if from_file:
        design = fork_template(parse_template(read_text(from_file)), source.template.name)
        path = wb.templates.save_source(source, design).path
        emit({"path": str(path)}) if json_output else typer.echo(f"Saved {path}")
    elif json_output:
        raise JevError(
            "file_required", "JSON mode requires --from PATH.", "Provide the replacement YAML."
        )
    else:
        launch(wb, "edit", name)


@templates_app.command("validate", help="Check a YAML design without saving or calling Jev.")
@guarded
def template_validate(path: Path, json_output: JsonFlag = False) -> None:
    design = parse_template(read_text(path))
    emit(
        {"valid": True, "template": design.model_dump(mode="json")}
    ) if json_output else typer.echo(f"Valid: {design.name}")


@history_app.callback()
@guarded
def history(
    ctx: typer.Context,
    search: str = "",
    template: str = "",
    status: str = "",
    model: str = "",
    limit: Annotated[int, typer.Option(min=1, max=10000)] = 100,
    json_output: JsonFlag = False,
) -> None:
    if ctx.invoked_subcommand:
        return
    wb = workbench()
    runs = wb.storage.history(
        search=search, template=template, status=status, model=model, limit=limit
    )
    data = {
        "runs": [r.model_dump() for r in runs],
        "daily_totals": wb.storage.totals(),
        "template_totals": wb.storage.totals("template"),
        "totals_scope": "all retained history",
    }
    if json_output:
        emit(data)
    else:
        table = Table("Run", "UTC time", "Template", "Status", "ms", "$ est.", box=None)
        for result in runs:
            table.add_row(
                result.id[:8],
                result.started_at[:19],
                Text(result.template_name),
                result.status,
                Text(
                    str(result.latency_ms) if result.latency_ms is not None else "—",
                    justify="right",
                ),
                Text(format_cost(result.cost_nanousd), justify="right"),
            )
        console.print(table)
        console.print(
            Text(f"{len(runs)} shown. Use --json for daily and template totals.", style="dim")
        )


@history_app.command("show", help="Show one saved run: answers, routing, usage and errors.")
@guarded
def history_show(run_id: str, json_output: JsonFlag = False) -> None:
    result = workbench().storage.get(run_id)
    emit(result.model_dump()) if json_output else console.print(
        render_run(result, verbose=verbose_errors())
    )


@history_app.command("rerun", help="Repeat a saved run with its exact design and state (one call).")
@guarded
def history_rerun(run_id: str, json_output: JsonFlag = False) -> None:
    wb = workbench()
    result = asyncio.run(wb.rerun(run_id))
    emit(result.model_dump()) if json_output else console.print(
        render_run(result, verbose=verbose_errors())
    )


def change_settings(wb: Workbench, values: dict[str, object]) -> None:
    try:
        updated = wb.settings.with_updates(values)
    except ValueError as error:
        coach_fields = {"coach_provider", "coach_model", "anthropic_model", "openai_model"}
        if coach_fields.intersection(values):
            raise JevError(
                "invalid_setting",
                "The coach provider or model setting is invalid.",
                "Select anthropic, openai, or disabled. Use a model ID for that provider, "
                "such as claude-opus-5 or gpt-5.6-luna, without spaces or API keys.",
            ) from None
        raise JevError(
            "invalid_setting",
            validation_message(error),
            "Set model=jev-latest or a pinned version such as jev-1.13.0."
            if "model" in values
            else "Correct the named setting; jevlab config --json shows the current values.",
        ) from None
    wb.update_settings(updated)


@app.command(help="Configure credentials, models, and local preferences.", rich_help_panel=OPERATE)
@guarded
def config(
    json_output: JsonFlag = False,
    set_values: Annotated[
        list[str] | None, typer.Option("--set", help="Nonsecret FIELD=VALUE; repeatable.")
    ] = None,
    provider: Annotated[str, typer.Option(help="Credential provider.")] = "typesafe",
    key_stdin: Annotated[
        bool, typer.Option("--key-stdin", help="Read a key from stdin into protected storage.")
    ] = False,
) -> None:
    wb = workbench()
    if provider not in ("typesafe", "anthropic", "openai"):
        raise JevError(
            "invalid_provider", "Unknown provider.", "Choose typesafe, anthropic, or openai."
        )
    selected = cast(Provider, provider)
    values: dict[str, object] = {}
    for setting in set_values or []:
        field, separator, value = setting.partition("=")
        if not separator or field not in Settings.model_fields:
            raise JevError(
                "invalid_setting",
                "Unknown or malformed setting.",
                "Use --set FIELD=VALUE. Keys belong in protected storage.",
            )
        values[field] = value
    if key_stdin and values.get("credential_mode") == "environment":
        raise JevError(
            "invalid_setting",
            "The key would be saved to protected storage but environment-only mode was requested.",
            "Omit --set credential_mode=environment when using --key-stdin, or set the key "
            "in the provider's environment variable instead.",
        )
    if set_values:
        change_settings(wb, values)
    if key_stdin:
        value = sys.stdin.read(16385).strip()
        if len(value) > 16384:
            raise JevError("invalid_key", "Key input is too long.", "Supply only the API key.")
        Credentials().save(selected, value)
        if wb.settings.credential_mode != "keychain":
            change_settings(wb, {"credential_mode": "keychain"})
    if not json_output and not set_values and not key_stdin:
        if not sys.stdin.isatty():
            raise JevError(
                "terminal_required",
                "Interactive setup needs a terminal.",
                "Use --json to inspect or --set to configure.",
            )
        if wb.settings.ui_mode == "simple" and selected == "typesafe":
            console.print(
                Text(
                    "Add a TypeSafe key for live decisions. Press Enter to skip "
                    "and try the free demo."
                )
            )
            value = typer.prompt(
                "TypeSafe API key (Enter to skip)",
                default="",
                hide_input=True,
                show_default=False,
            )
            if value:
                Credentials().save(selected, value)
                values["credential_mode"] = "keychain"
        else:
            console.print(Text("JevLab setup · keys stay in protected storage or your environment"))
            expert = typer.confirm(
                "Show Expert mode (all technical options)? "
                "Simple mode guides you through common tasks.",
                default=wb.settings.ui_mode == "expert",
            )
            values["ui_mode"] = "expert" if expert else "simple"
            environment = typer.confirm(
                "Use environment variables only?",
                default=wb.settings.credential_mode == "environment",
            )
            values["credential_mode"] = "environment" if environment else "keychain"
            if not environment:
                value = typer.prompt(
                    f"{selected} API key (empty keeps current)",
                    default="",
                    hide_input=True,
                    show_default=False,
                )
                if value:
                    Credentials().save(selected, value)
            values["model"] = typer.prompt("Default model", default=wb.settings.model)
            if selected != "typesafe":
                enable = typer.confirm(
                    f"Use {selected} for optional coaching? "
                    "Selected designs/states are sent to it.",
                    default=wb.settings.coach_provider == selected,
                )
                if enable:
                    values["coach_provider"] = selected
                    values["coach_model"] = typer.prompt(
                        "Coach API model ID", default=wb.settings.coach_model_for(selected)
                    )
        if values:
            change_settings(wb, values)
    data = {
        "settings": wb.settings.model_dump(),
        "credentials": Credentials(wb.settings.credential_mode).status(),
        "privacy": PRIVACY,
        "retention_enforced": True,
    }
    if json_output:
        emit(data)
    elif wb.settings.ui_mode == "simple":
        credentials = cast(dict[str, object], data["credentials"])
        typesafe_status = cast(dict[str, object], credentials["typesafe"])
        saved_or_checked = (
            "Settings saved." if values or set_values or key_stdin else "Setup checked."
        )
        if isinstance(typesafe_status.get("error"), dict):
            readiness = (
                f"{saved_or_checked} The TypeSafe key could not be checked. "
                "Run jevlab doctor for the cause."
            )
        elif typesafe_status.get("present"):
            readiness = f"{saved_or_checked} A TypeSafe key was found; its validity was not tested."
        else:
            readiness = (
                f"{saved_or_checked} No TypeSafe key was found. Add one with jevlab config "
                "or set TYPESAFE_API_KEY before a live run."
            )
        console.print(
            Text(
                f"{readiness}\n"
                "Display: Simple (guided screens; advanced options stay available).\n"
                f"Decision model: {wb.settings.model}\n"
                f"Optional coach: {wb.settings.coach_provider}\n"
                f"Local files: {wb.root}\n"
                "API keys are private access codes. Their values are never shown below."
            )
        )
        visible_credentials = {"typesafe": credentials["typesafe"]}
        if wb.settings.coach_provider in ("anthropic", "openai"):
            visible_credentials[wb.settings.coach_provider] = credentials[
                wb.settings.coach_provider
            ]
        display_credentials(visible_credentials)
        console.print(
            Text(
                "Try jevlab demo for a free recorded example, or jevlab tour for a guided start.\n"
                "Show advanced settings: jevlab config --set ui_mode=expert"
            )
        )
    else:
        console.print(Text(json.dumps(data, indent=2)))


def display_credentials(credentials: dict[str, object]) -> None:
    for provider, value in credentials.items():
        details = cast(dict[str, object], value)
        if isinstance(error := details.get("error"), dict):
            console.print(Text(f"{provider} key: could not check"))
            console.print(Text(human_error(JevError.from_dict(error))))
            continue
        present = bool(details.get("present"))
        status = f"found in {details.get('source')}" if present else "not added"
        console.print(Text(f"{provider} key: {status}"))


def display_doctor(report: dict[str, object]) -> None:
    templates = cast(list[dict[str, object]], report["templates"])
    good = sum(bool(row["valid"]) for row in templates)
    retention = cast(dict[str, object], report["retention"])
    disk_bytes = cast(int, report["disk_bytes"])
    console.print(
        Text(
            "Installation check\n"
            f"Saved history: {'healthy' if report['database'] == 'ok' else 'needs attention'}\n"
            f"Decision designs (templates): {good} of {len(templates)} can be read\n"
            f"Space used: {disk_bytes / 1_000_000:.2f} MB\n"
            f"History retention: {retention['days']} days, with a size limit\n"
            f"Local files: {report['data_directory']}"
            + (
                f"\nTypeSafe endpoint: {report['endpoint']} (custom)"
                if report.get("endpoint") != DEFAULT_BASE_URL
                else ""
            )
        )
    )
    display_credentials(cast(dict[str, object], report["credentials"]))
    for row in templates:
        if isinstance(detail := row.get("error"), dict):
            emit_error(JevError.from_dict(detail), False)
    if report["path_collision"]:
        emit_error(
            JevError(
                "path_collision",
                "More than one installed command is named jevlab.",
                "Use jevlab doctor --json to see the locations "
                "before removing an old installation.",
            ),
            False,
        )
    if report["database"] != "ok":
        emit_error(
            JevError(
                "database_health",
                "The saved history database did not pass its local check.",
                "Keep a copy of your history database and its WAL/SHM files "
                "and ask for help before changing it.",
            ),
            False,
        )
    if retention.get("maintenance_error"):
        emit_error(
            JevError(
                "history_maintenance",
                "Automatic history cleanup could not finish.",
                "Run jevlab clean --dry-run to inspect what can be removed.",
            ),
            False,
        )
    console.print(
        Text(
            (
                "TypeSafe connection: checked successfully.\n"
                if report["network_checked"]
                else "This was a local check. "
                "No internet connection or API key validity was tested.\n"
            )
            + "Coach setup: jevlab doctor --coach --offline\n"
            + "Full report for troubleshooting: jevlab doctor --json"
        )
    )


@app.command(
    help="Check installation and credentials; live checks are opt-in.", rich_help_panel=OPERATE
)
@guarded
def doctor(
    json_output: JsonFlag = False,
    online_check: Annotated[
        bool, typer.Option("--online", help="Also list available models with your key; no charge.")
    ] = False,
    coach_check: Annotated[
        bool,
        typer.Option("--coach", help="Check both coaches, including cost-confirmed live calls."),
    ] = False,
    offline: Annotated[
        bool, typer.Option("--offline", help="With --coach, inspect setup and estimates only.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Authorize one live request per ready coach provider.")
    ] = False,
) -> None:
    if (offline or yes) and not coach_check or (coach_check and online_check) or (offline and yes):
        raise JevError(
            "invalid_options",
            "These doctor options cannot be combined.",
            "Use --online for TypeSafe, --coach for live coach checks, or --coach --offline.",
        )
    if coach_check:
        from jevlab.cli.diagnostics import coach_diagnostics

        coach_diagnostics(
            load_settings(data_directory()), machine=json_output, offline=offline, yes=yes
        )
        return
    wb = workbench()
    report = inspect(wb)
    if online_check:
        confirm_spend(
            SpendEstimate(
                1,
                0,
                "Check your TypeSafe connection by reading the available model list.",
                "This sends no decision request and is not expected to incur a model charge.",
            ),
            settings=wb.settings,
            machine=json_output,
        )
        report["models"] = asyncio.run(online(wb))
        report["network_checked"] = True
    if json_output:
        emit(report)
    elif wb.settings.ui_mode == "simple":
        display_doctor(report)
    else:
        console.print(Text(json.dumps(report, indent=2)))


# Registered after run/config/doctor so each help panel lists the everyday command first.
app.command(
    "compare", help="Compare two designs or model versions on one state.", rich_help_panel=DEVELOP
)(compare_command)
app.command(
    "export",
    help="Generate a standalone SDK module from a saved template.",
    rich_help_panel=DEVELOP,
)(export)
app.command(
    "batch", help="Run a dataset with concurrency and resumable progress.", rich_help_panel=OPERATE
)(batch)
app.command(
    "serve", help="Expose saved templates through a local HTTP API.", rich_help_panel=OPERATE
)(serve)
app.command("clean", help="Preview or apply history retention limits.", rich_help_panel=OPERATE)(
    clean
)
register_guidance(app, panel=LEARN)
app.command("guide", rich_help_panel=LEARN)(guide)


def main() -> None:
    configure_error_details("--verbose" in sys.argv)
    try:
        exit_code = app(standalone_mode=False)
        if isinstance(exit_code, int) and exit_code:
            raise SystemExit(exit_code)
    except typer.TyperException as error:
        machine = "--json" in sys.argv or ("--state" in sys.argv and "-" in sys.argv)
        safe = JevError("usage", error.format_message(), "Run jevlab --help for usage.")
        emit_error(safe, machine)
        raise SystemExit(error.exit_code) from None
    except (typer.Abort, KeyboardInterrupt):
        raise SystemExit(130) from None
