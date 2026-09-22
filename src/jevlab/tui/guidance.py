"""Free, keyboard-accessible explanations, glossary, tour, and disk-backed demo."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from jevlab.core.demo import load_demo
from jevlab.core.errors import JevError
from jevlab.core.guidance import GLOSSARY, WELCOME, Explanation
from jevlab.core.service import Workbench
from jevlab.presentation import human_error
from jevlab.rendering import render_run
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.fields import Field


class Glossary(ModalScreen[None]):
    """Searchable help that returns to the same focused form without changing it."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("f1", "help", "Help"),
        Binding("question_mark", "help", "Help", show=False),
    ]
    DEFAULT_CSS = """
    Glossary > VerticalScroll { width: 90%; height: 85%; }
    """

    def __init__(self, term: str | None = None) -> None:
        super().__init__()
        self.initial_term = term or ""

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static("Words explained", classes="eyebrow")
            yield Field(
                Input(self.initial_term, id="guidance-search"),
                "Find a word",
                "Filter terms and explanations as you type. Clear this field to see all words.",
                "confidence",
            )
            yield Static(self.entries(self.initial_term), id="glossary-list", markup=False)
            yield Button("Close", id="glossary-close", variant="primary")
        yield Footer()

    @staticmethod
    def entries(search: str) -> str:
        query = search.strip().casefold()
        matching = [
            f"{term}\n{body}"
            for term, body in GLOSSARY.items()
            if not query or query in term.casefold() or query in body.casefold()
        ]
        return (
            "\n\n".join(matching) or "No matching word. Clear the search to see every explanation."
        )

    @on(Input.Changed, "#guidance-search")
    def search_changed(self, event: Input.Changed) -> None:
        self.query_one("#glossary-list", Static).update(self.entries(event.value))

    @on(Button.Pressed, "#glossary-close")
    def action_close(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        self.notify(
            "Type a word to filter. Clear the field for all words. Esc returns to your work."
        )


class Explain(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+g", "glossary", "Glossary"),
        Binding("f1", "help", "Help"),
        Binding("question_mark", "help", "Help", show=False),
    ]
    DEFAULT_CSS = """
    Explain > VerticalScroll { width: 90%; max-width: 76; height: auto; max-height: 85%; }
    """

    def __init__(self, explanation: Explanation) -> None:
        super().__init__()
        self.explanation = explanation

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(self.explanation.title, markup=False, classes="eyebrow")
            yield Static(self.explanation.body, markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Back to my work", id="explain-close", variant="primary")
                yield Button("Glossary", id="explain-glossary")
        yield Footer()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "explain-glossary":
            self.action_glossary()
        else:
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_glossary(self) -> None:
        self.app.push_screen(Glossary(self.explanation.term))

    def action_help(self) -> None:
        self.notify("This help is free. Open the glossary for examples, or Esc to return.")


class DemoScreen(WorkbenchScreen):
    """A demonstration that is structurally unable to call a provider or save a run."""

    def compose(self) -> ComposeResult:
        recording = load_demo()
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static(recording.disclaimer, id="demo-notice", classes="eyebrow", markup=False)
            yield Static(recording.title, classes="headline", markup=False)
            yield Label("The information to judge (state)")
            yield Static(
                "I was charged twice for one order. Please refund the duplicate charge.",
                markup=False,
            )
            yield Label("Illustrative answers loaded from the bundled file")
            yield Static(
                render_run(recording.run, compact=True, illustrative=True), id="demo-result"
            )
            yield Static(recording.provenance, markup=False, classes="muted")
            yield Static(
                "The team meets its review cutoff, while the impact score does not. "
                "These rules only recommend what a connected program could do. "
                "No ticket was sent and no refund was issued.",
                markup=False,
            )
            yield Button("Back", id="demo-back", variant="primary")
        yield Footer()

    @on(Button.Pressed, "#demo-back")
    def back(self) -> None:
        self.action_back()


class TourScreen(WorkbenchScreen):
    BINDINGS = [
        Binding("escape", "skip", "Skip tour"),
        Binding("f1", "help", "Help"),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def __init__(self, wb: Workbench) -> None:
        super().__init__(wb)
        self.step = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="page"):
            yield Static("Welcome to jevlab", classes="eyebrow")
            yield Static("", id="tour-copy", markup=False)
            yield Static("", id="tour-illustration")
            with Horizontal(classes="buttons"):
                yield Button("Continue", id="tour-next", variant="primary")
                yield Button("Add a key", id="tour-key")
            with Horizontal(classes="buttons"):
                yield Button("Open free demo", id="tour-demo")
                yield Button("Skip tour", id="tour-skip")
            yield Static("", id="tour-status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.show_step()

    def show_step(self) -> None:
        copy = (
            WELCOME + "\n\nThis short tour covers account setup, a free example, and its results. "
            "Use Tab to move, Enter to choose, and Escape to leave. Ctrl+E explains the "
            "focused control; Ctrl+G opens the glossary.",
            "Step 1 of 3 / Add a key when you are ready\n\n"
            "A TypeSafe API key is a private password for your account. It allows live Jev "
            "requests, which may cost money. Choose Add a key to open Settings, select "
            "TypeSafe, enter the key in the hidden password field, and choose Save key "
            "to protected storage. Press Escape to return here.\n\n"
            "You can continue to the free demo without a key. You can add one later in Settings.",
            "Step 2 of 3 / Explore a free recorded example\n\n"
            "A customer says: I was charged twice for one order. Please refund the duplicate "
            "charge.\n\nThe demo asks which team should help, how much work is disrupted, "
            "and whether a refund was requested. Open free demo to inspect the bars, then "
            "press Escape to return here. These teaching values come from a file, not a "
            "live model; no key or payment is needed.",
            "Step 3 of 3 / Read an answer\n\n"
            "In the recorded example, billing has a 0.90 probability: the model's chance "
            "for that option. Confidence summarizes how strongly the probabilities concentrate "
            "around an answer; 0.85 does not mean 85% of such answers will be right. "
            "The recorded team confidence is 0.85, which meets the example "
            "cutoff of 0.85.\n\nThe impact confidence is 0.80, below the same cutoff, so a "
            "person should check it. The yes-or-no question gives a 0.95 probability of "
            "a refund request; it has no separate confidence. These are recommendations, "
            "not actions: nothing was sent or refunded.\n\n"
            "You can run this tour again with jevlab tour. "
            "Choose Finish to open your saved designs.",
        )
        self.query_one("#tour-copy", Static).update(copy[self.step])
        self.query_one("#tour-key", Button).display = self.step == 1
        self.query_one("#tour-demo", Button).display = self.step >= 2
        next_button = self.query_one("#tour-next", Button)
        next_button.label = "Finish" if self.step == 3 else "Continue"
        next_button.focus()
        self.query_one(VerticalScroll).scroll_home(animate=False)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "tour-next":
            if self.step == 3:
                self.finish()
            else:
                self.step += 1
                self.show_step()
        elif button == "tour-key":
            from jevlab.tui.screens import SettingsScreen

            self.app.push_screen(SettingsScreen(self.wb))
        elif button == "tour-demo":
            self.app.push_screen(DemoScreen(self.wb))
        elif button == "tour-skip":
            self.action_skip()

    def finish(self) -> None:
        try:
            self.wb.update_settings(self.wb.settings.with_updates({"tour_completed": True}))
        except (JevError, OSError, ValueError):
            message = human_error(
                JevError(
                    "tour_save",
                    "Tour completion could not be remembered because settings could not be saved.",
                    "Check permissions for your jevlab data folder. "
                    "Ctrl+Q can still close the app.",
                )
            )
            self.query_one("#tour-status", Static).update(message)
            self.notify(message, severity="error", timeout=10)
            return
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()
        else:
            self.app.exit()

    def action_skip(self) -> None:
        self.finish()

    def action_back(self) -> None:
        self.action_skip()
