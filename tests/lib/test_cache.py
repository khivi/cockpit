"""Tests for cockpit/lib/cache.py — cockpit-cache writers and refreshers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import cockpit.lib.cache as cache_mod
from cockpit.lib.gh import PR
from cockpit.lib.git import Worktree
from cockpit.lib.nudges import KNOWN_CATEGORIES, NudgePref


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


def test_write_branch_pr_cache_writes_comments(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="CHANGES_REQUESTED",
        number=20,
        title="Review me",
        comments=3,
    )
    assert (cache_dir / "pr-comments-khivi-feature").read_text() == "3"


def test_write_branch_pr_cache_zero_comments_writes_empty(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=21,
        title="Clean",
        comments=0,
    )
    assert (cache_dir / "pr-comments-khivi-feature").read_text() == ""


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
    assert (cache_dir / "pr-comments-khivi-foo").read_text() == ""


def test_refresh_pr_data_populates_from_json_snapshot(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "CHANGES_REQUESTED",
        "number": 99,
        "title": "Fix it",
        "unaddressed": 2,
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/bar")
    assert (cache_dir / "pr-state-khivi-bar").read_text() == "CHANGES_REQUESTED"
    assert (cache_dir / "pr-num-khivi-bar").read_text() == "99"
    assert (cache_dir / "pr-title-khivi-bar").read_text() == "Fix it"
    assert (cache_dir / "pr-comments-khivi-bar").read_text() == "2"


def test_refresh_pr_data_zero_unaddressed_writes_empty(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "number": 5,
        "title": "t",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/clean")
    assert (cache_dir / "pr-comments-khivi-clean").read_text() == ""


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
    "branch,count,expected",
    [
        ("khivi/feature", 5, "5"),
        ("khivi/feature", -1, ""),
        ("khivi/feature", 0, "0"),
    ],
    ids=[
        "writes_payload",
        "empty_on_negative_count",
        "zero_count_is_valid",
    ],
)
def test_write_base_relative_payload(
    cache_dir, writer, cache_file, branch, count, expected
):
    """0 commits is a legitimate, fresh observation; the reader hides 0
    but the writer preserves it for staleness gating."""
    writer(branch, count)
    assert (cache_dir / cache_file).read_text() == expected


@pytest.mark.parametrize(
    "writer",
    [cache_mod.write_base_distance, cache_mod.write_base_ahead],
    ids=["write_base_distance", "write_base_ahead"],
)
def test_write_base_relative_no_branch_noop(cache_dir, writer):
    writer("", 3)
    assert not any(cache_dir.iterdir())


# ── write_git_state_cache (lib.cache) ──────────────────────────────────────


def test_cwd_key_slug_shape():
    """Slug must be filesystem-safe and unambiguous across cwds."""
    from pathlib import Path as _P

    a = cache_mod._cwd_key("/tmp/foo/repo")
    b = cache_mod._cwd_key("/tmp/foo/repo2")
    assert a != b
    assert "/" not in a
    assert not a.startswith("-")
    # Path / string inputs produce the same slug.
    assert cache_mod._cwd_key(_P("/tmp/foo/repo")) == a


def test_write_git_state_cache_in_real_repo(_clean_git_env, cache_dir, tmp_path):
    from tests.fixtures import make_git_repo

    repo = make_git_repo(tmp_path, branch="main")
    cache_mod.write_git_state_cache(repo)
    slug = cache_mod._cwd_key(repo)
    assert (cache_dir / f"git-branch-{slug}").read_text() == "main"
    assert (cache_dir / f"git-status-{slug}").read_text() == "0 0 0"
    assert (cache_dir / f"git-sync-{slug}").read_text() == "0 0"


def test_write_git_state_cache_writes_status_counts(
    _clean_git_env, cache_dir, tmp_path
):
    from tests.fixtures import make_git_repo

    repo = make_git_repo(tmp_path, branch="main", status=(2, 0, 3))
    cache_mod.write_git_state_cache(repo)
    slug = cache_mod._cwd_key(repo)
    assert (cache_dir / f"git-status-{slug}").read_text() == "2 0 3"


def test_write_git_state_cache_writes_ahead(_clean_git_env, cache_dir, tmp_path):
    from tests.fixtures import make_git_repo

    repo = make_git_repo(tmp_path, ahead=3)
    cache_mod.write_git_state_cache(repo)
    slug = cache_mod._cwd_key(repo)
    assert (cache_dir / f"git-sync-{slug}").read_text() == "3 0"


def test_republish_pr_caches_from_disk_rewrites_flat_cells(tmp_path, monkeypatch):
    """Daemon-side fast-tick republisher: walks the per-PR JSON snapshots and
    re-writes pr-state / pr-num / pr-title / pr-muted / pr-checks. Replaces
    the old renderer-spawned `*-refresh` path."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    # Write a PR JSON snapshot first (daemon side).
    pr = _pr(ci="failed:lint", review_decision="APPROVED", number=42, title="Fix it")
    wt = _wt()
    pref = NudgePref(disabled_categories={"ci"})
    cache_mod.write_pr_cache("testrepo", pr, wt, pref)

    # Wipe the flat cells to simulate an OS tmpdir cleanup, then republish.
    for stem in (
        "pr-state",
        "pr-num",
        "pr-title",
        "pr-muted",
        "pr-checks",
        "pr-comments",
    ):
        cache_mod.branch_cache(stem, "khivi/feature").unlink(missing_ok=True)
    cache_mod.republish_pr_caches_from_disk()

    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-state-khivi-feature").read_text() == "APPROVED"
    assert (flat / "pr-num-khivi-feature").read_text() == "42"
    assert (flat / "pr-title-khivi-feature").read_text() == "Fix it"
    assert (flat / "pr-muted-khivi-feature").read_text() == "ci"
    assert (flat / "pr-checks-khivi-feature").read_text() == "✗"
    assert (flat / "pr-comments-khivi-feature").read_text() == ""


