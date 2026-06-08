"""Right pane: a scrolling log of tick output and kick messages.

The app installs a process-wide stdout/stderr writer that funnels every
`print(...)` from the tick functions into a queue; a timer drains that queue
into this widget. The cycle output carries raw ANSI colour codes, so each line
is parsed with `Text.from_ansi` (not fed as a plain/markup string) — otherwise
Rich mangles the escape sequences into garbled boxes. Rich-markup interpretation
stays off so a stray bracket in tool output can't corrupt the display.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import RichLog


class LogPane(RichLog):
    DEFAULT_CSS = """
    LogPane {
        width: 1fr;
        border-left: solid $panel;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(
            highlight=False,
            markup=False,
            wrap=True,
            max_lines=2000,
            **kwargs,  # type: ignore[arg-type]
        )

    def append(self, line: str) -> None:
        self.write(Text.from_ansi(line))
