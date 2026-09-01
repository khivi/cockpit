"""Top bar: slow + fast tick countdowns on the left, the cursor row's repo and
the menu on the right.

Pure display: the app sets the reactive attributes each second; this widget
just formats them. A remaining value of -1 means "tick running now", -2 means
"this tick is disabled" (fast tick off), -3 means "this tick is blocked
waiting on the app's tick lock" (the other tick currently holds it).
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import Static

from cockpit.lib.colors import CMUX_COLOR_ANSI

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


def repo_text(repo_name: str, repo_color: str | None) -> Text:
    """The cursor row's repo, tinted with its cmux `sidebar_color`.

    The table already names each repo on its group-header row, but that header
    scrolls off as soon as a repo holds more rows than fit — so on a fleet of
    any size the row under the cursor names no repo at all. This is the same
    name in the same colour, pinned where it can't scroll away.

    Duplicates `worktree_table._header_cells`' three-line tint deliberately: the
    two render different things (that one appends the `─` rule) and sharing
    would couple two sibling widgets for less code than the import costs.
    """
    if not repo_name:
        return Text("")
    colorizer = CMUX_COLOR_ANSI.get(repo_color or "")
    name = (
        Text(repo_name) if colorizer is None else Text.from_ansi(colorizer(repo_name))
    )
    name.stylize("bold")
    return Text.assemble(("▪ ", "dim"), name)


def status_text(version_text: str, slow_remaining: int, fast_remaining: int) -> str:
    """The left half: version + tick countdowns, as Textual markup."""
    text = ""
    if version_text:
        text += f"[bold cyan]cockpit {version_text}[/]   "
    text += f"slow ⏱ {_fmt(slow_remaining)}"
    if fast_remaining != OFF:
        text += f"   fast ⏱ {_fmt(fast_remaining)}"
    return text


class HeaderBar(Horizontal):
    """A one-line bar; both halves repaint whenever a reactive changes."""

    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        background: $boost;
        color: $text;
    }
    HeaderBar > #header-status {
        width: 1fr;
        content-align: left middle;
        padding-left: 1;
    }
    HeaderBar > #header-repo {
        width: auto;
        content-align: right middle;
        padding-right: 2;
    }
    HeaderBar > #header-menu {
        width: auto;
        color: $text-muted;
        content-align: right middle;
        padding-right: 1;
    }
    """

    # The command palette's only visible entry point. It lives here rather than
    # in the footer because it targets the app, not the cursor row — the footer
    # is the row-key reference, gated per row, and every key in it is a letter
    # you press. This one is clicked, so it sits in the corner a pointer goes
    # to. The key is deliberately NOT printed: `ctrl+p` is Textual's binding,
    # not one of cockpit's, and the label has to read as "there is something
    # else here" to someone hunting for a thing they can't find. The glyph is
    # what says clickable; the tooltip carries the key for anyone who wants it.
    MENU_LABEL = "☰ Menu"
    MENU_TOOLTIP = (
        "Output log, show/edit config, theme, and the feature guide.\n"
        "Click, or press ctrl+p."
    )
    REPO_TOOLTIP = (
        "The repo owning the highlighted row — the same name and colour as its\n"
        "group header, kept on screen once the header has scrolled away."
    )

    version_text: reactive[str] = reactive("")
    slow_remaining: reactive[int] = reactive(0)
    fast_remaining: reactive[int] = reactive(OFF)
    # Cursor-row state, so it repaints on arrow keys without dragging the
    # once-a-second tick countdowns through a repaint with it.
    repo_name: reactive[str] = reactive("")
    repo_color: reactive[str | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Static("", id="header-status")
        yield Static("", id="header-repo")
        yield Static(
            f"[@click=app.command_palette]{self.MENU_LABEL}[/]", id="header-menu"
        )

    def on_mount(self) -> None:
        self._sync_tooltip()
        # Their own tooltips, so they win the ancestor walk over the bar's tick
        # explanation — hovering the menu should explain the menu.
        self.query_one("#header-menu", Static).tooltip = self.MENU_TOOLTIP
        self.query_one("#header-repo", Static).tooltip = self.REPO_TOOLTIP
        self._repaint()
        self._repaint_repo()

    def watch_version_text(self, version_text: str) -> None:
        self._repaint()

    def watch_slow_remaining(self, slow_remaining: int) -> None:
        self._sync_tooltip()
        self._repaint()

    def watch_fast_remaining(self, fast_remaining: int) -> None:
        self._sync_tooltip()
        self._repaint()

    def watch_repo_name(self, repo_name: str) -> None:
        self._repaint_repo()

    def watch_repo_color(self, repo_color: str | None) -> None:
        self._repaint_repo()

    def _sync_tooltip(self) -> None:
        self.tooltip = build_tooltip(self.slow_remaining, self.fast_remaining)

    def _repaint_repo(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#header-repo", Static).update(
            repo_text(self.repo_name, self.repo_color)
        )

    def _repaint(self) -> None:
        # Watchers fire before compose has run, so there is nothing to query yet
        # on the first assignments; on_mount paints the initial state.
        if not self.is_mounted:
            return
        self.query_one("#header-status", Static).update(
            status_text(self.version_text, self.slow_remaining, self.fast_remaining)
        )