def test_republish_pr_caches_no_cache_dir_is_noop(tmp_path, monkeypatch):
    """No JSON snapshots → republisher is a no-op (doesn't crash)."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "nope"))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)
    cache_mod.republish_pr_caches_from_disk()


def test_write_git_state_cache_outside_repo_writes_empty(
    _clean_git_env, cache_dir, tmp_path
):
    """Empty branch (not a repo) must write empty cells, not skip — so a
    cached value from a previous cwd state can't survive."""
    slug = cache_mod._cwd_key(tmp_path)
    # Pre-seed stale data so we can assert it gets cleared.
    (cache_dir / f"git-branch-{slug}").write_text("stale-branch")
    (cache_dir / f"git-status-{slug}").write_text("9 9 9")
    (cache_dir / f"git-sync-{slug}").write_text("9 9")
    cache_mod.write_git_state_cache(tmp_path)
    assert (cache_dir / f"git-branch-{slug}").read_text() == ""
    assert (cache_dir / f"git-status-{slug}").read_text() == ""
    assert (cache_dir / f"git-sync-{slug}").read_text() == ""


# ── write_pr_cache pill round-trip (lib.cache) ─────────────────────────────


def test_write_pr_cache_includes_pills(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

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


# ── muted (pr-muted flat cell + JSON field) ────────────────────────────────


def test_muted_payload_helper_serializes_pref():
    assert cache_mod.muted_payload(None) == ""
    assert cache_mod.muted_payload(NudgePref()) == ""
    assert (
        cache_mod.muted_payload(NudgePref(disabled_categories=set(KNOWN_CATEGORIES)))
        == "all"
    )
    assert (
        cache_mod.muted_payload(NudgePref(disabled_categories={"comments", "ci"}))
        == "ci,comments"
    )


def test_write_branch_pr_cache_writes_muted_cell(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="t",
        muted="all",
    )
    assert (cache_dir / "pr-muted-khivi-feature").read_text() == "all"


def test_write_branch_pr_cache_unmute_clears_cell(cache_dir):
    # First write a muted state, then an unmuted one — cell must clear.
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="t",
        muted="ci,comments",
    )
    assert (cache_dir / "pr-muted-khivi-feature").read_text() == "ci,comments"
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="",
        number=1,
        title="t",
    )
    assert (cache_dir / "pr-muted-khivi-feature").read_text() == ""


