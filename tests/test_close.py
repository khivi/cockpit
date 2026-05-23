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
