#!/usr/bin/env python3
"""`/cockpit:sync` — kick a running daemon, else run one cycle inline."""

from __future__ import annotations

import subprocess
import sys

from cockpit.lib.daemon_signal import kick_running


def main() -> int:
    if kick_running():
        return 0
    # No daemon running — run one cycle inline via the dispatcher so this
    # works regardless of where the package is installed (no file-path probe).
    return subprocess.run([sys.executable, "-m", "cockpit.cli", "once"]).returncode


if __name__ == "__main__":
    sys.exit(main())
