"""Tests for lib.git branch-existence helpers.

Pins down the suffix-match bug in _fetch_remote_branch: a bare branch
name like `cship` used to match `refs/heads/*/cship` on `ls-remote --heads`
and trigger a hard-failing `fetch origin cship:cship`. The fix queries
`refs/heads/{branch}` exactly.
"""

from __future__ import annotations

import subprocess

from lib.git import (
    _fetch_remote_branch,
    _has_local_branch,
    _has_remote_branch,
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
