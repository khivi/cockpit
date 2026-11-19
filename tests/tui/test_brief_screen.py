"""The `d` Day overlay."""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Collapsible, Static

from cockpit.lib.brief import GONE, Change
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
                tickets=[("A-2", "Fix login", "Todo → In Progress")],
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
        assert "Todo → In Progress" in text and "Fix login" in text
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


def _since(**kwargs) -> BriefScreen:
    base: dict = {"since": "2026-10-29", "start": [], "changes": [], "elsewhere": []}
    return BriefScreen(**{**base, **kwargs})


@pytest.mark.asyncio
@pytest.mark.covers("brief.fold~1")
async def test_sections_are_folds_titled_with_their_counts():
    app: App[None] = App()
    async with app.run_test(size=(140, 40)) as pilot:
        await app.push_screen(
            _since(
                start=[("pr", "o/r#7 fix", "ci")],
                tickets=[("A-1", "x", "new (Todo)"), ("A-2", "y", "new (Todo)")],
            )
        )
        await pilot.pause()
        folds = {c.title: c.collapsed for c in app.screen.query(Collapsible)}
        assert folds == {"Tickets (2)": True}
        assert "o/r#7 fix" in _text(app)


@pytest.mark.asyncio
@pytest.mark.covers("brief.fold~1")
async def test_commits_cap_per_repo_and_gone_prs_fold_into_a_count():
    commits = [("dotfiles", f"c{i}", "Ana", f"subject {i}") for i in range(7)]
    changes = [
        Change("o_r__pr-1", "kept", "ci FAILURE → SUCCESS"),
        Change("o_r__pr-2", "merged one", GONE),
        Change("o_r__pr-3", "merged two", GONE),
    ]
    app: App[None] = App()
    async with app.run_test(size=(140, 40)) as pilot:
        await app.push_screen(_since(commits=commits, changes=changes))
        await pilot.pause()
        text = _text(app)
        assert "subject 4" in text and "subject 5" not in text
        assert "+2 more" in text
        assert f"2 {GONE}" in text and "merged one" not in text
        assert "kept" in text


@pytest.mark.asyncio
@pytest.mark.covers("brief.click~1")
async def test_a_linked_row_posts_its_target_and_an_unlinked_one_has_no_click():
    followed: list[str] = []

    class _App(App[None]):
        def on_brief_screen_follow(self, event: BriefScreen.Follow) -> None:
            followed.append(event.target)

    app = _App()
    async with app.run_test(size=(140, 40)) as pilot:
        screen = _since(
            start=[("pr", "o/r#7 fix", "ci"), ("spawn", "o/r#8", "spawn failed")],
            links={"o/r#7 fix": "focus:/wt"},
        )
        await app.push_screen(screen)
        await pilot.pause()
        rows = next(w for w in screen.query(Static) if "o/r#7" in str(w.render()))
        await pilot.click(rows, offset=(8, 0))
        await pilot.click(rows, offset=(8, 1))
        await pilot.pause()
        assert followed == ["focus:/wt"]
