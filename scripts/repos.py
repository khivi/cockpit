#!/usr/bin/env python3
"""`/cockpit:repos` entry point — list configured repos."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.repos import render_repos  # noqa: E402

if __name__ == "__main__":
    sys.exit(render_repos())
