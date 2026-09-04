"""`cockpit broadcast` — send a line of text to every idle Claude session.

A one-shot admin gesture (typically a slash command like `/compact`) fanned
out to every workspace cmux/limux knows about, so one command reaches every
open session instead of clicking through each one by hand.

`--repo NAME` narrows that fan-out to one configured repo, and `--worktree
PATH` to the sessions rooted at one worktree. Both are *filters over the same
loop*, not a second delivery path: every ref that survives one still goes
through `nudge_if_idle` unchanged.

`--worktree` is the narrowest scope there is, and it exists so reaching one
session is a *target* rather than a coincidence — without it the only way down
to a single workspace was a `--repo` that happened to have exactly one open,
which is a blast radius that silently widens the moment a second worktree
opens. It matches the cwd exactly, never by prefix: a subdirectory of a
worktree is a different session's business, and a `use_worktree: false` repo
deliberately hosts several sessions at one cwd, all of which are in scope.

Reuses the two existing primitives in `cockpit.lib.cmux` rather than building
a parallel send path:

  - `workspace_cwds()` enumerates every OTHER workspace (it excludes the
    caller's own by default — never broadcast into the daemon/TUI's own
    session).
  - `nudge_if_idle(ref, message, dry=..., tag="broadcast", skips=...)` is the
    entire safety story: it refuses a mid-turn (`Running`) session, refuses the
    ambiguous `Needs input` state (a pending y/n permission — typing into it
    would answer the prompt, not deliver the message), and skips a
    `parked=` session. Called with no `pref_key` it bypasses PR mute/snooze,
    which is correct here — mute is a PR-nudge concept, not an admin-command
    one. Its `skips` out-dict carries the per-ref reason, so this module can
    report an accurate breakdown without a second `list-status` or a second
    copy of the gate.

This module writes NOTHING durable: no cache cell, no pill, no `pill_state`,
no config. It is outside the "only the daemon writes the cache" invariant —
there is no queue and no retry of skipped sessions; a skipped ref is printed
and forgotten, and re-running the command is the retry.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cockpit.lib.cmux import (
    CmuxUnavailable,
    nudge_if_idle,
    refs_at,
    skip_summary,
    workspace_cwds,
)
from cockpit.lib.config import load_config
from cockpit.lib.git import repo_worktree_paths, worktrees


def _repo_label(repo: dict) -> str:
    """The repo's one identity — the same `name`-or-basename the table shows.

    Deliberately ONE axis, not "name or basename" as alternatives: under a bare
    clone every repo's path ends in `.bare`, so accepting the basename as a
    second spelling makes `--repo .bare` match whichever bare repo happens to
    come first in the config and silently broadcast into the wrong one.
    """
    return repo.get("name") or Path(os.path.expanduser(repo["path"])).name


def _repo_paths(name: str) -> set[Path]:
    """Every path a session in the configured repo `name` can be rooted at.

    The cwd-not-prefix matching rule is `git.repo_worktree_paths`'; this only
    resolves the *name* to a repo entry.

    Raises `LookupError`, naming the configured repos, when nothing matches.
    """
    repos = load_config().get("repos", [])
    for repo in repos:
        # Casefolded: a config `name` is a display string ("Cockpit"), and
        # failing a broadcast over its capital letter is a papercut with no
        # upside.
        if _repo_label(repo).casefold() == name.casefold():
            path = Path(os.path.expanduser(repo["path"]))
            return repo_worktree_paths(
                path, worktrees(path, repo.get("branch_prefix", ""))
            )
    known = ", ".join(sorted(_repo_label(r) for r in repos))
    raise LookupError(f"unknown repo {name!r}; configured: {known or '(none)'}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cockpit broadcast",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "message",
        help="Text to send + enter to every idle workspace (e.g. '/compact').",
    )
    p.add_argument(
        "--dry",
        action="store_true",
        help="Report which workspaces would receive the message without sending.",
    )
    scope_group = p.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--repo",
        metavar="NAME",
        help="Only send to workspaces in this configured repo, named as the "
        "dashboard names it (case-insensitive). Default: every idle workspace.",
    )
    scope_group.add_argument(
        "--worktree",
        metavar="PATH",
        help="Only send to the session(s) rooted at this worktree, matched by "
        "exact directory. The narrowest scope there is.",
    )
    args = p.parse_args(argv)

    try:
        cwds = workspace_cwds()
    except CmuxUnavailable as e:
        print(f"cockpit broadcast: workspace backend unavailable: {e}", file=sys.stderr)
        return 1

    scope = ""
    refs = sorted(cwds)
    if args.repo:
        try:
            paths = _repo_paths(args.repo)
        except LookupError as e:
            print(f"cockpit broadcast: {e}", file=sys.stderr)
            return 2
        except (RuntimeError, OSError) as e:
            print(
                f"cockpit broadcast: could not enumerate {args.repo} worktrees: {e}",
                file=sys.stderr,
            )
            return 1
        refs = refs_at(cwds, paths)
        scope = f" in {args.repo}"
    elif args.worktree:
        target = Path(os.path.expanduser(args.worktree)).resolve()
        # A path that isn't a directory is a typo, and the alternative — an
        # empty filter reported as "no other workspaces there" — reads as a
        # quiet success on a message that reached nobody.
        if not target.is_dir():
            print(
                f"cockpit broadcast: no such worktree: {args.worktree}",
                file=sys.stderr,
            )
            return 2
        refs = refs_at(cwds, [target])
        scope = f" in {target}"
    if not refs:
        print(f"cockpit broadcast: no other workspaces{scope or ' found'}")
        return 0

    skips: dict[str, str] = {}
    sent = sum(
        1
        for ref in refs
        if nudge_if_idle(ref, args.message, dry=args.dry, tag="broadcast", skips=skips)
    )

    if args.dry:
        # Under `dry` nothing is sent, so eligibility is the complement of the
        # skip set — not `sent`, which is empty by construction.
        print(
            f"cockpit broadcast: dry-run — {len(refs) - len(skips)}/{len(refs)} "
            f"workspace(s){scope} would receive it"
        )
    else:
        print(f"cockpit broadcast: sent to {sent}/{len(refs)} workspace(s){scope}")
    _print_skips(skips, retry=not args.dry)
    return 0


def _print_skips(skips: dict[str, str], *, retry: bool) -> None:
    """One line per skip reason, biggest group first, with its refs.

    The grouping and its order are `cmux.skip_summary`'s, shared with the TUI's
    fan-out toast; only the wording here is broadcast's own — a terminal has
    room to name the refs, a toast does not.
    """
    if not skips:
        return
    print(f"  skipped {len(skips)}{' — re-run to retry' if retry else ''}:")
    for reason, refs in skip_summary(skips):
        print(f"    {reason} ({len(refs)}): {', '.join(refs)}")


if __name__ == "__main__":
    sys.exit(main())
