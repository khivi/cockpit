#!/usr/bin/env python3
"""`/cockpit:list` entry point — render the cached worktree+PR table."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.list import render_list  # noqa: E402

if __name__ == "__main__":
    sys.exit(render_list())
