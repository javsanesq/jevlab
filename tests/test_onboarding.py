"""Keyboard tour/help journeys work on a small terminal without any model calls."""

from textual.app import App
from textual.widgets import Button, Input, Static

from jev.core.config import load_settings
from jev.core.guidance import explain_control
from jev.core.service import Workbench
from jev.tui.base import WorkbenchScreen
from jev.tui.guidance import DemoScreen, Explain, Glossary, TourScreen


class GuidanceApp(App[None]):
    CSS_PATH = "../src/jev/tui/theme.tcss"

    def __init__(self, wb: Workbench) -> None:
        super().__init__()
        self.wb = wb

    def on_mount(self) -> None:
        self.push_screen(WorkbenchScreen(self.wb))
        self.push_screen(TourScreen(self.wb))


async def test_tour_keyboard_skip_is_saved_without_a_key(wb: Workbench) -> None:
    async with GuidanceApp(wb).run_test(size=(80, 24)) as pilot:
        assert isinstance(pilot.app.screen, TourScreen)
        await pilot.press("escape")
        assert isinstance(pilot.app.screen, WorkbenchScreen)
        assert not isinstance(pilot.app.screen, TourScreen)
        assert load_settings(wb.root).tour_completed is True


async def test_tour_can_be_repeated_and_completed_without_a_key(wb: Workbench) -> None:
    async with GuidanceApp(wb).run_test(size=(80, 24)) as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, TourScreen)
        for expected_step in (1, 2, 3):
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert screen.step == expected_step
            assert isinstance(screen.focused, Button)
            assert screen.focused.id == "tour-next"
        await pilot.press("enter")
        assert load_settings(wb.root).tour_completed is True
        pilot.app.push_screen(TourScreen(wb))
        await pilot.pause()
        assert isinstance(pilot.app.screen, TourScreen)
        assert pilot.app.screen.step == 0


async def test_demo_return_preserves_tour_step(wb: Workbench) -> None:
    async with GuidanceApp(wb).run_test(size=(80, 24)) as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, TourScreen)
        await pilot.press("enter")
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert screen.step == 2
        screen.query_one("#tour-demo", Button).press()
        await pilot.pause()
        assert isinstance(pilot.app.screen, DemoScreen)
        assert "RECORDED EXAMPLE" in str(
            pilot.app.screen.query_one("#demo-notice", Static).render()
        )
        await pilot.press("escape")
        assert pilot.app.screen is screen
        assert screen.step == 2
        assert wb.storage.history() == []


async def test_explanation_links_to_searchable_glossary_and_returns(wb: Workbench) -> None:
    async with GuidanceApp(wb).run_test(size=(80, 24)) as pilot:
        screen = pilot.app.screen
        pilot.app.push_screen(Explain(explain_control("api-key", "SettingsScreen")))
        await pilot.pause()
        assert isinstance(pilot.app.screen, Explain)
        pilot.app.screen.query_one("#explain-glossary", Button).press()
        await pilot.pause()
        assert isinstance(pilot.app.screen, Glossary)
        assert pilot.app.screen.query_one(Input).value == "API key"
        pilot.app.screen.query_one(Input).value = "Noul"
        await pilot.pause()
        text = str(pilot.app.screen.query_one("#glossary-list", Static).render())
        assert "no separate confidence" in text
        await pilot.press("escape", "escape")
        assert pilot.app.screen is screen
