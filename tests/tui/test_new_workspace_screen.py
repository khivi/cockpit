"""Headless tests for the NewWorkspaceScreen modal
(cockpit/tui/widgets/new_workspace_screen.py).

The screen is a text box whose dismiss value becomes the `spawn.py` source. It
returns the trimmed input on Enter, `None` on a blank submit or escape, and
focuses the input on mount. These pin that contract; the app-side spawn wiring
is tested in test_app.py.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen

pytestmark = pytest.mark.asyncio


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("host", id="host")


async def test_input_focused_on_mount():
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen())
        await pilot.pause()
        assert isinstance(app.focused, Input)


async def test_submit_dismisses_with_trimmed_value():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen(), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = "  fix-login  "
        await pilot.press("enter")
        await pilot.pause()
    assert result == ["fix-login"]  # trimmed source handed back


async def test_blank_submit_dismisses_with_none():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen(), result.append)
        await pilot.pause()
        await pilot.press("enter")  # empty input
        await pilot.pause()
    assert result == [None]  # blank → no spawn


async def test_escape_cancels_with_none():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen(), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = "fix-login"
        await pilot.press("escape")
        await pilot.pause()
    assert result == [None]  # escape discards the typed value


async def test_repo_hint_rendered():
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen("needl-ai"))
        await pilot.pause()
        hints = [str(s.render()) for s in app.screen.query(Static)]
        assert any("needl-ai" in h for h in hints)
