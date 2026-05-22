"""Tests for `cockpit:close` with no query (cwd-self mode)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import close as close_script
from lib import teardown as teardown_mod
from lib.git import Worktree
from lib.teardown import worktree_state_blockers as hard_blockers


def _make_wt(repo_dir: Path, path: Path, branch: str) -> Worktree:
    """Create a real worktree on disk and return a populated Worktree."""
    subprocess.run(
        ["git", "-C", str(repo_dir), "worktree", "add", "-b", branch, str(path)],
        check=True,
        capture_output=True,
    )
    return Worktree(path=path, branch=branch, dirty_count=0, unpushed=0)


def test_match_from_cwd_resolves_unique(cockpit_repo, monkeypatch, tmp_path):
    wt_path = cockpit_repo.repo.parent / "feat-x"
    wt = _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-x")

    monkeypatch.chdir(wt_path)

    with (
        patch.object(close_script, "worktrees", return_value=[wt]),
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
    wt = _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-y")

    monkeypatch.chdir(wt_path)

    with (
        patch.object(close_script, "worktrees", return_value=[wt]),
        patch.object(close_script, "workspace_cwds", return_value={}),
        patch.object(close_script, "workspace_names", return_value={}),
        pytest.raises(LookupError, match="no cmux workspace rooted at"),
    ):
        close_script._match_from_cwd(cockpit_repo.repo)


def test_match_from_cwd_rejects_ambiguity(cockpit_repo, monkeypatch):
    wt_path = cockpit_repo.repo.parent / "feat-z"
    wt = _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-z")

    monkeypatch.chdir(wt_path)

    with (
        patch.object(close_script, "worktrees", return_value=[wt]),
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
    wt = _make_wt(cockpit_repo.repo, wt_path, "khivi/feat-sub")
    sub = wt_path / "src" / "deep"
    sub.mkdir(parents=True)

    monkeypatch.chdir(sub)

    with (
        patch.object(close_script, "worktrees", return_value=[wt]),
        patch.object(
            close_script, "workspace_cwds", return_value={"workspace:9": wt_path}
        ),
        patch.object(
            close_script, "workspace_names", return_value={"workspace:9": "feat-sub"}
        ),
    ):
        match = close_script._match_from_cwd(cockpit_repo.repo)

    assert match.ref == "workspace:9"


# ── hard_blockers: dirty + unpushed cannot be --force'd through ────────────


def test_hard_blockers_clean_returns_empty(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=0),
    ):
        assert hard_blockers(wt) == []


def test_hard_blockers_flags_dirty(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=3),
        patch.object(teardown_mod, "_count_unpushed", return_value=0),
    ):
        blockers = hard_blockers(wt)
    assert any("3 uncommitted" in b for b in blockers)


def test_hard_blockers_flags_unpushed(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=2),
    ):
        blockers = hard_blockers(wt)
    assert any("2 unpushed commit" in b for b in blockers)


def test_hard_blockers_flags_unverifiable_push_state(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with (
        patch.object(teardown_mod, "count_dirty", return_value=0),
        patch.object(teardown_mod, "_count_unpushed", return_value=-1),
    ):
        blockers = hard_blockers(wt)
    assert any("could not verify" in b for b in blockers)


def test_hard_blockers_skips_missing_path():
    assert hard_blockers(Path("/nope/missing")) == []


def test_hard_blockers_skips_none():
    assert hard_blockers(None) == []
