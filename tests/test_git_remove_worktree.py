"""Tests for lib.git.remove_worktree double-force + lock-reason logging."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from lib import git as gitlib


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
