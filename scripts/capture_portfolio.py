"""Render portfolio SVGs with isolated synthetic fixtures and no network or Keychain.

Run from the checkout: ``uv run python scripts/capture_portfolio.py``.
Only docs/assets/*.svg are written permanently. Temporary profiles and their
synthetic history are removed on exit; the user's ~/.jevlab is never opened.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Static
from typesafe_sdk import Choice, JSONContent, Noul, SystemOneResponse

from jevlab.core.client import Evaluation
from jevlab.core.compare import compare
from jevlab.core.content import LabeledCase, export_dataset, lesson, pattern
from jevlab.core.jobs import BatchService
from jevlab.core.models import Settings, Template
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.evaluation import CompareResultScreen, EvalScreen, ThresholdScreen
from jevlab.tui.learning import LearnScreen, LessonScreen
from jevlab.tui.screens import ResultScreen

ASSETS = Path(__file__).resolve().parents[1] / "docs" / "assets"
CAPTION = (
    "Mock responses for UI demonstration. Values are fixtures; timings are not API benchmarks."
)


class FixtureEvaluator:
    """A deliberately controlled example, never an inference or performance test."""

    def __init__(self, cases: list[LabeledCase]) -> None:
        self.cases = cases

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        index, case = next(
            (index, case) for index, case in enumerate(self.cases) if case.state == state
        )
        variant = template.name.endswith("-variant")
        confidence = 0.94 if variant else [0.72, 0.90, 0.96, 0.62, 0.91][index]
        probability = 0.96 if variant else [0.82, 0.94, 0.98, 0.67, 0.95][index]
        answers: dict[str, object] = {}
        for name, question in template.questions.items():
            expected = case.expected[name]
            if isinstance(question, Choice):
                selected = str(expected)
                if index == 3 and not variant:
                    selected = "billing"  # One deliberate miss makes report inspection visible.
                answers[name] = {
                    "type": "choice",
                    "choice": selected,
                    "confidence": confidence,
                    "probabilities": {
                        option: probability
                        if option == selected
                        else (1 - probability) / (len(question.criteria) - 1)
                        for option in question.criteria
                    },
                }
            elif isinstance(question, Noul):
                yes = 0.96 if variant else 0.78
                answers[name] = {"type": "noul", "noul": yes if expected else 1 - yes}
            else:
                selected_level = int(expected)
                distribution = {
                    level: probability
                    if level == selected_level
                    else (1 - probability) / (len(question.criteria) - 1)
                    for level in range(len(question.criteria))
                }
                answers[name] = {
                    "type": "score",
                    "score": sum(level * value for level, value in distribution.items()),
                    "confidence": confidence,
                    "legend": {str(level): text for level, text in enumerate(question.criteria)},
                    "probabilities": {str(level): value for level, value in distribution.items()},
                }
        raw: dict[str, object] = {
            "model": "jev-1.13.0",
            "answers": answers,
            "usage": {"input_tokens": 640, "output_tokens": 84},
        }
        response = SystemOneResponse.model_validate_json(json.dumps(raw))
        return Evaluation(response=response, raw=raw, request_id="synthetic-portfolio-fixture")


def blocked(*args: object, **kwargs: object) -> None:
    raise RuntimeError("Portfolio capture must not access the network or Keychain.")


async def capture(profile: Path) -> None:
    wb = Workbench(profile)
    wb.update_settings(Settings(credential_mode="environment", coach_provider="disabled"))
    design = wb.templates.load("support-triage")
    design.description = "Synthetic demo: route a ticket, inspect impact, and identify refunds."
    wb.templates.save(design, overwrite=True)
    # Keep the comparison focused on two judgments so both panels and deltas fit.
    baseline = design.model_copy(deep=True)
    baseline.name = "routing-baseline"
    baseline.questions = {
        name: question for name, question in baseline.questions.items() if name != "impact"
    }
    baseline.thresholds = {
        name: gate for name, gate in baseline.thresholds.items() if name != "impact"
    }
    wb.templates.save(baseline)
    variant = baseline.model_copy(deep=True)
    variant.name = "routing-variant"
    variant.questions["route"].instructions = (
        "Which team should handle the primary request in ticket.message? "
        "Treat duplicate payments and invoice corrections as billing; requests about "
        "hiring belong to other."
    )
    wb.templates.save(variant)
    examples = pattern("support-routing")
    evaluator = FixtureEvaluator(examples.cases)
    state = examples.cases[0].state
    result = await wb.run(design, state, evaluator=evaluator)
    comparison = await compare(wb, baseline, variant, state, evaluator=evaluator)
    dataset = export_dataset("support-routing", profile / "synthetic-support.jsonl")
    job = await BatchService(wb).run(
        design,
        dataset,
        kind="eval",
        evaluator=evaluator,
        concurrency=2,
        requests_per_second=1000,
    )
    if job.status != "completed":
        raise RuntimeError("Synthetic evaluation failed; do not publish its screenshots.")

    shots: list[tuple[str, WorkbenchScreen]] = [
        ("playground.svg", ResultScreen(wb, result)),
        ("evaluation.svg", EvalScreen(wb, job)),
        ("thresholds.svg", ThresholdScreen(wb, job)),
        ("compare.svg", CompareResultScreen(wb, comparison)),
        ("learn.svg", LearnScreen(wb)),
        ("lesson-10.svg", LessonScreen(wb, lesson("10"))),
    ]
    ASSETS.mkdir(parents=True, exist_ok=True)
    app = JevApp(wb)
    app.sub_title = "Synthetic demo · no live API calls"
    async with app.run_test(size=(120, 48)) as pilot:
        await pilot.pause()
        for filename, screen in shots:
            await app.push_screen(screen)
            await pilot.pause()
            await screen.query_one(".page").mount(
                Static(CAPTION, classes="muted", markup=False), before=0
            )
            await pilot.pause()
            svg = app.export_screenshot(title="jevlab · synthetic portfolio demo")
            if str(profile) in svg:
                raise RuntimeError("A temporary filesystem path leaked into a screenshot.")
            destination = ASSETS / filename
            destination.write_text(
                "\n".join(line.rstrip() for line in svg.splitlines()) + "\n", encoding="utf-8"
            )
            print(destination.relative_to(ASSETS.parent.parent))
            await app.pop_screen()
            await pilot.pause()


def main() -> None:
    # Give exported screenshots their intended theme independently of the capture shell.
    # This process does not alter the caller's environment or any user preferences.
    os.environ.pop("NO_COLOR", None)
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    for name in ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(name, None)
    with (
        tempfile.TemporaryDirectory(prefix="jevlab-portfolio-") as temporary,
        patch("socket.socket.connect", side_effect=blocked),
        patch("socket.socket.connect_ex", side_effect=blocked),
        patch("keyring.get_password", side_effect=blocked),
    ):
        asyncio.run(capture(Path(temporary)))


if __name__ == "__main__":
    main()
