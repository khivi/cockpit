"""PR-issue → colorizer mapping.

Maps cockpit's `display_issue` vocabulary (`clean`, `approved`, `comments`,
`changes-requested`, `ci`, `conflicts`, `draft`, `orphan`) to a primitive
colorizer from `lib.colors`. Lives in its own module so `lib.colors` stays
domain-free.
"""

from __future__ import annotations

from .colors import Colorizer, dim, green, red, yellow


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
