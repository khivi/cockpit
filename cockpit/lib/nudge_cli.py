"""CLI implementation for `cockpit nudge {mute,unmute,snooze,wake,list,status}`.

Inferring the PR from the current branch (via `gh pr view`) lets the Claude
session that's being nudged mute its own PR without knowing the number, which
is the whole point of this surface (`cockpit nudge`).

The repo comes from the cwd the same way (`gh repo view`), because a pref is
keyed per repo (`nudges.pref_key`) — a PR number alone is shared with every
other repo's PR of that number. So these commands must run inside the repo.

`snooze`/`wake` mirror the TUI's `z` key (`tui/app.py::_toggle_snooze`) rather
than reusing it directly — a CLI has no row to toggle, so they're the explicit
set/clear pair `mute`/`unmute` already established. Both repaint the cached PR
snapshot immediately (`cache.restamp_pref`, the same call `z` makes) and kick
the daemon for a full cycle (`z`'s one caveat: sidebar-fold membership is only
rebuilt on an unscoped cycle), so a session doesn't have to wait out the
next slow-tick interval for either to take visible effect.

Both are idempotent *writes* rather than no-ops on an unchanged pref: that
re-stamp-and-kick pair is the only way to converge a pref and a screen that
disagree, so the second run has to keep doing it. Where `z` applies the pressed
row's direction to a whole stacked chain, these stay per-PR and warn instead
(`_warn_split_chain`) — a per-PR snooze below a chain's tip moves nothing on
either fold surface.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .cache import find_pr_payload_for_cwd, load_pr_payloads_by_branch, restamp_pref
from .daemon_signal import kick_running
from .gh import repo_nwo
from .git import current_branch
from .nudges import (
    NudgePref,
    delete_pref,
    list_prefs,
    load_pref,
    parse_duration,
    pref_key,
    save_pref,
    wake_signature,
)
from .stacks import chain_tip


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


def _resolve_pr(arg_pr: int | None) -> tuple[int, str, str]:
    """(PR number, repo nwo, pref key) for the command. Exits 2 when either the
    number or the repo is unresolvable.

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
    return pr, repo, pref_key(repo, pr)


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
    pr, _repo, key = _resolve_pr(args.pr)
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
    pr, _repo, key = _resolve_pr(args.pr)
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


def _stack_tip_pr(repo: str, branch: str) -> tuple[int, str] | None:
    """(number, branch) of the open PR whose snooze decides `branch`'s fold, or
    None when `branch` is itself the tip (or isn't stacked at all).

    Read off the daemon's cached payloads, so this costs no round-trip and an
    absent cache simply means no warning — the write it annotates has already
    happened either way.
    """
    if not branch:
        return None
    payloads = {
        head: payload
        for head, payload in load_pr_payloads_by_branch(repo).items()
        if str(payload.get("state") or "") == "OPEN"
    }
    tip = chain_tip(
        {head: str(p.get("base") or "") for head, p in payloads.items()}, branch
    )
    number = payloads[tip].get("number") if tip != branch else None
    return (int(number), tip) if number else None


def _warn_split_chain(repo: str, pr: int, branch: str, *, snoozing: bool) -> None:
    """Say so when this PR is stacked under one whose snooze state disagrees.

    `cockpit nudge` stays per-PR — someone naming one number gets that number,
    unlike the TUI's `z`, which applies the pressed row's direction to the whole
    chain. But both fold surfaces band a chain by its **tip**, and a snoozed row
    deliberately carries no glyph, so a snooze below the tip is silent *and*
    invisible: the row neither sinks nor folds, which reads as the command
    having done nothing at all. The write stays per-PR; this only names the PR
    that has to agree before the screen moves.
    """
    tip = _stack_tip_pr(repo, branch)
    if tip is None:
        return
    number, tip_branch = tip
    if load_pref(pref_key(repo, number)).snoozed == snoozing:
        return
    state = "is not snoozed" if snoozing else "is still snoozed"
    print(f"  note: #{pr} is stacked under #{number} ({tip_branch}), which {state}.")
    print(
        "  Both folds band a chain by its tip, so this row won't move until: "
        f"cockpit nudge {'snooze' if snoozing else 'wake'} {number}"
    )


def _cmd_snooze(args: argparse.Namespace) -> int:
    pr, repo, key = _resolve_pr(args.pr)
    pref = load_pref(key)
    was_snoozed = pref.snoozed
    cwd = Path.cwd()
    branch = current_branch(cwd)
    payload = (find_pr_payload_for_cwd(cwd, branch) if branch else None) or {}
    pref.snoozed = True
    # Re-snoozing an already-snoozed PR re-arms deliberately: it is the only
    # gesture that re-stamps the cells and kicks the daemon, so it has to stay
    # available as the recovery path when the pref and the surfaces disagree.
    # With no payload to read, though, the snapshots are left alone — blanking a
    # live `wake_on` would wake the snooze on the very next tick.
    if payload or not was_snoozed:
        pref.wake_on = wake_signature(
            int(payload.get("total") or 0), str(payload.get("review") or "")
        )
        pref.wake_nudge = str(payload.get("nudge") or "")
        pref.wake_head = str(payload.get("headRefOid") or "")
    # A snooze supersedes a mute — see `nudges.NudgePref` docstring.
    pref.muted = False
    pref.until = None
    pref.reason = ""
    save_pref(key, pref)
    restamp_pref(repo, pr, cwd, pref)
    kick_running(quiet=True)
    print(
        f"{'re-snoozed' if was_snoozed else 'snoozed'} PR #{pr} — wakes on a new "
        "comment, review, CI/conflict issue, or a push to a PR you're reviewing"
    )
    _warn_split_chain(repo, pr, branch, snoozing=True)
    return 0


def _cmd_wake(args: argparse.Namespace) -> int:
    pr, repo, key = _resolve_pr(args.pr)
    pref = load_pref(key)
    was_snoozed = pref.snoozed
    cwd = Path.cwd()
    pref.snoozed = False
    pref.wake_on = ""
    pref.wake_nudge = ""
    pref.wake_head = ""
    save_pref(key, pref)
    restamp_pref(repo, pr, cwd, pref)
    kick_running(quiet=True)
    print(f"woke PR #{pr}" if was_snoozed else f"PR #{pr}: already awake — re-stamped")
    _warn_split_chain(repo, pr, current_branch(cwd), snoozing=False)
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
    pr, _repo, key = _resolve_pr(args.pr)
    _print_status(pr, load_pref(key))
    return 0


def _cmd_forget(args: argparse.Namespace) -> int:
    pr, _repo, key = _resolve_pr(args.pr)
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

    snooze = sub.add_parser(
        "snooze",
        help="Snooze a PR until it changes (new comment, review, or issue).",
    )
    snooze.add_argument("pr", type=int, nargs="?")
    snooze.set_defaults(func=_cmd_snooze)

    wake = sub.add_parser("wake", help="Clear a PR's snooze.")
    wake.add_argument("pr", type=int, nargs="?")
    wake.set_defaults(func=_cmd_wake)

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
