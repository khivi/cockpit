"""Regression test: _maybe_autoclose must close the cmux workspace BEFORE
removing the worktree. Otherwise the cwd is yanked out from under a live
Claude Code session and every Stop/PreToolUse hook fails with ENOENT.
"""

from __future__ import annotations

from unittest.mock import patch

import cockpit
from lib.git import Worktree


def test_cmux_close_runs_before_remove_worktree(tmp_path):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    calls: list[str] = []

    def fake_cmux_close(ref):
        calls.append("cmux_close")
        return True

    def fake_remove(repo_path, path, **kwargs):
        calls.append("remove_worktree")
        return True, ""

    with (
        patch.object(
            cockpit, "cmux_close_workspace_best_effort", side_effect=fake_cmux_close
        ),
        patch.object(cockpit, "remove_worktree", side_effect=fake_remove),
        patch.object(cockpit, "count_commits_since", return_value=0),
        patch.object(cockpit, "delete_pr_caches_for_branch"),
    ):
        cockpit._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            dry=False,
        )

    assert calls == [
        "cmux_close",
        "remove_worktree",
    ], f"cmux workspace must close before worktree removal; got {calls}"


def test_dry_run_calls_neither(tmp_path):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(cockpit, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(cockpit, "remove_worktree") as remove_mock,
        patch.object(cockpit, "count_commits_since", return_value=0),
        patch.object(cockpit, "delete_pr_caches_for_branch"),
    ):
        cockpit._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            dry=True,
        )

    close_mock.assert_not_called()
    remove_mock.assert_not_called()


def test_remove_failure_still_runs_cmux_close_and_skips_cache_delete(tmp_path):
    """If remove_worktree fails, cmux close has already run (correct), and
    we skip delete_pr_caches_for_branch (preserves prior gating behavior)."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(cockpit, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(cockpit, "remove_worktree", return_value=(False, "boom")),
        patch.object(cockpit, "count_commits_since", return_value=0),
        patch.object(cockpit, "delete_pr_caches_for_branch") as cache_mock,
    ):
        cockpit._maybe_autoclose(
            cfg={"auto_cleanup_on_merge": True},
            repo_path=tmp_path,
            repo_name="testrepo",
            wts=[wt],
            merged_branches={"khivi/feat": "deadbeef"},
            cwds={"ws-ref": wt_path},
            dry=False,
        )

    close_mock.assert_called_once()
    cache_mock.assert_not_called()
