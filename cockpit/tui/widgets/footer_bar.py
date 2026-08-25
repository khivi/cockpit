"""A two-group footer: row/workspace keys on the left, global keys on the right.

Textual's stock `Footer` renders every binding in one flat row in one colour.
This splits them by *what the key acts on* — a row action (operates on the
cursor's workspace) vs a global app action — and tints the two groups
differently, so a glance tells you which keys need a selected row. It's derived
from the app's `BINDINGS`, so a new binding only needs classifying in
`ROW_ACTIONS` (default: global), never re-listing here. Keys stay clickable via
Textual markup action links.
"""

from __future__ import annotations

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from cockpit.tui.widgets.worktree_table import (
    EXPANDED_CAP,
    HEADER_CAP,
    HIDDEN_CAP,
    PARKED_CAP,
)


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
    # not listed (sync, quit) is global → right.
    ROW_ACTIONS = frozenset(
        {
            "focus_row",
            "open_pr",
            "open_ticket",
            "close_row",
            "force_close_row",
            "mute_row",
            "snooze_row",
            "nudge_row",
            "read_pr",
            "open_diff",
            "ask_row",
        }
    )

    # Row actions that only make sense for a row in a given state — gated on the
    # highlighted row's capability tokens (`set_row_state`). `p`/`m`/`z` act on a
    # PR; `t` opens a ticket. An action absent here has no per-row requirement (shown
    # for any row, subject to backend / `show_tickets` gating). When the row caps
    # are unknown (`None`, e.g. an empty table) nothing is capability-gated, so
    # the footer shows the full row-key legend.
    ACTION_REQUIRES = {
        "open_pr": "pr",
        "mute_row": "pr",
        "snooze_row": "pr",
        "open_ticket": "ticket",
        # `N` (nudge) reaches an *existing* workspace — it no-ops on a
        # workspace-less row, so only advertise it when one is live. `f` is NOT
        # gated: it focuses an existing workspace or spawns one first, so it's
        # meaningful on any backed row.
        "nudge_row": "workspace",
        # `r` reads the row's PR — nothing to read without one. Gated on the
        # same cached `pr-num` cell as `p`, so it costs no network to decide.
        "read_pr": "pr",
        # `d` needs a PR to diff, same cached `pr-num` cell as `p`/`r`.
        "open_diff": "pr",
        # `a` sends text to an *existing* session, exactly like `N` — it can't
        # spawn one (that's `f`), so advertise it only when one is live.
        "ask_row": "workspace",
    }

    # Explicit render order for the global (right) group — independent of BINDINGS
    # order. Actions not listed here render after these, in BINDINGS order.
    GLOBAL_ORDER = (
        # `h` acts on the cursor row's *repo* (or the hidden disclosure row), not
        # its workspace, so it stays global — a group header (where every
        # ROW_ACTION is suppressed) is exactly where you reach for it, and it's
        # the only place the hint is shown (see `_skip`). It renders first so it
        # sits against the row-key group: it's the most row-adjacent of the
        # global keys.
        "hide_repo",
        "new_workspace",
        "sync",
        "show_output",
        "quit",
    )

    # One-word footer label per action — the BINDINGS descriptions are verbose
    # ("Sync now", "Force close") and two open_* actions would both first-word to
    # "Open". Unmapped actions fall back to the description's first word.
    LABELS = {
        "sync": "Sync",
        "focus_row": "Focus",
        "open_pr": "PR",
        "open_ticket": "Ticket",
        "show_output": "Output",
        "close_row": "Close",
        "force_close_row": "Force",
        "mute_row": "Mute",
        "snooze_row": "Snooze",
        "nudge_row": "Nudge",
        "read_pr": "Read",
        "open_diff": "Diff",
        "ask_row": "Ask",
        "new_workspace": "New",
        "hide_repo": "Hide",
        "quit": "Quit",
    }

    # Row actions that stay advertised on a repo group header, where every
    # other row key is suppressed. `a` is one key whose meaning is read off the
    # cursor row (the `h` pattern): on a worktree row it asks that session, on a
    # header it asks the whole repo — so a header is exactly where it must stay
    # visible, and `_label` renames it there so the live meaning is announced.
    # It also drops its `workspace` requirement on a header: a header carries no
    # workspace cap, but the repo behind it may have several sessions.
    HEADER_ROW_ACTIONS = frozenset({"ask_row"})

    # Actions never shown in the footer (handled implicitly / not key-hint worthy).
    HIDDEN_ACTIONS = frozenset({"dismiss_overlay"})

    # Row actions that only work on one backend — rendered only when the resolved
    # backend ("cmux" | "limux" | "none") is in the action's set. `f` (focus)
    # both spawns a missing workspace and focuses an existing one; spawning works
    # on cmux AND limux (focus is the cmux-only bonus — on limux `f` spawns and
    # the user switches via limux's own UI), so it's hidden only on "none" (no
    # backend to spawn into). `N` (nudge) is a cmux-only verb.
    BACKEND_ACTIONS = {
        "focus_row": frozenset({"cmux", "limux"}),
        "nudge_row": frozenset({"cmux"}),
        # `a` rides the same cmux-only send verb as `N`. `r` is deliberately
        # absent: it shells out to `gh`, not to a workspace backend, so reading
        # a PR works on limux and on `none`.
        "ask_row": frozenset({"cmux"}),
        # `d` pipes into `cmux diff`; limux/none have no such viewer, so the
        # hint hides there and `p` is the answer instead.
        "open_diff": frozenset({"cmux"}),
    }

    def __init__(
        self,
        bindings: Iterable[object],
        *,
        show_tickets: bool = True,
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
        self._show_tickets = show_tickets
        self._backend = backend
        # The highlighted row's capability tokens (e.g. {"pr", "ticket",
        # "muted"}), or None when no row is selected — drives per-row gating of
        # the row keys and the Mute/Unmute label.
        self._row_caps: frozenset[str] | None = None
        # Last-rendered group strings, exposed for tests / introspection.
        self.row_text = ""
        self.global_text = ""

    def _label(self, action: str, desc: str) -> str:
        # Mute flips to Unmute when the highlighted row's PR is already muted, so
        # the key hint reflects what pressing `m` will actually do.
        caps = self._row_caps or frozenset()
        if action == "mute_row" and "muted" in caps:
            return "Unmute"
        # Same idea for `z`: a snoozed row's key wakes it.
        if action == "snooze_row" and "snoozed" in caps:
            return "Wake"
        # `h` is one key with three meanings, read off the cursor row — the hint
        # says which one is live (see `app.action_hide_repo`).
        # `a` on a header addresses the repo, not a session — say so.
        if action == "ask_row" and HEADER_CAP in caps:
            return "Ask repo"
        if action == "hide_repo":
            if HIDDEN_CAP in caps:
                return "Collapse" if EXPANDED_CAP in caps else "Reveal"
            if PARKED_CAP in caps:
                return "Unhide"
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

    def set_row_state(self, caps: frozenset[str] | None) -> None:
        """Set the highlighted row's capability tokens and re-render. `None` (no
        row selected) shows the full row-key legend; a set gates the row keys per
        `ACTION_REQUIRES` and drives the Mute/Unmute label."""
        if caps != self._row_caps:
            self._row_caps = caps
            if self.is_mounted:
                self._rebuild()

    def _skip(self, action: str) -> bool:
        # Conditional keys: the ticket key only when some repo has a ticket
        # provider; backend-conditional keys only on their backend; per-row keys
        # only when the highlighted row supports them; hidden actions
        # (escape/back) never shown.
        if action in self.HIDDEN_ACTIONS:
            return True
        # A repo group-header row carries no workspace, so hide every
        # row-targeted key — only the global keys stay.
        on_header = self._row_caps is not None and HEADER_CAP in self._row_caps
        if (
            action in self.ROW_ACTIONS
            and on_header
            and action not in self.HEADER_ROW_ACTIONS
        ):
            return True
        # `h` parks the cursor row's whole *repo*, so it's only advertised on a
        # row that reads as a repo: a group header, the `▸ N hidden` disclosure
        # row, or a revealed parked repo — all three carry HEADER_CAP, and all
        # three are where `h`'s Hide/Reveal/Collapse/Unhide labels are
        # unambiguous. On a worktree row "Hide" would read as "hide this row"
        # while actually parking every sibling row with it. The binding itself
        # stays live everywhere (`action_hide_repo` resolves the repo from any of
        # its rows) — only the hint follows the row, exactly like `p`/`t`/`m`/`z`.
        if (
            action == "hide_repo"
            and self._row_caps is not None
            and HEADER_CAP not in self._row_caps
        ):
            return True
        if action == "open_ticket" and not self._show_tickets:
            return True
        allowed = self.BACKEND_ACTIONS.get(action)
        if allowed is not None and self._backend not in allowed:
            return True
        # A `use_worktree: false` primary checkout on its default branch (the
        # "primary" cap) can't be removed as a worktree and keeps its branch, so
        # `c`/`C` reduce to a workspace-only close — pointless with no workspace.
        # Hide them there. Feature rows keep `c` (it removes the worktree), and a
        # primary checkout parked on a *feature* branch lacks the "primary" cap
        # (see `row_capabilities`) so it keeps `c` too — that close tears the
        # branch down.
        if (
            action in ("close_row", "force_close_row")
            and self._row_caps is not None
            and "primary" in self._row_caps
            and "workspace" not in self._row_caps
        ):
            return True
        # Per-row gating: when row caps are known, hide a row key whose required
        # capability the highlighted row lacks. Unknown caps (None) → no gating.
        if self._row_caps is not None:
            req = self.ACTION_REQUIRES.get(action)
            # A HEADER_ROW_ACTION drops its per-row requirement on a header: `a`
            # needs a live `workspace` on a worktree row, but a header carries no
            # workspace cap while the repo behind it may have several sessions.
            if on_header and action in self.HEADER_ROW_ACTIONS:
                req = None
            if req is not None and req not in self._row_caps:
                return True
        return False

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
        self.row_text = "   ".join(left)
        self.global_text = "   ".join(right_segs)
        self.query_one("#footer-row", Static).update(self.row_text)
        self.query_one("#footer-global", Static).update(self.global_text)
