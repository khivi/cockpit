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
            "open_workspace",
            "open_pr",
            "open_linear",
            "show_repo_config",
            "close_row",
            "force_close_row",
            "mute_row",
            "nudge_row",
        }
    )

    # Explicit render order for the global (right) group — independent of BINDINGS
    # order. Actions not listed here render after these, in BINDINGS order. The
    # command palette is always appended last (it has no BINDINGS entry).
    GLOBAL_ORDER = ("new_workspace", "sync", "show_output", "update", "quit")

    # One-word footer label per action — the BINDINGS descriptions are verbose
    # ("Sync now", "Force close") and two open_* actions would both first-word to
    # "Open". Unmapped actions fall back to the description's first word.
    LABELS = {
        "sync": "Sync",
        "focus_row": "Focus",
        "open_workspace": "Open",
        "open_pr": "PR",
        "open_linear": "Linear",
        "show_repo_config": "Repo",
        "show_output": "Output",
        "close_row": "Close",
        "force_close_row": "Force",
        "mute_row": "Mute",
        "nudge_row": "Nudge",
        "new_workspace": "New",
        "update": "Update",
        "quit": "Quit",
    }

    # Actions never shown in the footer (handled implicitly / not key-hint worthy).
    HIDDEN_ACTIONS = frozenset({"dismiss_overlay"})

    # Row actions that only work on one backend — rendered only when the resolved
    # backend ("cmux" | "limux" | "none") is in the action's set. focus/nudge are
    # cmux-only verbs; open_workspace is limux's only way to reach a workspace
    # (redundant with focus on cmux).
    BACKEND_ACTIONS = {
        "focus_row": frozenset({"cmux"}),
        "nudge_row": frozenset({"cmux"}),
        "open_workspace": frozenset({"limux"}),
    }

    def __init__(
        self,
        bindings: Iterable[object],
        *,
        show_update: bool = False,
        show_linear: bool = True,
        backend: str,
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
        self._backend = backend
        # Last-rendered group strings, exposed for tests / introspection.
        self.row_text = ""
        self.global_text = ""

    def _label(self, action: str, desc: str) -> str:
        return self.LABELS.get(action) or (desc.split()[0] if desc else action)

    def _seg(self, key: str, action: str, desc: str) -> str:
        # Clickable key (bold) + one-word label, via a Textual markup action link.
        return f"[@click=app.{action}][b]{key}[/b][/] {self._label(action, desc)}"

    def _close_seg(self, close_key: str, force_key: str | None) -> str:
        # `c/C Close`: close and force-close share one footer slot. Each letter
        # stays independently clickable (`c` → close, `C` → force). `force_close_row`
        # is folded in here rather than rendered as its own segment.
        close_link = f"[@click=app.close_row][b]{close_key}[/b][/]"
        label = self._label("close_row", "Close")
        if force_key is None:
            return f"{close_link} {label}"
        force_link = f"[@click=app.force_close_row][b]{force_key}[/b][/]"
        return f"{close_link}/{force_link} {label}"

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
        # is Linear-configured; backend-conditional keys only on their backend;
        # hidden actions (escape/back) never shown.
        if action in self.HIDDEN_ACTIONS:
            return True
        if action == "update" and not self._show_update:
            return True
        if action == "open_linear" and not self._show_linear:
            return True
        allowed = self.BACKEND_ACTIONS.get(action)
        return allowed is not None and self._backend not in allowed

    def _rebuild(self) -> None:
        left: list[str] = []
        # (order, insertion-index, seg) — the global group renders in GLOBAL_ORDER,
        # not BINDINGS order; insertion index keeps unlisted actions stable.
        right: list[tuple[int, int, str]] = []
        key_by_action = {action: key for key, action, _ in self._hints}
        for key, action, desc in self._hints:
            if self._skip(action):
                continue
            if action == "force_close_row":
                continue  # folded into the close_row segment as `c/C`
            if action == "close_row":
                seg = self._close_seg(key, key_by_action.get("force_close_row"))
            else:
                seg = self._seg(key, action, desc)
            if action in self.ROW_ACTIONS:
                left.append(seg)
            else:
                order = (
                    self.GLOBAL_ORDER.index(action)
                    if action in self.GLOBAL_ORDER
                    else len(self.GLOBAL_ORDER)
                )
                right.append((order, len(right), seg))
        right.sort()
        right_segs = [seg for _, _, seg in right]
        # The built-in command palette has no app BINDINGS entry — surface it last.
        right_segs.append("[@click=app.command_palette][b]^p[/b][/] Palette")
        self.row_text = "   ".join(left)
        self.global_text = "   ".join(right_segs)
        self.query_one("#footer-row", Static).update(self.row_text)
        self.query_one("#footer-global", Static).update(self.global_text)
