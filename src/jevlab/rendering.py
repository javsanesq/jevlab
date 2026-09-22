"""Shared Rich presentation, independent of the core execution path."""

import json

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse

from jevlab.core.guidance import explain_answer
from jevlab.core.models import Run
from jevlab.core.pricing import format_cost
from jevlab.presentation import error_for_run, human_error

ACCENT = "#67d9e8"


def probability_bar(value: float, width: int = 28) -> Text:
    filled = round(value * width)
    bar = Text("━" * filled, style=ACCENT)
    bar.append("─" * (width - filled), style="#414852")
    return bar


def render_run(
    run: Run, *, compact: bool = False, verbose: bool = False, illustrative: bool = False
) -> Group:
    parts: list[Table | Panel | Text] = []
    if illustrative:
        parts.append(
            Text("Illustrative values · time, tokens and cost were not measured", style="dim")
        )
    else:
        metadata = Table.grid(padding=(0, 3))
        metadata.add_column()
        metadata.add_column(justify="right")
        metadata.add_column(justify="right")
        metadata.add_column(justify="right")
        metadata.add_row(
            Text(f"Model: {run.resolved_model or run.requested_model}"),
            f"{run.latency_ms:,} ms" if run.latency_ms is not None else "latency unknown",
            f"{run.input_tokens:,} in" if run.input_tokens is not None else "tokens unknown",
            f"{format_cost(run.cost_nanousd)} est.",
        )
        if compact:
            compact_metadata = Table.grid(padding=(0, 1), expand=True)
            compact_metadata.add_column()
            compact_metadata.add_column(justify="right")
            compact_metadata.add_row(
                Text(run.resolved_model or run.requested_model),
                f"{run.latency_ms:,} ms" if run.latency_ms is not None else "unknown ms",
            )
            compact_metadata.add_row(
                f"{run.input_tokens:,} input tokens"
                if run.input_tokens is not None
                else "unknown tokens",
                f"{format_cost(run.cost_nanousd)} est.",
            )
            parts.append(compact_metadata)
        else:
            parts.append(metadata)
        if run.output_tokens is not None:
            free_output = (
                " · output free at the recorded rate"
                if run.price_snapshot.get("output_per_million") == "0"
                else ""
            )
            parts.append(
                Text(
                    f"{run.output_tokens:,} output tokens{free_output}",
                    style="dim",
                )
            )
    if error := error_for_run(run):
        parts.append(Text(human_error(error, verbose=verbose)))
    if run.status != "succeeded" or not run.response:
        parts.append(
            Text(
                "No complete answer is available. F2 in the TUI or jevlab --verbose history show "
                f"{run.id} opens the saved diagnostic details.",
                style="dim",
            )
        )
        return Group(*parts)
    response = SystemOneResponse.model_validate_json(json.dumps(run.response))
    for name, answer in response.answers.items():
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(ratio=2)
        table.add_column(ratio=3)
        table.add_column(justify="right", width=5)
        if isinstance(answer, NoulAnswer):
            table.add_row(
                "Probability of yes",
                probability_bar(answer.noul, 12 if compact else 28),
                f"{answer.noul:.2f}",
            )
            caption = "Noul · probability of yes · no separate confidence"
        else:
            for label, value in answer.probabilities.items():
                if isinstance(answer, ScoreAnswer):
                    legend = str(answer.legend.get(int(label), label))
                    label_text = f"{label}  {legend}"
                else:
                    label_text = str(label)
                table.add_row(
                    Text(label_text), probability_bar(value, 12 if compact else 28), f"{value:.2f}"
                )
            if isinstance(answer, ChoiceAnswer):
                caption = (
                    f"Choice: {answer.choice} · confidence "
                    f"(how firmly it favors this) {answer.confidence:.2f}"
                )
            else:
                caption = (
                    f"Score: {answer.score:.2f} · confidence "
                    f"(how focused the levels are) {answer.confidence:.2f}"
                )
        parts.append(
            Panel(
                Group(table, Text(caption, style="dim"), Text(explain_answer(run, name))),
                title=Text(name),
                border_style="#414852",
                padding=(1, 1 if compact else 2),
            )
        )
    return Group(*parts)
