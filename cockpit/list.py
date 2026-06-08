#!/usr/bin/env python3
"""`/cockpit:list` entry point — render the cached worktree+PR table."""

from __future__ import annotations

import sys

from cockpit.lib.list import render_list


def main() -> int:
    return render_list()


if __name__ == "__main__":
    sys.exit(main())
