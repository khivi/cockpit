"""The `d` Day overlay."""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Static

from cockpit.lib.brief import Change
from cockpit.tui.widgets.brief_screen import BriefScreen


def _text(app: App) -> str:
    return "\n".join(str(s.render()) for s in app.screen.query(Static))


@pytest.mark.asyncio
@pytest.mark.covers("brief.screen~1")
async def test_overlay_groups_by_repo_and_strips_control():
    app: App[None] = App()
    async with app.run_test(size=(140, 40)) as pilot:
        await app.push_screen(
            BriefScreen(
                since="2026-10-29",
                start=[("pr", "o/r#7 fix\x1b]8;;evil\x07", "ci")],
                changes=[Change("o_other__pr-3", "feat", "ci FAILURE → SUCCESS")],
                commits=[("dotfiles", "abc1", "Ana", "tune zsh")],
                sessions=[("cockpit", "morning", "idle", "Opened PR #4.")],
                tickets=[("A-2", "Todo → In Progress")],
                elsewhere=["2 snoozed PRs"],
            )
        )
        await pilot.pause()
        text = _text(app)
        assert "\x1b" not in text
        assert "o/r#7 fix" in text
        assert "Since 2026-10-29" in text
        assert "o/other" in text and "ci FAILURE → SUCCESS" in text
        assert "dotfiles" in text and "tune zsh" in text
        assert "Opened PR #4." in text
        assert "Todo → In Progress" in text
        assert "2 snoozed PRs" in text
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, BriefScreen)


@pytest.mark.asyncio
@pytest.mark.covers("brief.screen~1")
async def test_no_baseline_says_so():
    app: App[None] = App()
    async with app.run_test(size=(140, 40)) as pilot:
        await app.push_screen(
            BriefScreen(since=None, start=[], changes=[], elsewhere=[])
        )
        await pilot.pause()
        text = _text(app)
        assert "No baseline yet" in text
        assert "Nothing needs you." in text
