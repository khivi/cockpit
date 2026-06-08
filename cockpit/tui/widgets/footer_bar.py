"""A two-group footer: row/workspace keys on the left, global keys on the right.

Textual's stock `Footer` renders every binding in one flat row in one colour.
This splits them by *what the key acts on* — a row action (operates on the
cursor's workspace) vs a global app action — and tints the two groups
differently, so a glance tells you which keys need a selected row. It's derived
from the app's `BINDINGS`, so a new binding only needs classifying in
`ROW_ACTIONS` (default: global), never re-listing here. Keys stay clickable via
Textual markup action links.

The `u`/update key is conditional: it only renders once an update is available
(`set_show_update(True)`), matching the header's update indicator.
"""

from __future__ import annotations

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static


class FooterBar(Horizontal):
    DEFAULT_CSS = """
    FooterBar {
        height: 1;
        dock: bottom;
        background: $panel;
    }
    FooterBar > #footer-row {
        width: 1fr;
        color: $accent;
        content-align: left middle;
        padding-left: 1;
    }
    FooterBar > #footer-global {
        width: auto;
        color: $text-muted;
        content-align: right middle;
        padding-right: 1;
    }
    """

    # Actions that operate on the selected row's workspace → left group. Anything
    # not listed (sync, update, quit, the command palette) is global → right.
    ROW_ACTIONS = frozenset(
        {
            "focus_row",
            "open_pr",
            "open_linear",
            "close_row",
            "force_close_row",
            "mute_row",
            "nudge_row",
        }
    )

    # One-word footer label per action — the BINDINGS descriptions are verbose
    # ("Sync now", "Force close") and two open_* actions would both first-word to
    # "Open". Unmapped actions fall back to the description's first word.
    LABELS = {
        "sync": "Sync",
        "focus_row": "Focus",
        "open_pr": "PR",
        "open_linear": "Linear",
        "show_output": "Output",
        "close_row": "Close",
        "force_close_row": "Force",
        "mute_row": "Mute",
        "nudge_row": "Nudge",
        "update": "Update",
        "quit": "Quit",
    }

    # Actions never shown in the footer (handled implicitly / not key-hint worthy).
    HIDDEN_ACTIONS = frozenset({"dismiss_overlay"})

    def __init__(
        self,
        bindings: Iterable[object],
        *,
        show_update: bool = False,
        show_linear: bool = True,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        # Normalize: keep only (key, action, description) triples. App BINDINGS
        # may also hold 2-tuples or Binding objects, which carry no footer hint.
        self._hints: list[tuple[str, str, str]] = [
            (str(b[0]), str(b[1]), str(b[2]))
            for b in bindings
            if isinstance(b, tuple) and len(b) >= 3
        ]
        self._show_update = show_update
        self._show_linear = show_linear
        # Last-rendered group strings, exposed for tests / introspection.
        self.row_text = ""
        self.global_text = ""

    def _label(self, action: str, desc: str) -> str:
        # A single word: the curated label, else the description's first word.
        return self.LABELS.get(action) or (desc.split()[0] if desc else action)

    def _seg(self, key: str, action: str, desc: str) -> str:
        # Clickable key (bold) + one-word label, via a Textual markup action link.
        return f"[@click=app.{action}][b]{key}[/b][/] {self._label(action, desc)}"

    def compose(self) -> ComposeResult:
        yield Static("", id="footer-row")
        yield Static("", id="footer-global")

    def on_mount(self) -> None:
        self._rebuild()

    def set_show_update(self, show: bool) -> None:
        """Reveal/hide the `u` update key (called when an update is detected)."""
        if show != self._show_update:
            self._show_update = show
            if self.is_mounted:
                self._rebuild()

    def _skip(self, action: str) -> bool:
        # Conditional keys: update only once available; Linear only when a repo
        # is Linear-configured; hidden actions (escape/back) never shown.
        if action in self.HIDDEN_ACTIONS:
            return True
        if action == "update" and not self._show_update:
            return True
        return action == "open_linear" and not self._show_linear

    def _rebuild(self) -> None:
        left: list[str] = []
        right: list[str] = []
        for key, action, desc in self._hints:
            if self._skip(action):
                continue
            target = left if action in self.ROW_ACTIONS else right
            target.append(self._seg(key, action, desc))
        # The built-in command palette has no app BINDINGS entry — surface it.
        right.append("[@click=app.command_palette][b]^p[/b][/] Palette")
        self.row_text = "   ".join(left)
        self.global_text = "   ".join(right)
        self.query_one("#footer-row", Static).update(self.row_text)
        self.query_one("#footer-global", Static).update(self.global_text)
