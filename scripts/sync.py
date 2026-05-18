#!/usr/bin/env python3
"""`/cockpit:sync` — kick a running daemon, else run one cycle inline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.daemon import kick_running  # noqa: E402

if __name__ == "__main__":
    if kick_running():
        sys.exit(0)
    here = Path(__file__).parent
    sys.exit(
        subprocess.run([sys.executable, str(here / "cockpit.py"), "--once"]).returncode
    )
