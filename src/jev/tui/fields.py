"""Consistent, value-independent form guidance and immediate local feedback."""

import math
import re
from collections.abc import Callable
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Collapsible, Input, Label, Select, Static, TextArea

Validator = Callable[[str], str | None]


class Field(Vertical):
    """A named control with persistent errors and optional explanatory help.

    Help never incorporates entered values, especially credentials. Validators return
    safe, actionable prose; complex forms can set an error with ``set_error``.
    """

    def __init__(
        self,
        control: Widget,
        label: str,
        description: str,
        example: str = "",
        *,
        validator: Validator | None = None,
        simple: bool | None = None,
        classes: str | None = None,
    ) -> None:
        if not control.id:
            raise ValueError("Guided controls need a stable ID.")
        super().__init__(id=f"field-{control.id}", classes=classes)
        self.control = control
        self.label = label
        self.description = description
        self.example = example
        self.validator = validator
        self.simple = simple
        self.mode_simple: bool | None = None
        self.error = ""
        if (
            isinstance(control, Input)
            and not control.placeholder
            and example
            and not control.password
        ):
            control.placeholder = example

    def compose(self) -> ComposeResult:
        yield Label(self.label)
        yield self.control
        with Collapsible(title="Field help", collapsed=False, classes="field-help"):
            yield Static(self.description, markup=False, classes="field-description")
            if self.example:
                yield Static(f"Example: {self.example}", markup=False, classes="field-example")
        yield Static("", markup=False, classes="field-error")

    def on_mount(self) -> None:
        wb = getattr(self.screen, "wb", None) or getattr(self.app, "wb", None)
        simple = (
            self.simple
            if self.simple is not None
            else (wb is None or wb.settings.ui_mode == "simple")
        )
        self.apply_mode(simple)
        self.validate()

    def apply_mode(self, simple: bool) -> None:
        if self.mode_simple == simple:
            return
        self.mode_simple = simple
        self.set_class(simple, "simple-field")
        self.query_one(Collapsible).collapsed = not simple

    def set_error(self, message: str | None) -> None:
        self.error = message or ""
        notice = self.query_one(".field-error", Static)
        notice.update(self.error)
        notice.display = bool(self.error)
        self.control.set_class(bool(self.error), "invalid-field")

    def validate(self) -> bool:
        if self.validator:
            value = (
                self.control.value
                if isinstance(self.control, Input)
                else self.control.text
                if isinstance(self.control, TextArea)
                else str(self.control.value)
                if isinstance(self.control, Select)
                else ""
            )
            self.set_error(self.validator(value))
        return not self.error

    @on(Input.Changed)
    @on(TextArea.Changed)
    @on(Select.Changed)
    def changed(self) -> None:
        if self.is_mounted:
            self.validate()


def required(label: str, example: str) -> Validator:
    def validate(value: str) -> str | None:
        return None if value.strip() else f"{label} is empty. Enter {example}."

    return validate


def identifier(value: str, *, question: bool = False) -> str | None:
    pattern = r"[A-Za-z][A-Za-z0-9_-]{0,63}" if question else r"[a-z][a-z0-9_-]{0,63}"
    if re.fullmatch(pattern, value):
        return None
    return (
        "Use 1–64 letters, digits, hyphens or underscores, starting with "
        f"{'a letter' if question else 'a lowercase letter; use lowercase throughout'}. "
        f"Example: {'urgency' if question else 'support-routing'}."
    )


def numeric(
    label: str,
    minimum: float,
    maximum: float | None = None,
    *,
    integer: bool = False,
    exclusive_minimum: bool = False,
) -> Validator:
    def validate(value: str) -> str | None:
        try:
            number = int(value) if integer else float(value)
            valid = math.isfinite(number) and (
                number > minimum if exclusive_minimum else number >= minimum
            )
            valid = valid and (maximum is None or number <= maximum)
        except (ValueError, OverflowError):
            valid = False
        if valid:
            return None
        low = f"{minimum:f}".rstrip("0").rstrip(".")
        bound = f"greater than {low}" if exclusive_minimum else f"at least {low}"
        if maximum is not None:
            high = f"{maximum:f}".rstrip("0").rstrip(".")
            bound += f" and at most {high}"
        example = " Use digits, such as 4; omit decimal points and exponents." if integer else ""
        return f"{label} needs a {'whole ' if integer else ''}number {bound}.{example}"

    return validate


def input_file(value: str) -> str | None:
    if not value.strip():
        return "Choose an existing .csv or .jsonl file, such as ~/Downloads/support-cases.jsonl."
    path = Path(value).expanduser()
    if path.suffix.lower() not in (".csv", ".jsonl"):
        return "Use a .csv or .jsonl file. Each row should contain one case."
    if not path.is_file():
        return (
            "That file does not exist. Check its location or drag the file's path into this field."
        )
    return None


def output_file(
    value: str, *, require_jsonl: bool = True, resume_path: Path | None = None
) -> str | None:
    if not value.strip():
        return "Choose a result filename, such as ~/Desktop/support-results.jsonl."
    try:
        path = Path(value).expanduser()
        if require_jsonl and path.suffix.lower() != ".jsonl":
            return "Use a filename ending in .jsonl, such as support-results.jsonl."
        if path.is_symlink():
            return "This path is a symbolic link. Choose a new result filename."
        if path.is_dir():
            return "This is a folder. Add a result filename, such as support-results.jsonl."
        if path.exists() and not (
            resume_path is not None and path.resolve() == resume_path.expanduser().resolve()
        ):
            return "This file already exists. Choose a new filename; existing data is not replaced."
        for parent in path.parents:
            if parent.exists():
                if not parent.is_dir():
                    return "Part of the folder path is a file. Choose a different destination."
                break
    except (OSError, RuntimeError, ValueError):
        return "This path cannot be used. Choose a writable folder and a new result filename."
    return None
