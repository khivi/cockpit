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
from dataclasses import dataclass
from pathlib import Path

from scripts.lib.cache import delete_pr_caches_for_branch, find_pr_payload
from scripts.lib.cmux import cmux_close_workspace_best_effort
from scripts.lib.colors import dim
from scripts.lib.git import (
    _count_unpushed,
    count_dirty,
    ff_default_branch_worktrees,
    log_ff_advances,
    remove_worktree,
    worktrees,
)
from scripts.lib.log_format import verb


@dataclass(frozen=True)
class TeardownRequest:
    """Inputs for a single workspace teardown.

    `worktree_path` / `branch` / `repo_path` / `repo_name` are all optional
    because the `close_gone_cwd_workspaces` path has only `ref` to work with.
    """

    ref: str
    name: str = ""
    worktree_path: Path | None = None
    branch: str | None = None
    repo_path: Path | None = None
    repo_name: str | None = None
    forced: bool = False


def worktree_state_blockers(worktree_path: Path | None) -> list[str]:
    """Dirty + unpushed checks. Hard blockers — `--force` never overrides these."""
    blockers: list[str] = []
    if worktree_path is None or not worktree_path.is_dir():
        return blockers
    dirty = count_dirty(worktree_path)
    if dirty > 0:
        blockers.append(f"{dirty} uncommitted file(s)")
    unpushed = _count_unpushed(worktree_path)
    if unpushed > 0:
        blockers.append(f"{unpushed} unpushed commit(s)")
    elif unpushed == -1:
        blockers.append("could not verify push state")
    return blockers


def probe_blockers(
    worktree_path: Path | None, branch: str | None, repo_name: str | None
) -> list[str]:
    """Read-only check: reasons to refuse close. Empty list = safe to close.

    Combines `worktree_state_blockers` (hard — not `--force`-overridable) and
    the open-PR check (soft — `--force` overrides).
    """
    blockers = worktree_state_blockers(worktree_path)
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
