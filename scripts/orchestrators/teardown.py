"""Centralized workspace + worktree teardown.

One callable replaces three duplicate sequences (`scripts/close.py` inline,
`cockpit._maybe_autoclose`, `close_gone_cwd_workspaces`). Order, every time:

  1. Re-check blockers (dirty / unpushed / open PR) unless `forced`.
  2. Close the cmux workspace.
  3. Remove the worktree if `worktree_path` exists on disk.
  4. Delete the PR cache rows for `branch` if `repo_name` is known.

Step ordering matters: yanking the cwd out from under a live Claude session
breaks every Stop/PreToolUse hook with ENOENT. Workspace close must precede
worktree remove.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts.lib.cache import delete_pr_caches_for_branch, find_pr_payload
from scripts.lib.cmux import cmux_close_workspace_best_effort
from scripts.lib.colors import dim
from scripts.lib.git import (
    _count_unpushed,
    commits_only_local,
    count_dirty,
    ff_default_branch_worktrees,
    log_ff_advances,
    remove_worktree,
    worktrees,
)
from scripts.lib.log_format import verb
from scripts.lib.teardown_types import TeardownRequest

__all__ = [
    "TeardownRequest",
    "probe_blockers",
    "teardown",
    "worktree_state_blockers",
]


def worktree_state_blockers(
    worktree_path: Path | None,
    *,
    branch: str | None = None,
    is_mine: bool = True,
) -> list[str]:
    """Dirty + unpushed checks.

    `dirty` is always a hard blocker — `--force` never overrides it. The
    `unpushed` baseline depends on ownership: for our own branches (the default,
    `is_mine=True`) we keep the conservative default-branch baseline
    (`_count_unpushed`), so a branch whose commits haven't merged still blocks.
    For someone else's branch (a PR checked out for review, `is_mine=False`) we
    baseline against the branch's own remote (`commits_only_local`): a
    teammate's pushed-but-unmerged PR is therefore not flagged, leaving only the
    soft open-PR blocker that `--force` can override. Commits that exist only
    locally still block, regardless of ownership.
    """
    blockers: list[str] = []
    if worktree_path is None or not worktree_path.is_dir():
        return blockers
    dirty = count_dirty(worktree_path)
    if dirty > 0:
        blockers.append(f"{dirty} uncommitted file(s)")
    if not is_mine and branch:
        unpushed = commits_only_local(worktree_path, branch)
    else:
        unpushed = _count_unpushed(worktree_path)
    if unpushed > 0:
        blockers.append(f"{unpushed} unpushed commit(s)")
    elif unpushed == -1:
        blockers.append("could not verify push state")
    return blockers


def probe_blockers(
    worktree_path: Path | None,
    branch: str | None,
    repo_name: str | None,
    *,
    is_mine: bool = True,
) -> list[str]:
    """Read-only check: reasons to refuse close. Empty list = safe to close.

    Combines `worktree_state_blockers` (dirty is hard; unpushed is hard only for
    our own branches — see that function) and the open-PR check (soft — `--force`
    overrides).
    """
    blockers = worktree_state_blockers(worktree_path, branch=branch, is_mine=is_mine)
    if branch is not None and repo_name is not None:
        payload = find_pr_payload(branch, repo_name=repo_name)
        if payload and str(payload.get("state", "")).upper() == "OPEN":
            blockers.append(f"PR #{payload['number']} is OPEN")
    return blockers


def teardown(req: TeardownRequest, *, dry: bool = False) -> tuple[bool, list[str]]:
    """Close workspace, remove worktree, delete cache.

    Returns `(ok, blockers)`. On `ok=False`, `blockers` is non-empty and
    nothing was changed. Callers should log the refusal and decide whether
    to drop the request or surface it for user attention.
    """
    label = req.name or req.ref
    if not req.forced:
        blockers = probe_blockers(req.worktree_path, req.branch, req.repo_name)
        if blockers:
            return False, blockers

    action = "[dry] teardown" if dry else "teardown"
    detail = f"branch={req.branch}" if req.branch else f"cwd={req.worktree_path}"
    print(f"  {verb(action)} {label} ({req.ref})  {dim(detail)}", flush=True)
    if dry:
        return True, []

    cmux_close_workspace_best_effort(req.ref)

    if (
        req.worktree_path is not None
        and req.worktree_path.exists()
        and req.repo_path is not None
    ):
        ok, err = remove_worktree(req.repo_path, req.worktree_path, force=req.forced)
        if not ok:
            print(
                f"  warn: git worktree remove failed for {req.worktree_path}: {err}",
                file=sys.stderr,
                flush=True,
            )
            return False, [f"git worktree remove failed: {err}"]

    if req.branch is not None and req.repo_name is not None:
        delete_pr_caches_for_branch(req.repo_name, req.branch)

    if req.repo_path is not None:
        log_ff_advances(
            ff_default_branch_worktrees(req.repo_path, worktrees(req.repo_path))
        )

    return True, []
