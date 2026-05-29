"""Tests for `cockpit:close` with no query (cwd-self mode)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.close as close_script
from scripts.lib.git import Worktree


def _make_wt(repo_dir: Path, path: Path, branch: str) -> Worktree:
    """Create a real worktree on disk and return a populated Worktree."""
    subprocess.run(
        ["git", "-C", str(repo_dir), "worktree", "add", "-b", branch, str(path)],
        check=True,
        capture_output=True,
    )
    return Worktree(path=path, branch=branch, dirty_count=0, unpushed=0)


def test_match_from_cwd_resolves_unique(cockpit_repo, monkeypatch):
    wt_path = cockpit_repo.repo.parent / "feat-x"
    _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-x")

    monkeypatch.chdir(wt_path)

    with (
        patch.object(
            close_script, "workspace_cwds", return_value={"workspace:7": wt_path}
        ),
        patch.object(
            close_script, "workspace_names", return_value={"workspace:7": "feat-x"}
        ),
    ):
        match = close_script._match_from_cwd(cockpit_repo.repo)

    assert match.ref == "workspace:7"
    assert match.name == "feat-x"
    assert match.worktree.branch == "khivi/feat-x"


def test_match_from_cwd_rejects_when_no_workspace(cockpit_repo, monkeypatch):
    wt_path = cockpit_repo.repo.parent / "feat-y"
    _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-y")

    monkeypatch.chdir(wt_path)

    with (
        patch.object(close_script, "workspace_cwds", return_value={}),
        patch.object(close_script, "workspace_names", return_value={}),
        pytest.raises(LookupError, match="no cmux workspace rooted at"),
    ):
        close_script._match_from_cwd(cockpit_repo.repo)


def test_match_from_cwd_rejects_ambiguity(cockpit_repo, monkeypatch):
    wt_path = cockpit_repo.repo.parent / "feat-z"
    _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-z")

    monkeypatch.chdir(wt_path)

    with (
        patch.object(
            close_script,
            "workspace_cwds",
            return_value={"workspace:1": wt_path, "workspace:2": wt_path},
        ),
        patch.object(
            close_script,
            "workspace_names",
            return_value={"workspace:1": "z-1", "workspace:2": "z-2"},
        ),
        pytest.raises(LookupError, match="multiple workspaces"),
    ):
        close_script._match_from_cwd(cockpit_repo.repo)


def test_match_from_cwd_rejects_outside_worktree(tmp_path, monkeypatch):
    """No git worktree at cwd → clean LookupError, not a traceback.

    macOS `tmp_path` lives under `/private/var/...`, which `git rev-parse`
    may successfully resolve as its own toplevel; the worktree lookup
    against the configured repo still fails, just with a different message.
    Either form is acceptable — both indicate the no-arg path bailed out.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LookupError, match="not inside a git worktree|no worktree at"):
        close_script._match_from_cwd(tmp_path)


def test_match_from_cwd_resolves_from_subdirectory(cockpit_repo, monkeypatch):
    """`git rev-parse --show-toplevel` collapses subdir → worktree root."""
    wt_path = cockpit_repo.repo.parent / "feat-sub"
    _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-sub")
    sub = wt_path / "src" / "deep"
    sub.mkdir(parents=True)

    monkeypatch.chdir(sub)

    with (
        patch.object(
            close_script, "workspace_cwds", return_value={"workspace:9": wt_path}
        ),
        patch.object(
            close_script, "workspace_names", return_value={"workspace:9": "feat-sub"}
        ),
    ):
        match = close_script._match_from_cwd(cockpit_repo.repo)

    assert match.ref == "workspace:9"


# ── main: daemon-required ───────────────────────────────────────────────────


