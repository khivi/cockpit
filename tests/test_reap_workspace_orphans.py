"""Tests for `_reap_workspace_orphans` gating.

Ownership is derived from cwd vs registered repos. A workspace is reap-eligible
iff its cwd resolves under a registered repo (main path or live worktree) AND
no live worktree matches by cwd or name.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    import lib.config as cfg

    importlib.reload(cfg)
    import lib.close_requests as cr

    importlib.reload(cr)
    import cockpit

    importlib.reload(cockpit)
    return cockpit, cr


def _wt_stub(path: Path, branch: str):
    from lib.git import Worktree

    return Worktree(path=path, branch=branch, dirty_count=0, unpushed=0)


def test_tracked_workspace_not_reaped(isolated, tmp_path):
    cockpit, cr = isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt_path = tmp_path / "wt-tracked"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat")

    repos = [{"path": str(repo_path), "name": "repo"}]

    with (
        patch.object(cockpit, "worktrees", return_value=[wt]),
        patch(
            "lib.cmux.workspace_state",
            return_value=({"workspace:1": "feat-x"}, {"workspace:1": wt_path}),
        ),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_stranded_in_registered_repo_is_reaped(isolated, tmp_path):
    cockpit, cr = isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]

    ghost_cwd = repo_path / "removed-worktree"
    ghost_cwd.mkdir()

    with (
        patch.object(cockpit, "worktrees", return_value=[wt]),
        patch(
            "lib.cmux.workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=False)

    pending = cr.iter_pending()
    assert len(pending) == 1
    _, req = pending[0]
    assert req.ref == "workspace:99"
    assert req.worktree_path is None
    assert req.forced is True
    assert req.repo_name == "repo"


def test_workspace_outside_registered_repos_is_ignored(isolated, tmp_path):
    cockpit, cr = isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with (
        patch.object(cockpit, "worktrees", return_value=[wt]),
        patch(
            "lib.cmux.workspace_state",
            return_value=(
                {"workspace:42": "research"},
                {"workspace:42": elsewhere},
            ),
        ),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_dry_run_does_not_enqueue(isolated, tmp_path):
    cockpit, cr = isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt = _wt_stub(repo_path, "main")

    repos = [{"path": str(repo_path), "name": "repo"}]
    ghost_cwd = repo_path / "ghost"
    ghost_cwd.mkdir()

    with (
        patch.object(cockpit, "worktrees", return_value=[wt]),
        patch(
            "lib.cmux.workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=True)

    assert cr.iter_pending() == []


def test_workspace_matching_by_name_not_reaped(isolated, tmp_path):
    """Even with a missing cwd, name-match to an existing wt.short keeps it alive."""
    cockpit, cr = isolated
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    wt_path = tmp_path / "feat-named"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat-named")

    repos = [{"path": str(repo_path), "name": "repo"}]

    with (
        patch.object(cockpit, "worktrees", return_value=[wt]),
        patch(
            "lib.cmux.workspace_state",
            return_value=({"workspace:5": "feat-named"}, {}),
        ),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []
