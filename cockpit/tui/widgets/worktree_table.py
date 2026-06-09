"""Navigable worktree table — a DataTable with a row cursor (arrow keys).

Strictly a renderer: it only *reads* the same flat cache cells starship reads
(`pr-*` by branch) plus the per-PR JSON for Linear. It never writes a cell,
preserving the daemon-is-sole-writer invariant. Rows are keyed by worktree path
so the app's `f`/`c` keybindings can resolve the cursor row (`current_path`)
back to its workspace for focus / close.

Repos are distinguished by colour, not a column: the workspace name is tinted
with the repo's `sidebar_color` via the same `CMUX_COLOR_ANSI` colorizer cmux
uses, so the table and the cmux sidebar agree. The Author column (right after
PR) shows the PR author's login prefixed with `@`, populated by the daemon only
for other-authored PRs (coworker / review PRs) and blank for my own. The Dirty
column (headed with the
`✎` modifications glyph rather than the word "Dirty") reads the same
daemon-written `git-status` cell the footer does (`●S ✎M ✚U`). The Ticket and
Status columns are added only when some configured repo is Linear-enabled
(`show_linear`); Ticket shows the delivered Linear ticket id(s) and Status shows
one workflow-state *icon* per ticket (headed with the `📍` glyph rather than the
word "Status", mapped from the state name via `_linear_status_icon`), both from
the cached per-PR block, with Ticket placed right after Author and Status right
after the PR-state column (`🔀`) so the two status columns are adjacent.

A muted PR (nudges silenced via `m` / `/cockpit:nudge`) prefixes its workspace
name with the 🔇 glyph, read from the daemon-written `pr-muted` cell — the same
snapshot starship reads, so the table never diverges from the sidebar.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from cockpit.lib.cache import branch_cache, cwd_cache, find_pr_payload, read_text
from cockpit.lib.cmux import DEVDONE_ICON
from cockpit.lib.colors import CMUX_COLOR_ANSI
from cockpit.lib.git import Worktree
from cockpit.lib.starship import (
    _PR_STATE_ICON,
    ICON_PR_MUTED,
    ICON_STAGED,
    ICON_UNSTAGED,
    ICON_UNTRACKED,
)

# Header glyph for the PR-state column (was the word "Approval"). The merge
# arrows read as "pull-request / review verdict" and collide with none of the
# value icons.
_APPROVAL_ICON = "🔀"

# Header glyph for the Linear workflow-state column (was the word "Status"). The
# pin reads as "pipeline position" and collides with none of the value icons
# below. Sits right after `_APPROVAL_ICON` so the two status columns are
# adjacent.
_STATUS_ICON = "📍"

# Linear workflow-state *name* (case-insensitive substring) → (icon, style).
# Matched top-to-bottom so the more specific names win over their bare
# fallbacks ("dev done" before "done", "in review" before a bare match). State
# names are arbitrary per team, so this is a heuristic over Linear's common
# vocabulary — the same name-substring approach `_linear_cells` already uses for
# the status colour. An unrecognised state falls back to a neutral ◎.
#
# These deliberately share NO glyph with the adjacent PR-state column
# (`_STATE` / `_PR_STATE_ICON`): a "workflow position" family (squares + tools)
# rather than PR's "review verdict" family (circles + checks). Without this the
# two columns — now side by side — were indistinguishable (both used 🔵/👀/✅/⛔).
_LINEAR_STATUS_ICONS: tuple[tuple[str, str, str], ...] = (
    ("cancel", "🚫", "red"),
    ("duplicate", "🚫", "red"),
    ("dev done", DEVDONE_ICON, "green"),
    ("review", "🔍", "yellow"),
    ("progress", "🚧", "cyan"),
    ("doing", "🚧", "cyan"),
    ("started", "🚧", "cyan"),
    ("done", "🟢", "green"),
    ("complete", "🟢", "green"),
    ("ship", "🟢", "green"),
    ("deploy", "🟢", "green"),
    ("backlog", "📋", "grey50"),
    ("triage", "🩺", "grey50"),
    ("todo", "⬜", "grey50"),
    ("to do", "⬜", "grey50"),
)
_LINEAR_STATUS_FALLBACK = ("◎", "white")


def _linear_status_icon(state: str) -> tuple[str, str]:
    """Map a Linear workflow-state name to a `(icon, style)` pair via the ordered
    `_LINEAR_STATUS_ICONS` substring table, falling back to a neutral ◎."""
    low = state.lower()
    for needle, icon, style in _LINEAR_STATUS_ICONS:
        if needle in low:
            return icon, style
    return _LINEAR_STATUS_FALLBACK


# (repo display name, sidebar_color, linear-enabled, worktrees)
Inventory = list[tuple[str, str | None, bool, list[Worktree]]]

# Raw `pr-state` enum → (icon shown in the PR-state column, style). The icons
# reuse the sidebar's `_PR_STATE_ICON` vocabulary (single source of truth) so the
# table and the statusline never disagree; the style is kept for the few terminals
# that tint emoji and to drive colour assertions in tests.
_STATE = {
    "APPROVED": (_PR_STATE_ICON["APPROVED"], "green"),
    "OPEN": (_PR_STATE_ICON["OPEN"], "cyan"),
    "DRAFT": (_PR_STATE_ICON["DRAFT"], "grey50"),
    "REVIEW_REQUIRED": (_PR_STATE_ICON["REVIEW_REQUIRED"], "yellow"),
    "CHANGES_REQUESTED": (_PR_STATE_ICON["CHANGES_REQUESTED"], "red"),
    "MERGED": (_PR_STATE_ICON["MERGED"], "magenta"),
    "CLOSED": (_PR_STATE_ICON["CLOSED"], "red"),
}
_CI_STYLE = {"✓": "green", "✗": "red", "•": "yellow", "?": "grey50"}

# The Dirty column header is the modifications glyph (matching its cell content)
# rather than the word "Dirty".
_DIRTY_ICON = ICON_UNSTAGED


def column_labels(*, show_linear: bool) -> tuple[str, ...]:
    """Column headers in display order. The `Author` column sits right after
    `PR` (always present — blank for self-authored PRs, the coworker login for
    a review PR). The Linear `Ticket` column follows it; the Linear `Status`
    column sits right after the PR-state column so the two status columns are
    adjacent. Both Linear columns appear only when some configured repo is
    Linear-enabled (`show_linear`)."""
    cols = ["Workspace", "PR", "Author"]
    if show_linear:
        cols.append("Ticket")
    cols.append(_APPROVAL_ICON)
    if show_linear:
        cols.append(_STATUS_ICON)
    cols += ["CI", "💬", _DIRTY_ICON, "Title"]
    return tuple(cols)


def _workspace_cell(wt: Worktree, repo_color: str | None, *, muted: bool) -> Text:
    """The workspace name, tinted with the repo's cmux colour when set and
    prefixed with the 🔇 glyph when the PR's nudges are muted."""
    label = wt.label or wt.short
    colorizer = CMUX_COLOR_ANSI.get(repo_color or "")
    if colorizer is not None:
        # Reuse the exact cmux colorizer (the source of truth) → parse its ANSI.
        cell = Text.from_ansi(colorizer(label))
    else:
        cell = Text(label, style="bold")
    if muted:
        return Text.assemble((f"{ICON_PR_MUTED} ", "yellow"), cell)
    return cell


