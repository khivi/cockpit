"""Tests for scripts/lib/cache.py — cockpit-cache writers and refreshers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import lib.cache as cache_mod
from lib.gh import PR
from lib.git import Worktree


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


# ── write_branch_pr_cache (daemon-tick path, lib.cache) ────────────────────


def test_write_branch_pr_cache_resolves_state(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="APPROVED",
        number=17,
        title="Hello",
        ci_glyph="✓",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "APPROVED"
    assert (cache_dir / "pr-num-khivi-feature").read_text() == "17"
    assert (cache_dir / "pr-title-khivi-feature").read_text() == "Hello"
    assert (cache_dir / "pr-checks-khivi-feature").read_text() == "✓"


def test_write_branch_pr_cache_draft_overrides_open(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=True,
        review_decision="",
        number=18,
        title="Draft",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "DRAFT"


def test_write_branch_pr_cache_closed_state_preserved(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="MERGED",
        is_draft=False,
        review_decision="APPROVED",
        number=19,
        title="Done",
    )
    assert (cache_dir / "pr-state-khivi-feature").read_text() == "MERGED"


def test_write_branch_pr_cache_no_branch_noop(cache_dir):
    cache_mod.write_branch_pr_cache(
        "",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="x",
    )
    assert not any(cache_dir.iterdir())


# ── refresh_pr_data via mocked gh (lib.cache) ──────────────────────────────


def test_refresh_pr_data_writes_no_pr_sentinel(cache_dir):
    with patch.object(cache_mod, "_gh_pr_view", return_value=None):
        cache_mod.refresh_pr_data("khivi/foo")
    assert (cache_dir / "pr-state-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-num-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-title-khivi-foo").read_text() == ""


def test_refresh_pr_data_populates_from_gh(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "reviewDecision": "CHANGES_REQUESTED",
        "number": 99,
        "title": "Fix it",
    }
    with patch.object(cache_mod, "_gh_pr_view", return_value=payload):
        cache_mod.refresh_pr_data("khivi/bar")
    assert (cache_dir / "pr-state-khivi-bar").read_text() == "CHANGES_REQUESTED"
    assert (cache_dir / "pr-num-khivi-bar").read_text() == "99"
    assert (cache_dir / "pr-title-khivi-bar").read_text() == "Fix it"


# ── write_base_distance (lib.cache) ────────────────────────────────────────


def test_write_base_distance_writes_payload(cache_dir):
    cache_mod.write_base_distance("khivi/feature", 5, 1700000000)
    assert (cache_dir / "base-distance-khivi-feature").read_text() == "5 1700000000"


def test_write_base_distance_empty_on_negative_count(cache_dir):
    cache_mod.write_base_distance("khivi/feature", -1, 1700000000)
    assert (cache_dir / "base-distance-khivi-feature").read_text() == ""


def test_write_base_distance_empty_on_missing_epoch(cache_dir):
    cache_mod.write_base_distance("khivi/feature", 3, 0)
    assert (cache_dir / "base-distance-khivi-feature").read_text() == ""


def test_write_base_distance_zero_count_is_valid(cache_dir):
    """0 commits behind base is a legitimate, fresh observation; the
    reader hides 0 but the writer should preserve it for staleness gating."""
    cache_mod.write_base_distance("khivi/feature", 0, 1700000000)
    assert (cache_dir / "base-distance-khivi-feature").read_text() == "0 1700000000"


def test_write_base_distance_no_branch_noop(cache_dir):
    cache_mod.write_base_distance("", 3, 1700000000)
    assert not any(cache_dir.iterdir())


# ── write_base_ahead (lib.cache) ───────────────────────────────────────────


def test_write_base_ahead_writes_payload(cache_dir):
    cache_mod.write_base_ahead("khivi/feature", 5, 1700000000)
    assert (cache_dir / "base-ahead-khivi-feature").read_text() == "5 1700000000"


def test_write_base_ahead_empty_on_negative_count(cache_dir):
    cache_mod.write_base_ahead("khivi/feature", -1, 1700000000)
    assert (cache_dir / "base-ahead-khivi-feature").read_text() == ""


def test_write_base_ahead_empty_on_missing_epoch(cache_dir):
    cache_mod.write_base_ahead("khivi/feature", 3, 0)
    assert (cache_dir / "base-ahead-khivi-feature").read_text() == ""


def test_write_base_ahead_zero_count_is_valid(cache_dir):
    cache_mod.write_base_ahead("khivi/feature", 0, 1700000000)
    assert (cache_dir / "base-ahead-khivi-feature").read_text() == "0 1700000000"


def test_write_base_ahead_no_branch_noop(cache_dir):
    cache_mod.write_base_ahead("", 3, 1700000000)
    assert not any(cache_dir.iterdir())


# ── write_pr_cache pill round-trip (lib.cache) ─────────────────────────────


def test_write_pr_cache_includes_pills(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint", review_decision="APPROVED")
    wt = _wt(dirty=2)
    payload = cache_mod.write_pr_cache("testrepo", pr, wt)

    assert "pills" in payload
    kinds = [p["kind"] for p in payload["pills"]]
    assert kinds == ["wip", "ci_failed", "approved"]

    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None
    assert [p["kind"] for p in on_disk["pills"]] == kinds


def test_write_pr_cache_without_worktree(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint")
    payload = cache_mod.write_pr_cache("testrepo", pr)

    assert "pills" in payload
    # Without wt, no rebase/merge/wip pills appear.
    kinds = [p["kind"] for p in payload["pills"]]
    assert "wip" not in kinds
    assert "ci_failed" in kinds
