r"""Modal text box for sending a line to a row's Claude session.

The app's `a` action pushes this screen; it dismisses with an `(outcome, text)`
pair (see the class docstring) whose `send` case the app hands to
`cmux.nudge_if_idle` — the same gated path `cockpit broadcast` uses, so a
mid-turn or permission-pending session refuses the message rather than having
it typed into a y/n prompt.

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

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

# Offered as the box's opening text when `d` comments are waiting and no draft
# is. `_send_ask` appends them as ` · diff comments: file:line — remark`, so the
# wording deliberately doesn't repeat that label.
COMMENTS_LEAD = "Address the following"


class AskScreen(ModalScreen["tuple[str, str]"]):
    """A one-line prompt. Dismisses with `(outcome, text)`.

    Three outcomes, deliberately distinct — collapsing any two of them loses a
    real user intention:

    - `("send", text)`   Enter with text. Deliver it.
    - `("clear", "")`    Enter on an emptied box. The user deleted a draft they
                         no longer want; the app drops it rather than restoring
                         it on the next `a`.
    - `("cancel", text)` Escape. Stepping away to check something must not cost
                         what you typed, so the *current* text is handed back to
                         be stashed — not just a previously-refused draft.

    An earlier version dismissed with `None` for both escape and a blank submit,
    which made those two indistinguishable: a deliberately-cleared draft came
    back on the next `a`, and escaping discarded anything typed in this modal.
    """

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

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, target: str = "", initial: str = "", comments: int = 0) -> None:
        super().__init__()
        self._target = target
        # A draft the previous send couldn't deliver (the session was mid-turn).
        # Restored so a refusal never costs you what you typed — see
        # `app._send_ask`.
        self._initial = initial
        # How many `d` diff-viewer comments will ride this message
        # (`lib.diff_comments`). Announced rather than silently appended: the
        # send is one line and you should know what is in it before pressing
        # enter.
        self._comments = comments

    def _prefill(self) -> str:
        """The lead-in this modal offered, or "" if it offered none.

        Comments only travel on a message, so a pending set still needs a line
        to ride — and inventing one for remarks that already say everything is
        exactly the friction that leaves them sitting undelivered. Offering the
        line makes it `a`-then-enter while keeping the send a typed gesture: the
        text is editable, and an emptied box still means *drop the draft*.

        A restored draft always wins — it is what a previous send couldn't
        deliver, and overwriting it would cost the user the very text this modal
        exists to preserve.
        """
        return "" if self._initial or not self._comments else COMMENTS_LEAD

    def compose(self) -> ComposeResult:
        title = f"Ask {self._target}" if self._target else "Ask"
        with VerticalScroll():
            yield Static(title, classes="ask-title")
            yield Static(
                "Sent to this row's Claude session as a prompt. One line — "
                "a newline would submit early.",
                classes="ask-hint",
            )
            if self._comments:
                n = self._comments
                yield Static(
                    f"{n} diff review comment{'s' if n > 1 else ''} "
                    "will be included",
                    id="ask-comments",
                    classes="ask-hint",
                )
            yield Input(
                value=self._initial or self._prefill(),
                placeholder="rebase onto main and force-push",
                id="ask-input",
            )
            # Filled in asynchronously by `app._ask_state_hint` once cmux has
            # been read: blank until then, so the modal opens instantly rather
            # than waiting on a subprocess.
            yield Static("", id="ask-state", classes="ask-hint")
            yield Static("enter to send · esc to cancel", classes="ask-hint")

    def set_state_hint(self, message: str, *, warn: bool = False) -> None:
        """Show the row session's live at-rest state above the footer hint.

        Advisory only — `nudge_if_idle` re-checks at send time and remains the
        authority. It must NOT block submitting: a turn that ends while you are
        still typing would have accepted the message, so a stale "mid-turn"
        warning must never be upgraded into a refusal here.
        """
        node = self.query_one("#ask-state", Static)
        node.update(f"[b]{message}[/b]" if warn else message)

    def on_mount(self) -> None:
        inp = self.query_one(Input)
        inp.focus()
        # Restored draft or offered lead-in: cursor to the end so you can keep
        # typing, not overtype.
        if inp.value:
            inp.action_end()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.dismiss(("send", text) if text else ("clear", ""))

    def action_cancel(self) -> None:
        # Hand the typed text back so the app can stash it — escape is "hold
        # this thought", not "throw it away". An untouched lead-in is not a
        # thought the user had, and stashing it would outlive the comments that
        # prompted it: the next `a` on a row with nothing pending would restore
        # "Address the following" as a draft referring to nothing.
        text = self.query_one(Input).value.strip()
        self.dismiss(("cancel", "" if text == self._prefill() else text))
