"""CI checks, saved baselines, confidence intervals and threshold recommendations."""

import json
import shutil
from pathlib import Path

import httpx2
import pytest
from typer.testing import CliRunner
from typesafe_sdk import Choice, JSONContent

from jevlab.cli.app import app
from jevlab.core.client import Evaluation, Evaluator, SDKClient
from jevlab.core.evaluation import (
    Observation,
    QuestionMetrics,
    recommend_threshold,
    wilson_interval,
)
from jevlab.core.models import ConfidenceGate, NoulGate, Run, Settings, Template
from jevlab.core.regression import read_snapshot
from jevlab.core.service import Workbench
from jevlab.core.templates import parse_template

ROUTING = Path(__file__).parents[1] / "examples" / "ticket-routing" / "decision.yaml"
CASES = {
    "c1": ("Please refund my duplicate charge.", "billing"),
    "c2": ("The export button crashes the app.", "technical"),
    "c3": ("Can I become a reseller?", "other"),
    "c4": ("My invoice total is wrong.", "billing"),
    "c5": ("Login fails with an error code.", "technical"),
    "c6": ("Where is your office?", "other"),
}
runner = CliRunner()


class Router:
    """Synthetic answers per state, returned through the official SDK and a mock transport."""

    def __init__(self, answers: dict[str, tuple[str, float]], fail: str | None = None) -> None:
        self.answers, self.fail = answers, fail
        self.calls = 0

    async def evaluate(self, template: Template, state: JSONContent) -> Evaluation:
        self.calls += 1
        choice, confidence = self.answers[str(state)]
        question = template.questions["route"]
        assert isinstance(question, Choice)
        others = [label for label in question.criteria if label != choice]
        probabilities = {choice: confidence} | {label: (1 - confidence) / 2 for label in others}
        body = {
            "model": "jev-1.13.0",
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": choice,
                    "confidence": confidence,
                    "probabilities": probabilities,
                }
            },
            "usage": {"input_tokens": 40, "output_tokens": 0},
        }
        status = 401 if state == self.fail else 200

        def respond(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(status, json=body if status == 200 else {"error": "denied"})

        transport = httpx2.MockTransport(respond)
        async with SDKClient("synthetic-key", Settings(max_retries=0), transport=transport) as c:
            return await c.evaluate(template, state)


def correct_answers(**overrides: tuple[str, float]) -> dict[str, tuple[str, float]]:
    answers = {state: (label, 0.9) for state, label in CASES.values()}
    for case_id, answer in overrides.items():
        answers[CASES[case_id][0]] = answer
    return answers


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    design = tmp_path / "decision.yaml"
    shutil.copy(ROUTING, design)
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        "".join(
            json.dumps({"id": case_id, "state": state, "expected": {"route": label}}) + "\n"
            for case_id, (state, label) in CASES.items()
        )
    )
    return design, cases


