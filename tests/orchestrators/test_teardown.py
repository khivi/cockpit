"""Unit tests for the shared teardown helper."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from cockpit.orchestrators import teardown as teardown_mod
from cockpit.orchestrators.teardown import (
    TeardownRequest,
    probe_blockers,
    resolve_pr_state,
    teardown,
    worktree_state_blockers,
)


def _patch_all(*, dirty=0, unpushed=0, pr_state=None, live=None):
    """Patch the four leaves probe_blockers leans on.

    `pr_state` seeds the cached payload (`find_pr_payload`); `live` seeds the
    one-shot `fetch_pr_state_for_branch` fallback (a dict like
    `{"state": "MERGED", "number": 7}`, or None for "no live PR / gh failure").
    """
    payload = (
        None
        if pr_state is None
        else {"state": pr_state, "number": 99, "branch": "khivi/x"}
    )
    return (
        patch.object(teardown_mod, "count_dirty", return_value=dirty),
        patch.object(teardown_mod, "_count_unpushed", return_value=unpushed),
        patch.object(teardown_mod, "find_pr_payload", return_value=payload),
        patch.object(teardown_mod, "fetch_pr_state_for_branch", return_value=live),
    )


def test_probe_blockers_clean_returns_empty(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(dirty=0, unpushed=0, pr_state=None)
    with p1, p2, p3, p4:
        assert probe_blockers(wt, "khivi/x", "repo") == []


def test_probe_blockers_dirty_unpushed_open_pr(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(dirty=3, unpushed=2, pr_state="OPEN")
    with p1, p2, p3, p4:
        blockers = probe_blockers(wt, "khivi/x", "repo")
    assert any("3 uncommitted" in b for b in blockers)
    assert any("2 unpushed" in b for b in blockers)
    assert any("PR #99 is OPEN" in b for b in blockers)


def test_probe_blockers_unpushed_verification_failed(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(dirty=0, unpushed=-1, pr_state=None)
    with p1, p2, p3, p4:
        assert "could not verify push state" in probe_blockers(wt, None, None)


def test_probe_blockers_skips_missing_path():
    assert probe_blockers(Path("/nope/missing"), "branch", "repo") == []


# ── worktree_state_blockers: subset called from close.py for --force gating ──


def test_worktree_state_blockers_clean_returns_empty(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=0),
    ):
        assert worktree_state_blockers(wt) == []


def test_worktree_state_blockers_flags_dirty(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=3),
        patch.object(teardown_mod, "_count_unpushed", return_value=0),
    ):
        blockers = worktree_state_blockers(wt)
    assert any("3 uncommitted" in b for b in blockers)


def test_worktree_state_blockers_flags_unpushed(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=2),
    ):
        blockers = worktree_state_blockers(wt)
    assert any("2 unpushed commit" in b for b in blockers)


def test_worktree_state_blockers_flags_unverifiable_push_state(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=-1),
    ):
        blockers = worktree_state_blockers(wt)
    assert any("could not verify" in b for b in blockers)


def test_worktree_state_blockers_primary_skips_unpushed_keeps_dirty(tmp_path):
    # A primary checkout (a `use_worktree: false` `master`) closes workspace-only, so unpushed
    # commits are safe (the checkout stays) — skip that guard. Dirty still holds.
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=5),
    ):
        assert worktree_state_blockers(wt, is_primary=True) == []
    with (
        patch.object(teardown_mod, "count_dirty", return_value=2),
        patch.object(teardown_mod, "_count_unpushed", return_value=5),
    ):
        blockers = worktree_state_blockers(wt, is_primary=True)
    assert any("2 uncommitted" in b for b in blockers)
    assert not any("unpushed" in b for b in blockers)


def test_worktree_state_blockers_skips_missing_path():
    assert worktree_state_blockers(Path("/nope/missing")) == []


def test_worktree_state_blockers_skips_none():
    assert worktree_state_blockers(None) == []


def test_teardown_refuses_on_blockers(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    req = TeardownRequest(
        ref="ws:1",
        name="x",
        worktree_path=wt,
        branch="khivi/x",
        repo_path=tmp_path,
        repo_name="repo",
        forced=False,
    )
    p1, p2, p3, p4 = _patch_all(dirty=2)
    with (
        p1,
        p2,
        p3,
        p4,
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
    ):
        ok, blockers = teardown(req)
    assert not ok
    assert any("uncommitted" in b for b in blockers)
    close_mock.assert_not_called()
    remove_mock.assert_not_called()


def test_teardown_forced_bypasses_blockers(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    req = TeardownRequest(
        ref="ws:1",
        name="x",
        worktree_path=wt,
        branch="khivi/x",
        repo_path=tmp_path,
        repo_name="repo",
        forced=True,
    )
    p1, p2, p3, p4 = _patch_all(dirty=99, unpushed=99, pr_state="OPEN")
    with (
        p1,
        p2,
        p3,
        p4,
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(
            teardown_mod, "remove_worktree", return_value=(True, "")
        ) as rm_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(
            teardown_mod, "ff_default_branch_worktrees", return_value=[]
        ) as ff_mock,
    ):
        ok, _ = teardown(req)
    assert ok
    close_mock.assert_called_once_with("ws:1")
    rm_mock.assert_called_once()
    cache_mock.assert_called_once_with("repo", "khivi/x")
    ff_mock.assert_called_once()


def test_teardown_no_worktree_skips_remove(tmp_path):
    """cwd-missing case: only the workspace is closed."""
    req = TeardownRequest(ref="ws:1", forced=True)
    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as rm_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
    ):
        ok, _ = teardown(req)
    assert ok
    close_mock.assert_called_once()
    rm_mock.assert_not_called()
    cache_mock.assert_not_called()


def test_teardown_primary_checkout_closes_workspace_only(tmp_path):
    """worktree_path == repo_path (a `use_worktree: false` `master`): the session is closed but
    `git worktree remove` is skipped — git refuses it on a primary checkout."""
    req = TeardownRequest(
        ref="ws:1",
        worktree_path=tmp_path,
        branch="master",
        repo_path=tmp_path,  # == worktree_path → primary checkout
        repo_name="repo",
    )
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=4),
        patch.object(teardown_mod, "find_pr_payload", return_value=None),
        patch.object(teardown_mod, "fetch_pr_state_for_branch", return_value=None),
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as rm_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "log_ff_advances"),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
    ):
        ok, blockers = teardown(req)
    assert ok and blockers == []
    close_mock.assert_called_once()  # workspace closed
    rm_mock.assert_not_called()  # checkout left in place (unpushed didn't block)


def test_teardown_remove_failure_returns_error(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    req = TeardownRequest(
        ref="ws:1",
        worktree_path=wt,
        branch="khivi/x",
        repo_path=tmp_path,
        repo_name="repo",
        forced=True,
    )
    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree", return_value=(False, "locked")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
    ):
        ok, blockers = teardown(req)
    assert not ok
    assert any("locked" in b for b in blockers)
    cache_mock.assert_not_called()


def test_teardown_advances_default_branch_worktree(tmp_path, capsys):
    """Successful teardown fast-forwards any worktree on the default branch."""
    wt = tmp_path / "wt"
    wt.mkdir()
    req = TeardownRequest(
        ref="ws:1",
        worktree_path=wt,
        branch="khivi/x",
        repo_path=tmp_path,
        repo_name="repo",
        forced=True,
    )
    from cockpit.lib.git import Worktree

    main_wt = Worktree(path=tmp_path / "main", branch="main")
    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree", return_value=(True, "")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[main_wt]),
        patch.object(
            teardown_mod,
            "ff_default_branch_worktrees",
            return_value=[(main_wt, 3)],
        ) as ff_mock,
    ):
        ok, _ = teardown(req)
    assert ok
    ff_mock.assert_called_once_with(tmp_path, [main_wt])
    out = capsys.readouterr().out
    assert "ff-main" in out
    assert "main → origin/main" in out
    assert "3 commits" in out


def test_teardown_skips_ff_when_repo_path_missing():
    """Orphan-reaping case (repo_path=None) must not invoke ff."""
    req = TeardownRequest(ref="ws:1", forced=True)
    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "ff_default_branch_worktrees") as ff_mock,
    ):
        ok, _ = teardown(req)
    assert ok
    ff_mock.assert_not_called()


def test_teardown_dry_run(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    req = TeardownRequest(
        ref="ws:1",
        worktree_path=wt,
        branch="khivi/x",
        repo_path=tmp_path,
        repo_name="repo",
        forced=True,
    )
    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as rm_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
    ):
        ok, _ = teardown(req, dry=True)
    assert ok
    close_mock.assert_not_called()
    rm_mock.assert_not_called()
    cache_mock.assert_not_called()


# ── ownership-aware unpushed baseline ────────────────────────────────────────


def test_state_blockers_others_pushed_pr_not_blocked(tmp_path):
    """A teammate's pushed-but-unmerged PR (commits on its own remote) is clean."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "commits_only_local", return_value=0) as col,
        patch.object(teardown_mod, "_count_unpushed") as default_baseline,
    ):
        blockers = worktree_state_blockers(wt, branch="alice/feat", is_mine=False)
    assert blockers == []
    col.assert_called_once_with(wt, "alice/feat")
    default_baseline.assert_not_called()


