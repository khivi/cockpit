"""Modal config viewer + command-palette commands.

`ConfigScreen` is a read-only scrollable overlay that prints a JSON blob (a
single repo's config, or the whole `config.json`). `ConfigCommands` registers
"Show config: …" entries in the built-in command palette (Ctrl+P) that resolve
the *currently selected* repo (the cursor row's repo) and push the screen.

Like the rest of the TUI it never writes a cell — it only reads `load_config()`.
"""

from __future__ import annotations

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
            yield Static(self._body)
            yield Static("esc / q to close", classes="config-hint")


class ConfigCommands(Provider):
    """Command-palette entries for showing cockpit config."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        app = self.app
        commands = (
            (
                "Show config: current repo",
                "action_show_repo_config",
                "Show the cockpit config for the selected row's repo",
            ),
            (
                "Show config: all repos",
                "action_show_full_config",
                "Show the full cockpit config (all repos + globals)",
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
