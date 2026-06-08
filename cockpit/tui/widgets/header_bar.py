"""Top bar: slow + fast tick countdowns (left) and update indicator (right).

Pure display: the app sets the reactive attributes each second; this widget
just formats them. A remaining value of -1 means "tick running now", -2 means
"this tick is disabled" (fast tick off).
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.table import Table
from textual.reactive import reactive
from textual.widgets import Static


def _fmt(seconds: int) -> str:
    if seconds == -3:
        return "waiting"
    if seconds == -2:
        return "off"
    if seconds == -1:
        return "running…"
    return f"{seconds // 60}:{seconds % 60:02d}"


class HeaderBar(Static):
    """A one-line bar; re-renders whenever a reactive changes."""

    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    slow_remaining: reactive[int] = reactive(0)
    fast_remaining: reactive[int] = reactive(-2)
    update_text: reactive[str] = reactive("")

    def render(self) -> RenderableType:
        left = f"slow ⏱ {_fmt(self.slow_remaining)}"
        if self.fast_remaining != -2:
            left += f"   fast ⏱ {_fmt(self.fast_remaining)}"
        right = f"[yellow]⬆ update {self.update_text}[/]" if self.update_text else ""
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(left, right)
        return grid
