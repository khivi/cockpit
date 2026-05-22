"""Unit tests for the shared teardown helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lib import teardown as teardown_mod
from lib.teardown import TeardownRequest, probe_blockers, teardown


def _patch_all(*, dirty=0, unpushed=0, pr_state=None):
    payload = (
        None
        if pr_state is None
        else {"state": pr_state, "number": 99, "branch": "khivi/x"}
    )
    return (
        patch.object(teardown_mod, "count_dirty", return_value=dirty),
        patch.object(teardown_mod, "_count_unpushed", return_value=unpushed),
        patch.object(teardown_mod, "find_pr_payload", return_value=payload),
    )


def test_probe_blockers_clean_returns_empty(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3 = _patch_all(dirty=0, unpushed=0, pr_state=None)
    with p1, p2, p3:
        assert probe_blockers(wt, "khivi/x", "repo") == []


def test_probe_blockers_dirty_unpushed_open_pr(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3 = _patch_all(dirty=3, unpushed=2, pr_state="OPEN")
    with p1, p2, p3:
        blockers = probe_blockers(wt, "khivi/x", "repo")
    assert any("3 uncommitted" in b for b in blockers)
    assert any("2 unpushed" in b for b in blockers)
    assert any("PR #99 is OPEN" in b for b in blockers)


def test_probe_blockers_unpushed_verification_failed(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    p1, p2, p3 = _patch_all(dirty=0, unpushed=-1, pr_state=None)
    with p1, p2, p3:
        assert "could not verify push state" in probe_blockers(wt, None, None)


def test_probe_blockers_skips_missing_path():
    assert probe_blockers(Path("/nope/missing"), "branch", "repo") == []


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
    p1, p2, p3 = _patch_all(dirty=2)
    with (
        p1,
        p2,
        p3,
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
    p1, p2, p3 = _patch_all(dirty=99, unpushed=99, pr_state="OPEN")
    with (
        p1,
        p2,
        p3,
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
    from lib.git import Worktree

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
