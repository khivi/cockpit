"""Modal asking which of several repos a ticket should be started in.

The one place cockpit *asks* rather than routes. `spawn.route_ticket_repos`
runs both stages — the free key match and the paid `narrow_repos` tiebreak —
and when several repos survive there is nothing left to derive from: the many-
repos-one-team shape (every member declaring the same `tickets.keys`) is a
legitimate config, and a ticket spanning two of them is legitimate work.

Before this, the inbox refused that ticket and named `n` as the way out, which
meant reading the ref off the list, closing the modal, and retyping it into a
second one. The refusal is kept for the case it was right about — *no* repo
claims the ticket, where there is no candidate to offer.

Deliberately not `NewWorkspaceScreen`: that screen's job is a typed source
routed to a repo, and its dismiss value is a repo *path* (which becomes the
spawn cwd). Here the source is already known and the answer travels as an
explicit `--repo <name>`, so an editable source line would be a second thing to
get wrong in a modal that exists to answer one question.

Like every other screen it writes nothing: it dismisses with a repo name and the
app shells out to `cockpit new`.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Select, Static


class RepoPickScreen(ModalScreen["str | None"]):
    """Repo chooser for an ambiguous ticket. Dismisses with a name, or None."""

    DEFAULT_CSS = """
    RepoPickScreen { align: center middle; }
    RepoPickScreen > Vertical {
        width: 80%;
        max-width: 70;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    RepoPickScreen .rp-title { text-style: bold; color: $accent; }
    RepoPickScreen .rp-hint { color: $text-muted; }
    RepoPickScreen Select { margin: 1 0; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, ref: str, repos: list[str]) -> None:
        super().__init__()
        self._ref = ref
        # Config order, which is the order every other repo list in the TUI uses.
        self._repos = list(repos)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._ref, classes="rp-title")
            yield Static(
                f"{len(self._repos)} repos claim this ticket. Which one?",
                classes="rp-hint",
            )
            # Starts blank, with nothing pre-selected. Two reasons: a seeded
            # `Select` posts `Changed` as it mounts, which the handler below
            # would read as the user's answer and dismiss on; and the obvious
            # default — the repo the daemon's cwd sits in — is precisely the
            # fallback that cut two worktrees off `dotfiles` and that naming the
            # repo explicitly exists to kill.
            yield Select(
                [(name, name) for name in self._repos],
                allow_blank=True,
                id="rp-select",
            )
            yield Static("enter to start · esc to cancel", classes="rp-hint")

    def on_mount(self) -> None:
        self.query_one("#rp-select", Select).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        # Textual's Select commits on selection, so that *is* the enter gesture —
        # there is no second field to move on to and nothing to confirm.
        #
        # `Select.NULL`, not `Select.BLANK`: the latter is a plain `False` in
        # Textual 8.x, so an `is not` against it is true for the unselected
        # sentinel too and would dismiss with the string "Select.NULL".
        if event.value is not Select.NULL:
            self.dismiss(str(event.value))

    def action_cancel(self) -> None:
        self.dismiss(None)
