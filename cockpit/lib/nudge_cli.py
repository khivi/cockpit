"""CLI implementation for `cockpit nudge {mute,unmute,list,status}`.

Inferring the PR from the current branch (via `gh pr view`) lets the Claude
session that's being nudged mute its own PR without knowing the number, which
is the whole point of this surface (`cockpit nudge`).

The repo comes from the cwd the same way (`gh repo view`), because a pref is
keyed per repo (`nudges.pref_key`) — a PR number alone is shared with every
other repo's PR of that number. So these commands must run inside the repo.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .gh import repo_nwo
from .nudges import (
    NudgePref,
    delete_pref,
    list_prefs,
    load_pref,
    parse_duration,
    pref_key,
    save_pref,
)


def _infer_pr_number() -> int | None:
    """Return the PR number for the current branch via `gh pr view`, else None.

    The daemon stores nudge prefs by PR number, so this is what `cockpit nudge`
    uses when invoked without an explicit number.
    """
    res = subprocess.run(
        ["gh", "pr", "view", "--json", "number", "-q", ".number"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    out = res.stdout.strip()
    if not out:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def _resolve_pr(arg_pr: int | None) -> tuple[int, str]:
    """(PR number, pref key) for the command. Exits 2 when either is unresolvable.

    The number can be passed explicitly; the repo never can — it's always the
    cwd's, since that's the only thing that makes a bare number unambiguous.
    """
    pr = arg_pr
    if pr is None:
        pr = _infer_pr_number()
    if pr is None:
        print(
            "no PR number given and could not infer from current branch — "
            "pass the PR number explicitly (e.g. `cockpit nudge mute 12345`)",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        repo = repo_nwo(Path.cwd())[1]
    except RuntimeError as e:
        print(
            f"could not resolve the repo for PR #{pr} — nudge prefs are keyed "
            f"per repo, so run this inside the repo's checkout ({e})",
            file=sys.stderr,
        )
        sys.exit(2)
    return pr, pref_key(repo, pr)


def _fmt_until(until: float | None) -> str:
    if until is None:
        return "forever"
    dt = datetime.fromtimestamp(until, tz=UTC).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def _print_status(pr_number: int, pref: NudgePref) -> None:
    if not pref.muted:
        # A snooze silences nudges too (`NudgePref.quiet`), so "not muted" alone
        # would read as "will nudge" on a PR that won't.
        if pref.snoozed:
            print(f"PR #{pr_number}: snoozed until a new comment or review")
        else:
            print(f"PR #{pr_number}: not muted")
        if pref.last_nudge_at:
            ago = int(time.time() - pref.last_nudge_at)
            print(f"  last nudge: {ago}s ago")
        return
    print(f"PR #{pr_number}: muted until {_fmt_until(pref.until)}")
    if pref.reason:
        print(f"  reason: {pref.reason}")


def _cmd_mute(args: argparse.Namespace) -> int:
    until: float | None = None
    if args.until:
        try:
            until = time.time() + parse_duration(args.until)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
    pr, key = _resolve_pr(args.pr)
    pref = load_pref(key)
    pref.muted = True
    pref.until = until
    pref.reason = args.reason or ""
    save_pref(key, pref)
    print(f"muted PR #{pr} until {_fmt_until(until)}")
    if args.reason:
        print(f"  reason: {args.reason}")
    return 0


def _cmd_unmute(args: argparse.Namespace) -> int:
    pr, key = _resolve_pr(args.pr)
    pref = load_pref(key)
    if not pref.muted:
        print(f"PR #{pr}: not muted")
        return 0
    pref.muted = False
    pref.until = None
    pref.reason = ""
    save_pref(key, pref)
    print(f"unmuted PR #{pr}")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    # Repo-wide: keys are `<repo>__<number>` stems (or a bare number for a
    # legacy file not yet migrated), rendered as `repo#N` / `#N`.
    muted = {k: p for k, p in list_prefs().items() if p.muted}
    if not muted:
        print("no muted PRs")
        return 0
    for key, pref in sorted(muted.items()):
        repo, _, number = key.rpartition("__")
        line = f"{repo}#{number}  muted  until {_fmt_until(pref.until)}"
        if pref.reason:
            line += f"  — {pref.reason}"
        print(line)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    pr, key = _resolve_pr(args.pr)
    _print_status(pr, load_pref(key))
    return 0


def _cmd_forget(args: argparse.Namespace) -> int:
    pr, key = _resolve_pr(args.pr)
    if delete_pref(key):
        print(f"deleted nudge file for PR #{pr}")
    else:
        print(f"no nudge file for PR #{pr}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cockpit nudge",
        description="Manage cockpit nudge mutes (persisted under ~/.config/cockpit/cache/nudges/).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    mute = sub.add_parser("mute", help="Mute all nudges for a PR.")
    mute.add_argument(
        "pr", type=int, nargs="?", help="PR number (default: current branch's PR)."
    )
    mute.add_argument(
        "--until", help="Duration before auto-unmute (e.g. 30m, 2h, 7d, 1w)."
    )
    mute.add_argument("--reason", help="Free-text note shown in `list` / `status`.")
    mute.set_defaults(func=_cmd_mute)

    unmute = sub.add_parser("unmute", help="Resume nudges for a PR.")
    unmute.add_argument("pr", type=int, nargs="?")
    unmute.set_defaults(func=_cmd_unmute)

    lst = sub.add_parser("list", help="Show currently muted PRs.")
    lst.set_defaults(func=_cmd_list)

    status = sub.add_parser("status", help="Show mute / last-nudge state for a PR.")
    status.add_argument("pr", type=int, nargs="?")
    status.set_defaults(func=_cmd_status)

    forget = sub.add_parser(
        "forget",
        help="Delete the on-disk nudge file for a PR (clears rate-limit timer too).",
    )
    forget.add_argument("pr", type=int, nargs="?")
    forget.set_defaults(func=_cmd_forget)

    args = p.parse_args(argv)
    return int(args.func(args))
