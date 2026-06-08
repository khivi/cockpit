"""`/cockpit:nudge` entry point — manage nudge mutes."""

from __future__ import annotations

import sys

from cockpit.lib.nudge_cli import main

if __name__ == "__main__":
    sys.exit(main())
