r"""Modal text box for sending a line to a row's Claude session.

The app's `a` action pushes this screen; on submit it dismisses with the typed
text, which the app hands to `cmux.nudge_if_idle` — the same gated send path
`N`/nudge and `cockpit broadcast` use, so a mid-turn or permission-pending
session refuses the message rather than having it typed into a y/n prompt.
Empty input / escape dismisses with `None` (nothing sent).

**Single-line by construction — this is an `Input`, never a `TextArea`.**
`cmux send` synthesizes keypresses rather than doing a bracketed paste, so
every newline (the literal `\n` escape *and* a real 0x0A byte) arrives as
Enter. A multi-line message would therefore not deliver one prompt containing
newlines; it would submit the first fragment as its own truncated prompt and
the remainder as a second. `cmux.one_line` normalizes defensively at the send
funnel, but the input widget is the place the constraint is actually honest:
one line in, one prompt out. **Do not** swap in a `TextArea` without first
re-probing whether `cmux send` has grown a bracketed-paste mode.

Like the rest of the TUI this screen never writes a cell — the send is a
one-shot gesture with no cache cell, pill or retry, exactly like
`cockpit broadcast`.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class AskScreen(ModalScreen["str | None"]):
    """A dismissable one-line prompt returning the text to send, or `None`."""

    DEFAULT_CSS = """
    AskScreen { align: center middle; }
    AskScreen > VerticalScroll {
        width: 80%;
        max-width: 90;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    AskScreen .ask-title { text-style: bold; color: $accent; margin-bottom: 1; }
    AskScreen .ask-hint { color: $text-muted; }
    AskScreen Input { margin: 1 0; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, target: str = "") -> None:
        super().__init__()
        self._target = target

    def compose(self) -> ComposeResult:
        title = f"Ask {self._target}" if self._target else "Ask"
        with VerticalScroll():
            yield Static(title, classes="ask-title")
            yield Static(
                "Sent to this row's Claude session as a prompt. One line — "
                "a newline would submit early.",
                classes="ask-hint",
            )
            yield Input(placeholder="rebase onto main and force-push", id="ask-input")
            yield Static("enter to send · esc to cancel", classes="ask-hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the box: hand back the text; blank → None (nothing sent).
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
