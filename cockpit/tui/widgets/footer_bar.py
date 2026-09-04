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
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from cockpit.tui.widgets.worktree_table import (
    EXPANDED_CAP,
    FOLD_CAP,
    HEADER_CAP,
    HIDDEN_CAP,
    PARKED_CAP,
    SNOOZED_CAP,
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
    # not listed (quit) is global → right.
    ROW_ACTIONS = frozenset(
        {
            "focus_row",
            "open_pr",
            "open_ticket",
            "close_row",
            "force_close_row",
            "mute_row",
            "snooze_row",
            "ask_row",
            "ask_snoozed",
        }
    )

    # Row actions that only make sense for a row in a given state — gated on the
    # highlighted row's capability tokens (`set_row_state`). `p`/`m`/`z` act on a
    # PR; `t` opens a ticket. An action absent here has no per-row requirement (shown
    # for any row, subject to backend / `show_tickets` gating). When the row caps
    # are unknown (`None`, e.g. an empty table) nothing is capability-gated, so
    # the footer shows the full row-key legend.
    ACTION_REQUIRES: ClassVar[dict[str, str]] = {
        "open_pr": "pr",
        "mute_row": "pr",
        "snooze_row": "pr",
        "open_ticket": "ticket",
        # `a` sends text to an *existing* session — it can't spawn one (that's
        # `f`), so advertise it only when one is live. On a repo header it
        # addresses the repo instead and this requirement is dropped.
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
        "quit",
    )

    # One-word footer label per action — the BINDINGS descriptions are verbose
    # ("Force close") and two open_* actions would both first-word to "Open".
    # Unmapped actions fall back to the description's first word.
    LABELS: ClassVar[dict[str, str]] = {
        "focus_row": "Focus",
        "open_pr": "PR",
        "open_ticket": "Ticket",
        "close_row": "Close",
        "force_close_row": "Force",
        "mute_row": "Mute",
        "snooze_row": "Snooze",
        "ask_row": "Ask",
        "ask_snoozed": "Ask snoozed",
        "new_workspace": "New",
        "hide_repo": "Hide",
        "sync": "Sync",
        "quit": "Quit",
    }

    # `c` and `C` share one footer slot, so they share one tooltip — hovering
    # either letter has to explain both, since neither is described anywhere
    # else on screen.
    _CLOSE_TOOLTIP = (
        "c closes the workspace and removes the worktree; C additionally "
        "overrides the refusal on a PR that is still open.\n"
        "Neither ever discards uncommitted changes or unlanded commits — those "
        "refuse both ways, naming what is in the way."
    )

    # Hover text per action: the sentence the one-word label can't carry.
    # Keyed by action, matched off the hovered segment's `@click` meta
    # (`on_mouse_move`), so a segment's whole width — key and label — explains
    # itself. An action missing here simply has no tooltip.
    TOOLTIPS: ClassVar[dict[str, str]] = {
        "focus_row": (
            "Go to this row's workspace, spawning one first if it doesn't have "
            "one yet."
        ),
        "open_pr": "Open this row's pull request on GitHub in your browser.",
        "open_ticket": (
            "Open the ticket this PR delivers — Linear, Jira, GitHub issue or "
            "Trello card, whichever the repo tracks — in your browser."
        ),
        "ask_row": (
            "Send one line to this row's Claude session. A session that is "
            "mid-turn or waiting on a permission prompt refuses it, and your "
            "text is kept.\n"
            "On a repo header it goes to every session in that repo."
        ),
        "ask_snoozed": (
            "Send one line to every session in this repo's snoozed section, "
            "without expanding it first.\n"
            "Snoozing silences the automatic nudge, never a message you type — "
            "and the rows stay snoozed afterwards."
        ),
        "close_row": _CLOSE_TOOLTIP,
        "force_close_row": _CLOSE_TOOLTIP,
        "mute_row": (
            "Stop nudging me about this PR, indefinitely. Press again to unmute."
        ),
        "snooze_row": (
            "Not my turn: fold this PR into the repo's snoozed section and go "
            "quiet.\n"
            "It wakes itself on review activity or on new trouble like CI going "
            "red — there is no timer."
        ),
        "hide_repo": (
            "Park this repo: it stops polling GitHub, its rows fold into "
            "'▸ N repos hidden', and its idle workspaces close.\n"
            "Nothing is torn down — no worktree removed, no branch deleted."
        ),
        "new_workspace": (
            "Start new work. A branch name, PR number, ticket id, or Slack link "
            "becomes a worktree with a workspace on it."
        ),
        "sync": (
            "Reconcile every repo now instead of waiting for the tick — the "
            "same full cycle the timer runs, never scoped to one repo."
        ),
        "quit": "Quit the dashboard. The reconcile daemon stops with it.",
    }

    # Row actions that stay advertised on a repo group header, where every
    # other row key is suppressed. `a` is one key whose meaning is read off the
    # cursor row (the `h` pattern): on a worktree row it asks that session, on a
    # header it asks the whole repo — so a header is exactly where it must stay
    # visible, and `_label` renames it there so the live meaning is announced.
    # It also drops its `workspace` requirement on a header: a header carries no
    # workspace cap, but the repo behind it may have several sessions. `A` is
    # here for the same reason and keeps its own FOLD_CAP rule below: a header
    # is a repo-level row, and asking the repo's snoozed pile is a repo-level
    # gesture — it must not require scrolling to the disclosure row first.
    HEADER_ROW_ACTIONS = frozenset({"ask_row", "ask_snoozed"})

    # Row actions that stay advertised on a repo's `▸ N snoozed` disclosure row,
    # where every other row key is suppressed. `z` opens and shuts the fold; `A`
    # asks every session in it, and this is the row it exists for — reaching the
    # pile without unfolding it first is the whole point, so the collapsed row
    # must advertise it. Both act on the fold itself, not on a workspace, which
    # is why neither needs the `workspace` cap the row cannot carry.
    SNOOZED_ROW_ACTIONS = frozenset({"snooze_row", "ask_snoozed"})

    # Actions never shown in the footer (handled implicitly / not key-hint worthy).
    HIDDEN_ACTIONS = frozenset({"dismiss_overlay"})

    # Row actions that only work on one backend — rendered only when the resolved
    # backend ("cmux" | "limux" | "none") is in the action's set. `f` (focus)
    # both spawns a missing workspace and focuses an existing one; spawning works
    # on cmux AND limux (focus is the cmux-only bonus — on limux `f` spawns and
    # the user switches via limux's own UI), so it's hidden only on "none" (no
    # backend to spawn into).
    BACKEND_ACTIONS: ClassVar[dict[str, frozenset[str]]] = {
        "focus_row": frozenset({"cmux", "limux"}),
        # `a` delivers through cmux's `send`, which limux has no equivalent for.
        "ask_row": frozenset({"cmux"}),
        "ask_snoozed": frozenset({"cmux"}),
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
        if action == "snooze_row":
            # `z` is one key with three meanings, read off the cursor row (like
            # `h`): on a repo's `▸ N snoozed` disclosure row it opens/shuts the
            # fold, on a snoozed row it wakes it, elsewhere it snoozes.
            if SNOOZED_CAP in caps:
                return "Collapse" if EXPANDED_CAP in caps else "Expand"
            if "snoozed" in caps:
                return "Wake"
        # `h` is one key with three meanings, read off the cursor row — the hint
        # says which one is live (see `app.action_hide_repo`).
        # `a` on a header addresses the repo, not a session — say so.
        if action == "ask_row" and HEADER_CAP in caps and HIDDEN_CAP not in caps:
            return "Ask repo"
        if action == "hide_repo":
            if HIDDEN_CAP in caps:
                return "Collapse" if EXPANDED_CAP in caps else "Reveal"
            if PARKED_CAP in caps:
                return "Unhide"
        return self.LABELS.get(action) or (desc.split()[0] if desc else action)

    def _seg(self, key: str, action: str, desc: str) -> str:
        # Clickable key (bold) + one-word label, via a Textual markup action
        # link. The link spans the label too, not just the key: it carries the
        # `@click` meta `on_mouse_move` reads, so wrapping the whole segment is
        # what makes the *label* hoverable — and pointing at the word is what
        # anyone hunting for an explanation does.
        return f"[@click=app.{action}][b]{key}[/b] {self._label(action, desc)}[/]"

    def _close_seg(self, close_key: str, force_key: str | None) -> str:
        # `c/C Close`: close and force-close share one footer slot. Each letter
        # stays independently clickable (`c` → close, `C` → force). `force_close_row`
        # is folded in here rather than rendered as its own segment. The shared
        # label links back to `close_row`, whose tooltip describes both.
        close_link = f"[@click=app.close_row][b]{close_key}[/b][/]"
        label = f"[@click=app.close_row]{self._label('close_row', 'Close')}[/]"
        if force_key is None:
            return f"{close_link} {label}"
        force_link = f"[@click=app.force_close_row][b]{force_key}[/b][/]"
        return f"{close_link}/{force_link} {label}"

    def compose(self) -> ComposeResult:
        yield Static("", id="footer-row")
        yield Static("", id="footer-global")

    def on_mount(self) -> None:
        self._rebuild()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Explain whichever key the pointer is over.

        The hints are two `Static`s of concatenated markup, not a widget per
        key, so there is nothing per-key to hang a tooltip on — but every
        segment already carries an action link, and Textual puts that action in
        the hovered cell's style meta. So the segment under the pointer names
        itself, and this sets the bar's own tooltip to match; the children carry
        none, so the ancestor walk lands here. A gap between segments has no
        meta and clears it."""
        action = str(event.style.meta.get("@click", "")).removeprefix("app.")
        tooltip = self.TOOLTIPS.get(action)
        if tooltip != self.tooltip:
            self.tooltip = tooltip

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
        # `A` asks a whole snoozed fold, so it is advertised only on a row that
        # stands for one — the repo group header or the `▸ N snoozed` disclosure
        # row, both of which carry FOLD_CAP, and only when the repo actually has
        # a pile. On a worktree row it would read as "ask this row" while
        # addressing every snoozed sibling, the same misreading that keeps `h`
        # off worktree rows; inside an opened fold the disclosure row sits right
        # above, which is where a fold-level key belongs.
        #
        # Unknown caps (None, the empty first-run table) HIDE it, unlike every
        # other row key. The full-legend default exists because a key that acts
        # on the cursor row is still meaningful before one is selected; this key
        # names a *section* that may not exist at all, and advertising a fold
        # nobody has is worse than saying nothing.
        if action == "ask_snoozed" and FOLD_CAP not in (self._row_caps or frozenset()):
            return True
        # A repo's `▸ N snoozed` disclosure row: `z` opens/shuts the fold and `A`
        # asks every session in it, which is the one row where that key needs no
        # unfolding first. No other row key has anything to act on (the row
        # carries no workspace). The global keys stay, exactly as on a group
        # header. `h` needs no rule of its own — the row carries no HEADER_CAP,
        # so the branch below already hides it, which is right: parking the whole
        # repo from a row standing for one *section* of it would read as folding,
        # not parking.
        #
        # A permitted key falls THROUGH rather than returning False: the checks
        # below still apply to it, and `A` is backend-gated (cmux `send`), so an
        # early "allowed" here would advertise it on limux.
        on_fold_row = self._row_caps is not None and SNOOZED_CAP in self._row_caps
        if (
            action in self.ROW_ACTIONS
            and on_fold_row
            and action not in self.SNOOZED_ROW_ACTIONS
        ):
            return True
        # A repo group-header row carries no workspace, so hide every
        # row-targeted key — only the global keys stay.
        on_header = self._row_caps is not None and HEADER_CAP in self._row_caps
        # HEADER_CAP covers three row kinds; only two of them name a repo. The
        # `▸ N repos hidden` disclosure row sets caps but no `_row_repo`, so
        # `current_repo_name()` is None there and a repo-scoped action cannot
        # resolve a target — worse, in a single-repo config
        # `_repo_config_by_name`'s sole-repo fallback would silently pick a repo
        # this row does not name. So it is not a repo row for these purposes.
        on_repo_row = on_header and HIDDEN_CAP not in (self._row_caps or frozenset())
        if (
            action in self.ROW_ACTIONS
            and not (on_repo_row and action in self.HEADER_ROW_ACTIONS)
            and on_header
        ):
            return True
        # `h` parks the cursor row's whole *repo*, so it's only advertised on a
        # row that reads as a repo: a group header, the `▸ N repos hidden` disclosure
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
            if on_repo_row and action in self.HEADER_ROW_ACTIONS:
                req = None
            # Same drop on the fold row, and it is what keeps `z` visible there:
            # the row carries no `pr` cap, so `snooze_row`'s requirement would
            # gate away the one key that opens the pile.
            if on_fold_row and action in self.SNOOZED_ROW_ACTIONS:
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
