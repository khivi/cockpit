"""Modal config viewer + command-palette commands.

`ConfigScreen` is a read-only scrollable overlay that prints a JSON blob (the
whole `config.json`). `ConfigCommands` registers "Show config: all repos" /
"Edit config" entries in the built-in command palette (Ctrl+P): the show entry
pushes the screen with the full config; the edit entry opens `config.json` in
$EDITOR.

Like the rest of the TUI the *viewer* never writes a cell — it only reads
`load_config()`. The edit entry delegates to `app.action_edit_config`, the one
sanctioned full-config write (mirroring `save_tui_theme`).
"""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfigScreen(ModalScreen[None]):
    """A dismissable overlay showing a title + a (pre-formatted) config body."""

    DEFAULT_CSS = """
    ConfigScreen { align: center middle; }
    ConfigScreen > VerticalScroll {
        width: 80%;
        max-width: 110;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    ConfigScreen .config-title { text-style: bold; color: $accent; margin-bottom: 1; }
    ConfigScreen .config-hint { color: $text-muted; margin-top: 1; }
    """

    BINDINGS = [Binding("escape,q", "dismiss", "Close")]

    def __init__(self, title: str, body: str | Text) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self._title, classes="config-title")
            # A pre-styled `Text` (the colourised post-update changelog) renders
            # as-is. A raw `str` body goes through `Text.from_ansi` (not a markup
            # string): the captured tick output carries ANSI colour codes and
            # stray brackets (`[clean]`, `[timestamp]`), and JSON config bodies
            # contain `[` `]` array delimiters — both of which Textual's markup
            # parser mangles into garbled cream-highlighted boxes. `from_ansi`
            # decodes the colour codes and disables markup interpretation; on a
            # body with no ANSI (the JSON views) it yields plain, unstyled text.
            body = (
                self._body
                if isinstance(self._body, Text)
                else Text.from_ansi(self._body)
            )
            yield Static(body)
            yield Static("esc / q to close", classes="config-hint")


# Conventional-commit type → line colour. Unlisted types (chore/ci/build/style/
# test and anything non-conforming) fall back to dim, so feat/fix stand out.
_TYPE_COLORS = {
    "feat": "green",
    "fix": "red",
    "perf": "cyan",
    "refactor": "magenta",
    "docs": "blue",
    "revert": "yellow",
}


def _commit_color(subject: str) -> str:
    typ = subject.split(":", 1)[0].split("(", 1)[0].strip().lower()
    return _TYPE_COLORS.get(typ, "dim")


def _append_subject(text: Text, subject: str) -> None:
    """One `• <subject>` line: dim bullet + subject tinted by commit type."""
    text.append("• ", style="dim")
    text.append(subject, style=_commit_color(subject))


def render_changelog(items: list[tuple[str, str]]) -> Text:
    """Render `(subject, bucket)` entries as the shared ChangeLog body: a dim
    relative-age header (today / yesterday / this week / …) whenever the bucket
    changes, then each subject tinted by commit type. Used by both the paginated
    `r` ChangeLog screen and the post-update modal so they render identically."""
    out = Text()
    last_bucket: str | None = None
    for subject, bucket in items:
        if bucket != last_bucket:
            if last_bucket is not None:
                out.append("\n\n")  # blank line between age groups
            out.append(bucket, style="dim")
            out.append("\n")
            last_bucket = bucket
        else:
            out.append("\n")
        _append_subject(out, subject)
    return out


class _LazyScroll(VerticalScroll):
    """A VerticalScroll that fires `NearBottom` as the view nears its end, so a
    paginated screen can fetch the next page just-in-time instead of up front."""

    class NearBottom(Message):
        pass

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        # max_scroll_y == 0 while content fits without scrolling.
        if self.max_scroll_y > 0 and new_value >= self.max_scroll_y - 2:
            self.post_message(self.NearBottom())


class ReleaseNotesScreen(ModalScreen[None]):
    """The `r` ChangeLog overlay: scrollable, loading one page of merged-PR
    entries per scroll-to-bottom so the first paint is quick and history is
    only fetched as far as you scroll. `fetch(page)` runs in a thread worker
    (it shells `gh`) and returns `([(subject, bucket), …], exhausted)`."""

    DEFAULT_CSS = """
    ReleaseNotesScreen { align: center middle; }
    ReleaseNotesScreen > _LazyScroll {
        width: 80%;
        max-width: 110;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    ReleaseNotesScreen .config-title { text-style: bold; color: $accent; margin-bottom: 1; }
    ReleaseNotesScreen .config-hint { color: $text-muted; margin-top: 1; }
    """

    BINDINGS = [Binding("escape,q", "dismiss", "Close")]

    def __init__(
        self,
        title: str,
        fetch: Callable[[int], tuple[list[tuple[str, str]], bool]],
    ) -> None:
        super().__init__()
        self._title = title
        self._fetch = fetch
        self._page = 0
        self._loading = False
        self._exhausted = False
        self._items: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with _LazyScroll():
            yield Static(self._title, classes="config-title")
            yield Static("", id="rn-body")
            yield Static("loading…", id="rn-hint", classes="config-hint")

    def on_mount(self) -> None:
        self._load_next()

    def on__lazy_scroll_near_bottom(self, _: _LazyScroll.NearBottom) -> None:
        self._load_next()

    def _load_next(self) -> None:
        if self._loading or self._exhausted:
            return
        self._loading = True
        self._fetch_page(self._page + 1)

    @work(thread=True, exit_on_error=False)
    def _fetch_page(self, page: int) -> None:
        items, exhausted = self._fetch(page)
        self.app.call_from_thread(self._append, page, items, exhausted)

    def _append(self, page: int, items: list[tuple[str, str]], exhausted: bool) -> None:
        self._loading = False
        self._exhausted = self._exhausted or exhausted
        if items:
            self._page = page
            self._items.extend(items)
            # Shared renderer (append, never markup): per-type colours, dim
            # age headers, and stray `[` `]` stay literal.
            self.query_one("#rn-body", Static).update(render_changelog(self._items))
        hint = self.query_one("#rn-hint", Static)
        if self._exhausted:
            hint.update(
                "esc / q to close" if self._page else "no release notes available"
            )
        else:
            hint.update("scroll for more · esc / q to close")
            # A page that fits without overflow never fires `watch_scroll_y`, so
            # on a tall terminal the loader would stall after page 1 with older
            # history unreachable. Pull the next page until the view overflows
            # (max_scroll_y > 0) or history is exhausted.
            self.call_after_refresh(self._fill_if_short)

    def _fill_if_short(self) -> None:
        if not self._exhausted and self.query_one(_LazyScroll).max_scroll_y == 0:
            self._load_next()


class ConfigCommands(Provider):
    """Command-palette entries for showing cockpit config."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        app = self.app
        commands = (
            (
                "Show config: all repos",
                "action_show_full_config",
                "Show the full cockpit config (all repos + globals)",
            ),
            (
                "Edit config: open in $EDITOR",
                "action_edit_config",
                "Edit config.json in $EDITOR (changes apply on restart)",
            ),
        )
        for label, action, help_text in commands:
            score = matcher.match(label)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    lambda a=action: getattr(app, a)(),
                    help=help_text,
                )
