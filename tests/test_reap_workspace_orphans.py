"""Tests for `_reap_workspace_orphans` gating.

Validates that:
- workspaces resolving to a known worktree (via cwd OR name) are LEFT ALONE
- workspaces with the MANAGED pill but no matching worktree are ENQUEUED for close
- workspaces with NO MANAGED pill are LEFT ALONE (free-form user workspace)
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
    wt_path = tmp_path / "wt-tracked"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat")

    repos = [{"path": str(tmp_path / "repo"), "name": "repo"}]
    (tmp_path / "repo").mkdir()

    with (
        patch.object(cockpit, "worktrees", return_value=[wt]),
        patch(
            "cockpit._reap_workspace_orphans.__wrapped__", create=True
        ),  # no-op decorator import-guard
        patch(
            "lib.cmux.workspace_state",
            return_value=({"workspace:1": "feat-x"}, {"workspace:1": wt_path}),
        ),
        patch.object(cockpit, "has_managed_pill", return_value=True),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_managed_orphan_workspace_enqueued(isolated, tmp_path):
    cockpit, cr = isolated
    wt_path = tmp_path / "wt-live"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/live")

    repos = [{"path": str(tmp_path / "repo"), "name": "repo"}]
    (tmp_path / "repo").mkdir()

    # workspace:99 has a cwd at a path that doesn't match any wt.path
    ghost_cwd = tmp_path / "ghost"
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
        patch.object(cockpit, "has_managed_pill", return_value=True),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=False)

    pending = cr.iter_pending()
    assert len(pending) == 1
    _, req = pending[0]
    assert req.ref == "workspace:99"
    assert req.worktree_path is None
    assert req.forced is True


def test_unmanaged_orphan_workspace_left_alone(isolated, tmp_path):
    cockpit, cr = isolated
    wt_path = tmp_path / "wt-live"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/live")

    repos = [{"path": str(tmp_path / "repo"), "name": "repo"}]
    (tmp_path / "repo").mkdir()

    ghost_cwd = tmp_path / "freeform"
    ghost_cwd.mkdir()

    with (
        patch.object(cockpit, "worktrees", return_value=[wt]),
        patch(
            "lib.cmux.workspace_state",
            return_value=(
                {"workspace:42": "research"},
                {"workspace:42": ghost_cwd},
            ),
        ),
        patch.object(cockpit, "has_managed_pill", return_value=False),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []


def test_dry_run_does_not_enqueue(isolated, tmp_path):
    cockpit, cr = isolated
    repos = [{"path": str(tmp_path / "repo"), "name": "repo"}]
    (tmp_path / "repo").mkdir()
    ghost_cwd = tmp_path / "ghost"
    ghost_cwd.mkdir()

    with (
        patch.object(cockpit, "worktrees", return_value=[]),
        patch(
            "lib.cmux.workspace_state",
            return_value=(
                {"workspace:99": "khivi/ghost"},
                {"workspace:99": ghost_cwd},
            ),
        ),
        patch.object(cockpit, "has_managed_pill", return_value=True),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=True)

    assert cr.iter_pending() == []


def test_workspace_matching_by_name_not_reaped(isolated, tmp_path):
    """Even with a missing cwd, name-match to an existing wt.short keeps it alive."""
    cockpit, cr = isolated
    wt_path = tmp_path / "feat-named"
    wt_path.mkdir()
    wt = _wt_stub(wt_path, "khivi/feat-named")

    repos = [{"path": str(tmp_path / "repo"), "name": "repo"}]
    (tmp_path / "repo").mkdir()

    with (
        patch.object(cockpit, "worktrees", return_value=[wt]),
        patch(
            "lib.cmux.workspace_state",
            return_value=({"workspace:5": "feat-named"}, {}),
        ),
        patch.object(cockpit, "has_managed_pill", return_value=True),
    ):
        cockpit._reap_workspace_orphans(repos, "khivi", dry=False)

    assert cr.iter_pending() == []
