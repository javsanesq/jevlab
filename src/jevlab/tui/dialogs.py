"""Small keyboard-accessible dialogs."""

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

from jevlab.tui.errors import report_error
from jevlab.tui.fields import Field, Validator, required


def state_file(value: str) -> str | None:
    if not value.strip():
        return "Choose a text or JSON file, such as ~/Downloads/ticket.json."
    if not Path(value).expanduser().is_file():
        return "That file does not exist. Enter the location of an existing text or JSON file."
    return None


class Confirm(ModalScreen[bool]):
    BINDINGS = [
        ("escape", "cancel", "Keep editing"),
        ("f1", "help", "Help"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(
        self, message: str, *, accept_label: str = "Discard", cancel_label: str = "Keep editing"
    ) -> None:
        super().__init__()
        self.message = message
        self.accept_label, self.cancel_label = accept_label, cancel_label

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(self.message, markup=False)
            with Horizontal(classes="buttons"):
                yield Button(self.cancel_label, id="keep", variant="primary")
                yield Button(self.accept_label, id="discard")

    def on_mount(self) -> None:
        self.query_one("#keep", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "discard")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_help(self) -> None:
        self.notify(f"{self.accept_label} accepts this action. {self.cancel_label} or Esc cancels.")


class Prompt(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("f1", "help", "Help"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(
        self,
        title: str,
        value: str = "",
        *,
        description: str = "",
        example: str = "",
        validator: Validator | None = None,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.value = value
        self.description = description or "Enter a value, then choose Continue. Esc cancels."
        self.example = example
        self.validator = validator or required(title, example or "a value before continuing")

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Field(
                Input(self.value, id="prompt-value"),
                self.title_text,
                self.description,
                self.example,
                validator=self.validator,
            )
            with Horizontal(classes="buttons"):
                yield Button("Continue", id="accept", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submitted(self) -> None:
        if self.query_one(Field).validate():
            self.dismiss(self.query_one(Input).value)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "accept":
            self.submitted()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        self.notify("Enter the requested name or path. Enter accepts; Esc cancels.")


class Help(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("question_mark", "close", "Close"),
        ("f1", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog help-dialog"):
            yield Static("JEV / QUICK REFERENCE", classes="eyebrow")
            yield Static(
                "Tab moves between controls; Enter activates the selected button.\n"
                "Ctrl+E  Explain the focused item\nCtrl+G  Plain-language glossary\n"
                "F2  Reopen the last error\nCtrl+P  Find any action by name\n"
                "Ctrl+N  New template (saved decision design)\n"
                "Ctrl+S  Validate and save (editor/settings)\n"
                "Ctrl+O  Load state file (playground)\nCtrl+R  Run (playground)\n"
                "Tab / Shift+Tab  Move focus\nEsc  Back or cancel\nCtrl+Q  Quit\n"
                "?  Help outside text fields    F1  Help anywhere\n\n"
                "Choice compares competing options. Score locates an input on your rubric.\n"
                "Noul is the probability of yes; it has no separate confidence.\n\n"
                "Keep state relevant. Ask one judgment per question. Compute arithmetic in code.\n"
                "Thresholds are your policy, not a guarantee of correctness.\n\n"
                "Runs call TypeSafe directly and save inputs/responses locally.\n"
                "Cost is a published-rate estimate. Unknown retry billing is not included.\n"
                "Learn: edit a draft, select state fields, then grade with real Jev calls.\n"
                "Library: open or fork examples. Coach: advice only; configure a provider first.\n"
                "Evals: inspect reliability, confusion, and misses; tune gates with arrow keys.\n"
                "Batch: preview cost, run or resume. Compare: two designs over identical state.\n"
                "Export: portable SDK modules. Cleanup: inspect eligible history before pruning.",
                markup=False,
            )
            yield Button("Close", id="close", variant="primary")

    @on(Button.Pressed)
    def action_close(self) -> None:
        self.dismiss(None)


class ErrorDetails(ModalScreen[None]):
    """Saved diagnostic information, including credential-redacted provider responses."""

    BINDINGS = [("escape", "close", "Close"), ("f1", "help", "Help")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog help-dialog"):
            yield Static("ERROR DETAILS", classes="eyebrow")
            yield Static(self.message, markup=False, id="error-details")
            yield Static(
                "Use jevlab doctor --json when asking for help. Never share an API key.",
                classes="muted",
            )
            yield Button("Close", id="close-error", variant="primary")

    @on(Button.Pressed, "#close-error")
    def action_close(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        self.notify(
            "This shows the saved cause, identifiers, and provider response when available. "
            "Credentials are redacted. Esc closes it; Ctrl+G opens the glossary."
        )


class YamlEditor(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "apply", "Apply"),
        ("f1", "help", "Help"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide-dialog"):
            yield Field(
                TextArea(self.text, show_line_numbers=True, id="yaml-editor"),
                "Full template as YAML (editable text)",
                "Edit the saved design's structure. Apply returns to the form; "
                "Ctrl+S there saves it.",
                "model: jev-latest",
                validator=self.validate_yaml,
            )
            with Horizontal(classes="buttons"):
                yield Button("Apply", id="apply", variant="primary")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self.action_apply()
        else:
            self.action_cancel()

    @staticmethod
    def validate_yaml(value: str) -> str | None:
        from jevlab.core.errors import JevError
        from jevlab.core.templates import parse_template

        try:
            parse_template(value)
        except JevError as error:
            return str(error)
        return None

    def action_apply(self) -> None:
        from jevlab.core.errors import JevError
        from jevlab.core.templates import parse_template

        text = self.query_one(TextArea).text
        try:
            parse_template(text)
        except JevError as error:
            report_error(self, error, timeout=8)
            return
        self.dismiss(text)

    def action_cancel(self) -> None:
        if self.query_one(TextArea).text == self.text:
            self.dismiss(None)
        else:

            def discard(accepted: bool | None) -> None:
                if accepted:
                    self.dismiss(None)

            self.app.push_screen(
                Confirm("Discard your YAML changes?"),
                discard,
            )

    def action_help(self) -> None:
        self.notify(
            "Use SDK-shaped questions. Quote Noul true/false keys. Apply validates the full design."
        )
