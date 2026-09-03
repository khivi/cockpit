"""Top bar: the app and the cursor row's repo on the left, tick countdowns and
the menu on the right.

Left to right that reads app · context · telemetry · control. The repo is the
only segment whose width changes while the app runs, so it takes the flexible
`1fr` slot and everything to its right is anchored to the right edge — put the
countdowns in that slot instead and they slide sideways on every arrow key.

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

# The two ticks are named by glyph in the bar and by word in the tooltip. The
# bar has room for one mark per counter and the pair only has to read as "one
# of these is the slower one"; the tooltip is where each glyph is spelled out,
# so these constants are what keep the legend from drifting off the bar.
SLOW_GLYPH = "🐢"
FAST_GLYPH = "🐇"

_SLOW_DESC = (
    f"{SLOW_GLYPH} Slow tick: full reconcile — fetches PRs from GitHub and "
    "rebuilds the PR cache and git-state cells."
)
_FAST_DESC = (
    f"{FAST_GLYPH} Fast tick: network-free republish of the already-cached "
    "git state and PR data from disk."
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


def brand_text(version_text: str, url: str) -> Text:
    """The leftmost segment: the app and the version it is running.

    Dim, because it is the one thing in the bar that cannot change while the
    app runs — but kept on screen rather than moved into the menu or a hover,
    since a TUI bug report is a screenshot and neither of those survives one.
    cockpit ships a release per merge through brew with no in-app update check,
    so this is the only always-on answer to which build you are looking at.

    Linked to the release notes, following the table's rule that a cell naming
    something on the web is an OSC 8 hyperlink — noticing your version and
    wanting to know what changed in it is one gesture. The URL is passed in
    rather than imported: the app owns it, and this widget stays pure display.
    """
    text = Text(f"cockpit {version_text}".strip(), style="dim")
    if url:
        text.stylize(f"link {url}")
    return text


def status_text(slow_remaining: int, fast_remaining: int) -> str:
    """The tick countdowns, as Textual markup."""
    text = f"{SLOW_GLYPH} {_fmt(slow_remaining)}"
    if fast_remaining != OFF:
        text += f"   {FAST_GLYPH} {_fmt(fast_remaining)}"
    return text


class HeaderBar(Horizontal):
    """A one-line bar; both halves repaint whenever a reactive changes."""

    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        background: $boost;
        color: $text;
    }
    HeaderBar > #header-brand {
        width: auto;
        content-align: left middle;
        padding-left: 1;
        padding-right: 2;
    }
    /* The one segment that changes width as the cursor moves, so it takes the
       flexible slot and grows into the empty middle. Its text is left-aligned
       against the brand, so nothing on either side of it shifts. */
    HeaderBar > #header-repo {
        width: 1fr;
        content-align: left middle;
    }
    HeaderBar > #header-status {
        width: auto;
        content-align: right middle;
        padding-right: 2;
    }
    /* Every theme sets `link-style: underline` and `link-color: $text` on an
       `@click` span, which override a `color:` rule here and leave the one
       clickable word both underlined and brighter than the telemetry beside
       it. The table's OSC 8 cells carry no underline either, so the menu is
       pinned to the brand's chrome weight at rest and hover — already a bold
       `$primary` pill by default — is left to carry the whole affordance. */
    HeaderBar > #header-menu {
        width: auto;
        content-align: right middle;
        padding-right: 1;
        link-color: $text-muted;
        link-style: none;
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
    #
    # The glyph is single-cell on purpose. `☰` (U+2630) measures 2 cells but is
    # drawn with one cell of ink, so it paints as a hamburger, a blank half, and
    # only then the label — the glyph detaches from the word it belongs to. Same
    # ink-width-vs-cell-width trap as the table's `_STATUS_SLOT`, inverted.
    MENU_LABEL = "≡ Menu"
    MENU_TOOLTIP = (
        "Output log, show/edit config, theme, and the feature guide.\n"
        "Click, or press ctrl+p."
    )
    REPO_TOOLTIP = (
        "The repo owning the highlighted row — the same name and colour as its\n"
        "group header, kept on screen once the header has scrolled away."
    )
    BRAND_TOOLTIP = (
        "The cockpit version this process is running.\n"
        "Opens the release notes; updates ship through brew, not from here."
    )

    version_text: reactive[str] = reactive("")
    version_url: reactive[str] = reactive("")
    slow_remaining: reactive[int] = reactive(0)
    fast_remaining: reactive[int] = reactive(OFF)
    # Cursor-row state, so it repaints on arrow keys without dragging the
    # once-a-second tick countdowns through a repaint with it.
    repo_name: reactive[str] = reactive("")
    repo_color: reactive[str | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Static("", id="header-brand")
        yield Static("", id="header-repo")
        yield Static("", id="header-status")
        yield Static(
            f"[@click=app.command_palette]{self.MENU_LABEL}[/]", id="header-menu"
        )

    def on_mount(self) -> None:
        self._sync_tooltip()
        # Their own tooltips, so they win the ancestor walk over the bar's tick
        # explanation — hovering the menu should explain the menu.
        self.query_one("#header-menu", Static).tooltip = self.MENU_TOOLTIP
        self.query_one("#header-repo", Static).tooltip = self.REPO_TOOLTIP
        self.query_one("#header-brand", Static).tooltip = self.BRAND_TOOLTIP
        self._repaint()
        self._repaint_repo()
        self._repaint_brand()

    def watch_version_text(self, version_text: str) -> None:
        self._repaint_brand()

    def watch_version_url(self, version_url: str) -> None:
        self._repaint_brand()

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

    def _repaint_brand(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#header-brand", Static).update(
            brand_text(self.version_text, self.version_url)
        )

    def _repaint(self) -> None:
        # Watchers fire before compose has run, so there is nothing to query yet
        # on the first assignments; on_mount paints the initial state.
        if not self.is_mounted:
            return
        self.query_one("#header-status", Static).update(
            status_text(self.slow_remaining, self.fast_remaining)
        )
