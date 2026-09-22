"""Versioned, bundled teaching material. No network or inference on reads."""

import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr
from typesafe_sdk import JSONContent

from jevlab.core.errors import JevError
from jevlab.core.models import StrictModel, Template
from jevlab.core.templates import dump_template, parse_template


class LabeledCase(StrictModel):
    id: str
    state: dict[str, JSONContent | int | bool | None]
    expected: dict[str, StrictStr | StrictBool | StrictInt]
    note: str


class Pattern(StrictModel):
    id: str
    title: str
    when_to_use: str
    notes: str
    sources: list[str]
    template: Template
    cases: list[LabeledCase]


class Lesson(StrictModel):
    id: str
    version: int = 1
    title: str
    concept: str
    example: str
    exercise: str
    pattern: str
    question_types: dict[str, Literal["choice", "score", "noul"]]
    required_fields: list[str]
    default_fields: list[str]
    tips: list[str]
    pass_fraction: float = Field(default=0.8, ge=0, le=1)
    analysis: Literal["basic", "calibration", "routing"] = "basic"
    required_thresholds: list[str] = Field(default_factory=list)


def patterns() -> list[Pattern]:
    root = files("jevlab.resources").joinpath("library")
    catalog = json.loads(root.joinpath("catalog.json").read_text())
    return [
        Pattern(
            **entry,
            template=parse_template(root.joinpath(entry["id"] + ".yaml").read_text()),
            cases=[
                LabeledCase.model_validate_json(line)
                for line in root.joinpath(entry["id"] + ".jsonl").read_text().splitlines()
                if line.strip()
            ],
        )
        for entry in catalog
    ]


def pattern(name: str) -> Pattern:
    for entry in patterns():
        if entry.id == name:
            return entry
    raise JevError("pattern_not_found", "Unknown library pattern.", "Run jevlab library --json.")


def export_dataset(pattern_id: str, path: Path) -> Path:
    """Publish canonical JSONL atomically without replacing any existing path."""
    item = pattern(pattern_id)
    destination = path.expanduser().absolute()
    if destination.suffix.lower() != ".jsonl":
        raise JevError(
            "dataset_extension",
            "Exported examples need a .jsonl filename.",
            "Choose OUTPUT.jsonl, then import it with the matching pattern template.",
        )
    content = "".join(
        case.model_dump_json(include={"id", "state", "expected"}) + "\n" for case in item.cases
    )
    temporary: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Unlike replace(), link() refuses existing files and dangling symlinks.
        # The temporary inode already contains the complete, private (0600) file.
        os.link(temporary, destination)
    except FileExistsError:
        raise JevError(
            "already_exists", "The output path already exists.", "Choose a new dataset path."
        ) from None
    except OSError:
        raise JevError(
            "dataset_export",
            "Could not write the dataset.",
            "Choose a writable local directory with available space.",
        ) from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return destination


def lessons() -> list[Lesson]:
    data = files("jevlab.resources").joinpath("lessons.json").read_text()
    return [Lesson.model_validate(item) for item in json.loads(data)]


def lesson(name: str) -> Lesson:
    for item in lessons():
        if name == item.id or (name.isdigit() and item.id.startswith(f"{int(name):02d}-")):
            return item
    raise JevError("lesson_not_found", "Unknown lesson.", "Run jevlab learn --json for the track.")


def starter(item: Lesson, name: str, model: str) -> Template:
    design = pattern(item.pattern).template.model_copy(deep=True)
    design.name, design.model = name, model
    design.questions = {key: design.questions[key] for key in item.question_types}
    design.thresholds = {}
    design.notes = f"Exercise draft for {item.id}. " + item.exercise
    # Valid but deliberately broad: the learner supplies precise instructions and boundaries.
    for question in design.questions.values():
        question.instructions = "Judge the supplied state using these criteria."
    return parse_template(dump_template(design))
