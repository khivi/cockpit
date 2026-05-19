"""Integration tests for spawn.resolve_worktree.

Covers the `--name <slug>` (from_name=True) path and regression for the
non-from_name path. Uses the `cockpit_repo` fixture from conftest.py:
real tmp git repo with origin remote, plus a fake COCKPIT_HOME config.
"""

from __future__ import annotations

import subprocess

import pytest


def test_from_name_creates_prefixed_branch_when_free(cockpit_repo):
    from spawn import resolve_worktree

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship"
    assert attached is False
    assert wt.exists()
    assert wt == cockpit_repo.repo.parent / "cship"


def test_from_name_bumps_branch_when_remote_collides(cockpit_repo, push_branch):
    from spawn import resolve_worktree

    push_branch("khivi/cship")

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship-2"
    assert attached is False
    assert wt.exists()


def test_from_name_bumps_branch_when_local_collides(cockpit_repo):
    from spawn import resolve_worktree

    subprocess.run(
        ["git", "-C", str(cockpit_repo.repo), "branch", "khivi/cship", "main"],
        check=True,
    )

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship-2"


def test_from_name_does_not_match_suffix_ref(cockpit_repo, push_branch):
    """Regression: with OLD code, ls-remote --heads origin cship would
    suffix-match a remote like `khivi/foo/cship` and trigger a failing
    `fetch origin cship:cship`. The from_name path must skip the fetch
    dance entirely and create khivi/cship fresh."""
    from spawn import resolve_worktree

    push_branch("khivi/foo/cship")

    wt, branch, attached = resolve_worktree("cship", None, "testrepo", from_name=True)
    assert branch == "khivi/cship"
    assert attached is False


def test_from_name_creates_branch_from_origin_main(cockpit_repo):
    """New branch's tip must be origin/main, not some stale local ref."""
    from spawn import resolve_worktree

    wt, _branch, _ = resolve_worktree("cship", None, "testrepo", from_name=True)

    head = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    main_tip = subprocess.run(
        ["git", "-C", str(cockpit_repo.repo), "rev-parse", "origin/main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == main_tip


def test_unknown_repo_name_raises(cockpit_repo):
    from spawn import resolve_worktree

    with pytest.raises(ValueError, match="no configured repo"):
        resolve_worktree("cship", None, "nonexistent", from_name=True)


def test_non_from_name_attaches_to_existing_remote_branch(cockpit_repo, push_branch):
    """Regression on the original code path: passing an existing branch
    explicitly (no from_name) should still attach to it, not bump."""
    from spawn import resolve_worktree

    push_branch("khivi/existing")

    wt, branch, attached = resolve_worktree(
        "khivi/existing", None, "testrepo", from_name=False
    )
    assert branch == "khivi/existing"
    assert wt.exists()
