"""Load + render the first-turn prompt templates shipped under ``cockpit/prompts``.

The spawn first-turn prompts (Linear / GitHub-issue / Slack / plan-only / review /
Actions) are static prose with ``{placeholder}`` slots — the prose lives in
editable ``cockpit/prompts/*.txt`` files, this module loads them, and the spawn
builders compute the values + decide which template / sub-block to use. The split
is deliberate: templates carry no control flow (no conditionals, no value
formatting), so editing the wording never touches Python, and the builders own
the branching (e.g. Slack's fetch-vs-context modes are two separate templates).

Rendering is ``str.format``. The rendered prompt bodies contain no literal ``{``/
``}`` (every brace in the source was an f-string interpolation, now a named slot),
so no escaping is needed; a missing slot raises ``KeyError`` loudly rather than
silently emitting a stray placeholder.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files

_PACKAGE = "cockpit.prompts"


@cache
def _raw(name: str) -> str:
    """The verbatim template text (trailing newline stripped), cached per name.

    ``importlib.resources`` resolves the file whether cockpit runs from a source
    checkout or the installed wheel — the ``.txt`` ships alongside the package.
    """
    return (files(_PACKAGE) / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")


def render(name: str, /, **fields: object) -> str:
    """Render template ``name`` with ``fields`` substituted into its ``{slots}``."""
    return _raw(name).format(**fields)
