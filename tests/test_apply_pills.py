"""Tests for `apply_pills` clear/set behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lib.cmux import ACTIONABLE_KEYS, COCKPIT_KEY, apply_pills
from lib.gh import PR
from lib.git import Worktree


def _pr(**overrides) -> PR:
    base = dict(
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


def _wt() -> Worktree:
    return Worktree(path=Path("/tmp/wt"), branch="khivi/feature", dirty_count=0)


def test_apply_pills_clears_legacy_managed_key():
    calls: list[tuple] = []

    def fake_cmux(*args, **_kwargs):
        calls.append(args)
        return ""

    with patch("lib.cmux.cmux", side_effect=fake_cmux):
        apply_pills("workspace:1", _pr(), _wt())

    cleared_keys = {args[1] for args in calls if args and args[0] == "clear-status"}
    for k in ACTIONABLE_KEYS:
        assert k in cleared_keys
    assert COCKPIT_KEY in cleared_keys
    assert "cockpit_managed" in cleared_keys
