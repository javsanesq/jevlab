"""Form-like template and question editors with an advanced YAML escape hatch."""

import json
from collections.abc import Callable

import yaml
from pydantic import TypeAdapter
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, TextArea
from typesafe_sdk import Choice

from jevlab.core.errors import JevError
from jevlab.core.models import QuestionSpec, StateSpec, Template, validate_jev_model
from jevlab.core.service import Workbench
from jevlab.core.templates import (
    TemplateSource,
    dump_template,
    load_json,
    load_yaml,
    parse_template,
    validation_message,
)
from jevlab.presentation import human_error
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.dialogs import Confirm, YamlEditor
from jevlab.tui.errors import report_error
from jevlab.tui.fields import Field, identifier, required

DEFAULT_CRITERIA = {
    "choice": {
        "matches": "The stated condition applies.",
        "other": "The condition does not apply.",
    },
    "score": ["No evidence of the property.", "Clear evidence of the property."],
    "noul": {"true": "The condition holds.", "false": "The condition does not hold."},
}


class QuestionEditor(ModalScreen[tuple[str, QuestionSpec] | None]):
    BINDINGS = [
        ("ctrl+s", "save", "Apply question"),
        ("escape", "cancel", "Cancel"),
        ("f1", "help", "Help"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(
        self,
        name: str = "judgment",
        question: QuestionSpec | None = None,
        *,
        simple: bool = False,
        reserved_names: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.question_name = name
        self.reserved_names = reserved_names or set()
        self.question = question
        self.kind = question.type if question else "choice"
        self.initial = ""
        criteria = question.criteria if question else None
        values = criteria.values() if isinstance(criteria, dict) else criteria or []
        self.guided = simple
        self.simple = simple and all(isinstance(value, str) for value in values)
        self.friendly_rows = 0
        self.criteria_drafts: dict[str, tuple[str, list[tuple[str, str]]]] = {}
        self.validation_ready = False

    def compose(self) -> ComposeResult:
        instructions = self.question.instructions if self.question else ""
        criteria = (self.question.criteria if self.question else None) or DEFAULT_CRITERIA[
            self.kind
        ]
        with VerticalScroll(classes="dialog question-dialog"):
            yield Label("QUESTION / ask one clear thing", classes="eyebrow")
            yield Field(
                Input(self.question_name, id="question-id"),
                "Answer name",
                "This short name labels the answer in results and in your code.",
                "urgency",
                validator=self.validate_name,
                simple=self.guided,
            )
            yield Field(
                Select(
                    [
                        ("Choice — pick one option", "choice"),
                        ("Score — rate on ordered levels", "score"),
                        ("Noul — how likely is yes?", "noul"),
                    ],
                    value=self.kind,
                    allow_blank=False,
                    id="question-type",
                ),
                "Kind of answer",
                "Choice: select one category, such as a support team.\n"
                "Score: measure a property on levels ordered from lowest to highest.\n"
                "Noul: estimate whether one statement is true, such as a refund request.",
                "Choice for billing / technical / other",
                simple=self.guided,
            )
            yield Field(
                TextArea(
                    instructions if isinstance(instructions, str) else json.dumps(instructions),
                    id="instructions",
                ),
                "Question wording",
                "Ask one clear thing about the supplied information. Keep answer meanings below.",
                "Which team should handle the customer's main request?",
                validator=required("Question wording", "one question about the information"),
                simple=self.guided,
            )
            with Vertical(id="criteria-fields"):
                pass
            with Horizontal(classes="buttons", id="criteria-row-buttons"):
                yield Button("Add option / level", id="add-criterion")
                yield Button("Remove last", id="remove-criterion")
            with Vertical(id="criteria-yaml-fields"):
                yield Field(
                    TextArea(yaml.safe_dump(criteria, sort_keys=False), id="criteria"),
                    "Answer meanings (criteria in YAML)",
                    "Choice: named options that do not overlap. Score: 2–10 descriptions, "
                    "ordered from lowest to highest; position sets the number. "
                    "Noul: descriptions under the quoted names 'true' and 'false'.",
                    "Choice: billing: Questions about charges or refunds.\n"
                    "Score: - No disruption.\n       - The customer cannot continue.",
                    simple=self.guided,
                )
            yield Static(
                "Describe concrete boundaries. Question IDs are not sent to the model.",
                classes="muted",
                markup=False,
            )
            yield Static("", id="question-validation", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Apply question", id="apply-question", variant="primary")
                yield Button("Cancel", id="cancel-question")
        yield Footer()

    def snapshot(self) -> str:
        return json.dumps(
            [
                self.query_one("#question-id", Input).value,
                str(self.query_one(Select).value),
                self.query_one("#instructions", TextArea).text,
                self.query_one("#criteria", TextArea).text,
                [(x.id, x.value) for x in self.query("#criteria-fields Input").results(Input)],
            ]
        )

    async def on_mount(self) -> None:
        self.query_one("#criteria-yaml-fields").display = not self.simple
        self.query_one("#criteria-fields").display = self.simple
        self.query_one("#criteria-row-buttons").display = self.simple
        if self.simple:
            await self.populate_criteria()
        self.initial = self.snapshot()
        self.validation_ready = True
        self.validate_draft()

    async def populate_criteria(self) -> None:
        container = self.query_one("#criteria-fields", Vertical)
        await container.remove_children()
        cached = self.criteria_drafts.get(self.kind)
        if cached is not None:
            items = cached[1]
        else:
            criteria = load_yaml(self.query_one("#criteria", TextArea).text)
            if not isinstance(criteria, (dict, list)):
                raise ValueError("Answer descriptions must be a mapping or list.")
            items = (
                list(criteria.items()) if isinstance(criteria, dict) else list(enumerate(criteria))
            )
        self.friendly_rows = 0
        for name, description in items:
            await self.add_criterion(str(name), str(description))
        self.query_one("#criteria-row-buttons").display = self.simple and self.kind != "noul"

    async def add_criterion(self, name: str = "", description: str = "") -> None:
        index = self.friendly_rows
        label = (
            "Answer name"
            if self.kind == "choice"
            else "Level"
            if self.kind == "score"
            else "Answer"
        )
        row = Vertical(
            Field(
                Input(
                    name if self.kind != "score" else str(index),
                    id=f"criterion-name-{index}",
                    disabled=self.kind != "choice",
                ),
                f"{label} {index + 1} — name" if self.kind == "choice" else f"{label} value",
                "Give this possible answer a unique name."
                if self.kind == "choice"
                else "Levels start at 0; higher positions mean more of the property."
                if self.kind == "score"
                else "This fixed name means yes (true) or no (false).",
                "billing"
                if self.kind == "choice"
                else "0 = no disruption"
                if self.kind == "score"
                else "true = yes",
                simple=self.guided,
            ),
            Field(
                Input(
                    description,
                    id=f"criterion-description-{index}",
                ),
                f"{label} {index + 1} — when to use it",
                "Describe concrete evidence for this answer; avoid overlap with other options."
                if self.kind == "choice"
                else "Describe this scale position; order levels from lowest to highest."
                if self.kind == "score"
                else "Describe evidence that makes the statement true or false.",
                "The message asks about charges or refunds."
                if self.kind == "choice"
                else "The customer can continue using a workaround."
                if self.kind == "score"
                else "The customer explicitly asks for money back.",
                validator=required("Answer description", "when this answer applies"),
                simple=self.guided,
            ),
            id=f"criterion-row-{index}",
            classes="criterion-row",
        )
        await self.query_one("#criteria-fields", Vertical).mount(row)
        self.friendly_rows += 1

    def criteria_value(self) -> object:
        if not self.simple:
            return load_yaml(self.query_one("#criteria", TextArea).text)
        names = [
            self.query_one(f"#criterion-name-{i}", Input).value.strip()
            for i in range(self.friendly_rows)
        ]
        descriptions = [
            self.query_one(f"#criterion-description-{i}", Input).value.strip()
            for i in range(self.friendly_rows)
        ]
        for index, description in enumerate(descriptions):
            if not description:
                raise ValueError(
                    f"{'Score level' if self.kind == 'score' else 'Answer'} {index} "
                    "has no description. Describe when that answer applies."
                )
        if self.kind != "score":
            for index, name in enumerate(names):
                if not name:
                    raise ValueError(f"Answer {index + 1} needs a name, such as billing.")
                if name in names[:index]:
                    raise ValueError(
                        f"Answer {index + 1} repeats an earlier name. Give it a unique name."
                    )
        return descriptions if self.kind == "score" else dict(zip(names, descriptions, strict=True))

    @on(Select.Changed, "#question-type")
    async def type_changed(self, event: Select.Changed) -> None:
        kind = str(event.value)
        if kind in DEFAULT_CRITERIA and kind != self.kind:
            self.validation_ready = False
            self.criteria_drafts[self.kind] = (
                self.query_one("#criteria", TextArea).text,
                [
                    (
                        self.query_one(f"#criterion-name-{i}", Input).value,
                        self.query_one(f"#criterion-description-{i}", Input).value,
                    )
                    for i in range(self.friendly_rows)
                ],
            )
            self.kind = kind
            self.query_one("#criteria", TextArea).load_text(
                self.criteria_drafts[kind][0]
                if kind in self.criteria_drafts
                else yaml.safe_dump(DEFAULT_CRITERIA[kind], sort_keys=False)
            )
            if self.simple:
                await self.populate_criteria()
            self.validation_ready = True
            self.validate_draft()

    @on(Button.Pressed)
    async def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-question":
            self.action_save()
        elif event.button.id == "add-criterion":
            if self.friendly_rows < (10 if self.kind == "score" else 255):
                await self.add_criterion()
                self.validate_draft()
        elif event.button.id == "remove-criterion":
            if self.friendly_rows > 2:
                self.friendly_rows -= 1
                await self.query_one(f"#criterion-row-{self.friendly_rows}").remove()
                self.validate_draft()
        else:
            self.action_cancel()

    def build_question(self) -> tuple[str, QuestionSpec]:
        text = self.query_one("#instructions", TextArea).text.strip()
        instructions = load_json(text) if text.startswith(("{", "[")) else text
        question = TypeAdapter(QuestionSpec).validate_python(
            {"type": self.kind, "instructions": instructions, "criteria": self.criteria_value()}
        )
        name = self.query_one("#question-id", Input).value
        if name in self.reserved_names:
            raise ValueError(
                "Another question already uses this name. Choose a unique answer name."
            )
        Template(
            name="check",
            description="Validation",
            state=StateSpec(description="State"),
            questions={name: question},
        )  # type: ignore[arg-type]
        return name, question

    def validate_name(self, value: str) -> str | None:
        if value in self.reserved_names:
            return "Another question already uses this name. Choose a unique answer name."
        return identifier(value, question=True)

    @on(Input.Changed)
    @on(TextArea.Changed)
    def validate_draft(self) -> bool:
        if not self.validation_ready:
            return False
        try:
            self.build_question()
            message = ""
        except (ValueError, yaml.YAMLError) as error:
            message = validation_message(error)
        self.query_one("#question-validation", Static).update(message)
        for field in self.query(Field):
            field.validate()
        instruction_field = self.query_one("#field-instructions", Field)
        text = self.query_one("#instructions", TextArea).text.strip()
        if text.startswith(("{", "[")):
            try:
                load_json(text)
            except ValueError as error:
                instruction_field.set_error(
                    validation_message(error)
                    + " Repair the JSON, or write the question as plain text."
                )
        if self.simple:
            names: list[str] = []
            for index in range(self.friendly_rows):
                name = self.query_one(f"#criterion-name-{index}", Input).value.strip()
                duplicate = name in names
                names.append(name)
                self.query_one(f"#field-criterion-name-{index}", Field).set_error(
                    "Give this answer a name, such as billing."
                    if not name
                    else "This name repeats an earlier answer. Give each option a unique name."
                    if duplicate and self.kind == "choice"
                    else ""
                )
        else:
            self.query_one("#field-criteria", Field).set_error(
                message
                if message
                and not instruction_field.error
                and not self.query_one("#field-question-id", Field).error
                else ""
            )
        self.query_one("#apply-question", Button).disabled = bool(message)
        return not message

    def action_save(self) -> None:
        if self.validate_draft():
            self.dismiss(self.build_question())
        else:
            try:
                self.build_question()
            except (ValueError, yaml.YAMLError) as error:
                report_error(
                    self,
                    JevError(
                        "invalid_question",
                        validation_message(error),
                        "Correct the named question field or syntax location, then apply again.",
                    ),
                )

    def action_cancel(self) -> None:
        if self.snapshot() != self.initial:

            def discard(yes: bool | None) -> None:
                if yes:
                    self.dismiss(None)

            self.app.push_screen(
                Confirm("Discard changes to this question?"),
                discard,
            )
        else:
            self.dismiss(None)

    def action_help(self) -> None:
        self.notify(
            'Choice: 2–255 options. Score: 2–10 levels. Noul keys: "true" and "false".', timeout=8
        )


class TemplateEditor(WorkbenchScreen):
    compact_fields = True
    BINDINGS = [*WorkbenchScreen.BINDINGS, Binding("ctrl+s", "save", "Save template")]

    def __init__(
        self,
        wb: Workbench,
        template: Template | None = None,
        name: str | None = None,
        *,
        source: TemplateSource | None = None,
    ) -> None:
        super().__init__(wb)
        self.source = source
        if source is not None:
            template = source.template
        self.original_name = template.name if template else None
        self.design = template or Template(
            name=name or "my-decision",
            description="Route a customer request to the right support team.",
            state=StateSpec(
                description="The customer's message and requested help.",
                format="text",
                example="I was charged twice. Please refund the duplicate charge.",
            ),
            model=wb.settings.model,
            questions={
                "route": Choice(
                    instructions="Which team should handle the customer's main request?",
                    criteria={
                        "billing": "Charges, payments, or refunds.",
                        "technical": "Product failures or account access problems.",
                        "other": "Requests unrelated to billing or technical problems.",
                    },
                )
            },
        )
        self.questions = dict(self.design.questions)
        self.baseline = ""

    def saved_template(self) -> Template:
        """Reload the exact edited source when moving to the Playground."""
        return self.wb.templates.load_reference(
            self.source.path
            if self.source and self.source.project_file
            else self.original_name or ""
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(classes="workspace-actions"):
            yield Button("Save template", id="save-template", variant="primary")
            yield Button("Edit question", id="edit-question")
            yield Button("Advanced YAML", id="advanced-yaml", classes="advanced")
        with VerticalScroll(id="editor-scroll", classes="page compact-page"):
            yield Static("TEMPLATE / save questions you can use again", classes="eyebrow")
            if self.source and self.source.project_file:
                yield Static(
                    f"Editing project file: {self.source.path}",
                    id="template-source",
                    markup=False,
                    classes="muted",
                )
            yield Static(
                "Edit a question, save the template, then run it in the Playground. "
                "Ctrl+E explains.",
                classes="muted",
            )
            with Horizontal(classes="form-row"):
                with Vertical():
                    yield Field(
                        Input(self.design.name, id="template-name"),
                        "Design name",
                        "Use this name to find and run the saved template.",
                        "support-routing",
                        validator=identifier,
                    )
                with Vertical(classes="advanced"):
                    yield Field(
                        Input(self.design.model, id="template-model"),
                        "Jev model version",
                        "Use jev-latest for the current model or pin a version for repeatable "
                        "comparisons.",
                        "jev-latest",
                    )
            yield Static("", id="model-validation", markup=False)
            yield Field(
                Select(self.question_options(), allow_blank=False, id="questions"),
                "Question to edit",
                "Choose one question, then select Edit. Each question asks one judgment.",
                "route — Choice",
            )
            with Horizontal(classes="buttons"):
                yield Button("Add", id="add-question")
                yield Button("Remove", id="remove-question")
                yield Button("Move up", id="move-up", classes="advanced")
                yield Button("Move down", id="move-down", classes="advanced")
            yield Static("", id="threshold-notice", markup=False)
            yield Field(
                Input(self.design.description, id="description"),
                "What this design does",
                "A short description helps you recognize the template later.",
                "Send customer messages to the right support team.",
                validator=required("Design description", "what this design is for"),
            )
            yield Field(
                Input(self.design.state.description, id="state-description"),
                "Information needed (state)",
                "Tell the person running this design which facts to provide for each case.",
                "The customer's message, including the problem and requested help.",
                validator=required("Information description", "which facts each case needs"),
            )
            yield Field(
                Select(
                    [
                        ("JSON — named fields, like the example below", "json"),
                        ("Text — ordinary words", "text"),
                    ],
                    value=self.design.state.format,
                    allow_blank=False,
                    id="state-format",
                ),
                "Information format",
                "Text accepts ordinary words. JSON uses named fields in braces; match the "
                "example to this choice.",
                'Text: Please refund my order. JSON: {"message": "Please refund my order."}',
            )
            yield Field(
                TextArea(
                    (
                        str(self.design.state.example or "")
                        if self.design.state.format == "text"
                        else json.dumps(self.design.state.example, indent=2, ensure_ascii=False)
                    ),
                    id="state-example",
                ),
                "Example information (optional)",
                "This starting case is loaded in the Playground. Use fictional details; never "
                "include passwords.",
                '{"ticket": {"message": "I was charged twice. Please refund one charge."}}',
            )
            yield Button("More options", id="editor-options", classes="options-toggle")
            yield Field(
                TextArea(
                    yaml.safe_dump(
                        self.design.model_dump(mode="json")["thresholds"], sort_keys=False
                    ),
                    id="thresholds",
                ),
                "When a person should check (thresholds)",
                "Choice and Score use a confidence cutoff from 0 to 1. Noul uses no/yes "
                "probability cutoffs, "
                "with no below yes. Use a question's name; {} sends all answers for review.",
                "route:\n  kind: confidence\n  automate_at_or_above: 0.90",
                classes="advanced",
            )
            yield Field(
                Input(self.design.notes, id="notes"),
                "Notes (optional)",
                "Keep reminders for yourself. Notes do not change Jev's judgment.",
                "Test against last month's anonymized support messages.",
                classes="advanced",
            )
            yield Static("", id="editor-status", markup=False)
        yield Footer()

    def question_options(self) -> list[tuple[str, str]]:
        return [(f"{name}   {q.type}", name) for name, q in self.questions.items()]

    def snapshot(self) -> str:
        values = [node.value for node in self.query(Input)]
        values.extend(node.text for node in self.query(TextArea))
        return json.dumps(
            [
                values,
                str(self.query_one("#state-format", Select).value),
                {k: q.model_dump(mode="json") for k, q in self.questions.items()},
            ],
            sort_keys=True,
        )

    def on_mount(self) -> None:
        self.baseline = self.snapshot()
        self.validate_draft()
        self.call_after_refresh(self.focus_name)

    def focus_name(self) -> None:
        self.query_one("#template-name", Input).focus(scroll_visible=False)
        self.query_one("#editor-scroll", VerticalScroll).scroll_home(animate=False)

    def validate_model(self) -> bool:
        if not self.is_mounted:
            return False
        try:
            validate_jev_model(self.query_one("#template-model", Input).value)
            message = ""
        except ValueError as error:
            message = str(error)
        self.query_one("#model-validation", Static).update(message)
        self.query_one("#field-template-model", Field).set_error(message)
        return not message

    @on(Input.Changed)
    @on(TextArea.Changed)
    @on(Select.Changed, "#state-format")
    def validate_draft(self) -> bool:
        if not self.is_mounted:
            return False
        example_field = self.query_one("#field-state-example", Field)
        thresholds_field = self.query_one("#field-thresholds", Field)
        example_field.set_error("")
        thresholds_field.set_error("")
        valid = self.validate_model()
        for field in self.query(Field):
            valid = field.validate() and valid
        try:
            if self.query_one("#state-format", Select).value == "json":
                load_json(self.query_one("#state-example", TextArea).text or "null")
        except ValueError as error:
            example_field.set_error(
                validation_message(error)
                + (
                    " Use double quotes around field names and text, or choose Text."
                    if isinstance(error, json.JSONDecodeError)
                    else ""
                )
            )
        try:
            load_yaml(self.query_one("#thresholds", TextArea).text)
        except (ValueError, yaml.YAMLError) as error:
            thresholds_field.set_error(
                validation_message(error) + " Use one mapping per question, or {} for human review."
            )
        message = ""
        try:
            self.build()
        except (JevError, ValueError, yaml.YAMLError) as error:
            message = error.message if isinstance(error, JevError) else validation_message(error)
            if not example_field.error and (
                "threshold" in message.lower() or isinstance(error, yaml.YAMLError)
            ):
                thresholds_field.set_error(message)
        self.query_one("#editor-status", Static).update(message)
        self.query_one("#save-template", Button).disabled = bool(message) or not valid
        return not message and valid

    def build(self) -> Template:
        data = self.design.model_dump(mode="json")
        data.update(
            {
                "name": self.query_one("#template-name", Input).value,
                "description": self.query_one("#description", Input).value,
                "model": self.query_one("#template-model", Input).value,
                "notes": self.query_one("#notes", Input).value,
                "state": {
                    "description": self.query_one("#state-description", Input).value,
                    "format": self.query_one("#state-format", Select).value,
                    "example": (
                        self.query_one("#state-example", TextArea).text or None
                        if self.query_one("#state-format", Select).value == "text"
                        else load_json(self.query_one("#state-example", TextArea).text or "null")
                    ),
                },
                "questions": {k: q.model_dump(mode="json") for k, q in self.questions.items()},
                "thresholds": load_yaml(self.query_one("#thresholds", TextArea).text) or {},
            }
        )
        return parse_template(yaml.safe_dump(data, sort_keys=False))

    def action_save(self) -> None:
        if not self.validate_draft():
            return
        try:
            design = self.build()
            if self.source is not None:
                self.source = self.wb.templates.save_source(self.source, design)
            else:
                self.wb.templates.save(design, overwrite=design.name == self.original_name)
            self.design, self.original_name = design, design.name
            self.baseline = self.snapshot()
            self.query_one("#editor-status", Static).update(
                f"Saved {design.name}. Open Playground from Ctrl+P to try it."
            )
            self.notify("Template saved")
        except (JevError, ValueError, yaml.YAMLError, OSError) as error:
            safe = (
                error
                if isinstance(error, JevError)
                else JevError(
                    "file_error" if isinstance(error, OSError) else "invalid_template",
                    "The template file could not be saved."
                    if isinstance(error, OSError)
                    else validation_message(error),
                    "Check that the templates directory is writable."
                    if isinstance(error, OSError)
                    else "Correct the named field or syntax location, then save again.",
                )
            )
            message = human_error(safe)
            self.query_one("#editor-status", Static).update(message)
            self.report_error(safe)

    def action_critique(self) -> None:
        from jevlab.tui.learning import CoachScreen

        try:
            design = self.build()
            self.app.push_screen(
                CoachScreen(self.wb, mode="critique", target=design.name, template=design)
            )
        except (JevError, ValueError, yaml.YAMLError):
            self.notify("Validate the draft before asking for a critique.", severity="error")

    def request_close(self, callback: Callable[[], object]) -> None:
        if self.snapshot() == self.baseline:
            callback()
        else:

            def discard(yes: bool | None) -> None:
                if yes:
                    callback()

            self.app.push_screen(
                Confirm("Discard unsaved template changes?"),
                discard,
            )

    def edit_question(self, existing: str | None = None) -> None:
        def apply(result: tuple[str, QuestionSpec] | None) -> None:
            if result is None:
                return
            name, question = result
            if name in self.questions and name != existing:
                self.notify("That question ID already exists; use a unique ID.", severity="error")
                return
            previous = self.questions.get(existing or "")
            gate_changed = previous is not None and (previous.type == "noul") != (
                question.type == "noul"
            )
            try:
                gates = load_yaml(self.query_one("#thresholds", TextArea).text) or {}
                if isinstance(gates, dict) and existing in gates:
                    gate = gates.pop(existing)
                    if gate_changed:
                        message = (
                            f"{name}: removed the incompatible threshold after changing type. "
                            "Answers now go to human review. "
                            "Set a new threshold under More options."
                        )
                        self.query_one("#threshold-notice", Static).update(message)
                        self.notify(message, timeout=15)
                    else:
                        gates[name] = gate
                    self.query_one("#thresholds", TextArea).load_text(
                        yaml.safe_dump(gates, sort_keys=False)
                    )
            except (ValueError, yaml.YAMLError):
                self.options_revealed = True
                self.apply_mode()
                self.notify("Repair the threshold YAML below before saving.")
            if existing and name != existing:
                self.questions = {
                    name if k == existing else k: question if k == existing else v
                    for k, v in self.questions.items()
                }
            else:
                self.questions[name] = question
            select = self.query_one("#questions", Select)
            select.set_options(self.question_options())
            select.value = name
            self.validate_draft()

        self.app.push_screen(
            QuestionEditor(
                existing or f"question_{len(self.questions) + 1}",
                self.questions.get(existing or ""),
                simple=self.wb.settings.ui_mode == "simple",
                reserved_names=set(self.questions) - {existing},
            ),
            apply,
        )

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        selected = str(self.query_one("#questions", Select).value)
        if button == "save-template":
            self.action_save()
        elif button == "add-question":
            self.edit_question()
        elif button == "edit-question" and selected in self.questions:
            self.edit_question(selected)
        elif button == "remove-question" and len(self.questions) > 1:
            self.questions.pop(selected, None)
            self.query_one("#questions", Select).set_options(self.question_options())
            try:
                gates = load_yaml(self.query_one("#thresholds", TextArea).text)
                if isinstance(gates, dict):
                    gates.pop(selected, None)
                    self.query_one("#thresholds", TextArea).load_text(
                        yaml.safe_dump(gates, sort_keys=False)
                    )
            except (ValueError, yaml.YAMLError):
                self.notify("Also remove the matching threshold before saving.")
        elif button in ("move-up", "move-down") and selected in self.questions:
            names = list(self.questions)
            index = names.index(selected)
            other = index + (-1 if button == "move-up" else 1)
            if 0 <= other < len(names):
                names[index], names[other] = names[other], names[index]
                self.questions = {name: self.questions[name] for name in names}
                self.query_one("#questions", Select).set_options(self.question_options())
                self.query_one("#questions", Select).value = selected
        elif button == "advanced-yaml":
            try:
                text = dump_template(self.build())
            except (JevError, ValueError, yaml.YAMLError):
                self.notify(
                    "Correct the form's invalid fields before opening advanced YAML.",
                    severity="error",
                )
                return
            self.app.push_screen(YamlEditor(text), self.apply_yaml)

    def apply_yaml(self, text: str | None) -> None:
        if text is None:
            return
        self.design = parse_template(text)
        self.questions = dict(self.design.questions)
        for selector, value in [
            ("template-name", self.design.name),
            ("template-model", self.design.model),
            ("description", self.design.description),
            ("state-description", self.design.state.description),
            ("notes", self.design.notes),
        ]:
            self.query_one(f"#{selector}", Input).value = value
        self.query_one("#state-format", Select).value = self.design.state.format
        self.query_one("#state-example", TextArea).load_text(
            str(self.design.state.example or "")
            if self.design.state.format == "text"
            else json.dumps(self.design.state.example, indent=2)
        )
        self.query_one("#thresholds", TextArea).load_text(
            yaml.safe_dump(self.design.model_dump(mode="json")["thresholds"], sort_keys=False)
        )
        self.query_one("#questions", Select).set_options(self.question_options())
