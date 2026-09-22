"""Minimal, keyboard-first terminal application."""

from collections.abc import Iterable
from typing import Any

from rich.text import Text
from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.notifications import SeverityLevel
from textual.screen import ModalScreen, Screen
from textual.theme import Theme

from jevlab.core.errors import JevError
from jevlab.core.service import Workbench
from jevlab.presentation import human_error
from jevlab.tui.base import WorkbenchScreen
from jevlab.tui.dialogs import Help
from jevlab.tui.editor import TemplateEditor
from jevlab.tui.evaluation import CompareScreen, JobScreen
from jevlab.tui.harness import CleanupScreen, ExportScreen
from jevlab.tui.learning import CoachScreen, LearnScreen, LibraryScreen
from jevlab.tui.screens import History, Home, Playground, SettingsScreen


class JevApp(App[None]):
    TITLE = "jevlab"
    SUB_TITLE = "TypeSafe decision workbench"
    CSS_PATH = "theme.tcss"
    COMMAND_PALETTE_BINDING = "ctrl+p"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+e", "explain", "Explain", priority=True),
        Binding("ctrl+g", "glossary", "Glossary", priority=True),
        Binding("f2", "error_details", "Error details", show=False, priority=True),
    ]

    def __init__(
        self, wb: Workbench, *, start: str = "home", template_name: str | None = None
    ) -> None:
        super().__init__()
        self.wb, self.start, self.template_name = wb, start, template_name
        self.last_error = "No error has been reported in this session."
        self.register_theme(
            Theme(
                name="jevlab-dark",
                primary="#67d9e8",
                secondary="#67d9e8",
                accent="#67d9e8",
                foreground="#d5dce3",
                background="#0c1015",
                surface="#11171f",
                panel="#151d26",
                success="#67d9e8",
                warning="#c2c9d1",
                error="#c2c9d1",
                dark=True,
            )
        )
        self.theme = "jevlab-dark"

    def on_mount(self) -> None:
        from jevlab.tui.guidance import DemoScreen, Glossary, TourScreen

        self.push_screen(Home(self.wb))
        if self.wb.profile_notice:
            self.notify(self.wb.profile_notice, title="Existing profile", timeout=15)
        if self.start == "tour":
            self.push_screen(TourScreen(self.wb))
        elif self.start == "demo":
            self.push_screen(DemoScreen(self.wb))
        elif self.start == "glossary":
            self.push_screen(Glossary())
        elif self.start == "edit" and self.template_name:
            self.push_screen(
                TemplateEditor(self.wb, source=self.wb.templates.resolve(self.template_name))
            )
        elif self.start == "new":
            self.push_screen(TemplateEditor(self.wb, name=self.template_name))
        elif self.start == "learn":
            self.push_screen(LearnScreen(self.wb))
        elif self.start == "library":
            self.push_screen(LibraryScreen(self.wb))
        elif self.start == "coach":
            self.push_screen(CoachScreen(self.wb))
        elif self.start in ("eval", "batch"):
            self.push_screen(JobScreen(self.wb, "eval" if self.start == "eval" else "batch"))
        elif self.start == "compare":
            self.push_screen(CompareScreen(self.wb))

    def action_explain(self) -> None:
        from jevlab.core.guidance import CONTROL_GUIDANCE, Explanation, explain_control
        from jevlab.tui.fields import Field
        from jevlab.tui.guidance import Explain

        # Read only control identity, never its value: a password may be focused.
        control = self.screen.focused
        ancestor = control
        while ancestor is not None and not isinstance(ancestor, Field):
            ancestor = ancestor.parent
        if isinstance(ancestor, Field):
            identity = ancestor.control.id
            explanation = explain_control(identity, type(self.screen).__name__)
            if identity == "prompt-value" or (
                identity not in CONTROL_GUIDANCE and not (identity or "").startswith("criterion-")
            ):
                explanation = Explanation(
                    ancestor.label,
                    ancestor.description
                    + (f" Example: {ancestor.example}" if ancestor.example else ""),
                )
            self.push_screen(Explain(explanation))
            return
        while control is not None and control.id is None:
            control = control.parent
        self.push_screen(
            Explain(explain_control(control.id if control else None, type(self.screen).__name__))
        )

    def action_glossary(self) -> None:
        from jevlab.tui.guidance import Glossary

        self.push_screen(Glossary())

    def action_error_details(self) -> None:
        from jevlab.tui.dialogs import ErrorDetails

        self.push_screen(ErrorDetails(self.last_error))

    def _handle_exception(self, error: Exception) -> None:
        """Textual's default fatal renderer includes locals; never expose them to users.

        Keep Textual's failure code and test propagation, but render only safe fields.
        Recoverable event/worker failures are handled at their own boundaries.
        """
        safe = (
            error
            if isinstance(error, JevError)
            else JevError(
                "internal_error",
                "The terminal interface stopped after an unexpected problem.",
                "Start jevlab again and run jevlab doctor. "
                "Check history before repeating a paid request.",
            )
        )
        self.last_error = human_error(safe, verbose=True)
        self._return_code = 1
        if self._exception is None:
            self._exception = error
            self._exception_event.set()
        self._exit_renderables.append(Text(human_error(safe)))
        self._close_messages_no_wait()

    def report_error(
        self, error: JevError, *, timeout: float | None = None, notify: bool = True
    ) -> None:
        """Keep full safe diagnostics for F2, while showing the actionable summary."""
        self.last_error = human_error(error, verbose=True)
        if notify:
            super().notify(
                human_error(error),
                title="Could not finish · F2 for details",
                severity="error",
                timeout=timeout,
                markup=False,
            )

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        if severity == "error":
            if not message.startswith("What happened:"):
                message = human_error(
                    JevError(
                        "action_failed",
                        message,
                        "Review the fields mentioned above, then try again. "
                        "Ctrl+E explains the focused field; F2 reopens this error.",
                    )
                )
            self.last_error = message
            title = title or "Could not finish · F2 for details"
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=False)

    async def action_quit(self) -> None:
        screen = self.screen
        if isinstance(screen, WorkbenchScreen):
            remaining = [s for s in self.screen_stack if isinstance(s, WorkbenchScreen)]

            def close_next() -> None:
                if remaining:
                    remaining.pop().request_close(close_next)
                else:
                    self.exit()

            close_next()
        else:
            # Modal drafts must be closed before quitting; don't silently lose them.
            self.notify("Close the dialog with Esc before quitting.")

    def get_system_commands(self, screen: Screen[Any]) -> Iterable[SystemCommand]:
        from jevlab.tui.guidance import DemoScreen, TourScreen

        yield SystemCommand(
            "Explain this", "Explain the focused item (Ctrl+E)", self.action_explain
        )
        yield SystemCommand(
            "Glossary", "Plain meanings and examples (Ctrl+G)", self.action_glossary
        )
        if isinstance(screen, ModalScreen):
            # A pending confirmation must not allow settings/design changes behind it.
            yield SystemCommand("Help", "Explain this dialog", lambda: self.push_screen(Help()))
            yield SystemCommand(
                "Show error details", "Read the latest error", self.action_error_details
            )
            return
        yield SystemCommand(
            "Welcome tour",
            "Start the guided introduction again",
            lambda: self.push_screen(TourScreen(self.wb)),
        )
        yield SystemCommand(
            "Recorded demo",
            "See an example without a key or cost",
            lambda: self.push_screen(DemoScreen(self.wb)),
        )
        yield SystemCommand(
            "Show error details", "Reopen the latest safe error message", self.action_error_details
        )
        yield SystemCommand(
            "Help", "Keys and primitive semantics", lambda: self.push_screen(Help())
        )
        yield SystemCommand(
            "New template",
            "Start a reusable design",
            lambda: self.push_screen(TemplateEditor(self.wb)),
        )
        yield SystemCommand(
            "History", "Inspect saved runs and totals", lambda: self.push_screen(History(self.wb))
        )
        yield SystemCommand(
            "Settings",
            "Credentials and offline doctor",
            lambda: self.push_screen(SettingsScreen(self.wb)),
        )
        yield SystemCommand("Quit", "Leave the workbench", self.action_quit)
        yield SystemCommand(
            "Learn",
            "Ten practical Jev design lessons",
            lambda: self.push_screen(LearnScreen(self.wb)),
        )
        yield SystemCommand(
            "Library",
            "Open or fork seven example patterns",
            lambda: self.push_screen(LibraryScreen(self.wb)),
        )
        yield SystemCommand(
            "Coach", "Optional design advice", lambda: self.push_screen(CoachScreen(self.wb))
        )
        yield SystemCommand(
            "Evals",
            "Import data and inspect calibration",
            lambda: self.push_screen(JobScreen(self.wb)),
        )
        yield SystemCommand(
            "Batch",
            "Run or resume a dataset job",
            lambda: self.push_screen(JobScreen(self.wb, "batch")),
        )
        yield SystemCommand(
            "Compare",
            "Two designs over the same state",
            lambda: self.push_screen(CompareScreen(self.wb)),
        )
        yield SystemCommand(
            "Export",
            "Generate a portable SDK module",
            lambda: self.push_screen(ExportScreen(self.wb)),
        )
        yield SystemCommand(
            "Cleanup",
            "Inspect and prune eligible history",
            lambda: self.push_screen(CleanupScreen(self.wb)),
        )
        if isinstance(screen, Home):
            yield SystemCommand("Playground", "Run the selected template", screen.open_playground)
            yield SystemCommand("Fork template", "Create an editable variant", screen.action_fork)
        if isinstance(screen, TemplateEditor):
            yield SystemCommand("Save template", "Validate and save YAML", screen.action_save)
            yield SystemCommand("Add question", "Add Choice, Score, or Noul", screen.edit_question)
            yield SystemCommand(
                "Critique design",
                "Ask the configured coach about this draft",
                screen.action_critique,
            )
            if screen.original_name:
                yield SystemCommand(
                    "Playground",
                    "Try the last saved design",
                    lambda: self.push_screen(Playground(self.wb, screen.saved_template())),
                )
        if isinstance(screen, Playground):
            yield SystemCommand(
                "Run Jev", "Send state to TypeSafe and save the result", screen.action_run
            )
            yield SystemCommand(
                "Load state file", "Read local UTF-8 text or JSON", screen.action_load
            )
