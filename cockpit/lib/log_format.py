"""Higher-level log-line formatters built on `lib.colors`.

Currently just `verb()` — padded, dim-colored verb prefix that aligns the
opening word of each cockpit log line (`refreshed`, `teardown`, `closing`,
`orphan`, …) so the scannable noun sits in a fixed column.
"""

from __future__ import annotations

from .colors import Colorizer, dim


def verb(label: str, *, width: int = 9, color: Colorizer | None = None) -> str:
    """Left-pad a log-line verb to `width` and colorize it.

    `width=9` aligns the longest routine verbs (`refreshed`, `duplicate`).
    Pass `color=yellow` / `red` for verbs that should retain their hue
    (e.g. `refused`, `WARN`) while keeping the same padding.
    """
    painter = color if color is not None else dim
    return painter(f"{label:<{width}}")
