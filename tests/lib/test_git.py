"""Tests for lib.git.

Covers branch-existence helpers (pinning down the suffix-match bug in
_fetch_remote_branch where a bare branch name like `cship` used to match
`refs/heads/*/cship` on `ls-remote --heads`), ahead/behind helpers, and
remove_worktree double-force + lock-reason logging.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from lib import git as gitlib
from lib.git import (
    _fetch_remote_branch,
    _has_local_branch,
    _has_remote_branch,
    ahead_of_base,
    behind_of_base,
    branch_exists,
)


def test_has_remote_branch_exact_match(cockpit_repo, push_branch):
    push_branch("khivi/cship")
    assert _has_remote_branch(cockpit_repo.repo, "khivi/cship") is True


def test_has_remote_branch_returns_false_for_unrelated_suffix(
    cockpit_repo, push_branch
):
    """`refs/heads/khivi/foo/cship` exists on origin; querying for bare
    `cship` must return False."""
    push_branch("khivi/foo/cship")
    assert _has_remote_branch(cockpit_repo.repo, "cship") is False


def test_has_remote_branch_false_when_missing(cockpit_repo):
    assert _has_remote_branch(cockpit_repo.repo, "no-such-branch") is False


def test_fetch_remote_branch_does_not_match_suffix(cockpit_repo, push_branch):
    """The original bug: ls-remote suffix-matched `*/cship` then `fetch
    origin cship:cship` blew up. With the fix, this returns False cleanly."""
    push_branch("khivi/foo/cship")
    assert _fetch_remote_branch(cockpit_repo.repo, "cship") is False


def test_fetch_remote_branch_real_match(cockpit_repo, push_branch):
    push_branch("khivi/cship")
    assert _fetch_remote_branch(cockpit_repo.repo, "khivi/cship") is True
    assert _has_local_branch(cockpit_repo.repo, "khivi/cship") is True


def test_branch_exists_local(cockpit_repo):
    subprocess.run(
        ["git", "-C", str(cockpit_repo.repo), "branch", "local-only", "main"],
        check=True,
    )
    assert branch_exists(cockpit_repo.repo, "local-only") is True


def test_branch_exists_remote(cockpit_repo, push_branch):
    push_branch("remote-only")
    assert branch_exists(cockpit_repo.repo, "remote-only") is True


def test_branch_exists_neither(cockpit_repo):
    assert branch_exists(cockpit_repo.repo, "nope") is False


def test_behind_of_base_counts_commits(cockpit_repo, push_branch):
    """Branch carved at seed; main advances by 2 commits on origin. Branch
    must report behind_of_base == 2 after fetching origin/main."""
    import os

    repo = cockpit_repo.repo
    env = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_AUTHOR_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
    }

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)

    push_branch("khivi/stale")  # carved off seed before main advances
    (repo / "a").write_text("a")
    _git("add", "a")
    _git("commit", "-q", "-m", "a")
    (repo / "b").write_text("b")
    _git("add", "b")
    _git("commit", "-q", "-m", "b")
    _git("push", "-q", "origin", "main")
    _git("fetch", "-q", "origin", "khivi/stale:khivi/stale")
    _git("checkout", "-q", "khivi/stale")
    assert behind_of_base(repo, "main") == 2


def test_behind_of_base_zero_when_no_base(cockpit_repo):
    assert behind_of_base(cockpit_repo.repo, "") == 0


def test_behind_of_base_zero_when_base_unknown(cockpit_repo):
    assert behind_of_base(cockpit_repo.repo, "no-such-base") == 0


def test_ahead_of_base_counts_commits(cockpit_repo, push_branch):
    """Branch carves off main, then adds 2 commits — must report
    ahead_of_base == 2 against origin/main."""
    import os

    repo = cockpit_repo.repo
    env = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_AUTHOR_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
    }

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)

    push_branch("khivi/feat")
    _git("checkout", "-q", "khivi/feat")
    (repo / "x").write_text("x")
    _git("add", "x")
    _git("commit", "-q", "-m", "x")
    (repo / "y").write_text("y")
    _git("add", "y")
    _git("commit", "-q", "-m", "y")
    assert ahead_of_base(repo, "main") == 2


def test_ahead_of_base_zero_when_no_base(cockpit_repo):
    assert ahead_of_base(cockpit_repo.repo, "") == 0


def test_ahead_of_base_zero_when_base_unknown(cockpit_repo):
    assert ahead_of_base(cockpit_repo.repo, "no-such-base") == 0


# ── remove_worktree: double-force + lock-reason logging ────────────────────


def _ok(stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=stderr)


def test_remove_worktree_force_passes_double_force(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    with patch.object(gitlib, "_git", return_value=_ok()) as mock_git:
        ok, _ = gitlib.remove_worktree(repo, wt, force=True)
    assert ok is True
    args = mock_git.call_args.args
    assert args[0] is repo
    assert list(args[1:]) == ["worktree", "remove", "--force", "--force", str(wt)]


def test_remove_worktree_no_force_omits_force_flag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    with patch.object(gitlib, "_git", return_value=_ok()) as mock_git:
        ok, _ = gitlib.remove_worktree(repo, wt, force=False)
    assert ok is True
    args = mock_git.call_args.args
    assert list(args[1:]) == ["worktree", "remove", str(wt)]
    assert "--force" not in args


def test_remove_worktree_force_logs_lock_reason(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    wt.mkdir()
    admin = repo / ".git" / "worktrees" / "wt"
    admin.mkdir(parents=True)
    (admin / "locked").write_text("checkout in progress\n")
    (wt / ".git").write_text(f"gitdir: {admin}\n")

    with patch.object(gitlib, "_git", return_value=_ok()):
        gitlib.remove_worktree(repo, wt, force=True)

    captured = capsys.readouterr()
    assert "preempting checkout in progress" in captured.err


def test_remove_worktree_force_no_lock_file_is_quiet(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    wt = tmp_path / "wt"
    wt.mkdir()

    with patch.object(gitlib, "_git", return_value=_ok()) as mock_git:
        ok, _ = gitlib.remove_worktree(repo, wt, force=True)

    assert ok is True
    args = mock_git.call_args.args
    assert list(args[1:]) == ["worktree", "remove", "--force", "--force", str(wt)]
    captured = capsys.readouterr()
    assert "preempting" not in captured.err
