"""Headless tests for the ConfigScreen modal (cockpit/tui/widgets/config_screen.py).

The body is rendered with `Text.from_ansi`, NOT a raw markup string — the
captured tick output carries ANSI colour codes and bare brackets (`[clean]`,
`[2026-…]`) and JSON config bodies carry `[` `]` array delimiters, all of which
Textual's markup parser mangles into garbled cream-highlighted boxes. These
tests pin that the body Static gets a parsed `Text` so brackets survive verbatim
and the only styling is the decoded foreground colour (no background fill).
"""

from __future__ import annotations

from typing import Any

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from cockpit.tui.widgets.config_screen import (
    ConfigCommands,
    ConfigScreen,
    ReleaseNotesScreen,
    _commit_color,
    _LazyScroll,
    render_changelog,
)

pytestmark = pytest.mark.asyncio


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield Static("host", id="host")


def _body_content(screen: Screen[object]) -> Any:
    # The middle Static (title, body, hint) holds the rendered body. Read the
    # stored content object: a `Text` (our fix) vs a raw `str` (markup-parsed).
    # `app.screen` is typed `Screen[object]`; the body content is dynamically a
    # `Text` or `str`, so the return is `Any` (callers assert the concrete type).
    body = screen.query(Static)[1]
    return body._Static__content  # type: ignore[attr-defined]


async def test_body_renders_as_text_not_markup_string():
    # A raw string would run through Textual's markup parser; a Text renderable
    # bypasses it.
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(ConfigScreen("t", "[clean] plain body"))
        await pilot.pause()
        assert isinstance(_body_content(app.screen), Text)


async def test_brackets_survive_verbatim():
    # `[clean]` must render literally — markup parsing would consume it as a tag.
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(ConfigScreen("t", "refreshed #1 -> tui  [clean]"))
        await pilot.pause()
        assert "[clean]" in _body_content(app.screen).plain


async def test_ansi_decoded_to_foreground_only():
    # An ANSI green span decodes to a foreground style with NO background fill —
    # the cream box bug was a background leaking onto styled segments.
    app = _Host()
    body = "\x1b[32m[2026-06-08]\x1b[0m ok"
    async with app.run_test() as pilot:
        await app.push_screen(ConfigScreen("t", body))
        await pilot.pause()
        text = _body_content(app.screen)
        assert text.plain == "[2026-06-08] ok"
        styled = [s for s in text.spans if s.style]
        assert styled, "expected the ANSI colour to produce a styled span"
        for span in styled:
            assert span.style.bgcolor is None


async def test_palette_offers_edit_config():
    # The Ctrl+P palette must list the edit entry alongside the read entries.
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = ConfigCommands(app.screen)
        hits = [h async for h in provider.search("edit config")]
        assert any("Edit config" in str(h.text) for h in hits)


