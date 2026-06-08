"""Navigable worktree table — a DataTable with a row cursor (arrow keys).

Strictly a renderer: it only *reads* the same flat cache cells starship reads
(`pr-*` by branch) plus the per-PR JSON for Linear. It never writes a cell,
preserving the daemon-is-sole-writer invariant. Rows are keyed by worktree path
so the app's `f`/`c` keybindings can resolve the cursor row (`current_path`)
back to its workspace for focus / close.

Repos are distinguished by colour, not a column: the workspace name is tinted
with the repo's `sidebar_color` via the same `CMUX_COLOR_ANSI` colorizer cmux
uses, so the table and the cmux sidebar agree. The Dirty column (headed with the
`✎` modifications glyph rather than the word "Dirty") reads the same
daemon-written `git-status` cell the footer does (`●S ✎M ✚U`). The Ticket and
Status columns are added only when some configured repo is Linear-enabled
(`show_linear`); they show the delivered Linear ticket id(s) and workflow state
from the cached per-PR block, with Ticket placed right after PR and Status right
before Title.

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
from cockpit.lib.colors import CMUX_COLOR_ANSI
from cockpit.lib.git import Worktree
from cockpit.lib.starship import (
    ICON_PR_MUTED,
    ICON_STAGED,
    ICON_UNSTAGED,
    ICON_UNTRACKED,
)

# (repo display name, sidebar_color, linear-enabled, worktrees)
Inventory = list[tuple[str, str | None, bool, list[Worktree]]]

# Raw `pr-state` enum → (friendly label shown in the Approval column, style).
_STATE = {
    "APPROVED": ("Approved", "green"),
    "OPEN": ("Open", "cyan"),
    "DRAFT": ("Draft", "grey50"),
    "REVIEW_REQUIRED": ("Waiting", "yellow"),
    "CHANGES_REQUESTED": ("Changes", "red"),
    "MERGED": ("Merged", "magenta"),
    "CLOSED": ("Closed", "red"),
}
_CI_STYLE = {"✓": "green", "✗": "red", "•": "yellow", "?": "grey50"}

# The Dirty column header is the modifications glyph (matching its cell content)
# rather than the word "Dirty".
_DIRTY_ICON = ICON_UNSTAGED


def column_labels(*, show_linear: bool) -> tuple[str, ...]:
    """Column headers in display order. The Linear `Ticket` column sits right
    after `PR` and the Linear `Status` column right before `Title`; both appear
    only when some configured repo is Linear-enabled (`show_linear`)."""
    cols = ["Workspace", "PR"]
    if show_linear:
        cols.append("Ticket")
    cols += ["Approval", "CI", "💬", _DIRTY_ICON]
    if show_linear:
        cols.append("Status")
    cols.append("Title")
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


def _linear_cells(wt: Worktree, repo_name: str) -> tuple[Text, Text]:
    """Delivered Linear ticket id(s) and workflow state(s) from the cached per-PR
    block, as two cells. Status is green when every ticket is done-ish, yellow
    otherwise. Both blank when there are no delivered tickets."""
    payload = find_pr_payload(wt.branch, repo_name) or {}
    tickets = (payload.get("linear") or {}).get("tickets") or []
    if not tickets:
        return Text(""), Text("")
    ids = ", ".join(str(t.get("id", "?")) for t in tickets)
    states = ", ".join(str(t.get("state", "")).strip() for t in tickets)
    done = all("done" in str(t.get("state", "")).lower() for t in tickets)
    return Text(ids, style="magenta"), Text(states, style="green" if done else "yellow")


def worktree_cells(
    wt: Worktree,
    repo_name: str,
    repo_color: str | None,
    linear_enabled: bool,
    *,
    show_linear: bool,
) -> list[Text]:
    """Build one row's cells (Rich Text, so colours survive), in `column_labels`
    order: the Ticket cell follows PR and the Status cell precedes Title, both
    present only when `show_linear` (the columns exist) and blank for a row whose
    repo isn't Linear-enabled."""

    def cell(stem: str) -> str:
        return read_text(branch_cache(stem, wt.branch))

    num, state, ci = cell("pr-num"), cell("pr-state"), cell("pr-checks")
    comments, title = cell("pr-comments"), cell("pr-title")
    label, style = _STATE.get(state, (state, "white"))
    ticket, ticket_status = (
        _linear_cells(wt, repo_name) if linear_enabled else (Text(""), Text(""))
    )

    cells = [
        _workspace_cell(wt, repo_color, muted=bool(cell("pr-muted"))),
        Text(f"#{num}") if num else Text(""),
    ]
    if show_linear:
        cells.append(ticket)
    cells += [
        Text(label, style=style) if state else Text(""),
        Text(ci, style=_CI_STYLE.get(ci, "white")) if ci else Text(""),
        Text(comments, style="red") if comments and comments != "0" else Text(""),
        _dirty_cell(wt),
    ]
    if show_linear:
        cells.append(ticket_status)
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
