"""A read-only card per worktree: git state, PR state, and Linear (if enabled).

Strictly a renderer — it only *reads* the same flat cache cells starship reads
(`git-branch/status/sync` by cwd, `pr-*` by branch) plus the persistent per-PR
JSON for Linear. It never writes a cell or shells out: the daemon owns all
writes (AGENTS.md invariant). Built fresh on each tick from the worktree
inventory, so it always reflects the latest cached snapshot.
"""

from __future__ import annotations

from rich.markup import escape
from textual.widgets import Static

from cockpit.lib.cache import branch_cache, cwd_cache, find_pr_payload, read_text
from cockpit.lib.git import Worktree

# Mirror starship's PR-state palette so the TUI and the footer agree at a glance.
_STATE_COLOR = {
    "APPROVED": "green",
    "OPEN": "cyan",
    "DRAFT": "grey50",
    "REVIEW_REQUIRED": "yellow",
    "CHANGES_REQUESTED": "red",
    "MERGED": "magenta",
    "CLOSED": "red",
}


def _ints(raw: str, n: int) -> list[int]:
    """Parse the first `n` whitespace-separated ints from a cell, 0-filling."""
    parts = raw.split()
    out: list[int] = []
    for i in range(n):
        try:
            out.append(int(parts[i]))
        except (IndexError, ValueError):
            out.append(0)
    return out


def _git_line(wt: Worktree) -> str:
    branch = read_text(cwd_cache("git-branch", wt.path)) or wt.branch
    staged, unstaged, untracked = _ints(read_text(cwd_cache("git-status", wt.path)), 3)
    ahead, behind = _ints(read_text(cwd_cache("git-sync", wt.path)), 2)
    parts = [f"[cyan]⎇ {escape(branch)}[/]"]
    if ahead:
        parts.append(f"↑{ahead}")
    if behind:
        parts.append(f"↓{behind}")
    if staged:
        parts.append(f"[green]●{staged}[/]")
    if unstaged:
        parts.append(f"[yellow]✎{unstaged}[/]")
    if untracked:
        parts.append(f"[blue]✚{untracked}[/]")
    return "  " + " ".join(parts)


def _pr_lines(wt: Worktree) -> list[str]:
    def cell(stem: str) -> str:
        return read_text(branch_cache(stem, wt.branch))

    num, state = cell("pr-num"), cell("pr-state")
    if not (num or state):
        return ["  [dim]no PR[/]"]
    parts: list[str] = []
    if num:
        parts.append(f"[b]#{num}[/]")
    if state:
        parts.append(f"[{_STATE_COLOR.get(state, 'white')}]{state}[/]")
    checks = cell("pr-checks")
    if checks:
        parts.append(f"CI {escape(checks)}")
    comments = cell("pr-comments")
    if comments and comments != "0":
        parts.append(f"[red]💬{comments}[/]")
    lines = ["  " + "  ".join(parts)]
    title = cell("pr-title")
    if title:
        lines.append(f"  [dim]{escape(title)}[/]")
    return lines


def _linear_line(wt: Worktree, repo_name: str | None) -> str | None:
    payload = find_pr_payload(wt.branch, repo_name) or {}
    tickets = (payload.get("linear") or {}).get("tickets") or []
    if not tickets:
        return None
    rendered = ", ".join(
        f"{t.get('id', '?')} {t.get('state', '')}".strip() for t in tickets
    )
    return f"  [magenta]Linear:[/] {escape(rendered)}"


def card_markup(wt: Worktree, repo_name: str | None, linear_enabled: bool) -> str:
    title = escape(wt.label or wt.short)
    suffix = " [dim](primary)[/]" if wt.is_primary else ""
    lines = [f"[b]{title}[/]{suffix}", _git_line(wt), *_pr_lines(wt)]
    if linear_enabled:
        linear = _linear_line(wt, repo_name)
        if linear:
            lines.append(linear)
    return "\n".join(lines)


class WorkspaceCard(Static):
    DEFAULT_CSS = """
    WorkspaceCard {
        border: round $panel;
        padding: 0 1;
        margin: 0 0 1 0;
        height: auto;
    }
    """

    def __init__(
        self, wt: Worktree, repo_name: str | None, linear_enabled: bool
    ) -> None:
        super().__init__(card_markup(wt, repo_name, linear_enabled))