def test_refresh_pr_data_copies_muted_from_json(cache_dir):
    payload = {
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "number": 7,
        "title": "x",
        "muted": "ci",
    }
    with patch.object(cache_mod, "find_pr_payload", return_value=payload):
        cache_mod.refresh_pr_data("khivi/feat")
    assert (cache_dir / "pr-muted-khivi-feat").read_text() == "ci"


def test_refresh_pr_data_clears_muted_on_no_pr(cache_dir):
    # Pre-seed a muted cell to ensure the no-PR branch wipes it.
    (cache_dir / "pr-muted-khivi-gone").write_text("all")
    with patch.object(cache_mod, "find_pr_payload", return_value=None):
        cache_mod.refresh_pr_data("khivi/gone")
    assert (cache_dir / "pr-muted-khivi-gone").read_text() == ""


def test_write_pr_cache_bakes_muted_into_json(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr()
    wt = _wt()
    pref = NudgePref(disabled_categories={"ci", "comments"})
    payload = cache_mod.write_pr_cache("testrepo", pr, wt, pref)
    assert payload["muted"] == "ci,comments"
    assert payload["pills"][0]["kind"] == "muted"


def test_write_pr_cache_without_worktree(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint")
    payload = cache_mod.write_pr_cache("testrepo", pr)

    assert "pills" in payload
    # Without wt, no rebase/merge/wip pills appear.
    kinds = [p["kind"] for p in payload["pills"]]
    assert "wip" not in kinds
    assert "ci_failed" in kinds


# ── keep flag (write_pr_cache preserves, set_pr_keep writes) ─────────────────


def test_write_pr_cache_preserves_existing_keep(tmp_path, monkeypatch):
    """Once `keep: true` is on disk, daemon rewrites must not clear it."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr()
    cache_mod.write_pr_cache("testrepo", pr)
    # Simulate spawn.py marking the PR as kept.
    cache_mod.set_pr_keep("testrepo", pr.number)

    # Daemon rewrite (no keep param) must preserve the flag.
    payload = cache_mod.write_pr_cache("testrepo", pr)
    assert payload["keep"] is True

    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None
    assert on_disk["keep"] is True


def test_write_pr_cache_keep_defaults_false(tmp_path, monkeypatch):
    """New PR cache without a prior keep marker has keep=False."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr()
    payload = cache_mod.write_pr_cache("testrepo", pr)
    assert payload["keep"] is False


def test_set_pr_keep_creates_stub_when_no_cache(tmp_path, monkeypatch):
    """set_pr_keep creates a stub file when the PR cache doesn't exist yet."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    cache_mod.set_pr_keep("owner/repo", 42)

    path = cache_mod.CACHE_DIR / "owner_repo__pr-42.json"
    assert path.exists()
    import json

    data = json.loads(path.read_text())
    assert data["keep"] is True


def test_set_pr_keep_merges_into_existing_cache(tmp_path, monkeypatch):
    """set_pr_keep preserves existing fields when updating an existing snapshot."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)

    pr = _pr()
    cache_mod.write_pr_cache("testrepo", pr)
    cache_mod.set_pr_keep("testrepo", pr.number)

    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None
    assert on_disk["keep"] is True
    assert on_disk["title"] == "t"


# ── reused-branch dedup (find_pr_payload / republish / prune) ──────────────
#
# A branch reused across PRs (old PR merged, new PR opened from the same head)
# leaves two `{repo}__pr-{N}.json` files carrying the same `branch`. The flat
# render cells are keyed by branch only, and `_iter_cache` glob order is
# undefined — so the footer would otherwise show whichever number the
# filesystem yielded first. These tests pin the deterministic winner.


@pytest.fixture
def json_cache(tmp_path, monkeypatch):
    """Redirect both the per-PR JSON cache (COCKPIT_HOME/cache) and the flat
    cache to tmpdirs; yield the JSON cache dir. Mirrors the reload pattern the
    other COCKPIT_HOME tests use."""
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import cockpit.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    importlib.reload(cache_mod)
    cache_mod.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    flat = tmp_path / "flat"
    flat.mkdir()
    monkeypatch.setattr(cache_mod, "FLAT_CACHE_DIR", flat)
    return cache_mod.CACHE_DIR


def _snapshot(json_dir: Path, repo: str, number: int, branch: str, **fields) -> Path:
    import json

    payload = {
        "number": number,
        "branch": branch,
        "state": "OPEN",
        "isDraft": False,
        "review": "",
        "ci": "passed",
        "title": "t",
        "updatedAt": "",
        "unaddressed": 0,
        "muted": "",
    }
    payload.update(fields)
    path = json_dir / f"{repo.replace('/', '_')}__pr-{number}.json"
    path.write_text(json.dumps(payload))
    return path


def test_pr_payload_rank_orders_open_then_recency_then_number():
    open_old = {"state": "OPEN", "updatedAt": "2024-01-01", "number": 5}
    open_new = {"state": "OPEN", "updatedAt": "2024-06-01", "number": 3}
    merged_new = {"state": "MERGED", "updatedAt": "2025-01-01", "number": 99}
    rank = cache_mod._pr_payload_rank
    # OPEN beats MERGED even when MERGED is newer / higher-numbered.
    assert rank(open_old) > rank(merged_new)
    # Among OPEN, newer updatedAt wins.
    assert rank(open_new) > rank(open_old)


def test_find_pr_payload_prefers_open_over_merged(json_cache):
    # MERGED #91 and OPEN #126 share the branch; OPEN must win regardless of
    # which file the glob yields first.
    _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    _snapshot(json_cache, "cockpit", 126, "khivi/side", state="OPEN")
    payload = cache_mod.find_pr_payload("khivi/side", repo_name="cockpit")
    assert payload is not None
    assert payload["number"] == 126


def test_find_pr_payload_prefers_newer_when_same_state(json_cache):
    _snapshot(json_cache, "cockpit", 5, "khivi/side", updatedAt="2025-03-01")
    _snapshot(json_cache, "cockpit", 6, "khivi/side", updatedAt="2025-05-01")
    _snapshot(json_cache, "cockpit", 7, "khivi/side", updatedAt="2025-01-01")
    payload = cache_mod.find_pr_payload("khivi/side", repo_name="cockpit")
    assert payload is not None
    assert payload["number"] == 6  # newest updatedAt


def test_load_pr_payloads_by_branch_matches_find_pr_payload(json_cache):
    """The one-pass map must pick the same per-branch winner find_pr_payload
    does — OPEN over MERGED on a reused branch — across multiple branches, and
    must scope to the requested repo only."""
    _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    _snapshot(json_cache, "cockpit", 126, "khivi/side", state="OPEN")
    _snapshot(json_cache, "cockpit", 200, "khivi/other", state="OPEN")
    _snapshot(json_cache, "elsewhere", 1, "khivi/side", state="OPEN")

    by_branch = cache_mod.load_pr_payloads_by_branch("cockpit")

    assert set(by_branch) == {"khivi/side", "khivi/other"}
    assert by_branch["khivi/side"]["number"] == 126  # OPEN beats MERGED #91
    assert by_branch["khivi/other"]["number"] == 200
    # Per-branch winners agree with the per-call lookup.
    for branch, payload in by_branch.items():
        assert payload == cache_mod.find_pr_payload(branch, repo_name="cockpit")


def test_republish_picks_winner_for_reused_branch(json_cache):
    _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    _snapshot(json_cache, "cockpit", 126, "khivi/side", state="OPEN", review="APPROVED")
    cache_mod.republish_pr_caches_from_disk()
    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-num-khivi-side").read_text() == "126"
    assert (flat / "pr-state-khivi-side").read_text() == "APPROVED"


def test_prune_superseded_drops_loser_keeps_winner(json_cache):
    merged = _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    live = _snapshot(json_cache, "cockpit", 126, "khivi/side", state="OPEN")
    pruned = cache_mod.prune_superseded_pr_caches("cockpit")
    assert pruned == [merged]
    assert not merged.exists()
    assert live.exists()


def test_prune_superseded_keeps_lone_snapshot(json_cache):
    # A merged PR with no reused-branch sibling must survive — find_pr_payload
    # still serves it until the worktree tears down.
    only = _snapshot(json_cache, "cockpit", 91, "khivi/side", state="MERGED")
    assert cache_mod.prune_superseded_pr_caches("cockpit") == []
    assert only.exists()


def test_prune_superseded_scoped_to_repo(json_cache):
    # Two repos with the same branch name must not cross-prune.
    a = _snapshot(json_cache, "repoA", 1, "khivi/side", state="MERGED")
    _snapshot(json_cache, "repoA", 2, "khivi/side", state="OPEN")
    b = _snapshot(json_cache, "repoB", 1, "khivi/side", state="MERGED")
    cache_mod.prune_superseded_pr_caches("repoA")
    assert not a.exists()  # superseded within repoA
    assert b.exists()  # repoB untouched (lone snapshot there)


# ── reused-branch suppression (reusedBranch flag → blank PR cells) ─────────
#
# When a merged/closed PR's branch is reused for new local work, the daemon's
# slow tick stamps `reusedBranch: true` on the snapshot (the one place that
# holds the worktree — see cycle._is_reused_branch_merge). Every git-free read
# path trusts the persisted flag and shows no PR.


def test_write_pr_cache_persists_reused_branch_and_head_oid(json_cache):
    pr = _pr(state="MERGED", head_oid="deadbeef")
    payload = cache_mod.write_pr_cache("testrepo", pr, reused_branch=True)
    assert payload["reusedBranch"] is True
    assert payload["headRefOid"] == "deadbeef"
    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None and on_disk["reusedBranch"] is True


def test_write_pr_cache_defaults_reused_branch_false(json_cache):
    payload = cache_mod.write_pr_cache("testrepo", _pr(head_oid="abc"))
    assert payload["reusedBranch"] is False
    assert payload["headRefOid"] == "abc"


def test_clear_branch_pr_cache_empties_all_cells(cache_dir):
    cache_mod.write_branch_pr_cache(
        "khivi/feature",
        state="OPEN",
        is_draft=False,
        review_decision="APPROVED",
        number=17,
        title="Hello",
        ci_glyph="✓",
        comments=3,
    )
    cache_mod.clear_branch_pr_cache("khivi/feature")
    for stem in cache_mod._BRANCH_PR_CELLS:
        assert (cache_dir / f"{stem}-khivi-feature").read_text() == ""


def test_refresh_pr_data_blanks_reused_branch(json_cache):
    _snapshot(
        json_cache, "cockpit", 86, "khivi/side", state="MERGED", reusedBranch=True
    )
    cache_mod.refresh_pr_data("khivi/side")
    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-state-khivi-side").read_text() == ""
    assert (flat / "pr-num-khivi-side").read_text() == ""


def test_refresh_pr_checks_blanks_reused_branch(json_cache):
    _snapshot(
        json_cache,
        "cockpit",
        86,
        "khivi/side",
        state="MERGED",
        ci="failed:1",
        reusedBranch=True,
    )
    cache_mod.refresh_pr_checks("khivi/side")
    assert (cache_mod.FLAT_CACHE_DIR / "pr-checks-khivi-side").read_text() == ""


def test_republish_blanks_reused_branch(json_cache):
    # The lone snapshot for the branch is a reused-branch merge → all cells blank,
    # so an OS-tmpdir-wipe recovery never resurrects the merged state.
    _snapshot(
        json_cache, "cockpit", 86, "khivi/side", state="MERGED", reusedBranch=True
    )
    cache_mod.republish_pr_caches_from_disk()
    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-num-khivi-side").read_text() == ""
    assert (flat / "pr-state-khivi-side").read_text() == ""


def test_republish_open_pr_wins_over_reused_merged_sibling(json_cache):
    # Reused merged #86 alongside a live OPEN #99 on the same branch: the OPEN
    # snapshot outranks the merged one, so the card shows the open PR, not blank.
    _snapshot(
        json_cache, "cockpit", 86, "khivi/side", state="MERGED", reusedBranch=True
    )
    _snapshot(json_cache, "cockpit", 99, "khivi/side", state="OPEN", review="APPROVED")
    cache_mod.republish_pr_caches_from_disk()
    flat = cache_mod.FLAT_CACHE_DIR
    assert (flat / "pr-num-khivi-side").read_text() == "99"
    assert (flat / "pr-state-khivi-side").read_text() == "APPROVED"
