"""Screen conventions and context-sensitive help."""

from collections.abc import Callable

from textual import on
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button

from jevlab.core.errors import JevError
from jevlab.core.service import Workbench
from jevlab.tui.dialogs import Help
from jevlab.tui.errors import report_error
from jevlab.tui.fields import Field


class WorkbenchScreen(Screen[None]):
    compact_fields = False
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("f1", "help", "Help"),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def __init__(self, wb: Workbench) -> None:
        super().__init__()
        self.wb = wb
        self.options_revealed = False

    def on_mount(self) -> None:
        self.apply_mode()

    def on_screen_resume(self) -> None:
        self.apply_mode()

    def apply_mode(self) -> None:
        simple = self.wb.settings.ui_mode == "simple"
        self.set_class(simple and not self.options_revealed, "simple-mode")
        for field in self.query(Field):
            field.apply_mode(simple and not self.compact_fields)
        for button in self.query(".options-toggle").results(Button):
            button.display = simple
            button.label = "Fewer options" if self.options_revealed else "More options"

    @on(Button.Pressed, ".options-toggle")
    def toggle_options(self, event: Button.Pressed) -> None:
        event.stop()
        self.options_revealed = not self.options_revealed
        self.apply_mode()

    def action_help(self) -> None:
        self.app.push_screen(Help())

    def report_error(
        self, error: JevError, *, timeout: float | None = None, notify: bool = True
    ) -> None:
        report_error(self, error, timeout=timeout, notify=notify)

    def request_close(self, callback: Callable[[], object]) -> None:
        callback()

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.request_close(self.app.pop_screen)
