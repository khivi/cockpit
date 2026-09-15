"""Headless tests for the ambiguous-ticket repo picker (repo_pick_screen.py).

The screen answers one question and dismisses with a repo *name* — the argument
`_with_repo` quotes into `--repo`, deliberately not `NewWorkspaceScreen`'s repo
path. What is pinned here is the property that makes it safe to open at all:
nothing is pre-selected, so mounting it cannot answer its own question.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Select, Static

from cockpit.tui.widgets.repo_pick_screen import RepoPickScreen


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("host", id="host")


async def _open(app, result, repos=("infra", "cluster"), ref="PLAT-77"):
    await app.push_screen(RepoPickScreen(ref, list(repos)), result.append)


@pytest.mark.asyncio
async def test_mounting_the_picker_answers_nothing():
    """A seeded `Select` posts `Changed` as it mounts, which the handler would
    read as the user's choice — so the picker starts blank. The obvious default,
    the daemon's own cwd repo, is also the exact fallback that cut two worktrees
    off `dotfiles`."""
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, result)
        await pilot.pause()
        assert result == []
        assert app.screen.query_one("#rp-select", Select).value is Select.NULL


@pytest.mark.asyncio
async def test_choosing_a_repo_dismisses_with_its_name():
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, result)
        await pilot.pause()
        app.screen.query_one("#rp-select", Select).value = "cluster"
        await pilot.pause()
    assert result == ["cluster"]


@pytest.mark.asyncio
async def test_escape_dismisses_with_none():
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, result)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result == [None]


@pytest.mark.asyncio
async def test_the_ticket_and_every_candidate_are_named():
    """The ref is the only thing tying this modal back to the row that opened it,
    since the inbox is gone by the time it appears."""
    app: _Host = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await _open(app, result, repos=("infra", "cluster", "os"))
        await pilot.pause()
        assert "PLAT-77" in app.export_screenshot()
        screen = app.screen
        assert isinstance(screen, RepoPickScreen)
        # Config order, the order every other repo list in the TUI uses.
        assert screen._repos == ["infra", "cluster", "os"]
