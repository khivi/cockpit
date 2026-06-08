"""Tests for `cockpit:close` with no query (cwd-self mode)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import cockpit.close as close_script
from cockpit.lib.git import Worktree


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
        pytest.raises(LookupError, match=r"no \w+ workspace rooted at"),
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
    assert "cockpit watch" in err
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


# ── pr_merged forwarding (squash-merge / non-default-base unpushed skip) ─────


def _run_main_capturing_blocker_kwargs_with_pr(
    cockpit_repo, monkeypatch, branch, pr_payload
):
    """Like `_run_main_capturing_blocker_kwargs` but wires repo name + a cached
    PR payload, so close.py's merged-state lookup runs and forwards `pr_merged`."""
    wt_path = cockpit_repo.repo.parent / "pm-feat"
    _make_wt(cockpit_repo.repo, wt_path, branch)
    monkeypatch.chdir(wt_path)

    seen: dict = {}

    def _capture(_path, **kwargs):
        seen.update(kwargs)
        return []

    with (
        patch.object(close_script, "require_workspace_binary"),
        patch.object(close_script, "workspace_cwds", return_value={"ws:7": wt_path}),
        patch.object(close_script, "workspace_names", return_value={"ws:7": "pm"}),
        patch.object(
            close_script,
            "discover_repo",
            return_value={
                "path": str(cockpit_repo.repo),
                "name": "testrepo",
                "branch_prefix": "khivi/",
            },
        ),
        patch.object(close_script, "worktree_state_blockers", side_effect=_capture),
        patch.object(close_script, "probe_blockers", return_value=[]),
        patch.object(close_script, "find_pr_payload", return_value=pr_payload),
        patch.object(close_script, "kick_running", return_value=True),
        patch.object(close_script, "enqueue"),
        patch("sys.argv", ["close"]),
    ):
        rc = close_script.main()
    return rc, seen


def test_main_forwards_pr_merged_when_merged(cockpit_repo, monkeypatch):
    """A MERGED cached PR → close.py passes pr_merged=True to the hard gate."""
    rc, seen = _run_main_capturing_blocker_kwargs_with_pr(
        cockpit_repo,
        monkeypatch,
        "khivi/merged-feat",
        {"state": "MERGED", "number": 5, "branch": "khivi/merged-feat"},
    )
    assert rc == 0
    assert seen.get("pr_merged") is True


def test_main_forwards_pr_merged_false_when_open(cockpit_repo, monkeypatch):
    rc, seen = _run_main_capturing_blocker_kwargs_with_pr(
        cockpit_repo,
        monkeypatch,
        "khivi/open-feat",
        {"state": "OPEN", "number": 6, "branch": "khivi/open-feat"},
    )
    assert rc == 0
    assert seen.get("pr_merged") is False


def test_main_forwards_pr_merged_false_when_no_pr(cockpit_repo, monkeypatch):
    rc, seen = _run_main_capturing_blocker_kwargs_with_pr(
        cockpit_repo, monkeypatch, "khivi/no-pr-feat", None
    )
    assert rc == 0
    assert seen.get("pr_merged") is False


# ── delete_branch on a merged PR ─────────────────────────────────────────────


def _run_main_capturing_request(cockpit_repo, monkeypatch, branch, pr_payload, *argv):
    """Run main() and return the enqueued TeardownRequest. Hard/soft blockers
    are mocked away so the run always reaches enqueue; `pr_payload` is what
    close.py's own merged-state lookup sees."""
    wt_path = cockpit_repo.repo.parent / "del-feat"
    _make_wt(cockpit_repo.repo, wt_path, branch)
    monkeypatch.chdir(wt_path)

    enqueued: list = []

    with (
        patch.object(close_script, "require_workspace_binary"),
        patch.object(close_script, "workspace_cwds", return_value={"ws:7": wt_path}),
        patch.object(close_script, "workspace_names", return_value={"ws:7": "del"}),
        patch.object(
            close_script,
            "discover_repo",
            return_value={
                "path": str(cockpit_repo.repo),
                "name": "testrepo",
                "branch_prefix": "khivi/",
            },
        ),
        patch.object(close_script, "worktree_state_blockers", return_value=[]),
        patch.object(close_script, "probe_blockers", return_value=[]),
        patch.object(close_script, "find_pr_payload", return_value=pr_payload),
        patch.object(close_script, "kick_running", return_value=True),
        patch.object(close_script, "enqueue", side_effect=enqueued.append),
        patch("sys.argv", ["close", *argv]),
    ):
        rc = close_script.main()
    assert rc == 0
    assert len(enqueued) == 1
    return enqueued[0]


def test_main_deletes_branch_when_pr_merged(cockpit_repo, monkeypatch):
    req = _run_main_capturing_request(
        cockpit_repo,
        monkeypatch,
        "khivi/merged-feat",
        {"state": "MERGED", "number": 5, "branch": "khivi/merged-feat"},
    )
    assert req.delete_branch is True


def test_main_keeps_branch_when_pr_open(cockpit_repo, monkeypatch):
    """A --force close of a still-OPEN PR must not delete the branch."""
    req = _run_main_capturing_request(
        cockpit_repo,
        monkeypatch,
        "khivi/open-feat",
        {"state": "OPEN", "number": 6, "branch": "khivi/open-feat"},
        "--force",
    )
    assert req.delete_branch is False


def test_main_keeps_branch_when_no_pr(cockpit_repo, monkeypatch):
    req = _run_main_capturing_request(
        cockpit_repo, monkeypatch, "khivi/no-pr-feat", None
    )
    assert req.delete_branch is False
