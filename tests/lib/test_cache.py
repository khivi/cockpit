"""Tests for scripts/lib/cache.py — cockpit-cache writers and refreshers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.lib.cache as cache_mod
from scripts.lib.gh import PR
from scripts.lib.git import Worktree


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


# ── refresh_pr_data / refresh_pr_checks read the per-PR JSON snapshot ──────


def test_refresh_pr_data_writes_no_pr_sentinel(cache_dir):
    with patch.object(cache_mod, "find_pr_payload", return_value=None):
        cache_mod.refresh_pr_data("khivi/foo")
    assert (cache_dir / "pr-state-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-num-khivi-foo").read_text() == ""
    assert (cache_dir / "pr-title-khivi-foo").read_text() == ""


def test_refresh_pr_data_populates_from_json_snapshot(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "CHANGES_REQUESTED",
        "number": 99,
        "title": "Fix it",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/bar")
    assert (cache_dir / "pr-state-khivi-bar").read_text() == "CHANGES_REQUESTED"
    assert (cache_dir / "pr-num-khivi-bar").read_text() == "99"
    assert (cache_dir / "pr-title-khivi-bar").read_text() == "Fix it"


def test_refresh_pr_data_resolves_draft(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": True,
        "review": "",
        "number": 12,
        "title": "wip",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/draft")
    assert (cache_dir / "pr-state-khivi-draft").read_text() == "DRAFT"


def test_refresh_pr_checks_writes_no_pr_sentinel(cache_dir):
    with patch.object(cache_mod, "find_pr_payload", return_value=None):
        cache_mod.refresh_pr_checks("khivi/foo")
    assert (cache_dir / "pr-checks-khivi-foo").read_text() == ""


@pytest.mark.parametrize(
    "ci,expected",
    [
        ("passed", "✓"),
        ("pending", "•"),
        ("failed:lint", "✗"),
        ("none", ""),
        ("", ""),
    ],
    ids=["passed", "pending", "failed", "no-runs", "unknown"],
)
def test_refresh_pr_checks_derives_glyph_from_json(cache_dir, ci, expected):
    """Daemon-written JSON snapshot is the single source for both the cmux
    sidebar pill and the footer's pr-checks cell — same ci → same glyph."""
    with patch.object(cache_mod, "find_pr_payload", return_value={"ci": ci}):
        cache_mod.refresh_pr_checks("khivi/feat")
    assert (cache_dir / "pr-checks-khivi-feat").read_text() == expected


# ── write_base_distance / write_base_ahead (lib.cache) ─────────────────────


@pytest.mark.parametrize(
    "writer,cache_file",
    [
        (cache_mod.write_base_distance, "base-distance-khivi-feature"),
        (cache_mod.write_base_ahead, "base-ahead-khivi-feature"),
    ],
    ids=["write_base_distance", "write_base_ahead"],
)
@pytest.mark.parametrize(
    "branch,count,epoch,expected",
    [
        ("khivi/feature", 5, 1700000000, "5 1700000000"),
        ("khivi/feature", -1, 1700000000, ""),
        ("khivi/feature", 3, 0, ""),
        ("khivi/feature", 0, 1700000000, "0 1700000000"),
    ],
    ids=[
        "writes_payload",
        "empty_on_negative_count",
        "empty_on_missing_epoch",
        "zero_count_is_valid",
    ],
)
def test_write_base_relative_payload(
    cache_dir, writer, cache_file, branch, count, epoch, expected
):
    """0 commits is a legitimate, fresh observation; the reader hides 0
    but the writer preserves it for staleness gating."""
    writer(branch, count, epoch)
    assert (cache_dir / cache_file).read_text() == expected


@pytest.mark.parametrize(
    "writer",
    [cache_mod.write_base_distance, cache_mod.write_base_ahead],
    ids=["write_base_distance", "write_base_ahead"],
)
def test_write_base_relative_no_branch_noop(cache_dir, writer):
    writer("", 3, 1700000000)
    assert not any(cache_dir.iterdir())


# ── write_pr_cache pill round-trip (lib.cache) ─────────────────────────────


def test_write_pr_cache_includes_pills(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import scripts.lib.config as cockpit_config

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
    import scripts.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint")
    payload = cache_mod.write_pr_cache("testrepo", pr)

    assert "pills" in payload
    # Without wt, no rebase/merge/wip pills appear.
    kinds = [p["kind"] for p in payload["pills"]]
    assert "wip" not in kinds
    assert "ci_failed" in kinds
