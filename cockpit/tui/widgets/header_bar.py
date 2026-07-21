"""Top bar: slow + fast tick countdowns.

Pure display: the app sets the reactive attributes each second; this widget
just formats them. A remaining value of -1 means "tick running now", -2 means
"this tick is disabled" (fast tick off).
"""

from __future__ import annotations

from rich.console import RenderableType
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

    version_text: reactive[str] = reactive("")
    slow_remaining: reactive[int] = reactive(0)
    fast_remaining: reactive[int] = reactive(-2)

    def render(self) -> RenderableType:
        left = ""
        if self.version_text:
            left += f"[bold cyan]cockpit {self.version_text}[/]   "
        left += f"slow ⏱ {_fmt(self.slow_remaining)}"
        if self.fast_remaining != -2:
            left += f"   fast ⏱ {_fmt(self.fast_remaining)}"
        return left