def test_state_blockers_others_local_commits_still_block(tmp_path):
    """Commits that exist only locally block even on someone else's branch."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "commits_only_local", return_value=2),
    ):
        blockers = worktree_state_blockers(wt, branch="alice/feat", is_mine=False)
    assert blockers == ["2 unpushed commit(s)"]


def test_state_blockers_mine_uses_default_baseline(tmp_path):
    """Our own pushed-but-unmerged branch still blocks (default-branch baseline)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=3),
        patch.object(teardown_mod, "commits_only_local") as remote_baseline,
    ):
        blockers = worktree_state_blockers(wt, branch="khivi/feat", is_mine=True)
    assert blockers == ["3 unpushed commit(s)"]
    remote_baseline.assert_not_called()


def test_state_blockers_others_dirty_still_hard(tmp_path):
    """Dirty is always hard, independent of ownership."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=1),
        patch.object(teardown_mod, "commits_only_local", return_value=0),
    ):
        blockers = worktree_state_blockers(wt, branch="alice/feat", is_mine=False)
    assert blockers == ["1 uncommitted file(s)"]


# ── merge-aware unpushed skip (squash-merge / non-default base) ──────────────


def test_state_blockers_merged_skips_unpushed(tmp_path):
    """A MERGED PR's over-counted unpushed commits don't block (squash-merge)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=5) as baseline,
        patch.object(teardown_mod, "commits_only_local") as remote_baseline,
    ):
        blockers = worktree_state_blockers(
            wt, branch="khivi/feat", is_mine=True, pr_merged=True
        )
    assert blockers == []
    baseline.assert_not_called()
    remote_baseline.assert_not_called()