def test_main_errors_when_daemon_absent(cockpit_repo, monkeypatch, capsys):
    """No running daemon → clean stderr + exit 1, no teardown, no enqueue."""
    wt_path = cockpit_repo.repo.parent / "feat-no-daemon"
    _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-no-daemon")
    monkeypatch.chdir(wt_path)

    enqueue_calls: list = []

    with (
        patch.object(close_script, "require_workspace_binary"),
        patch.object(
            close_script, "workspace_cwds", return_value={"workspace:7": wt_path}
        ),
        patch.object(
            close_script,
            "workspace_names",
            return_value={"workspace:7": "feat-no-daemon"},
        ),
        patch.object(
            close_script, "discover_repo", return_value={"path": str(cockpit_repo.repo)}
        ),
        patch.object(close_script, "worktree_state_blockers", return_value=[]),
        patch.object(close_script, "probe_blockers", return_value=[]),
        patch.object(close_script, "kick_running", return_value=False),
        patch.object(close_script, "enqueue", side_effect=enqueue_calls.append),
        patch("sys.argv", ["close"]),
    ):
        rc = close_script.main()

    assert rc == 1
    err = capsys.readouterr().err
    assert "daemon not running" in err
    assert "cockpit --watch" in err
    assert enqueue_calls == []


def test_main_queues_when_daemon_running(cockpit_repo, monkeypatch, capsys):
    """Daemon up → enqueue + 0; no inline teardown call."""
    wt_path = cockpit_repo.repo.parent / "feat-queued"
    _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-queued")
    monkeypatch.chdir(wt_path)

    enqueued: list = []

    with (
        patch.object(close_script, "require_workspace_binary"),
        patch.object(
            close_script, "workspace_cwds", return_value={"workspace:7": wt_path}
        ),
        patch.object(
            close_script,
            "workspace_names",
            return_value={"workspace:7": "feat-queued"},
        ),
        patch.object(
            close_script, "discover_repo", return_value={"path": str(cockpit_repo.repo)}
        ),
        patch.object(close_script, "worktree_state_blockers", return_value=[]),
        patch.object(close_script, "probe_blockers", return_value=[]),
        patch.object(close_script, "kick_running", return_value=True),
        patch.object(close_script, "enqueue", side_effect=enqueued.append),
        patch("sys.argv", ["close"]),
    ):
        rc = close_script.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "queued close" in out
    assert len(enqueued) == 1
    assert enqueued[0].ref == "workspace:7"


# ── ownership forwarding (is_mine) ───────────────────────────────────────────


def _run_main_capturing_blocker_kwargs(cockpit_repo, monkeypatch, wt_name, branch):
    wt_path = cockpit_repo.repo.parent / wt_name
    _make_wt(cockpit_repo.repo, wt_path, branch)
    monkeypatch.chdir(wt_path)

    seen: dict = {}

    def _capture(_path, **kwargs):
        seen.update(kwargs)
        return []

    with (
        patch.object(close_script, "require_workspace_binary"),
        patch.object(close_script, "workspace_cwds", return_value={"ws:7": wt_path}),
        patch.object(close_script, "workspace_names", return_value={"ws:7": wt_name}),
        patch.object(
            close_script,
            "discover_repo",
            return_value={"path": str(cockpit_repo.repo), "branch_prefix": "khivi/"},
        ),
        patch.object(close_script, "worktree_state_blockers", side_effect=_capture),
        patch.object(close_script, "probe_blockers", return_value=[]),
        patch.object(close_script, "kick_running", return_value=True),
        patch.object(close_script, "enqueue"),
        patch("sys.argv", ["close"]),
    ):
        rc = close_script.main()
    return rc, seen


def test_main_marks_others_branch_not_mine(cockpit_repo, monkeypatch):
    """A branch outside our prefix → is_mine=False (teammate's PR)."""
    rc, seen = _run_main_capturing_blocker_kwargs(
        cockpit_repo, monkeypatch, "alice-feat", "alice/feat"
    )
    assert rc == 0
    assert seen.get("branch") == "alice/feat"
    assert seen.get("is_mine") is False


def test_main_marks_own_branch_mine(cockpit_repo, monkeypatch):
    """A branch under our prefix → is_mine=True."""
    rc, seen = _run_main_capturing_blocker_kwargs(
        cockpit_repo, monkeypatch, "mine-feat", "khivi/mine-feat"
    )
    assert rc == 0
    assert seen.get("branch") == "khivi/mine-feat"
    assert seen.get("is_mine") is True
