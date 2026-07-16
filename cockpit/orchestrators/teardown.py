"""Centralized workspace + worktree teardown.

One callable is the single teardown sequence, driven by the daemon for both
enqueued `/cockpit:close` requests (`cycle._drain_close_requests`) and
autoclose-on-merge (`cycle._maybe_autoclose`). Order, every time:

  1. Re-check blockers (dirty / unpushed / open PR) unless `forced`.
  2. Close the cmux workspace.
  3. Remove the worktree if `worktree_path` exists on disk.
  4. Delete the local branch ref if `delete_branch` is set (and the branch is
     not the repo's default branch).
  5. Delete the PR cache rows for `branch` if `repo_name` is known.

Step ordering matters: yanking the cwd out from under a live Claude session
breaks every Stop/PreToolUse hook with ENOENT. Workspace close must precede
worktree remove.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cockpit.lib.cache import delete_pr_caches_for_branch, find_pr_payload
from cockpit.lib.cmux import cmux_close_workspace_best_effort
from cockpit.lib.colors import dim
from cockpit.lib.gh import fetch_pr_state_for_branch
from cockpit.lib.git import (
    _count_unpushed,
    checkout_branch,
    commits_only_local,
    count_dirty,
    delete_local_branch,
    ff_default_branch_worktrees,
    log_ff_advances,
    origin_head_branch,
    remove_worktree,
    worktrees,
)
from cockpit.lib.log_format import verb
from cockpit.lib.teardown_types import TeardownRequest

__all__ = [
    "TeardownRequest",
    "probe_blockers",
    "resolve_pr_state",
    "teardown",
    "worktree_state_blockers",
]


def worktree_state_blockers(
    worktree_path: Path | None,
    *,
    branch: str | None = None,
    is_mine: bool = True,
    pr_merged: bool = False,
    is_primary: bool = False,
) -> list[str]:
    """Dirty + unpushed checks.

    `dirty` is always a hard blocker — `--force` never overrides it, and
    `pr_merged` does not relax it either (uncommitted edits exist only locally
    regardless of whether the PR merged). The `unpushed` baseline depends on
    ownership: for our own branches (the default, `is_mine=True`) we keep the
    conservative default-branch baseline (`_count_unpushed`), so a branch whose
    commits haven't merged still blocks. For someone else's branch (a PR checked
    out for review, `is_mine=False`) we baseline against the branch's own remote
    (`commits_only_local`): a teammate's pushed-but-unmerged PR is therefore not
    flagged, leaving only the soft open-PR blocker that `--force` can override.
    Commits that exist only locally still block, regardless of ownership.

    `pr_merged=True` (the PR is MERGED) skips the unpushed check entirely:
    `_count_unpushed` over-counts both squash-merges (N commits collapse to one
    upstream patch-id matching none of the originals) and non-default-base
    merges (it baselines on `origin/<default>`, but the PR landed on e.g.
    `origin/stage`). A merged PR's work is safe on the remote, so only the dirty
    check needs to stand. Callers establish the merge via `resolve_pr_state`
    (cache first, then one live `gh` lookup — see `probe_blockers`), mirroring
    how autoclose uses `is_ancestor(wt, headRefOid)` instead of the commit count.

    `is_primary=True` means a **workspace-only close** — a primary checkout
    (`use_worktree: false`) staying on its default branch: nothing is removed
    (`teardown` refuses `git worktree remove` on a primary checkout, and the
    branch survives), so the checkout and any unpushed commits stay put and only
    the dirty guard stands. Callers pass `is_primary` as
    `wt.is_primary and on_default` — a primary checkout parked on a *non-default*
    branch is torn down (that branch is deleted), so its unpushed commits are NOT
    safe and the guard must still stand; those callers pass `is_primary=False`.
    """
    blockers: list[str] = []
    if worktree_path is None or not worktree_path.is_dir():
        return blockers
    dirty = count_dirty(worktree_path)
    if dirty > 0:
        blockers.append(f"{dirty} uncommitted file(s)")
    if pr_merged:
        return blockers
    if is_primary:
        # A primary checkout's close is workspace-only — the worktree stays, so
        # unpushed commits are never at risk. Only the dirty guard (above)
        # stands, honouring "close, but make sure it's all committed".
        return blockers
    if not is_mine and branch:
        unpushed = commits_only_local(worktree_path, branch)
    else:
        unpushed = _count_unpushed(worktree_path)
    if unpushed > 0:
        blockers.append(f"{unpushed} unpushed commit(s)")
    elif unpushed == -1:
        blockers.append("could not verify push state")
    return blockers


def resolve_pr_state(
    worktree_path: Path | None,
    branch: str | None,
    repo_name: str | None,
) -> tuple[str, int | None]:
    """Newest PR's `(state, number)` for `branch` — cache first, one live fallback.

    Reads the slow tick's cached PR payload (`find_pr_payload`); when that does
    not already report MERGED and a worktree dir is on hand to anchor `gh`, does
    ONE live `fetch_pr_state_for_branch` lookup and lets the live result win.

    The live fallback is what makes manual close squash/rebase-merge aware: a PR
    merged out-of-band (e.g. via `gh`, never discovered by the slow tick) has no
    cached MERGED payload, so the unpushed gate would otherwise fall through to
    `_count_unpushed` — which a squash defeats — producing a false-positive HARD
    block that `--force` cannot override. Live `gh pr list --state all` reports
    `state == "MERGED"` regardless of merge strategy. `("", None)` when no PR is
    known from either source (the caller then trusts the git-based unpushed
    count). The lookup is skipped when the worktree is gone — there is nothing
    left to close, and `gh` needs a working tree as its cwd.
    """
    payload = (
        find_pr_payload(branch, repo_name=repo_name)
        if branch is not None and repo_name is not None
        else None
    )
    state = str(payload.get("state", "")).upper() if payload else ""
    number = payload.get("number") if payload else None
    if (
        state != "MERGED"
        and branch
        and worktree_path is not None
        and worktree_path.is_dir()
    ):
        live = fetch_pr_state_for_branch(branch, worktree_path)
        if live:
            live_state = str(live.get("state", "")).upper()
            if live_state:
                state = live_state
                number = live.get("number", number)
    return state, number


def probe_blockers(
    worktree_path: Path | None,
    branch: str | None,
    repo_name: str | None,
    *,
    is_mine: bool = True,
    is_primary: bool = False,
) -> list[str]:
    """Read-only check: reasons to refuse close. Empty list = safe to close.

    Combines `worktree_state_blockers` (dirty is hard; unpushed is hard only for
    our own branches, and is skipped once the PR is MERGED — see that function)
    and the open-PR check (soft — `--force` overrides). The MERGED/OPEN state is
    resolved via `resolve_pr_state` (cache first, one live `gh` fallback), so an
    out-of-band squash/rebase merge is recognized here too — not only the
    in-cache MERGED case.
    """
    state, number = resolve_pr_state(worktree_path, branch, repo_name)
    blockers = worktree_state_blockers(
        worktree_path,
        branch=branch,
        is_mine=is_mine,
        pr_merged=state == "MERGED",
        is_primary=is_primary,
    )
    if state == "OPEN" and number is not None:
        blockers.append(f"PR #{number} is OPEN")
    return blockers


def teardown(req: TeardownRequest, *, dry: bool = False) -> tuple[bool, list[str]]:
    """Close workspace, remove worktree, delete cache.

    Returns `(ok, blockers)`. On `ok=False`, `blockers` is non-empty and
    nothing was changed. Callers should log the refusal and decide whether
    to drop the request or surface it for user attention.

    Exception: a **primary checkout** (`worktree_path == repo_path`, a
    `use_worktree: false` repo) never has its worktree removed (git refuses
    `git worktree remove` on a primary checkout, and the user works there in
    place). Two sub-cases:

      * On its **default branch** — a *workspace-only* close: close the session,
        leave the checkout. Only the dirty guard applies (unpushed is relaxed —
        nothing is removed, the branch survives).
      * On a **non-default (feature) branch** — the branch is torn down: after
        the workspace close, HEAD is moved back to the default branch
        (`checkout_branch`) and the feature ref is deleted (`git branch -D`).
        The unpushed guard is NOT relaxed here (the branch is going away, so its
        commits are not safe) — callers pass `is_primary=False` to the blockers.
    """
    label = req.name or req.ref
    # A primary checkout (worktree path == repo root) can't be removed as a
    # worktree, so `remove_worktree` below is skipped for it.
    is_primary = (
        req.worktree_path is not None
        and req.repo_path is not None
        and req.worktree_path.resolve() == req.repo_path.resolve()
    )
    # Resolve the default branch once (reused by the blocker relaxation and the
    # branch-delete guard). A primary checkout counts as a workspace-only close —
    # unpushed relaxed — only while it stays on that default branch; parked on a
    # feature branch it's a branch teardown, so the unpushed guard must stand.
    default = origin_head_branch(req.repo_path) if req.repo_path is not None else None
    workspace_only = is_primary and (default is None or req.branch == default)
    if not req.forced:
        blockers = probe_blockers(
            req.worktree_path, req.branch, req.repo_name, is_primary=workspace_only
        )
        if blockers:
            return False, blockers

    action = "[dry] teardown" if dry else "teardown"
    detail = f"branch={req.branch}" if req.branch else f"cwd={req.worktree_path}"
    print(f"  {verb(action)} {label} ({req.ref})  {dim(detail)}", flush=True)
    if dry:
        return True, []

    cmux_close_workspace_best_effort(req.ref)

    if (
        not is_primary
        and req.worktree_path is not None
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

    if (
        req.delete_branch
        and req.branch is not None
        and req.repo_path is not None
        and default is not None
        and req.branch != default
    ):
        # A primary checkout still has the feature branch as HEAD (its worktree
        # wasn't removed), and git refuses `branch -D` of the checked-out branch
        # — move HEAD to the default branch first. Soft-fail: a failed checkout
        # leaves the branch in place for a retry rather than aborting the
        # (already-run) workspace close.
        checked_out = True
        if is_primary:
            ok_c, err_c = checkout_branch(req.repo_path, default)
            checked_out = ok_c
            if not ok_c:
                print(
                    f"  warn: git checkout {default} failed, "
                    f"leaving {req.branch}: {err_c}",
                    file=sys.stderr,
                    flush=True,
                )
        if checked_out:
            ok_b, err_b = delete_local_branch(req.repo_path, req.branch)
            if not ok_b:
                # Non-fatal: a dangling local ref is cosmetic, and the worktree
                # is already gone. Match the soft-fail posture of the post-close
                # ff chore below rather than failing the whole teardown.
                print(
                    f"  warn: git branch -D {req.branch} failed: {err_b}",
                    file=sys.stderr,
                    flush=True,
                )

    if req.branch is not None and req.repo_name is not None:
        delete_pr_caches_for_branch(req.repo_name, req.branch)

    if req.repo_path is not None:
        log_ff_advances(
            ff_default_branch_worktrees(req.repo_path, worktrees(req.repo_path))
        )

    return True, []