def test_state_blockers_merged_dirty_still_hard(tmp_path):
    """Dirty stays a hard blocker even when the PR is MERGED."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=2),
        patch.object(teardown_mod, "_count_unpushed", return_value=5),
    ):
        blockers = worktree_state_blockers(
            wt, branch="khivi/feat", is_mine=True, pr_merged=True
        )
    assert blockers == ["2 uncommitted file(s)"]


def test_probe_blockers_merged_pr_clean_despite_unpushed(tmp_path):
    """probe_blockers derives pr_merged from the cache: MERGED + unpushed = clean."""
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(dirty=0, unpushed=4, pr_state="MERGED")
    with p1, p2, p3, p4:
        assert probe_blockers(wt, "khivi/x", "repo") == []


def test_probe_blockers_merged_pr_dirty_still_blocks(tmp_path):
    """A MERGED PR with uncommitted edits is still refused by the re-check."""
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(dirty=1, unpushed=4, pr_state="MERGED")
    with p1, p2, p3, p4:
        blockers = probe_blockers(wt, "khivi/x", "repo")
    assert blockers == ["1 uncommitted file(s)"]


# ── live merge-awareness: squash / rebase / deleted-branch out-of-band merge ──


def test_probe_blockers_squash_merged_no_cache_clears_false_unpushed(tmp_path):
    """The bug fix: a squash-merge the slow tick never cached as MERGED.

    No cached payload, `_count_unpushed` over-counts the (collapsed) commits, but
    the live `gh` lookup reports MERGED → the unpushed gate is skipped, so the
    close is no longer false-blocked.
    """
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(
        dirty=0, unpushed=3, pr_state=None, live={"state": "MERGED", "number": 7}
    )
    with p1, p2, p3, p4:
        assert probe_blockers(wt, "khivi/x", "repo") == []


def test_probe_blockers_rebase_merged_same_path(tmp_path):
    """A rebase-merge also records state=MERGED on the live lookup → cleared."""
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(
        dirty=0, unpushed=2, pr_state=None, live={"state": "MERGED", "number": 8}
    )
    with p1, p2, p3, p4:
        assert probe_blockers(wt, "khivi/x", "repo") == []


def test_probe_blockers_genuinely_unpushed_no_pr_still_blocks(tmp_path):
    """No PR anywhere (live None) + real local commits → hard unpushed block."""
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(dirty=0, unpushed=2, pr_state=None, live=None)
    with p1, p2, p3, p4:
        blockers = probe_blockers(wt, "khivi/x", "repo")
    assert blockers == ["2 unpushed commit(s)"]


def test_probe_blockers_genuinely_unpushed_open_pr_blocks_both(tmp_path):
    """Live OPEN (cache empty) + unpushed → unpushed (hard) AND PR-open (soft)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(
        dirty=0, unpushed=2, pr_state=None, live={"state": "OPEN", "number": 12}
    )
    with p1, p2, p3, p4:
        blockers = probe_blockers(wt, "khivi/x", "repo")
    assert any("2 unpushed" in b for b in blockers)
    assert any("PR #12 is OPEN" in b for b in blockers)