def _dirty_cell(wt: Worktree) -> Text:
    """Working-tree dirtiness from the daemon-written `git-status` cell
    (`"<staged> <unstaged> <untracked>"`), rendered as `●S ✎M ✚U` with the
    same glyphs and colours the footer's `print_worktree_status` uses. Blank
    when the tree is clean (or the cell isn't populated yet)."""
    parts = read_text(cwd_cache("git-status", wt.path)).split()
    if len(parts) != 3:
        return Text("")
    try:
        staged, unstaged, untracked = (int(p) for p in parts)
    except ValueError:
        return Text("")
    segs = []
    if staged:
        segs.append(Text(f"{ICON_STAGED}{staged}", style="green"))
    if unstaged:
        segs.append(Text(f"{ICON_UNSTAGED}{unstaged}", style="yellow"))
    if untracked:
        segs.append(Text(f"{ICON_UNTRACKED}{untracked}", style="grey50"))
    return Text(" ").join(segs) if segs else Text("")


def _comments_cell(unaddressed_raw: str, total_raw: str) -> Text:
    """The 💬 column: unaddressed review-thread count, with a `/total` denominator
    when there are addressed threads too.

    Reads the daemon-written `pr-comments` (unaddressed) and `pr-comments-total`
    (threads opened by others) cells. Renders:
      - blank when nothing is unaddressed — the column reads as "needs my
        attention", and zero-unaddressed is the happy path even if past threads
        exist;
      - `N` (red) when every thread from others is still unaddressed (the
        denominator would add no information);
      - `N/T` (red) when `T` threads exist and `N < T` are unaddressed, so the
        ratio signals "a few new threads among many already handled".
    """
    try:
        unaddressed = int(unaddressed_raw or 0)
        total = int(total_raw or 0)
    except ValueError:
        return Text("")
    if unaddressed <= 0:
        return Text("")
    label = f"{unaddressed}/{total}" if total > unaddressed else str(unaddressed)
    return Text(label, style="red")


