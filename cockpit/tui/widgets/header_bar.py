"""Top bar: slow + fast tick countdowns.

Pure display: the app sets the reactive attributes each second; this widget
just formats them. A remaining value of -1 means "tick running now", -2 means
"this tick is disabled" (fast tick off), -3 means "this tick is blocked
waiting on the app's tick lock" (the other tick currently holds it).
"""

from __future__ import annotations

from rich.console import RenderableType
from textual.reactive import reactive
from textual.widgets import Static

# Sentinel values for a tick's `*_remaining` reactive. Any non-negative int is
# a plain "seconds until next run" countdown; these three are out-of-band
# states. `_fmt` and `build_tooltip` are the only two readers of these
# numbers, so both live off these constants rather than repeating -1/-2/-3.
WAITING = -3
OFF = -2
RUNNING = -1

_SLOW_DESC = (
    "Slow tick: full reconcile — fetches PRs from GitHub and rebuilds the "
    "PR cache and git-state cells."
)
_FAST_DESC = (
    "Fast tick: network-free republish of the already-cached git state and "
    "PR data from disk."
)


def _fmt(seconds: int) -> str:
    if seconds == WAITING:
        return "waiting"
    if seconds == OFF:
        return "off"
    if seconds == RUNNING:
        return "running…"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _decode(label: str, seconds: int) -> str | None:
    """Explain an out-of-band sentinel for one tick; None for a plain countdown."""
    if seconds == WAITING:
        return (
            f"The {label} tick is waiting on the tick lock — the other tick "
            "is currently running."
        )
    if seconds == OFF:
        return f"The {label} tick is disabled."
    if seconds == RUNNING:
        return f"The {label} tick is running right now."
    return None


def build_tooltip(slow_remaining: int, fast_remaining: int) -> str:
    """Build the hover tooltip text for the current tick states.

    Always explains what the slow and fast ticks are, then appends a decode
    of whichever sentinel (waiting/off/running) is currently displayed — so
    the tooltip stays accurate to what's on screen rather than generic
    boilerplate.
    """
    lines = [_SLOW_DESC, _FAST_DESC]
    for label, seconds in (("slow", slow_remaining), ("fast", fast_remaining)):
        decoded = _decode(label, seconds)
        if decoded:
            lines.append(decoded)
    return "\n".join(lines)


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
    fast_remaining: reactive[int] = reactive(OFF)

    def on_mount(self) -> None:
        self._sync_tooltip()

    def watch_slow_remaining(self, slow_remaining: int) -> None:
        self._sync_tooltip()

    def watch_fast_remaining(self, fast_remaining: int) -> None:
        self._sync_tooltip()

    def _sync_tooltip(self) -> None:
        # Never assign self.tooltip from render() (also reactive) — drive it
        # from the watchers instead, so this can't turn into a refresh loop.
        self.tooltip = build_tooltip(self.slow_remaining, self.fast_remaining)

    def render(self) -> RenderableType:
        left = ""
        if self.version_text:
            left += f"[bold cyan]cockpit {self.version_text}[/]   "
        left += f"slow ⏱ {_fmt(self.slow_remaining)}"
        if self.fast_remaining != OFF:
            left += f"   fast ⏱ {_fmt(self.fast_remaining)}"
        return left