def test_probe_blockers_deleted_branch_no_pr_clean(tmp_path):
    """Deleted remote branch, no PR, nothing local-only → clean (no blockers)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3, p4 = _patch_all(dirty=0, unpushed=0, pr_state=None, live=None)
    with p1, p2, p3, p4:
        assert probe_blockers(wt, "khivi/x", "repo") == []


def test_probe_blockers_cached_merged_skips_live_lookup(tmp_path):
    """A cache hit on MERGED is authoritative — no live `gh` round-trip fires."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=4),
        patch.object(
            teardown_mod,
            "find_pr_payload",
            return_value={"state": "MERGED", "number": 99},
        ),
        patch.object(teardown_mod, "fetch_pr_state_for_branch") as live_mock,
    ):
        assert probe_blockers(wt, "khivi/x", "repo") == []
    live_mock.assert_not_called()


def test_probe_blockers_live_lookup_skipped_when_worktree_gone(tmp_path):
    """No working tree to anchor `gh` (already removed) → no live lookup."""
    with (
        patch.object(teardown_mod, "find_pr_payload", return_value=None),
        patch.object(teardown_mod, "fetch_pr_state_for_branch") as live_mock,
    ):
        assert probe_blockers(tmp_path / "gone", "khivi/x", "repo") == []
    live_mock.assert_not_called()


