"""Navigable worktree table — a DataTable with a row cursor (arrow keys).

Strictly a renderer: it only *reads* the same flat cache cells starship reads
(`pr-*` by branch) plus the per-PR JSON for Linear. It never writes a cell,
preserving the daemon-is-sole-writer invariant. Rows are keyed by worktree path
so a future keybinding can resolve the selected row back to its workspace.

Repos are distinguished by colour, not a column: the workspace name is tinted
with the repo's `sidebar_color` via the same `CMUX_COLOR_ANSI` colorizer cmux
uses, so the table and the cmux sidebar agree. Ticket + Status columns are added
only when some configured repo is Linear-enabled (`show_linear`); they show the
delivered Linear ticket id(s) and workflow state from the cached per-PR block.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import DataTable

from cockpit.lib.cache import branch_cache, find_pr_payload, read_text
from cockpit.lib.colors import CMUX_COLOR_ANSI
from cockpit.lib.git import Worktree

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

_BASE_COLUMNS = ("Workspace", "PR", "Approval", "CI", "💬", "Title")
_LINEAR_COLUMNS = ("Ticket", "Status")


def _workspace_cell(wt: Worktree, repo_color: str | None) -> Text:
    """The workspace name, tinted with the repo's cmux colour when set."""
    label = wt.label or wt.short
    colorizer = CMUX_COLOR_ANSI.get(repo_color or "")
    if colorizer is not None:
        # Reuse the exact cmux colorizer (the source of truth) → parse its ANSI.
        return Text.from_ansi(colorizer(label))
    return Text(label, style="bold")


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
    """Build one row's cells (Rich Text, so colours survive). The Ticket + Status
    cells are appended only when `show_linear` (the columns exist); both blank for
    a row whose repo isn't Linear-enabled."""

    def cell(stem: str) -> str:
        return read_text(branch_cache(stem, wt.branch))

    num, state, ci = cell("pr-num"), cell("pr-state"), cell("pr-checks")
    comments, title = cell("pr-comments"), cell("pr-title")
    label, style = _STATE.get(state, (state, "white"))

    cells = [
        _workspace_cell(wt, repo_color),
        Text(f"#{num}") if num else Text(""),
        Text(label, style=style) if state else Text(""),
        Text(ci, style=_CI_STYLE.get(ci, "white")) if ci else Text(""),
        Text(comments, style="red") if comments and comments != "0" else Text(""),
        Text((title[:48] + "…") if len(title) > 49 else title, style="grey62"),
    ]
    if show_linear:
        cells.extend(
            _linear_cells(wt, repo_name) if linear_enabled else (Text(""), Text(""))
        )
    return cells


class WorktreeTable(DataTable):
    DEFAULT_CSS = """
    WorktreeTable { width: 1fr; height: 1fr; }
    """

    def __init__(self, *, show_linear: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._show_linear = show_linear

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        labels = _BASE_COLUMNS + (_LINEAR_COLUMNS if self._show_linear else ())
        self.add_columns(*labels)

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
