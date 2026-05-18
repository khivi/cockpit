#!/usr/bin/env python3
"""statusLine entry point — one line of cwd+PR+model state to stdout."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.footer import render_footer  # noqa: E402

if __name__ == "__main__":
    sys.exit(render_footer())
