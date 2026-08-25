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

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.containers import VerticalScroll
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

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self._title, classes="config-title")
            # The body goes through `Text.from_ansi` (not a markup string): the
            # captured tick output carries ANSI colour codes and stray brackets
            # (`[clean]`, `[timestamp]`), and JSON config bodies contain `[` `]`
            # array delimiters — both of which Textual's markup parser mangles
            # into garbled cream-highlighted boxes. `from_ansi` decodes the
            # colour codes and disables markup interpretation; on a body with no
            # ANSI (the JSON views) it yields plain, unstyled text.
            yield Static(Text.from_ansi(self._body))
            yield Static("esc / q to close", classes="config-hint")


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
