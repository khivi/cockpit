"""ANSI color helpers for cockpit's terminal output.

Disabled when stdout is not a TTY or when NO_COLOR is set.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

Colorizer = Callable[[str], str]


def _ansi(code: str) -> Colorizer:
    return (lambda s: f"\x1b[{code}m{s}\x1b[0m") if _USE_COLOR else (lambda s: s)


dim = _ansi("2")
bold = _ansi("1")
red = _ansi("31")
green = _ansi("32")
yellow = _ansi("33")
blue = _ansi("94")
magenta = _ansi("35")
cyan = _ansi("36")


def issue_color(issue: str) -> Colorizer:
    base = issue.split()[0] if issue else ""
    return {
        "clean": green,
        "approved": green,
        "comments": red,
        "changes-requested": red,
        "ci": red,
        "conflicts": red,
        "draft": dim,
        "orphan": yellow,
    }.get(base, lambda s: s)
