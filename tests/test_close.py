"""Tests for the `cockpit close` CLI entry point (cockpit/close.py).

Resolution runs against a real `git worktree` on tmp_path (the leaf layer —
stubbing git would test the stub). The PR-state lookup, blocker probe, and the
enqueue/kick IPC are mocked at the module boundary so gating + routing are
asserted without a network round-trip or a live daemon.
"""

from __future__ import annotations

import pytest

import cockpit.close as close_mod
from tests.conftest import _GIT_ENV_LEAKS
from tests.fixtures import make_git_repo, setup_cockpit_config


@pytest.fixture
def repo_cfg(tmp_path, monkeypatch):
    """A real repo on branch `khivi/foo` (prefix `khivi/`) wired into config,
    with the backend-name lookups and IPC stubbed out so only resolution +
    gating hit real code."""
    # Strip ambient GIT_* vars so `make_git_repo`'s `git -C tmp` commands target
    # the tmpdir, not the host repo. `git push` exports GIT_DIR/GIT_INDEX_FILE
    # into the pre-push hook, which would otherwise corrupt the outer index.
    for var in _GIT_ENV_LEAKS:
        monkeypatch.delenv(var, raising=False)
    repo = make_git_repo(tmp_path, branch="khivi/foo")
    setup_cockpit_config(
        tmp_path,
        monkeypatch,
        {"repos": [{"path": str(repo), "branch_prefix": "khivi/", "name": "myrepo"}]},
    )
    # `cockpit.close` imported `load_config` before the reload — repoint it.
    import cockpit.lib.config as cfg

    monkeypatch.setattr(close_mod, "load_config", cfg.load_config)
    monkeypatch.setattr(close_mod, "_workspace_ref", lambda wt: "workspace:ws1")
    monkeypatch.setattr(close_mod, "_workspace_name", lambda ref: "foo")
    return repo


@pytest.fixture
def captured(monkeypatch):
    """Capture enqueue() requests and stub kick_running() to report a daemon up."""
    reqs: list = []
    monkeypatch.setattr(close_mod, "enqueue", lambda req: reqs.append(req))
    monkeypatch.setattr(close_mod, "kick_running", lambda *, quiet=False: True)
    return reqs


def _no_blockers(monkeypatch, *, state="", number=None):
    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: (state, number))
    monkeypatch.setattr(close_mod, "worktree_state_blockers", lambda *a, **k: [])


def test_resolves_cwd_worktree_and_enqueues(repo_cfg, captured, monkeypatch):
    monkeypatch.setattr(close_mod.Path, "cwd", classmethod(lambda cls: repo_cfg))
    _no_blockers(monkeypatch)
    assert close_mod.main([]) == 0
    assert len(captured) == 1
    req = captured[0]
    assert req.branch == "khivi/foo"
    assert req.repo_name == "myrepo"
    assert req.forced is False


def test_resolves_by_branch_query(repo_cfg, captured, monkeypatch):
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo"]) == 0
    assert captured[0].branch == "khivi/foo"


def test_resolves_by_label_query(repo_cfg, captured, monkeypatch):
    # branch_label strips the `khivi/` prefix → label "foo".
    _no_blockers(monkeypatch)
    assert close_mod.main(["foo"]) == 0
    assert captured[0].branch == "khivi/foo"


def test_unknown_query_errors(repo_cfg, captured, monkeypatch):
    _no_blockers(monkeypatch)
    assert close_mod.main(["nope-not-a-branch"]) == 1
    assert captured == []


def test_hard_blocker_refuses_even_with_force(repo_cfg, captured, monkeypatch, capsys):
    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: ("OPEN", 7))
    monkeypatch.setattr(
        close_mod, "worktree_state_blockers", lambda *a, **k: ["2 uncommitted file(s)"]
    )
    assert close_mod.main(["khivi/foo", "--force"]) == 1
    assert captured == []
    err = capsys.readouterr().err
    assert "uncommitted" in err
    assert "--force does not override" in err


def test_open_pr_soft_blocks_without_force(repo_cfg, captured, monkeypatch, capsys):
    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: ("OPEN", 7))
    monkeypatch.setattr(close_mod, "worktree_state_blockers", lambda *a, **k: [])
    assert close_mod.main(["khivi/foo"]) == 1
    assert captured == []
    assert "PR #7 is OPEN" in capsys.readouterr().err


def test_force_overrides_open_pr(repo_cfg, captured, monkeypatch):
    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: ("OPEN", 7))
    monkeypatch.setattr(close_mod, "worktree_state_blockers", lambda *a, **k: [])
    assert close_mod.main(["khivi/foo", "--force"]) == 0
    assert captured[0].forced is True


def test_merged_pr_sets_delete_branch(repo_cfg, captured, monkeypatch):
    # MERGED → unpushed gate skipped (asserted via the passed pr_merged flag),
    # and delete_branch opts in.
    seen = {}

    def blockers(path, *, branch, is_mine, pr_merged, is_primary=False):
        seen["pr_merged"] = pr_merged
        return []

    monkeypatch.setattr(close_mod, "resolve_pr_state", lambda *a, **k: ("MERGED", 7))
    monkeypatch.setattr(close_mod, "worktree_state_blockers", blockers)
    assert close_mod.main(["khivi/foo"]) == 0
    assert seen["pr_merged"] is True
    assert captured[0].delete_branch is True


def test_dry_run_does_not_enqueue(repo_cfg, captured, monkeypatch, capsys):
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo", "--dry-run"]) == 0
    assert captured == []
    assert "dry-run" in capsys.readouterr().out


def test_no_daemon_still_queues_and_returns_zero(repo_cfg, monkeypatch, capsys):
    reqs: list = []
    monkeypatch.setattr(close_mod, "enqueue", lambda req: reqs.append(req))
    monkeypatch.setattr(close_mod, "kick_running", lambda *, quiet=False: False)
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo"]) == 0
    assert len(reqs) == 1
    assert "no daemon running" in capsys.readouterr().err


def test_marker_ref_falls_back_to_branch_when_no_workspace(
    repo_cfg, captured, monkeypatch
):
    monkeypatch.setattr(close_mod, "_workspace_ref", lambda wt: None)
    monkeypatch.setattr(close_mod, "_workspace_name", lambda ref: "")
    _no_blockers(monkeypatch)
    assert close_mod.main(["khivi/foo"]) == 0
    assert captured[0].ref == "khivi/foo"