def test_resolve_pr_state_live_wins_over_stale_open_cache(tmp_path):
    """A stale OPEN cache is upgraded to MERGED when the live lookup says so."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(
            teardown_mod,
            "find_pr_payload",
            return_value={"state": "OPEN", "number": 5},
        ),
        patch.object(
            teardown_mod,
            "fetch_pr_state_for_branch",
            return_value={"state": "MERGED", "number": 5},
        ),
    ):
        assert resolve_pr_state(wt, "khivi/x", "repo") == ("MERGED", 5)


def test_resolve_pr_state_gh_failure_keeps_cache(tmp_path):
    """Live lookup failure (None) leaves the cached OPEN state intact."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(
            teardown_mod,
            "find_pr_payload",
            return_value={"state": "OPEN", "number": 5},
        ),
        patch.object(teardown_mod, "fetch_pr_state_for_branch", return_value=None),
    ):
        assert resolve_pr_state(wt, "khivi/x", "repo") == ("OPEN", 5)


# ── delete_branch: local branch deletion after worktree removal ──────────────


def _forced_req(tmp_path, *, delete_branch=False, branch="khivi/x"):
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    return TeardownRequest(
        ref="ws:1",
        worktree_path=wt,
        branch=branch,
        repo_path=tmp_path,
        repo_name="repo",
        forced=True,
        delete_branch=delete_branch,
    )


def _enter_success_patches(stack, *, default="main"):
    """Patch the post-remove collaborators so teardown reaches its happy path."""
    for cm in (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree", return_value=(True, "")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(teardown_mod, "origin_head_branch", return_value=default),
    ):
        stack.enter_context(cm)


def test_teardown_deletes_branch_when_flag_set(tmp_path):
    req = _forced_req(tmp_path, delete_branch=True, branch="khivi/x")
    with ExitStack() as stack:
        _enter_success_patches(stack, default="main")
        del_mock = stack.enter_context(
            patch.object(teardown_mod, "delete_local_branch", return_value=(True, ""))
        )
        ok, _ = teardown(req)
    assert ok
    del_mock.assert_called_once_with(tmp_path, "khivi/x")


def test_teardown_skips_branch_delete_when_flag_false(tmp_path):
    req = _forced_req(tmp_path, delete_branch=False, branch="khivi/x")
    with ExitStack() as stack:
        _enter_success_patches(stack, default="main")
        del_mock = stack.enter_context(
            patch.object(teardown_mod, "delete_local_branch")
        )
        ok, _ = teardown(req)
    assert ok
    del_mock.assert_not_called()


def test_teardown_never_deletes_default_branch(tmp_path):
    """Even with the flag set, the repo's default branch is never deleted."""
    req = _forced_req(tmp_path, delete_branch=True, branch="main")
    with ExitStack() as stack:
        _enter_success_patches(stack, default="main")
        del_mock = stack.enter_context(
            patch.object(teardown_mod, "delete_local_branch")
        )
        ok, _ = teardown(req)
    assert ok
    del_mock.assert_not_called()


def test_teardown_skips_branch_delete_when_default_unknown(tmp_path):
    """origin/HEAD unresolvable → can't prove the branch isn't default → skip."""
    req = _forced_req(tmp_path, delete_branch=True, branch="khivi/x")
    with ExitStack() as stack:
        _enter_success_patches(stack, default=None)
        del_mock = stack.enter_context(
            patch.object(teardown_mod, "delete_local_branch")
        )
        ok, _ = teardown(req)
    assert ok
    del_mock.assert_not_called()


def test_teardown_branch_delete_failure_is_nonfatal(tmp_path, capsys):
    """A failed `git branch -D` warns but doesn't fail the teardown, and the
    cache delete still runs."""
    req = _forced_req(tmp_path, delete_branch=True, branch="khivi/x")
    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree", return_value=(True, "")),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(teardown_mod, "origin_head_branch", return_value="main"),
        patch.object(teardown_mod, "delete_local_branch", return_value=(False, "boom")),
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
    ):
        ok, blockers = teardown(req)
    assert ok
    assert blockers == []
    cache_mock.assert_called_once_with("repo", "khivi/x")
    assert "git branch -D khivi/x failed: boom" in capsys.readouterr().err


# ── primary checkout parked on a non-default branch → tear the branch down ────