def _linear_cells(wt: Worktree, repo_name: str) -> tuple[Text, Text]:
    """Delivered Linear ticket id(s) and workflow state(s) from the cached per-PR
    block, as two cells. The Ticket cell is the comma-joined id(s); the Status
    cell is one workflow-state *icon* per ticket (space-joined), each tinted by
    its own `_linear_status_icon` style. Both blank when there are no delivered
    tickets."""
    payload = find_pr_payload(wt.branch, repo_name) or {}
    tickets = (payload.get("linear") or {}).get("tickets") or []
    if not tickets:
        return Text(""), Text("")
    ids = ", ".join(str(t.get("id", "?")) for t in tickets)
    icons = []
    for t in tickets:
        icon, style = _linear_status_icon(str(t.get("state", "")))
        icons.append(Text(icon, style=style))
    return Text(ids, style="magenta"), Text(" ").join(icons)


def worktree_cells(
    wt: Worktree,
    repo_name: str,
    repo_color: str | None,
    linear_enabled: bool,
    *,
    show_linear: bool,
) -> list[Text]:
    """Build one row's cells (Rich Text, so colours survive), in `column_labels`
    order: the Ticket cell follows Author and the Status cell follows the
    PR-state cell, both present only when `show_linear` (the columns exist) and
    blank for a row whose repo isn't Linear-enabled."""

    def cell(stem: str) -> str:
        return read_text(branch_cache(stem, wt.branch))

    num, state, ci = cell("pr-num"), cell("pr-state"), cell("pr-checks")
    comments = _comments_cell(cell("pr-comments"), cell("pr-comments-total"))
    title = cell("pr-title")
    author = cell("pr-author")
    state_icon, style = _STATE.get(state, (state, "white"))
    ticket, ticket_status = (
        _linear_cells(wt, repo_name) if linear_enabled else (Text(""), Text(""))
    )

    cells = [
        _workspace_cell(wt, repo_color, muted=bool(cell("pr-muted"))),
        Text(f"#{num}") if num else Text(""),
        # Author is populated by the daemon only for other-authored (coworker /
        # review) PRs — blank for my own, so the column reads "whose PR is this
        # that isn't mine".
        Text(f"@{author}", style="cyan") if author else Text(""),
    ]
    if show_linear:
        cells.append(ticket)
    cells.append(Text(state_icon, style=style) if state else Text(""))
    if show_linear:
        cells.append(ticket_status)
    cells += [
        Text(ci, style=_CI_STYLE.get(ci, "white")) if ci else Text(""),
        comments,
        _dirty_cell(wt),
    ]
    cells.append(Text((title[:48] + "…") if len(title) > 49 else title, style="grey62"))
    return cells


class WorktreeTable(DataTable):
    DEFAULT_CSS = """
    WorktreeTable { width: 1fr; height: 1fr; }
    """

    # Override DataTable's Enter→select_cursor so Enter raises FocusRequest
    # instead of a RowSelected (which a *single* click also raises — we don't
    # want single click to focus). Double-click is handled in `on_click`.
    BINDINGS = [Binding("enter", "request_focus", "Focus", show=False)]

    class FocusRequest(Message):
        """User asked to focus a row's workspace (Enter or double-click)."""

        def __init__(self, path: str) -> None:
            self.path = path
            super().__init__()

    def __init__(self, *, show_linear: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._show_linear = show_linear

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns(*column_labels(show_linear=self._show_linear))

    def current_path(self) -> str | None:
        """Worktree path (the row key) under the cursor, or None when empty."""
        if not self.row_count:
            return None
        row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        return row_key.value

    def action_request_focus(self) -> None:
        path = self.current_path()
        if path:
            self.post_message(self.FocusRequest(path))

    def on_click(self, event: events.Click) -> None:
        # Double-click focuses; single click only moves the cursor. DataTable's
        # own `_on_click` (private) still runs to move the cursor first, so by
        # the second click the row cursor already points at the clicked row.
        if getattr(event, "chain", 1) >= 2:
            path = self.current_path()
            if path:
                self.post_message(self.FocusRequest(path))

    def update_inventory(self, inventory: Inventory) -> None:
        """Rebuild rows from the worktree inventory, keeping the cursor on the
        same row index so a refresh doesn't yank the selection away."""
        saved = self.cursor_row
        self.clear()
        for repo_name, repo_color, linear_enabled, wts in inventory:
            for wt in wts:
                self.add_row(
                    *worktree_cells(
                        wt,
                        repo_name,
                        repo_color,
                        linear_enabled,
                        show_linear=self._show_linear,
                    ),
                    key=str(wt.path),
                )
        if self.row_count:
            self.move_cursor(row=min(saved, self.row_count - 1))
