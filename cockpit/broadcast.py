"""`cockpit broadcast` — send a line of text to every idle Claude session.

A one-shot admin gesture (typically a slash command like `/compact`) fanned
out to every workspace cmux/limux knows about, so one command reaches every
open session instead of clicking through each one by hand.

`--repo NAME` narrows that fan-out to one configured repo. The scope is a
*filter over the same loop*, not a second delivery path: every ref that
survives it still goes through `nudge_if_idle` unchanged.

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

from cockpit.lib.cmux import CmuxUnavailable, nudge_if_idle, workspace_cwds
from cockpit.lib.config import load_config
from cockpit.lib.git import worktrees


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

    Matched by cwd against the repo's own `worktrees()`, never a path-prefix
    test — a worktree usually lives in a *sibling* directory of the repo, and a
    prefix would both miss those and claim an unrelated repo nested underneath.

    Raises `LookupError`, naming the configured repos, when nothing matches.
    """
    repos = load_config().get("repos", [])
    for repo in repos:
        # Casefolded: a config `name` is a display string ("Cockpit"), and
        # failing a broadcast over its capital letter is a papercut with no
        # upside.
        if _repo_label(repo).casefold() == name.casefold():
            path = Path(os.path.expanduser(repo["path"]))
            wts = worktrees(path, repo.get("branch_prefix", ""))
            return {path.resolve(), *(wt.path.resolve() for wt in wts)}
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
    p.add_argument(
        "--repo",
        metavar="NAME",
        help="Only send to workspaces in this configured repo, named as the "
        "dashboard names it (case-insensitive). Default: every idle workspace.",
    )
    args = p.parse_args(argv)

    try:
        cwds = workspace_cwds()
    except CmuxUnavailable as e:
        print(f"cockpit broadcast: workspace backend unavailable: {e}", file=sys.stderr)
        return 1

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
        cwds = {ref: cwd for ref, cwd in cwds.items() if cwd.resolve() in paths}

    scope = f" in {args.repo}" if args.repo else ""
    if not cwds:
        print(f"cockpit broadcast: no other workspaces{scope or ' found'}")
        return 0

    sent: list[str] = []
    skips: dict[str, str] = {}
    for ref in sorted(cwds):
        if nudge_if_idle(ref, args.message, dry=args.dry, tag="broadcast", skips=skips):
            sent.append(ref)

    if args.dry:
        # Under `dry` nothing is sent, so eligibility is the complement of the
        # skip set — not `sent`, which is empty by construction.
        print(
            f"cockpit broadcast: dry-run — {len(cwds) - len(skips)}/{len(cwds)} "
            f"workspace(s){scope} would receive it"
        )
    else:
        print(f"cockpit broadcast: sent to {len(sent)}/{len(cwds)} workspace(s){scope}")
    _print_skips(skips, retry=not args.dry)
    return 0


def _print_skips(skips: dict[str, str], *, retry: bool) -> None:
    """One line per skip reason, biggest group first, with its refs.

    A bare count invites the reader to guess at the cause, and the guess is
    usually "they're all mid-turn" — in practice the big group is `Needs
    input` without an `idle=` pill, which is a different (and more fixable)
    thing. Naming the reason next to the count is the whole point.
    """
    if not skips:
        return
    groups: dict[str, list[str]] = {}
    for ref, reason in skips.items():
        groups.setdefault(reason, []).append(ref)
    print(f"  skipped {len(skips)}{' — re-run to retry' if retry else ''}:")
    for reason, refs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"    {reason} ({len(refs)}): {', '.join(sorted(refs))}")


if __name__ == "__main__":
    sys.exit(main())
