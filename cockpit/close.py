"""`/cockpit:close` — tear down a worktree + workspace.

Workflow:
  1. Resolve target (from query arg, or from `cwd` when no arg).
  2. Hard refuse on dirty, or on commits that exist only locally (these
     protect unsaved/unpushed work and are never `--force`-overridable). For
     our own branches, "unpushed" also means "not yet merged to the default
     branch"; for someone else's PR worktree (checked out for review) it means
     only "not on that PR's remote branch", so a teammate's pushed-but-unmerged
     PR does not hard-block. Once the cached PR payload reports state MERGED the
     unpushed half is skipped (`git cherry` over-counts squash-merges and
     non-default-base merges) — but dirty still hard-blocks, since uncommitted
     edits exist only locally regardless of the merge.
  3. Refuse on open-PR unless `--force` is given. Combined with (2), `--force`
     can tear down a teammate's open-PR worktree once their commits are pushed.
  4. Require a running daemon: write a close-request marker under
     `$COCKPIT_HOME/state/close-requests/` and SIGUSR1-kick it — the daemon
     drains and runs `teardown` outside this shell, so we don't yank the
     cwd out from under our own session. If no daemon is running, error out
     and tell the operator to start one (so we don't dual-run with a real
     daemon, and so transient teardown failures stay durable).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from cockpit.lib.cache import find_pr_payload
from cockpit.lib.cmux import (
    require_workspace_binary,
    resolve_workspace,
    workspace_cwds,
    workspace_names,
)
from cockpit.lib.config import discover_repo
from cockpit.lib.daemon_signal import enqueue, kick_running
from cockpit.lib.git import worktrees
from cockpit.lib.tool import resolve_tool
from cockpit.orchestrators.teardown import (
    TeardownRequest,
    probe_blockers,
    worktree_state_blockers,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Close a cockpit worktree + workspace.")
    p.add_argument(
        "query",
        nargs="?",
        help="PR (#N or N), branch, or workspace slug; defaults to the worktree at cwd",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "override open-PR refusal (and lets you close a teammate's pushed "
            "PR worktree); does not override dirty or local-only commits"
        ),
    )
    return p.parse_args()


def _git_toplevel(cwd: Path) -> Path | None:
    res = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    out = res.stdout.strip()
    return Path(out).resolve() if out else None


def _match_from_cwd(repo_dir: Path):
    """Resolve the workspace + worktree at the user's current directory.

    Used when `cockpit:close` is invoked with no query: pick the worktree
    rooted at `git rev-parse --show-toplevel`, then find the workspace
    whose cwd resolves there. Refuses on ambiguity.
    """
    cwd = Path.cwd().resolve()
    toplevel = _git_toplevel(cwd)
    if toplevel is None:
        raise LookupError(f"not inside a git worktree (cwd={cwd})")

    wt = next((w for w in worktrees(repo_dir) if w.path.resolve() == toplevel), None)
    if wt is None:
        raise LookupError(f"no worktree at {toplevel}")

    cwds = workspace_cwds()
    names = workspace_names()
    refs = [ref for ref, path in cwds.items() if path.resolve() == toplevel]
    if not refs:
        tool = resolve_tool()
        raise LookupError(f"no {tool} workspace rooted at {toplevel}")
    if len(refs) > 1:
        raise LookupError(
            f"multiple workspaces rooted at {toplevel}: {sorted(refs)} — "
            "pass an explicit query"
        )
    ref = refs[0]

    from cockpit.lib.cmux import WorkspaceMatch

    return WorkspaceMatch(ref=ref, name=names.get(ref, ""), worktree=wt)


def main() -> int:
    require_workspace_binary()
    args = parse_args()
    repo_cfg = discover_repo()
    repo_dir = Path(repo_cfg["path"]).expanduser() if repo_cfg else Path.cwd()
    repo_name = repo_cfg.get("name") if repo_cfg else None

    try:
        if args.query is None:
            match = _match_from_cwd(repo_dir)
        else:
            match = resolve_workspace(args.query, repo_dir)
    except LookupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    wt = match.worktree
    label = match.name or match.ref
    branch = wt.branch if wt is not None else None
    wt_path = wt.path if wt is not None else None

    prefix = (repo_cfg or {}).get("branch_prefix", "")
    is_mine = branch.startswith(prefix) if (prefix and branch is not None) else True

    # The cached PR payload drives both the merged-aware hard-blocker gate below
    # and the post-merge branch deletion further down — read it once.
    payload = (
        find_pr_payload(branch, repo_name=repo_name)
        if branch is not None and repo_name is not None
        else None
    )
    pr_is_merged = (
        payload is not None and str(payload.get("state", "")).upper() == "MERGED"
    )

    # A merged PR's commits are safe on the remote, so `_count_unpushed`'s
    # over-count (squash-merge or non-default base) must not hard-block. Dirty
    # uncommitted files still block — that work exists only locally.
    hard = worktree_state_blockers(
        wt_path, branch=branch, is_mine=is_mine, pr_merged=pr_is_merged
    )
    if hard:
        print(
            f"ERROR: refusing to close {label}: "
            + "; ".join(hard)
            + " (commit, push, or merge before closing — --force does not override)",
            file=sys.stderr,
        )
        return 1

    blockers = probe_blockers(wt_path, branch, repo_name, is_mine=is_mine)
    if blockers and not args.force:
        print(
            f"ERROR: refusing to close {label}: "
            + "; ".join(blockers)
            + " (re-run with --force to override)",
            file=sys.stderr,
        )
        return 1

    # Delete the local branch when the PR has merged. The hard-blocker check
    # above already refused on dirty files (not even --force overrides those),
    # so by here a merged branch carries no work the ref is protecting. A --force
    # close of a still-OPEN PR leaves the branch alone — that work is not merged.
    delete_branch = pr_is_merged

    req = TeardownRequest(
        ref=match.ref,
        name=match.name or "",
        worktree_path=wt_path,
        branch=branch,
        repo_path=repo_dir if wt is not None else None,
        repo_name=repo_name,
        forced=args.force,
        delete_branch=delete_branch,
    )
    if not kick_running(quiet=True):
        print(
            "ERROR: cockpit daemon not running; "
            "start with `cockpit watch` and retry",
            file=sys.stderr,
        )
        return 1
    enqueue(req)
    print(f"queued close: {label} (daemon will process)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
