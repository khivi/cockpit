"""ANSI color primitives for cockpit's terminal output.

NO_COLOR (https://no-color.org) opts the user out. The isatty heuristic
isn't useful here: cockpit.py output goes through cmux's renderer and
starship.py output is piped to cship — neither is ever a tty, but both
DO render ANSI when forwarded to the terminal.

This module exposes raw colorizers only. Higher-level helpers live elsewhere:
  - `lib.log_format.verb()` — padded dim verb prefixes for log lines
  - `lib.issue_color.issue_color()` — PR-issue → colorizer mapping
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

_USE_COLOR = os.environ.get("NO_COLOR") is None

Colorizer = Callable[[str], str]


def _read_theme() -> str:
    """Resolve the terminal-background theme ("dark" | "light") at import.

    Read straight from ~/.config/cockpit/config.json (honoring $COCKPIT_HOME)
    rather than importing lib.config — this leaf stays dependency-free, the way
    `_USE_COLOR` reads $NO_COLOR inline. Any error (missing file, bad JSON,
    unknown value) falls back to "dark", the palette this module is tuned for.
    """
    home = os.environ.get("COCKPIT_HOME")
    base = Path(home) if home else Path.home() / ".config" / "cockpit"
    try:
        with (base / "config.json").open() as f:
            theme = json.load(f).get("theme", "dark")
    except (OSError, ValueError):
        return "dark"
    return theme if theme in ("dark", "light") else "dark"


# Only the neutral greys are themed: the near-black text values were the sole
# contrast problem on dark backgrounds (see fix d42a1d2), so light mode darkens
# them while every saturated hue below stays background-agnostic. Mirrors the
# `text_primary`/`text_muted` palette roles in scripts/defaults/starship.toml.
_LIGHT = _read_theme() == "light"


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
slate = _ansi("38;5;236" if _LIGHT else "38;5;243")  # neutral gray; branch, tier-ok
azure = _ansi("38;5;38")  # cyan-blue; ahead-of-base, ↗ ahead
orange = _ansi("38;5;172")  # behind-of-origin, ↻ stale, tier-warn
crimson = _ansi("38;5;160")  # tier-red
leaf = _ansi("38;5;34")  # green; ● staged
amber = _ansi("38;5;220")  # yellow; ✎ unstaged
shadow = _ansi("38;5;238" if _LIGHT else "38;5;240")  # dim gray; untracked, ago

# Bold variants used for PR state pills and tier-100 emphasis.
bold_slate = _ansi("1;38;5;236" if _LIGHT else "1;38;5;243")
bold_azure = _ansi(
    "1;38;5;32"
)  # PR OPEN — a slightly different blue index from `azure`
bold_orange = _ansi("1;38;5;172")  # PR REVIEW_REQUIRED, tier-90+
bold_leaf = _ansi("1;38;5;34")  # PR APPROVED
bold_crimson = _ansi("1;38;5;160")  # PR CHANGES_REQUESTED, tier-100
bold_violet = _ansi("1;38;5;91")  # PR MERGED
bold_ruby = _ansi("1;38;5;88")  # PR CLOSED
bold_shadow = _ansi("1;38;5;238" if _LIGHT else "1;38;5;240")  # PR DRAFT

# cmux workspace-color names → bold 256-color colorizers, so a repo's
# configured `sidebar_color` can also tint its name in cockpit's own cycle
# log. These approximate cmux's rendered hues (cmux maps each name to its
# theme); they read as the same colour family, not a pixel match. Saturated
# hues stay background-agnostic, so the map is the same in dark and light.
#
# This dict is the single source of truth for the valid `sidebar_color` set:
# `cmux.WORKSPACE_COLORS` is `frozenset(CMUX_COLOR_ANSI)`, so the two can't drift.
CMUX_COLOR_ANSI: dict[str, Colorizer] = {
    "Red": _ansi("1;38;5;196"),
    "Crimson": _ansi("1;38;5;160"),
    "Orange": _ansi("1;38;5;172"),
    "Amber": _ansi("1;38;5;214"),
    "Olive": _ansi("1;38;5;142"),
    "Green": _ansi("1;38;5;34"),
    "Teal": _ansi("1;38;5;37"),
    "Aqua": _ansi("1;38;5;44"),
    "Blue": _ansi("1;38;5;33"),
    "Navy": _ansi("1;38;5;25"),
    "Indigo": _ansi("1;38;5;61"),
    "Purple": _ansi("1;38;5;91"),
    "Magenta": _ansi("1;38;5;165"),
    "Rose": _ansi("1;38;5;211"),
    "Brown": _ansi("1;38;5;130"),
    "Charcoal": _ansi("1;38;5;240"),
}
