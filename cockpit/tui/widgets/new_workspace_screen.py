"""Modal text box for creating a new worktree + workspace.

The app's `n` action pushes this screen; on submit it dismisses with the typed
string, which the app feeds to `spawn.py` as a source — a bare name (new
branch), a PR (`#N` / URL), or a Linear id — auto-detected by spawn.py (the same
path `/cockpit:new` walks). Empty input / escape dismisses with `None` (no
spawn).

Like the rest of the TUI this screen never writes a cell: the spawn it triggers
runs detached in the app and the new worktree surfaces on the next slow tick, so
the daemon stays the sole cache writer.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class NewWorkspaceScreen(ModalScreen[str | None]):
    """A dismissable text-box overlay returning the typed spawn source."""

    DEFAULT_CSS = """
    NewWorkspaceScreen { align: center middle; }
    NewWorkspaceScreen > VerticalScroll {
        width: 80%;
        max-width: 90;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    NewWorkspaceScreen .nw-title { text-style: bold; color: $accent; margin-bottom: 1; }
    NewWorkspaceScreen .nw-hint { color: $text-muted; }
    NewWorkspaceScreen Input { margin: 1 0; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, repo_hint: str | None = None) -> None:
        super().__init__()
        self._repo_hint = repo_hint

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("New workspace", classes="nw-title")
            target = f"  (repo: {self._repo_hint})" if self._repo_hint else ""
            yield Static(
                f"Branch name, PR (#N or URL), or Linear id{target}",
                classes="nw-hint",
            )
            yield Input(placeholder="fix-login  |  #1234  |  PE-1234", id="nw-input")
            yield Static("enter to create · esc to cancel", classes="nw-hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the box: hand the trimmed value back; blank → None (no spawn).
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
