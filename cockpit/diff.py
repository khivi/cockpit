"""`cockpit diff` — open this worktree's diff in cmux's viewer, from the shell.

The CLI sibling of the TUI's `d` row action, and it exists for the same reason
`cockpit close` does: a Claude session parked *inside* a worktree should be able
to read its own diff without reaching for the dashboard, finding its row, and
pressing a key — for a gesture that is entirely about the worktree you are
already standing in.

Two things make this more than an alias for `cmux diff`:

  - **The PR diff.** cmux has no PR source; `gh pr diff` piped to `cmux diff -`
    is what the TUI's `d` adds, and there was no CLI route to it. That is the
    default here, since the PR is what you review.
  - **`--comments`.** The notes you leave in the viewer are collected by
    `lib.diff_comments`, whose only consumer was the TUI's `a` key — cmux folds
    them into the next message its own composer submits, and a cockpit workspace
    is a terminal running Claude's TUI, which has no composer. In-workspace you
    *are* the session, so printing them here closes the loop with no dashboard
    round-trip. The delivered-ledger is shared with `a`, so whichever surface
    reads them, they are not delivered twice.

Everything else (`--branch`, `--staged`, `--unstaged`, `--last-turn`) is handed
straight to cmux, which already resolves merge bases and owns the `last-turn`
agent-turn baseline that cockpit cannot reconstruct.

Resolution is all cwd and needs **no cockpit config** — a repo does not have to
be registered to diff it. `git.worktree_root` finds the enclosing worktree,
`cache.find_pr_payload_for_cwd` supplies the PR number for the title from disk
(no network; a miss just means a less specific title).

Writes NOTHING durable: no cache cell, no pill, no `pill_state`. Like
`broadcast`, a one-shot gesture — the read-only rule `d` already follows.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from cockpit.lib import diff_comments
from cockpit.lib.cache import find_pr_payload_for_cwd
from cockpit.lib.cmux import render_diff
from cockpit.lib.git import (
    branch_label,
    current_branch,
    main_worktree_path,
    worktree_root,
)

# cmux's own git sources, exposed one flag each so `cockpit diff --branch` reads
# the way `cmux diff --branch` does. `--source X` is what actually goes over.
_SOURCES = ("branch", "staged", "unstaged", "last-turn")


def _pr_patch(root: Path) -> tuple[str, str]:
    """`gh pr diff` for the PR on this worktree's branch → `(patch, error)`.

    Plain `gh pr diff`, NOT `--color always`: the viewer does its own syntax
    highlighting and ANSI would only get in its way. Bare, with no number —
    `gh` resolves the branch's PR itself, which is authoritative where the
    cockpit cache may be stale or absent (no daemon running).

    `errors="replace"` because a diff can carry a non-UTF-8 byte and decoding
    strictly would raise where the user just wanted to read the patch.
    """
    try:
        res = subprocess.run(
            ["gh", "pr", "diff"],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return "", f"gh failed: {e}"
    if res.returncode != 0:
        return "", res.stderr.strip() or "gh pr diff failed"
    return res.stdout, ""


def _print_comments(root: Path) -> int:
    """Print this worktree's pending diff-viewer notes and mark them delivered.

    Offers both the worktree root and the checkout it was cut from as candidate
    keys, exactly as the TUI's `a` does: which of the two cmux files a worktree
    under is undocumented, both are cheap to offer, and the cost of guessing
    wrong is a lookup that silently returns nothing.
    """
    pend = diff_comments.pending([root, main_worktree_path(root)])
    if not pend:
        print("cockpit diff: no pending diff comments for this worktree")
        return 0
    for c in pend:
        print(f"{c.file}:{c.line} — {c.message}")
    diff_comments.mark_delivered([c.id for c in pend])
    print(f"\n{len(pend)} comment(s) marked delivered.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cockpit diff",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sources = p.add_mutually_exclusive_group()
    for name in _SOURCES:
        sources.add_argument(
            f"--{name}",
            action="store_const",
            const=name,
            dest="source",
            help=f"Show cmux's `{name}` diff instead of the PR.",
        )
    sources.add_argument(
        "--comments",
        action="store_true",
        help="Print the notes left in the diff viewer for this worktree and "
        "mark them delivered, instead of opening a diff.",
    )
    p.add_argument(
        "--base",
        metavar="REF",
        help="Base ref for --branch (default: cmux's origin/HEAD or main).",
    )
    args = p.parse_args(argv)

    root = worktree_root()
    if root is None:
        print("cockpit diff: not in a git repo", file=sys.stderr)
        return 2

    if args.comments:
        return _print_comments(root)

    branch = current_branch(root)
    label = branch_label(branch) or root.name
    source, patch = args.source, None

    if source is None:
        patch, err = _pr_patch(root)
        if err:
            # No PR is the ordinary case on a fresh branch, not a failure worth
            # exiting on: fall back to the branch diff so the command always
            # shows you something, and say which one you got.
            print(f"cockpit diff: no PR diff ({err}) — showing --branch instead")
            source, patch = "branch", None
        else:
            num = (find_pr_payload_for_cwd(root, branch) or {}).get("number")
            label = f"PR #{num} — {label}" if num else f"PR — {label}"

    if source is not None:
        label = f"{source} — {label}"

    # Nothing here names a workspace or a surface: running inside the one we
    # target is exactly what makes cmux's own `$CMUX_WORKSPACE_ID` /
    # `$CMUX_SURFACE_ID` defaults correct, and it is the property that made the
    # dashboard's `d` key unworkable (see `render_diff`).
    err = render_diff(patch, source=source, base=args.base, cwd=root, title=label)
    if err:
        print(f"cockpit diff: {err}", file=sys.stderr)
        return 1
    print(f"cockpit diff: opened {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
