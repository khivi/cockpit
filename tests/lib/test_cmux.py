"""Tests for cmux pill consumption targeting scripts/lib/cmux.py.

Covers `apply_pills` (clear/set behavior) and `status_pills` (kind→styling
mapping from `decide_pills` output).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.lib.cmux import (
    ACTIONABLE_KEYS,
    COCKPIT_KEY,
    MUTED_KEY,
    YELLOW,
    CmuxUnavailable,
    apply_pills,
    status_pills,
    workspace_cwds,
    workspace_names,
    workspace_state,
)
from scripts.lib.gh import PR
from scripts.lib.git import Worktree
from scripts.lib.nudges import KNOWN_CATEGORIES, NudgePref


def _pr(**overrides) -> PR:
    base: dict = dict(
        number=1,
        title="t",
        branch="khivi/feature",
        url="https://example/pr/1",
        author="khivi",
        is_draft=False,
        review_decision="REVIEW_REQUIRED",
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state="OPEN",
        updated_at="",
    )
    base.update(overrides)
    return PR(**base)


def _wt(
    branch: str = "khivi/feature",
    *,
    rebasing: bool = False,
    merging: bool = False,
    dirty: int = 0,
) -> Worktree:
    return Worktree(
        path=Path("/tmp/wt"),
        branch=branch,
        rebasing=rebasing,
        merging=merging,
        dirty_count=dirty,
    )


# ── apply_pills ─────────────────────────────────────────────────────────────


def test_apply_pills_clears_legacy_managed_key():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("scripts.lib.cmux.cmux", side_effect=fake_cmux):
        apply_pills("workspace:1", _pr(), _wt())

    cleared_keys = {args[1] for args in calls if args and args[0] == "clear-status"}
    for k in ACTIONABLE_KEYS:
        assert k in cleared_keys
    assert COCKPIT_KEY in cleared_keys
    assert "cockpit_managed" in cleared_keys


# ── status_pills (cmux mapper) ──────────────────────────────────────────────


def test_cmux_status_pills_matches_decisions():
    out = status_pills(_pr(ci="failed:lint", unaddressed=2), _wt(dirty=1))
    assert out == [
        ("wip", "✏️ 1 dirty", "#ff9500"),
        ("ci", "❌ ci:lint", "#eb445a"),
        ("comments", "💬 2 unaddressed", "#eb445a"),
    ]


def test_cmux_drops_state_pill():
    out = status_pills(_pr(state="MERGED"), _wt())
    assert out == []


def test_cmux_conflict_emits_merge_key():
    out = status_pills(_pr(mergeable="CONFLICTING"), _wt())
    assert out == [("merge", "⚠️ conflict", "#ff9500")]


def test_cmux_owner_pill_added_for_coworker():
    out = status_pills(_pr(author="bob"), _wt(), self_user="khivi")
    assert ("owner", "👥 @bob", "#3b82f6") in out
    assert out[0] == ("owner", "👥 @bob", "#3b82f6")


def test_cmux_owner_pill_absent_for_self():
    out = status_pills(_pr(author="khivi"), _wt(), self_user="khivi")
    assert all(k != "owner" for k, _, _ in out)


def test_cmux_owner_pill_absent_when_self_user_none():
    out = status_pills(_pr(author="bob"), _wt())
    assert all(k != "owner" for k, _, _ in out)


def test_apply_pills_clears_owner_key():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("scripts.lib.cmux.cmux", side_effect=fake_cmux):
        apply_pills("workspace:1", _pr(), _wt())

    cleared_keys = {args[1] for args in calls if args and args[0] == "clear-status"}
    assert "owner" in cleared_keys


# ── CmuxUnavailable: nonzero rc must raise, not return {} ────────────────────


def test_workspace_names_raises_on_nonzero_rc():
    def fake_cmux(*_args, **_kwargs):
        raise RuntimeError("cmux list-workspaces failed: socket missing")

    with patch("scripts.lib.cmux.cmux", side_effect=fake_cmux):
        with pytest.raises(CmuxUnavailable, match="list-workspaces failed"):
            workspace_names()


def test_workspace_cwds_raises_on_nonzero_rc():
    def fake_cmux(*_args, **_kwargs):
        raise RuntimeError("cmux rpc workspace.list failed: daemon down")

    with patch("scripts.lib.cmux.cmux", side_effect=fake_cmux):
        with pytest.raises(CmuxUnavailable, match="rpc workspace.list failed"):
            workspace_cwds()


def test_workspace_cwds_raises_on_non_json():
    with patch("scripts.lib.cmux.cmux", return_value="not json"):
        with pytest.raises(CmuxUnavailable, match="non-JSON"):
            workspace_cwds()


def test_workspace_state_propagates_cmux_unavailable():
    def fake_cmux(*_args, **_kwargs):
        raise RuntimeError("backend offline")

    with patch("scripts.lib.cmux.cmux", side_effect=fake_cmux):
        with pytest.raises(CmuxUnavailable):
            workspace_state()


def test_workspace_names_parses_ok_when_cmux_ok():
    with patch(
        "scripts.lib.cmux.cmux",
        return_value="workspace:1 feat-x\nworkspace:2 other\n",
    ):
        assert workspace_names() == {"workspace:1": "feat-x", "workspace:2": "other"}


def test_workspace_cwds_parses_ok_when_cmux_ok():
    payload = '{"workspaces":[{"ref":"workspace:1","current_directory":"/tmp/wt"}]}'
    with patch("scripts.lib.cmux.cmux", return_value=payload):
        assert workspace_cwds() == {"workspace:1": Path("/tmp/wt")}


# ── muted pill ──────────────────────────────────────────────────────────────


def test_status_pills_full_mute_emits_muted_tuple_at_front():
    pref = NudgePref(disabled_categories=set(KNOWN_CATEGORIES))
    out = status_pills(_pr(), _wt(), pref=pref)
    # muted anchors the row; ci_passed still emits since muted doesn't suppress it.
    assert out[0] == (MUTED_KEY, "🔇 muted", YELLOW)
    assert any(k == "ci" for k, _, _ in out)


def test_status_pills_partial_mute_lists_categories():
    pref = NudgePref(disabled_categories={"ci", "comments"})
    out = status_pills(_pr(), _wt(), pref=pref)
    assert out[0] == (MUTED_KEY, "🔇 muted: ci+comments", YELLOW)


def test_status_pills_no_mute_no_muted_tuple():
    pref = NudgePref()
    out = status_pills(_pr(), _wt(), pref=pref)
    assert all(k != MUTED_KEY for k, _, _ in out)


def test_status_pills_muted_with_owner_pill_for_coworker():
    pref = NudgePref(disabled_categories={"ci"})
    out = status_pills(_pr(author="bob"), _wt(), self_user="khivi", pref=pref)
    # owner is prepended for reversed set-order; muted comes from decide_pills.
    assert out[0] == ("owner", "👥 @bob", "#3b82f6")
    assert (MUTED_KEY, "🔇 muted: ci", YELLOW) in out


def test_apply_pills_clears_muted_key():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("scripts.lib.cmux.cmux", side_effect=fake_cmux):
        apply_pills("workspace:1", _pr(), _wt())

    cleared_keys = {args[1] for args in calls if args and args[0] == "clear-status"}
    assert MUTED_KEY in cleared_keys
