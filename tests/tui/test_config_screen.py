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

from cockpit.tui.widgets.config_screen import ConfigCommands, ConfigScreen

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