def _primary_req(tmp_path, *, branch, forced=False, delete_branch=False):
    """A `use_worktree: false` primary checkout (worktree_path == repo_path)."""
    return TeardownRequest(
        ref="ws:1",
        worktree_path=tmp_path,
        branch=branch,
        repo_path=tmp_path,  # == worktree_path → primary checkout
        repo_name="repo",
        forced=forced,
        delete_branch=delete_branch,
    )


def test_teardown_primary_on_feature_branch_enforces_unpushed(tmp_path):
    """A primary checkout on a *non-default* branch is a branch teardown, so the
    unpushed relaxation does NOT apply — unpushed commits still refuse."""
    req = _primary_req(tmp_path, branch="khivi/x")
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=2),
        patch.object(teardown_mod, "find_pr_payload", return_value=None),
        patch.object(teardown_mod, "fetch_pr_state_for_branch", return_value=None),
        patch.object(teardown_mod, "origin_head_branch", return_value="main"),
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
    ):
        ok, blockers = teardown(req)
    assert not ok
    assert any("unpushed" in b for b in blockers)
    close_mock.assert_not_called()  # refused before any mutation


def test_teardown_primary_on_default_branch_relaxes_unpushed(tmp_path):
    """On its default branch it stays workspace-only — unpushed is relaxed and
    neither the checkout nor the branch delete runs."""
    req = _primary_req(tmp_path, branch="main")
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=5),
        patch.object(teardown_mod, "find_pr_payload", return_value=None),
        patch.object(teardown_mod, "fetch_pr_state_for_branch", return_value=None),
        patch.object(teardown_mod, "origin_head_branch", return_value="main"),
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as rm_mock,
        patch.object(teardown_mod, "checkout_branch") as co_mock,
        patch.object(teardown_mod, "delete_local_branch") as del_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(teardown_mod, "log_ff_advances"),
    ):
        ok, blockers = teardown(req)
    assert ok and blockers == []
    close_mock.assert_called_once()
    rm_mock.assert_not_called()
    co_mock.assert_not_called()
    del_mock.assert_not_called()


def test_teardown_primary_feature_checks_out_default_then_deletes(tmp_path):
    """The core new behavior: close the workspace, move HEAD to the default
    branch, delete the feature ref — but never `git worktree remove`."""
    req = _primary_req(tmp_path, branch="khivi/x", forced=True, delete_branch=True)
    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as rm_mock,
        patch.object(teardown_mod, "origin_head_branch", return_value="main"),
        patch.object(
            teardown_mod, "checkout_branch", return_value=(True, "")
        ) as co_mock,
        patch.object(
            teardown_mod, "delete_local_branch", return_value=(True, "")
        ) as del_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(teardown_mod, "log_ff_advances"),
    ):
        ok, _ = teardown(req)
    assert ok
    close_mock.assert_called_once()
    rm_mock.assert_not_called()  # primary checkout: worktree never removed
    co_mock.assert_called_once_with(tmp_path, "main")
    del_mock.assert_called_once_with(tmp_path, "khivi/x")
    cache_mock.assert_called_once_with("repo", "khivi/x")


def test_teardown_primary_feature_checkout_failure_skips_delete(tmp_path, capsys):
    """If moving HEAD to the default branch fails, the feature branch is left in
    place (a failed `branch -D` of the checked-out branch would only warn) —
    delete is not attempted, and the teardown is still non-fatal."""
    req = _primary_req(tmp_path, branch="khivi/x", forced=True, delete_branch=True)
    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort"),
        patch.object(teardown_mod, "remove_worktree"),
        patch.object(teardown_mod, "origin_head_branch", return_value="main"),
        patch.object(teardown_mod, "checkout_branch", return_value=(False, "conflict")),
        patch.object(teardown_mod, "delete_local_branch") as del_mock,
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
        patch.object(teardown_mod, "log_ff_advances"),
    ):
        ok, _ = teardown(req)
    assert ok  # non-fatal
    del_mock.assert_not_called()
    assert "git checkout main failed" in capsys.readouterr().err
