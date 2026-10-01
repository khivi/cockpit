"""Spinner + live status while the ticket check runs, with escape to cancel.

The check is the one inbox gesture that reaches a tracker, so it is also the one
that can sit for several seconds against an unreachable host with nothing on
screen. It reports per *repo*, the only unit with an observable boundary.

Cancellation is cooperative and the flag lives here rather than on the worker:
a Textual thread worker cannot be interrupted mid-fetch, so the running check
reads `cancelled` at each repo boundary and raises out. The screen therefore
dismisses immediately on the keypress while the in-flight round-trip finishes
and is discarded.
"""

from __future__ import annotations

from contextlib import suppress
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import LoadingIndicator, Static


class CheckProgressScreen(ModalScreen[None]):
    """A dismissable overlay showing what the check is working on."""

    DEFAULT_CSS = """
    CheckProgressScreen { align: center middle; }
    CheckProgressScreen > Vertical {
        width: 60%;
        max-width: 80;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    CheckProgressScreen .check-title { text-style: bold; color: $accent; }
    CheckProgressScreen LoadingIndicator { height: 1; background: $surface; }
    CheckProgressScreen .check-status { color: $text-muted; }
    CheckProgressScreen .check-hint { color: $text-muted; margin-top: 1; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,q", "cancel", "Cancel")]

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title
        self._status = "starting…"
        self.cancelled = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="check-title")
            yield LoadingIndicator()
            yield Static(self._status, classes="check-status", id="check-status")
            yield Static("esc / q to cancel", classes="check-hint")

    def set_status(self, text: str) -> None:
        """The first repo can be reported before the overlay has mounted, so the
        text is held and `compose` reads it rather than the update being lost."""
        self._status = text
        with suppress(NoMatches):
            self.query_one("#check-status", Static).update(text)

    def action_cancel(self) -> None:
        self.cancelled = True
        self.dismiss()
