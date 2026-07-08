"""Headless tests for the NewWorkspaceScreen modal
(cockpit/tui/widgets/new_workspace_screen.py).

The screen is a text box (plus a repo `Select` when more than one repo is
configured) whose dismiss value is a `(source, repo_path)` tuple. It returns the
trimmed input + chosen repo on Enter, `None` on a blank submit or escape, and
focuses the input on mount. These pin that contract; the app-side spawn wiring is
tested in test_app.py.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Select, Static

from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen

pytestmark = pytest.mark.asyncio

_TWO = [("repo-a", "/tmp/a"), ("repo-b", "/tmp/b")]


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("host", id="host")


async def test_input_focused_on_mount():
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen())
        await pilot.pause()
        assert isinstance(app.focused, Input)


async def test_submit_dismisses_with_trimmed_source_and_repo():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen([("repo", "/tmp/r")]), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = "  fix-login  "
        await pilot.press("enter")
        await pilot.pause()
    assert result == [("fix-login", "/tmp/r")]  # trimmed source + sole repo path


async def test_blank_submit_dismisses_with_none():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen([("repo", "/tmp/r")]), result.append)
        await pilot.pause()
        await pilot.press("enter")  # empty input
        await pilot.pause()
    assert result == [None]  # blank → no spawn


async def test_escape_cancels_with_none():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen([("repo", "/tmp/r")]), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = "fix-login"
        await pilot.press("escape")
        await pilot.pause()
    assert result == [None]  # escape discards the typed value


async def test_single_repo_has_no_select_but_carries_path():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(
            NewWorkspaceScreen([("solo", "/tmp/solo")]), result.append
        )
        await pilot.pause()
        # No picker with one repo — just the static hint naming it.
        assert not app.screen.query(Select)
        hints = [str(s.render()) for s in app.screen.query(Static)]
        assert any("solo" in h for h in hints)
        app.screen.query_one(Input).value = "fix"
        await pilot.press("enter")
        await pilot.pause()
    assert result == [("fix", "/tmp/solo")]


async def test_multi_repo_defaults_to_given_path():
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen(_TWO, default_path="/tmp/b"))
        await pilot.pause()
        assert app.screen.query_one(Select).value == "/tmp/b"  # pre-selected default


async def test_multi_repo_default_falls_back_when_unknown():
    app = _Host()
    async with app.run_test() as pilot:
        # default not among options → first repo, not a stray (Select rejects it).
        await app.push_screen(NewWorkspaceScreen(_TWO, default_path="/tmp/nope"))
        await pilot.pause()
        assert app.screen.query_one(Select).value == "/tmp/a"


async def test_multi_repo_selected_path_rides_dismiss():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(
            NewWorkspaceScreen(_TWO, default_path="/tmp/a"), result.append
        )
        await pilot.pause()
        # Pick the non-default repo, then submit a bare name.
        app.screen.query_one(Select).value = "/tmp/b"
        app.screen.query_one(Input).value = "fix-login"
        await pilot.press("enter")
        await pilot.pause()
    assert result == [("fix-login", "/tmp/b")]  # chosen repo, not the default


async def test_no_worktree_repo_option_label_flags_open():
    # A `use_worktree: false` repo with an existing workspace is tagged in the
    # picker so the user sees why a second `n` is refused (Textual Select has no
    # per-option disable, so we label + reject rather than disable).
    screen = NewWorkspaceScreen(_TWO, busy_paths={"/tmp/a"})
    assert screen._option_label("repo-a", "/tmp/a").endswith(
        NewWorkspaceScreen.BUSY_SUFFIX
    )
    assert screen._option_label("repo-b", "/tmp/b") == "repo-b"


async def test_no_worktree_repo_prefills_name_with_repo_name():
    # (c) On a `use_worktree: false` repo the name Input defaults to the repo name
    # (its one addressable session is named after it); a worktree repo stays blank.
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(
            NewWorkspaceScreen([("scratch", "/tmp/s")], no_worktree_paths={"/tmp/s"})
        )
        await pilot.pause()
        assert app.screen.query_one(Input).value == "scratch"


async def test_worktree_repo_name_starts_blank():
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(NewWorkspaceScreen([("repo", "/tmp/r")]))
        await pilot.pause()
        assert app.screen.query_one(Input).value == ""


async def test_busy_no_worktree_repo_submit_is_rejected():
    # (b) A `use_worktree: false` repo that already has its one workspace refuses a
    # second create — the modal stays open with an error; `f` focuses the existing.
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(
            NewWorkspaceScreen(
                _TWO,
                default_path="/tmp/a",
                no_worktree_paths={"/tmp/a"},
                busy_paths={"/tmp/a"},
            ),
            result.append,
        )
        await pilot.pause()
        app.screen.query_one(Input).value = "whatever"
        await pilot.press("enter")
        await pilot.pause()
        assert result == []  # not dismissed
        assert isinstance(app.screen, NewWorkspaceScreen)
        err = app.screen.query_one("#nw-error", Static)
        assert "already has a workspace" in str(err.render())
