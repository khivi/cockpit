"""`/cockpit:repos` entry point — list configured repos."""

from __future__ import annotations

import sys

from cockpit.lib.repos import render_repos


def main() -> int:
    return render_repos()


if __name__ == "__main__":
    sys.exit(main())
