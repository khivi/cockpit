"""Navigable worktree table — a DataTable with a row cursor (arrow keys).

Strictly a renderer: it only *reads* the same flat cache cells starship reads
(`git-branch/status/sync` by cwd, `pr-*` by branch) plus the per-PR JSON for
Linear. It never writes a cell, preserving the daemon-is-sole-writer invariant.
Rows are keyed by worktree path so a future keybinding can resolve the selected
row back to its workspace.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import DataTable

from cockpit.lib.cache import branch_cache, cwd_cache, find_pr_payload, read_text
from cockpit.lib.git import Worktree

# (repo display name, linear-enabled, worktrees)
Inventory = list[tuple[str, bool, list[Worktree]]]

_STATE_STYLE = {
    "APPROVED": "green",
    "OPEN": "cyan",
    "DRAFT": "grey50",
    "REVIEW_REQUIRED": "yellow",
    "CHANGES_REQUESTED": "red",
    "MERGED": "magenta",
    "CLOSED": "red",
}
_CI_STYLE = {"✓": "green", "✗": "red", "•": "yellow", "?": "grey50"}

COLUMN_LABELS = ("Repo", "Workspace", "Branch", "PR", "State", "CI", "💬", "Title")


def _ints(raw: str, n: int) -> list[int]:
    parts = raw.split()
    out: list[int] = []
    for i in range(n):
        try:
            out.append(int(parts[i]))
        except (IndexError, ValueError):
            out.append(0)
    return out


def worktree_cells(
    wt: Worktree, repo_name: str, linear_enabled: bool, *, show_repo: bool
) -> list[Text]:
    """Build one row's cells (Rich Text, so colors survive). `show_repo` puts the
    repo name in the first column only on the first row of each repo group."""

    def cell(stem: str) -> str:
        return read_text(branch_cache(stem, wt.branch))

    branch = read_text(cwd_cache("git-branch", wt.path)) or wt.branch
    staged, unstaged, untracked = _ints(read_text(cwd_cache("git-status", wt.path)), 3)
    ahead, behind = _ints(read_text(cwd_cache("git-sync", wt.path)), 2)
    branch_txt = Text("⎇ ", style="cyan") + Text(branch, style="cyan")
    bits = []
    if ahead:
        bits.append(f"↑{ahead}")
    if behind:
        bits.append(f"↓{behind}")
    dirty = staged + unstaged + untracked
    if dirty:
        bits.append(f"●{dirty}")
    if bits:
        branch_txt += Text("  " + " ".join(bits), style="grey50")

    num, state = cell("pr-num"), cell("pr-state")
    ci = cell("pr-checks")
    comments = cell("pr-comments")
    title = cell("pr-title")
    if linear_enabled and not title:
        payload = find_pr_payload(wt.branch, repo_name) or {}
        tickets = (payload.get("linear") or {}).get("tickets") or []
        if tickets:
            title = ", ".join(str(t.get("id", "?")) for t in tickets)

    return [
        Text(repo_name, style="bold yellow") if show_repo else Text(""),
        Text(wt.label or wt.short, style="bold"),
        branch_txt,
        Text(f"#{num}") if num else Text(""),
        Text(state, style=_STATE_STYLE.get(state, "white")) if state else Text(""),
        Text(ci, style=_CI_STYLE.get(ci, "white")) if ci else Text(""),
        Text(comments, style="red") if comments and comments != "0" else Text(""),
        Text((title[:48] + "…") if len(title) > 49 else title, style="grey62"),
    ]


class WorktreeTable(DataTable):
    DEFAULT_CSS = """
    WorktreeTable { width: 2fr; height: 1fr; }
    """

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns(*COLUMN_LABELS)

    def update_inventory(self, inventory: Inventory) -> None:
        """Rebuild rows from the worktree inventory, keeping the cursor on the
        same row index so a refresh doesn't yank the selection away."""
        saved = self.cursor_row
        self.clear()
        for repo_name, linear_enabled, wts in inventory:
            for i, wt in enumerate(wts):
                self.add_row(
                    *worktree_cells(wt, repo_name, linear_enabled, show_repo=(i == 0)),
                    key=str(wt.path),
                )
        if self.row_count:
            self.move_cursor(row=min(saved, self.row_count - 1))
