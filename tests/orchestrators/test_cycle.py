"""Tests for scripts/orchestrators/cycle.py.

Two sections:
  - _maybe_autoclose: ordering + dry/error guards (delegates to orchestrators.teardown).
  - _reap_workspace_orphans: gating logic for orphan-workspace cleanup
    (ownership derived from cwd vs registered repos).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.orchestrators.cycle as cycle
from scripts.lib.git import Worktree
from scripts.orchestrators import teardown as teardown_mod


# ────────────────────────────────────────────────────────────────────────────
# _maybe_autoclose: cmux workspace MUST close before worktree removal,
# otherwise the cwd is yanked out from under a live Claude Code session and
# every Stop/PreToolUse hook fails with ENOENT.
# ────────────────────────────────────────────────────────────────────────────


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
            teardown_mod,
            "cmux_close_workspace_best_effort",
            side_effect=fake_cmux_close,
        ),
        patch.object(teardown_mod, "remove_worktree", side_effect=fake_remove),
        patch.object(cycle, "count_commits_since", return_value=0),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
        patch.object(teardown_mod, "worktrees", return_value=[]),
        patch.object(teardown_mod, "ff_default_branch_worktrees", return_value=[]),
    ):
        cycle._maybe_autoclose(
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


def test_autoclose_dry_run_calls_neither(tmp_path):
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree") as remove_mock,
        patch.object(cycle, "count_commits_since", return_value=0),
        patch.object(teardown_mod, "delete_pr_caches_for_branch"),
    ):
        cycle._maybe_autoclose(
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


def test_autoclose_remove_failure_still_closes_cmux_and_skips_cache_delete(tmp_path):
    """If remove_worktree fails, cmux close has already run (correct), and
    we skip delete_pr_caches_for_branch (preserves prior gating behavior)."""
    wt_path = tmp_path / "repo-feat"
    wt_path.mkdir()
    wt = Worktree(path=wt_path, branch="khivi/feat", dirty_count=0)

    with (
        patch.object(teardown_mod, "cmux_close_workspace_best_effort") as close_mock,
        patch.object(teardown_mod, "remove_worktree", return_value=(False, "boom")),
        patch.object(cycle, "count_commits_since", return_value=0),
        patch.object(teardown_mod, "delete_pr_caches_for_branch") as cache_mock,
    ):
        cycle._maybe_autoclose(
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


# ────────────────────────────────────────────────────────────────────────────
# _reap_workspace_orphans: a workspace is reap-eligible iff its cwd resolves
# under a registered repo (main path or live worktree) AND no live worktree
# matches by cwd or name.
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def reap_isolated(tmp_path, monkeypatch):
    """Isolated COCKPIT_HOME and reloaded modules so each test starts fresh."""
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    import scripts.lib.config as cfg

    importlib.reload(cfg)
    import scripts.lib.daemon_signal as cr

    importlib.reload(cr)
    import scripts.orchestrators.cycle as cycle_mod

    importlib.reload(cycle_mod)
    return cycle_mod, cr


def _wt_stub(path: Path, branch: str):
    return Worktree(path=path, branch=branch, dirty_count=0, unpushed=0)


def test_reap_skips_tracked_workspace(reap_isolated, tmp_path):
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt_path = tmp_path / "wt-tracked"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat")

    repos = [{"path": str(repo_path), "name": "repo"}]

    with (
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=({"workspace:1": "feat-x"}, {"workspace:1": wt_path}),
        ),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_reap_enqueues_stranded_workspace_in_registered_repo(reap_isolated, tmp_path):
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]

    ghost_cwd = repo_path / "removed-worktree"
    ghost_cwd.mkdir()

    with (
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cycle_mod, "workspace_is_idle", return_value=True),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    pending = cr.iter_pending()
    assert len(pending) == 1
    _, req = pending[0]
    assert req.ref == "workspace:99"
    assert req.worktree_path is None
    assert req.forced is True
    assert req.repo_name == "repo"


def test_reap_defers_when_workspace_not_idle(reap_isolated, tmp_path, capsys):
    """A stranded workspace whose Claude is mid-turn is left for next cycle."""
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]
    ghost_cwd = repo_path / "removed-worktree"
    ghost_cwd.mkdir()

    with (
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cycle_mod, "workspace_is_idle", return_value=False),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []
    out = capsys.readouterr().out
    assert "defer" in out
    assert "reap" in out
    assert "not idle" in out
    assert "workspace:99" in out


def test_reap_ignores_workspace_outside_registered_repos(reap_isolated, tmp_path):
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with (
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=(
                {"workspace:42": "research"},
                {"workspace:42": elsewhere},
            ),
        ),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_reap_dry_run_does_not_enqueue(reap_isolated, tmp_path):
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]
    ghost_cwd = repo_path / "ghost"
    ghost_cwd.mkdir()

    with (
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cycle_mod, "workspace_is_idle", return_value=True),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=True)

    assert cr.iter_pending() == []


def test_reap_skips_workspace_matched_by_name(reap_isolated, tmp_path):
    """Even with a missing cwd, name-match to an existing wt.short keeps it alive."""
    cycle_mod, cr = reap_isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt_path = tmp_path / "feat-named"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat-named")

    repos = [{"path": str(repo_path), "name": "repo"}]

    with (
        patch.object(cycle_mod, "worktrees", return_value=[wt]),
        patch.object(
            cycle_mod,
            "workspace_state",
            return_value=({"workspace:5": "feat-named"}, {}),
        ),
    ):
        cycle_mod._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []
