"""`cockpit broadcast` — send a line of text to every idle Claude session.

A one-shot admin gesture (typically a slash command like `/compact`) fanned
out to every workspace cmux/limux knows about, so one command reaches every
open session instead of clicking through each one by hand.

Reuses the two existing primitives in `cockpit.lib.cmux` rather than building
a parallel send path:

  - `workspace_cwds()` enumerates every OTHER workspace (it excludes the
    caller's own by default — never broadcast into the daemon/TUI's own
    session).
  - `nudge_if_idle(ref, message, dry=..., tag="broadcast")` is the entire
    safety story: it refuses a mid-turn (`Running`) session, refuses the
    ambiguous `Needs input` state (a pending y/n permission — typing into it
    would answer the prompt, not deliver the message), and skips a
    `parked=` session. Called with no `pref_key` it bypasses PR mute/snooze,
    which is correct here — mute is a PR-nudge concept, not an admin-command
    one.

This module writes NOTHING durable: no cache cell, no pill, no `pill_state`,
no config. It is outside the "only the daemon writes the cache" invariant —
there is no queue and no retry of skipped sessions; a skipped ref is printed
and forgotten, and re-running the command is the retry.
"""

from __future__ import annotations

import argparse
import sys

from cockpit.lib.cmux import CmuxUnavailable, nudge_if_idle, workspace_cwds


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
    args = p.parse_args(argv)

    try:
        cwds = workspace_cwds()
    except CmuxUnavailable as e:
        print(f"cockpit broadcast: workspace backend unavailable: {e}", file=sys.stderr)
        return 1

    if not cwds:
        print("cockpit broadcast: no other workspaces found")
        return 0

    sent: list[str] = []
    skipped: list[str] = []
    for ref in sorted(cwds):
        if nudge_if_idle(ref, args.message, dry=args.dry, tag="broadcast"):
            sent.append(ref)
        else:
            skipped.append(ref)

    if args.dry:
        print(
            f"cockpit broadcast: dry-run — checked {len(cwds)} workspace(s); "
            "see the [dry] lines above for which would receive it"
        )
        return 0

    print(f"cockpit broadcast: sent to {len(sent)}/{len(cwds)} workspace(s)")
    if skipped:
        print(
            f"  skipped (busy, awaiting input, or parked): {', '.join(skipped)}"
            " — re-run to retry"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
