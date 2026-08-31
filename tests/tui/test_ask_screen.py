r"""Headless tests for the AskScreen modal (cockpit/tui/widgets/ask_screen.py).

The screen is a one-line text box whose dismiss value is the trimmed text, or
`None` on a blank submit / escape. These pin that contract plus the load-bearing
structural constraint — it must stay an `Input`, never a `TextArea`, because
`cmux send` turns every newline into Enter. The app-side send wiring is tested
in test_app.py.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static, TextArea

from cockpit.tui.widgets.ask_screen import COMMENTS_LEAD, AskScreen

pytestmark = pytest.mark.asyncio


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("host", id="host")


async def test_input_focused_on_mount():
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen())
        await pilot.pause()
        assert isinstance(app.focused, Input)


async def test_submit_dismisses_with_trimmed_text():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = "  rebase onto main  "
        await pilot.press("enter")
        await pilot.pause()
    assert result == [("send", "rebase onto main")]


async def test_blank_submit_retracts_the_draft():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(), result.append)
        await pilot.pause()
        await pilot.press("enter")  # empty input
        await pilot.pause()
    # "clear", not "cancel": an emptied box submitted on purpose retracts the
    # draft, which is the only way to drop one.
    assert result == [("clear", "")]


async def test_whitespace_only_submit_retracts_the_draft():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = "   "
        await pilot.press("enter")
        await pilot.pause()
    assert result == [("clear", "")]  # whitespace-only is still a retraction


async def test_escape_reports_cancel():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(), result.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result == [("cancel", "")]


async def test_target_appears_in_title():
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen("feat-x"))
        await pilot.pause()
        titles = [str(s.render()) for s in app.screen.query(Static)]
        assert any("feat-x" in t for t in titles)


async def test_composer_is_single_line_never_a_textarea():
    r"""Structural regression guard. `cmux send` synthesizes keypresses rather
    than doing a bracketed paste, so a real newline AND the literal `\n` escape
    both arrive as Enter — a multi-line box would submit its first line as a
    truncated prompt and the rest as separate ones. Probed against cmux
    0.64.22; re-probe before relaxing this."""
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen())
        await pilot.pause()
        assert not app.screen.query(TextArea)
        assert len(app.screen.query(Input)) == 1


async def test_escape_hands_back_what_was_typed():
    """Escape is "hold this thought" — stepping away to check something must
    not cost the text, so the app gets it back to stash."""
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = "half a thought"
        await pilot.press("escape")
        await pilot.pause()
    assert result == [("cancel", "half a thought")]


async def test_pending_comments_offer_a_lead_in():
    """Comments ride a message, so a pending set still needs a line to carry
    it — and having to invent one is what leaves them undelivered."""
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(comments=2), result.append)
        await pilot.pause()
        assert app.screen.query_one(Input).value == COMMENTS_LEAD
        await pilot.press("enter")
        await pilot.pause()
    assert result == [("send", COMMENTS_LEAD)]


async def test_no_comments_no_lead_in():
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen())
        await pilot.pause()
        assert app.screen.query_one(Input).value == ""


async def test_a_restored_draft_beats_the_lead_in():
    """The draft is what a previous send couldn't deliver — overwriting it
    would cost exactly the text the stash exists to preserve."""
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(initial="rebase first", comments=3))
        await pilot.pause()
        assert app.screen.query_one(Input).value == "rebase first"


async def test_escape_does_not_stash_an_untouched_lead_in():
    """It isn't a thought the user had, and it would outlive the comments that
    prompted it — restoring on a later `a` as a draft referring to nothing."""
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(comments=1), result.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result == [("cancel", "")]


async def test_escape_stashes_a_lead_in_that_was_edited():
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(comments=1), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = f"{COMMENTS_LEAD}, carefully"
        await pilot.press("escape")
        await pilot.pause()
    assert result == [("cancel", f"{COMMENTS_LEAD}, carefully")]


async def test_emptying_the_lead_in_still_retracts():
    """The one way to retract a draft must survive the prefill: clearing the
    box and pressing enter drops it rather than sending the lead-in."""
    app = _Host()
    result: list = []
    async with app.run_test() as pilot:
        await app.push_screen(AskScreen(comments=2), result.append)
        await pilot.pause()
        app.screen.query_one(Input).value = ""
        await pilot.press("enter")
        await pilot.pause()
    assert result == [("clear", "")]
