"""ANSI color helpers for cockpit's terminal output.

NO_COLOR (https://no-color.org) opts the user out. The isatty heuristic
isn't useful here: cockpit.py output goes through cmux's renderer and
starship.py output is piped to cship — neither is ever a tty, but both
DO render ANSI when forwarded to the terminal.
"""

from __future__ import annotations

import os
from typing import Callable

_USE_COLOR = os.environ.get("NO_COLOR") is None

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

# 256-color palette used by the statusline pills. Names match perceived hue,
# not ANSI index — read-site code (and tests) refer to e.g. `orange("↓2")`,
# not `_ansi("38;5;172")`.
slate = _ansi("38;5;243")  # neutral gray; branch name, tier-ok
azure = _ansi("38;5;38")  # cyan-blue; ahead-of-base, ↗ ahead
orange = _ansi("38;5;172")  # behind-of-origin, ↻ stale, tier-warn
crimson = _ansi("38;5;160")  # tier-red
leaf = _ansi("38;5;34")  # green; ● staged
amber = _ansi("38;5;220")  # yellow; ✎ unstaged
shadow = _ansi("38;5;240")  # dim gray; ✚ untracked, ago-suffix

# Bold variants used for PR state pills and tier-100 emphasis.
bold_slate = _ansi("1;38;5;243")
bold_azure = _ansi(
    "1;38;5;32"
)  # PR OPEN — a slightly different blue index from `azure`
bold_orange = _ansi("1;38;5;172")  # PR REVIEW_REQUIRED, tier-90+
bold_leaf = _ansi("1;38;5;34")  # PR APPROVED
bold_crimson = _ansi("1;38;5;160")  # PR CHANGES_REQUESTED, tier-100
bold_violet = _ansi("1;38;5;91")  # PR MERGED
bold_ruby = _ansi("1;38;5;88")  # PR CLOSED
bold_shadow = _ansi("1;38;5;240")  # PR DRAFT


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