def use(wb: Workbench, fake: Evaluator, monkeypatch: pytest.MonkeyPatch) -> None:
    import jevlab.cli.evaluation as commands

    monkeypatch.setattr(commands, "workbench", lambda: wb)
    original = Workbench.run.__get__(wb)  # The unpatched method, even when re-patched.

    async def mocked(
        template: Template,
        state: JSONContent,
        *,
        evaluator: Evaluator | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        return await original(
            template, state, evaluator=fake, parent_run_id=parent_run_id, run_id=run_id
        )

    monkeypatch.setattr(wb, "run", mocked)


def invoke(*arguments: str) -> tuple[int, dict[str, object]]:
    result = runner.invoke(app, [*arguments, "--json"])
    return result.exit_code, json.loads(result.stdout)


def test_wilson_interval_is_bounded_and_informative_for_small_samples() -> None:
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(5, 5) or (0, 0)
    assert high == 1.0 and 0.56 < low < 0.57
    low, high = wilson_interval(50, 100) or (0, 0)
    assert 0.40 < low < 0.41 and 0.59 < high < 0.60


def choice_metrics(points: list[tuple[float, bool]]) -> QuestionMetrics:
    observations = [
        Observation(
            case_id=f"c{index}",
            run_id=f"r{index}",
            predicted="a",
            expected="a" if correct else "b",
            value="a",
            probability=confidence,
            confidence=confidence,
            correct=correct,
        )
        for index, (confidence, correct) in enumerate(points)
    ]
    return QuestionMetrics(
        primitive="choice", total=len(points), answered=len(points), observations=observations
    )


def test_recommendation_maximizes_coverage_meeting_the_target() -> None:
    metrics = choice_metrics([(0.5, False), (0.6, True), (0.7, False), (0.8, True), (0.9, True)])
    result = recommend_threshold(metrics, 0.9)
    assert result.recommended is not None
    assert result.recommended.gate == ConfidenceGate(automate_at_or_above=0.8)
    assert result.recommended.automated == 2 and result.recommended.accuracy == 1.0
    loose = recommend_threshold(metrics, 0.6)
    assert loose.recommended and loose.recommended.gate == ConfidenceGate(automate_at_or_above=0.5)
    # Two correct cases cannot establish 90% with 95% confidence.
    strict = recommend_threshold(metrics, 0.9, conservative=True)
    assert strict.recommended is None and "No gate" in strict.note


def test_noul_recommendation_chooses_each_side_separately() -> None:
    points = [(0.05, False), (0.2, False), (0.3, True), (0.6, False), (0.8, True), (0.95, True)]
    observations = [
        Observation(
            case_id=f"n{index}",
            run_id=f"r{index}",
            predicted=value >= 0.5,
            expected=expected,
            value=value,
            probability=value if value >= 0.5 else 1 - value,
            correct=(value >= 0.5) == expected,
        )
        for index, (value, expected) in enumerate(points)
    ]
    metrics = QuestionMetrics(primitive="noul", total=6, answered=6, observations=observations)
    result = recommend_threshold(metrics, 1.0)
    assert result.recommended is not None
    assert result.recommended.gate == NoulGate(no_at_or_below=0.2, yes_at_or_above=0.8)
    assert result.recommended.automated == 4 and result.recommended.accuracy == 1.0


def test_save_baseline_then_check_passes_and_regressions_fail_with_exit_5(
    wb: Workbench,
    project: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design, cases = project
    baseline = tmp_path / "baseline.json"
    use(wb, Router(correct_answers()), monkeypatch)
    code, envelope = invoke(
        "eval", "run", str(design), str(cases), "--save-baseline", str(baseline)
    )
    assert code == 0 and envelope["ok"] is True
    assert read_snapshot(baseline).evaluation.per_question["route"].accuracy == 1.0

    code, envelope = invoke("eval", "check", str(design), str(cases), "--baseline", str(baseline))
    assert code == 0 and envelope["ok"] is True
    data = envelope["data"]
    assert isinstance(data, dict) and data["kind"] == "jevlab_check" and data["passed"] is True
    route = data["per_question"]["route"]
    assert route["accuracy"] == 1.0 and route["accuracy_interval"][1] == 1.0

    use(wb, Router(correct_answers(c1=("technical", 0.8))), monkeypatch)
    report = tmp_path / "check.json"
    code, envelope = invoke(
        "eval", "check", str(design), str(cases), "--baseline", str(baseline),
        "--output", str(report),
    )  # fmt: skip
    assert code == 5 and envelope["ok"] is False
    assert envelope["error"]["code"] == "quality_gate_failed"  # type: ignore[index]
    comparison = envelope["data"]["comparison"]  # type: ignore[index]
    assert comparison["per_question"]["route"]["regressed"] == 1
    assert json.loads(report.read_text())["passed"] is False


def test_min_accuracy_only_check_and_limits_are_required(
    wb: Workbench, project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    design, cases = project
    fake = Router(correct_answers(c1=("technical", 0.8)))
    use(wb, fake, monkeypatch)
    code, envelope = invoke("eval", "check", str(design), str(cases))
    assert code == 2 and envelope["error"]["code"] == "check_limits_required"  # type: ignore[index]
    assert fake.calls == 0
    code, envelope = invoke("eval", "check", str(design), str(cases), "--min-accuracy", "0.9")
    assert code == 5
    failures = envelope["data"]["failures"]  # type: ignore[index]
    assert any("below 90.00%" in failure for failure in failures)
    code, envelope = invoke("eval", "check", str(design), str(cases), "--min-accuracy", "0.8")
    assert code == 0 and envelope["data"]["comparison"] is None  # type: ignore[index]


def test_unpaired_dataset_is_rejected_before_any_call(
    wb: Workbench,
    project: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design, cases = project
    baseline = tmp_path / "baseline.json"
    use(wb, Router(correct_answers()), monkeypatch)
    assert invoke("eval", "run", str(design), str(cases), "--save-baseline", str(baseline))[0] == 0
    changed = tmp_path / "changed.jsonl"
    changed.write_text("".join(cases.read_text().splitlines(keepends=True)[:5]))
    fake = Router(correct_answers())
    use(wb, fake, monkeypatch)
    before = len(wb.storage.history(limit=1000))
    code, envelope = invoke("eval", "check", str(design), str(changed), "--baseline", str(baseline))
    assert code == 2 and envelope["error"]["code"] == "unpaired_dataset"  # type: ignore[index]
    assert fake.calls == 0 and len(wb.storage.history(limit=1000)) == before
    existing = tmp_path / "exists.json"
    existing.write_text("{}")
    code, envelope = invoke(
        "eval", "check", str(design), str(cases), "--min-accuracy", "0.5",
        "--output", str(existing),
    )  # fmt: skip
    assert code == 2 and envelope["error"]["code"] == "artifact_exists"  # type: ignore[index]
    assert fake.calls == 0


def test_incomplete_check_is_an_execution_failure_not_a_quality_verdict(
    wb: Workbench, project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    design, cases = project
    use(wb, Router(correct_answers(), fail=CASES["c3"][0]), monkeypatch)
    result = runner.invoke(
        app, ["eval", "check", str(design), str(cases), "--min-accuracy", "0.5", "--json"]
    )
    assert result.exit_code == 4
    assert json.loads(result.stdout)["error"]["code"] == "job_incomplete"


def test_tune_recommends_and_saves_a_gate_and_shows_a_curve(
    wb: Workbench, project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    design, cases = project
    answers = correct_answers(c1=("technical", 0.5), c2=("technical", 0.95))
    use(wb, Router(answers), monkeypatch)
    code, envelope = invoke("eval", "run", str(design), str(cases))
    assert code == 0
    job = envelope["data"]["id"]  # type: ignore[index]
    code, envelope = invoke("eval", "tune", str(job), "route")
    assert code == 0 and len(envelope["data"]["curve"]) == 11  # type: ignore[index]
    code, envelope = invoke(
        "eval", "tune", str(job), "route", "--target-accuracy", "1",
        "--save", "--template", str(design),
    )  # fmt: skip
    assert code == 0
    data = envelope["data"]
    assert isinstance(data, dict) and data["saved"] is True
    assert data["stats"]["gate"] == {"kind": "confidence", "automate_at_or_above": 0.9}
    assert data["stats"]["automated"] == 5
    saved = parse_template(design.read_text())
    assert saved.thresholds["route"] == ConfidenceGate(automate_at_or_above=0.9)
    code, envelope = invoke("eval", "tune", str(job), "route", "--target-accuracy", "1",
                            "--threshold", "0.5")  # fmt: skip
    assert code == 2 and envelope["error"]["code"] == "invalid_gate"  # type: ignore[index]
