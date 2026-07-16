"""`cockpit close` — queue a worktree + workspace teardown for the daemon.

The CLI sibling of the TUI's `c`/`C` row actions (`cockpit/tui/app.py`
`_close_worktree`). Both resolve the same `(state, blockers)` and write the
same durable `TeardownRequest` marker the daemon drains through
`orchestrators.teardown` — the single teardown code path shared with
autoclose-on-merge and orphan reaping. This entry point exists so a Claude
session parked *inside* a worktree can close it without reaching for the TUI:
`cockpit close` with no query targets the cwd's worktree.

Gating mirrors the TUI exactly:

  - Hard blockers (`worktree_state_blockers`: dirty / unpushed) refuse even
    under `--force` — close never discards local work.
  - The open-PR blocker is *soft*: `--force` overrides it (and lets you close a
    teammate's pushed-but-unmerged PR worktree). An out-of-band squash/rebase
    merge is recognized via `resolve_pr_state` (cache first, one live `gh`
    fallback), so a merged PR isn't false-flagged as unpushed.

Like the old `/cockpit:close` skill, this **requires a running daemon**: the
request is enqueued durably and the daemon is SIGUSR1-kicked. If no daemon is
up, the marker stays queued (drained on the next `cockpit watch` start, within
`STALE_SECONDS`) and the command reports that — it never runs teardown inline,
so it can't dual-run with a real daemon and a transient failure stays durable.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cockpit.lib.cmux import CmuxUnavailable, workspace_cwds, workspace_names
from cockpit.lib.config import load_config
from cockpit.lib.daemon_signal import enqueue, kick_running
from cockpit.lib.gh import repo_nwo
from cockpit.lib.git import (
    Worktree,
    current_branch,
    origin_head_branch,
    worktrees,
)
from cockpit.lib.teardown_types import TeardownRequest
from cockpit.orchestrators.teardown import resolve_pr_state, worktree_state_blockers


def _configured_repos() -> list[dict]:
    return [
        repo
        for repo in load_config().get("repos", [])
        if Path(os.path.expanduser(repo["path"])).is_dir()
    ]


def _query_matches(wt: Worktree, query: str) -> bool:
    """True if `query` names this worktree by branch, label, dir basename, or
    PR ref (`#123` / `123` matched against the branch label's leading token is
    too lossy, so PR-number matching is left to the branch/label/short axes)."""
    q = query.lstrip("#")
    return q in {wt.branch, wt.label, wt.short}


def _resolve_target(query: str | None) -> tuple[dict, Worktree] | None:
    """Map a query (or the cwd) to its `(repo config, Worktree)`.

    Inventory is derived, never stored: re-reads `git worktree list` per
    configured repo on every call (mirrors the TUI's `_resolve_worktree`). With
    no query, resolves the worktree whose path is the cwd's worktree root."""
    if query is None:
        target = Path.cwd().resolve()
    else:
        # An exact path also resolves — convenient for `cockpit close <dir>`.
        maybe_path = Path(os.path.expanduser(query)).resolve()
        target = maybe_path if maybe_path.is_dir() else None  # type: ignore[assignment]

    for repo in _configured_repos():
        rp = Path(os.path.expanduser(repo["path"]))
        prefix = repo.get("branch_prefix", "")
        try:
            wts = worktrees(rp, prefix)
        except (RuntimeError, OSError):
            continue
        for wt in wts:
            wt_path = wt.path.resolve()
            if query is None:
                # cwd is inside (or equal to) the worktree root.
                if target == wt_path or target.is_relative_to(wt_path):
                    return repo, wt
            elif (target is not None and target == wt_path) or _query_matches(
                wt, query
            ):
                return repo, wt
    return None


def _workspace_ref(wt: Worktree) -> str | None:
    """The backend workspace ref whose cwd is this worktree, or None.

    Best-effort: a backend hiccup (`CmuxUnavailable`) falls back to None so the
    caller uses the branch/short as the marker ref.

    `include_self=True`: `cockpit close` is typically run from *inside* the
    worktree it tears down, so the workspace to close IS the caller's own — the
    default self-exclusion would drop it and lose the ref."""
    try:
        cwds = workspace_cwds(include_self=True)
    except CmuxUnavailable:
        return None
    target = wt.path.resolve()
    return next((ref for ref, p in cwds.items() if p.resolve() == target), None)


def _workspace_name(ref: str | None) -> str:
    if ref is None:
        return ""
    try:
        return workspace_names().get(ref, "")
    except CmuxUnavailable:
        return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cockpit close",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "query",
        nargs="?",
        metavar="branch|slug|path",
        help="Worktree to close by branch, label, dir basename, or path "
        "(default: the worktree at the current directory).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Override the soft open-PR refusal (and close a teammate's pushed "
        "PR worktree). Never overrides uncommitted/unpushed work.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report blockers and the resolved target without enqueuing.",
    )
    args = p.parse_args(argv)

    resolved = _resolve_target(args.query)
    if resolved is None:
        where = args.query or str(Path.cwd())
        print(f"cockpit close: no configured worktree at {where!r}", file=sys.stderr)
        return 1
    repo, wt = resolved

    repo_dir = Path(os.path.expanduser(repo["path"]))
    # The git nwo name, not the config label — `resolve_pr_state`/teardown key
    # the PR cache by it (the daemon wrote `{nwo}__pr-N.json`); the label misses
    # every file, misresolving PR state and orphaning the cache on teardown.
    # Falls back to the basename when `gh` can't resolve (off-GitHub repo, which
    # has no PR cache anyway).
    try:
        repo_name = repo_nwo(repo_dir)[1]
    except RuntimeError:
        repo_name = repo_dir.name
    prefix = repo.get("branch_prefix", "")
    branch = wt.branch or current_branch(wt.path)
    is_mine = branch.startswith(prefix) if (prefix and branch) else True
    label = wt.label or wt.short

    # Resolve PR state once (cache first, one live `gh` fallback) so an
    # out-of-band squash/rebase merge isn't false-flagged as unpushed.
    state, pr_number = resolve_pr_state(wt.path, branch, repo_name)
    pr_is_merged = state == "MERGED"

    # A primary checkout (`use_worktree: false`) relaxes the unpushed guard only
    # while it stays on its default branch — a workspace-only close where nothing
    # is removed. Parked on a feature branch it's a branch teardown (checkout
    # default + `git branch -D`), so the unpushed guard must stand; pass
    # `is_primary=False` there. `default is None` (off-GitHub) can't delete, so it
    # stays workspace-only.
    default_branch = origin_head_branch(repo_dir)
    on_default = default_branch is None or branch == default_branch
    ws_only_close = wt.is_primary and on_default
    hard = worktree_state_blockers(
        wt.path,
        branch=branch,
        is_mine=is_mine,
        pr_merged=pr_is_merged,
        is_primary=ws_only_close,
    )
    if hard:
        print(
            f"close refused {label}: "
            + "; ".join(hard)
            + " — commit/push/merge first (--force does not override this)",
            file=sys.stderr,
        )
        return 1
    if not args.force and state == "OPEN" and pr_number is not None:
        print(
            f"close refused {label}: PR #{pr_number} is OPEN "
            "— pass --force to close anyway",
            file=sys.stderr,
        )
        return 1

    ref = _workspace_ref(wt)
    req = TeardownRequest(
        ref=ref or branch or wt.short,
        name=_workspace_name(ref),
        worktree_path=wt.path,
        branch=branch,
        repo_path=repo_dir,
        repo_name=repo_name,
        forced=args.force,
        # Delete on merge, or when tearing down a primary checkout's feature
        # branch (workspace-only closes on the default branch keep the branch).
        delete_branch=pr_is_merged or (wt.is_primary and not on_default),
    )

    if args.dry_run:
        print(f"close OK (dry-run) {label}: would queue teardown of {wt.path}")
        return 0

    enqueue(req)
    if kick_running(quiet=True):
        print(f"queued {'force-' if args.force else ''}close: {label} — daemon kicked")
        return 0
    print(
        f"queued {'force-' if args.force else ''}close: {label} — "
        "no daemon running; start `cockpit watch` to drain the request.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
