"""The `b` brief overlay."""

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
                needs=[("o_r__pr-7", {"title": "fix\x1b]8;;evil\x07", "nudge": "ci"})],
                changes=[Change("o_other__pr-3", "feat", "ci FAILURE → SUCCESS")],
                elsewhere=["2 snoozed PRs"],
            )
        )
        await pilot.pause()
        text = _text(app)
        assert "\x1b" not in text
        assert "o/r" in text
        assert "#7" in text
        assert "Changed since 2026-10-29" in text
        assert "ci FAILURE → SUCCESS" in text
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
            BriefScreen(since=None, needs=[], changes=[], elsewhere=[])
        )
        await pilot.pause()
        assert "No baseline yet" in _text(app)