async def test_release_notes_loads_first_page_on_mount():
    pages = {1: ([("feat: a", "today"), ("fix: b", "today")], True)}
    calls: list[int] = []

    def fetch(page: int) -> tuple[list[tuple[str, str]], bool]:
        calls.append(page)
        return pages.get(page, ([], True))

    app = _Host()
    async with app.run_test() as pilot:
        screen = ReleaseNotesScreen("t", fetch)
        await app.push_screen(screen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == [1]
        assert screen._items == [("feat: a", "today"), ("fix: b", "today")]
        assert screen._exhausted is True


async def test_release_notes_fetches_next_page_near_bottom():
    pages = {
        1: ([(f"feat: {i}", "today") for i in range(15)], False),
        2: ([("fix: last", "today")], True),
    }
    calls: list[int] = []

    def fetch(page: int) -> tuple[list[tuple[str, str]], bool]:
        calls.append(page)
        return pages.get(page, ([], True))

    app = _Host()
    async with app.run_test() as pilot:
        screen = ReleaseNotesScreen("t", fetch)
        await app.push_screen(screen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == [1]  # only the first page so far

        lazy = screen.query_one(_LazyScroll)
        lazy.post_message(_LazyScroll.NearBottom())
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == [1, 2]
        assert screen._items[-1] == ("fix: last", "today")

        # Exhausted (short page 2): a further scroll fires no more fetches.
        lazy.post_message(_LazyScroll.NearBottom())
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert calls == [1, 2]


async def test_commit_color_by_type():
    assert _commit_color("feat(tui): add x") == "green"
    assert _commit_color("fix: bug") == "red"
    assert _commit_color("docs: readme") == "blue"
    assert _commit_color("chore: bump") == "dim"  # unlisted → dim
    assert _commit_color("not a conventional subject") == "dim"


async def test_release_notes_body_is_colored_text():
    app = _Host()
    async with app.run_test() as pilot:
        screen = ReleaseNotesScreen(
            "t", lambda page: ([("feat: a", "today"), ("fix: b", "today")], True)
        )
        await app.push_screen(screen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        body = screen.query_one("#rn-body", Static)
        content = body._Static__content  # type: ignore[attr-defined]
        assert isinstance(content, Text)
        # `[` `]` would be markup-consumed by a raw string; the styled Text keeps
        # them literal and carries the per-type colours.
        styles = {str(s.style) for s in content.spans}
        assert "green" in styles and "red" in styles


async def test_render_changelog_groups_by_bucket():
    # Shared renderer (ChangeLog screen + post-update modal): a dim age header
    # when the bucket changes, then each subject tinted by commit type.
    items = [("feat: a", "today"), ("fix: b", "today"), ("docs: c", "last week")]
    text = render_changelog(items)
    assert text.plain == "today\n• feat: a\n• fix: b\n\nlast week\n• docs: c"
    styles = {str(s.style) for s in text.spans}
    assert {"green", "red", "blue", "dim"} <= styles


async def test_config_screen_renders_colored_text():
    # A pre-styled `Text` body (e.g. a rendered changelog) is rendered as-is by
    # ConfigScreen — not re-parsed through from_ansi.
    app = _Host()
    async with app.run_test() as pilot:
        body = render_changelog([("feat: a", "today")])
        await app.push_screen(ConfigScreen("what's new", body))
        await pilot.pause()
        content = _body_content(app.screen)
        assert isinstance(content, Text)
        assert "green" in {str(s.style) for s in content.spans}


async def test_release_notes_fills_until_overflow_on_tall_terminal():
    # First page fits on a tall terminal (no overflow) → watch_scroll_y never
    # fires; _fill_if_short must keep pulling until the view overflows so older
    # history isn't stranded behind a non-scrolling page.
    pages = {
        1: ([(f"feat: {i}", "today") for i in range(15)], False),
        2: ([(f"fix: {i}", "today") for i in range(15)], False),
        3: ([(f"docs: {i}", "today") for i in range(15)], False),
    }
    calls: list[int] = []

    def fetch(page: int) -> tuple[list[tuple[str, str]], bool]:
        calls.append(page)
        return pages.get(page, ([], True))

    app = _Host()
    async with app.run_test(size=(100, 60)) as pilot:
        screen = ReleaseNotesScreen("t", fetch)
        await app.push_screen(screen)
        for _ in range(6):
            await app.workers.wait_for_complete()
            await pilot.pause()
        # Pulled past page 1 on its own until content overflowed the tall view.
        assert calls[0] == 1 and len(calls) >= 2
        assert screen.query_one(_LazyScroll).max_scroll_y > 0


async def test_release_notes_empty_shows_hint():
    app = _Host()
    async with app.run_test() as pilot:

        def empty(page: int) -> tuple[list[tuple[str, str]], bool]:
            return [], True

        screen = ReleaseNotesScreen("t", empty)
        await app.push_screen(screen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        hint = screen.query_one("#rn-hint", Static)
        assert "no release notes available" in str(hint._Static__content)  # type: ignore[attr-defined]


async def test_plain_json_body_is_unstyled():
    # A JSON config body (no ANSI) yields plain text with no spans — and its `[`
    # `]` array delimiters are not consumed as markup.
    app = _Host()
    async with app.run_test() as pilot:
        await app.push_screen(ConfigScreen("t", '{"repos": ["a", "b"]}'))
        await pilot.pause()
        text = _body_content(app.screen)
        assert text.plain == '{"repos": ["a", "b"]}'
        assert text.spans == []
