"""`/cockpit:sync` — kick a running daemon to run a cycle now."""

from __future__ import annotations

import sys

from cockpit.lib.daemon_signal import kick_running


def main() -> int:
    if kick_running():
        return 0
    print(
        "cockpit: no daemon running — start one with `cockpit watch` "
        "(or bin/cockpit.sh) and retry.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
