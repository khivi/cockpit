"""Navigable worktree table — a DataTable with a row cursor (arrow keys).

Strictly a renderer: it only *reads* the same flat cache cells starship reads
(`pr-*` by branch) plus the per-PR JSON for Linear. It never writes a cell,
preserving the daemon-is-sole-writer invariant. Rows are keyed by worktree path
so the app's `f`/`c` keybindings can resolve the cursor row (`current_path`)
back to its workspace for focus / close.

Repos are grouped under a per-repo *header row* (`HEADER_KEY_PREFIX`, `▸ <repo>`
tinted with the repo's `sidebar_color`), so same-named worktrees (every repo's
`master`) are disambiguated structurally by which header they sit under — no
`repo/label` prefix needed. The worktree rows below each header keep the same
`sidebar_color` tint on their label (matching the cmux sidebar). Header rows
carry no workspace, so `current_path()` returns None on them and every row
action no-ops there. The Author column (right after
PR) shows the PR author's login prefixed with `@`, populated by the daemon only
for other-authored PRs (coworker / review PRs) and blank for my own. The Dirty
column (headed with the
`✎` modifications glyph rather than the word "Dirty") reads the same
daemon-written `git-status` cell the footer does (`●S ✎M ✚U`). The Ticket and
Status columns are added only when some configured repo is Linear-enabled
(`show_tickets`); Ticket shows the delivered Linear ticket id(s) and Status shows
one workflow-state *icon* per ticket (headed with the `📍` glyph rather than the
word "Status", mapped from the state name via `_linear_status_icon`), both from
the cached per-PR block, with Ticket placed right after Author and Status right
after the PR-state column (`🔀`) so the two status columns are adjacent.

A muted PR (nudges silenced via `m` / `/cockpit:nudge`) prefixes its workspace
name with the 🔇 glyph, read from the daemon-written `pr-muted` cell — the same
snapshot starship reads, so the table never diverges from the sidebar. An
unmuted PR with an actionable nudge condition (failing CI / unresolved threads /
conflicts on an OPEN PR) instead shows the 🔔 glyph, read from the `pr-nudge`
cell — `PR.nudge_issue`, the same value the slow tick's nudge decision uses, so
the bell can't disagree with whether a nudge would fire. Mute wins over 🔔 (a
muted PR fires no nudge); the bell clears automatically when CI goes green /
threads resolve / the PR merges.
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
    ICON_PR_NUDGE,
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
    # GitHub-issue states (the `tickets: github` provider reports open/closed
    # when the issue lacks the dev-done label — the label itself, e.g. "ready
    # for review", matches "review" above). Closed reads as done; open as
    # in-progress.
    ("closed", "🟢", "green"),
    ("open", "🚧", "cyan"),
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


# (repo display name, sidebar_color, tickets-enabled, worktrees)
Inventory = list[tuple[str, str | None, bool, list[Worktree]]]

# Row-key prefix marking a repo *group header* row (repo name, no workspace).
# Real worktree keys are absolute filesystem paths, so this NUL-led sentinel
# can never collide with one. `current_path()` returns None on these rows so
# every row action no-ops there.
HEADER_KEY_PREFIX = "\x00hdr:"

# Capability sentinel handed to the footer when the highlighted row is a group
# header: it hides every row-targeted key (nothing to act on) while keeping the
# global keys (`n`/New, `s`/Sync, …). See `FooterBar._skip`.
HEADER_CAP = "header"

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


def column_labels(*, show_tickets: bool) -> tuple[str, ...]:
    """Column headers in display order. The `Author` column sits right after
    `PR` (always present — blank for self-authored PRs, the coworker login for
    a review PR). The `Ticket` column follows it; the ticket `Status` column
    sits right after the PR-state column so the two status columns are adjacent.
    Both ticket columns appear only when some configured repo has a ticket
    provider — Linear or GitHub (`show_tickets`)."""
    cols = ["Workspace", "PR", "Author"]
    if show_tickets:
        cols.append("Ticket")
    cols.append(_APPROVAL_ICON)
    if show_tickets:
        cols.append(_STATUS_ICON)
    cols += ["CI", "💬", _DIRTY_ICON, "Title"]
    return tuple(cols)


def _display_label(wt: Worktree) -> str:
    """The bare workspace label shown in the Workspace column (before any repo
    prefix or status glyph) — the branch-derived `label`, falling back to the
    dir basename."""
    return wt.label or wt.short


def _header_cells(repo_name: str, repo_color: str | None, ncols: int) -> list[Text]:
    """A repo group-header row: `▸ <repo>` in the Workspace column (bold, tinted
    with the repo's cmux colour when set), the rest blank. `ncols` is the live
    column count so the blank tail matches whatever `show_tickets` produced."""
    label = f"▸ {repo_name}"
    colorizer = CMUX_COLOR_ANSI.get(repo_color or "")
    if colorizer is not None:
        head = Text.from_ansi(colorizer(label))
        head.stylize("bold")
    else:
        head = Text(label, style="bold")
    return [head, *(Text("") for _ in range(ncols - 1))]


def _workspace_cell(
    wt: Worktree,
    repo_color: str | None,
    *,
    muted: bool,
    nudge: bool,
) -> Text:
    """The workspace name, tinted with the repo's cmux colour when set and
    prefixed with a status glyph: 🔇 when the PR's nudges are muted, else 🔔 when
    the PR has an actionable, unmuted nudge condition (failing CI / unresolved
    threads / conflicts on an OPEN PR — the `pr-nudge` cell). Mute wins: a muted
    PR fires no nudge, so it shows 🔇, never 🔔. No glyph when neither holds.

    Same-named worktrees across repos are disambiguated by their group-header
    row, not a `repo/` prefix, so the label renders bare."""
    label = _display_label(wt)
    colorizer = CMUX_COLOR_ANSI.get(repo_color or "")
    if colorizer is not None:
        # Reuse the exact cmux colorizer (the source of truth) → parse its ANSI.
        cell = Text.from_ansi(colorizer(label))
    else:
        cell = Text(label, style="bold")
    if muted:
        return Text.assemble((f"{ICON_PR_MUTED} ", "yellow"), cell)
    if nudge:
        return Text.assemble((f"{ICON_PR_NUDGE} ", "yellow"), cell)
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


def row_capabilities(
    wt: Worktree, repo_name: str, tickets_enabled: bool
) -> frozenset[str]:
    """The highlighted-row capability tokens the footer gates its row keys on,
    read from the same daemon-written cells the cells render from (no network):

      * ``"pr"``     — a PR is cached for the branch (`pr-num`), so `p`/`m` apply;
      * ``"ticket"`` — the repo has a provider and the PR delivers a ticket, so
        `l` applies;
      * ``"muted"``  — the PR's nudges are muted (`pr-muted`), so `m` reads
        "Unmute".
    """
    caps: set[str] = set()
    if read_text(branch_cache("pr-num", wt.branch)):
        caps.add("pr")
    if read_text(branch_cache("pr-muted", wt.branch)):
        caps.add("muted")
    if tickets_enabled and (
        (find_pr_payload(wt.branch, repo_name) or {}).get("linear") or {}
    ).get("tickets"):
        caps.add("ticket")
    return frozenset(caps)


def worktree_cells(
    wt: Worktree,
    repo_name: str,
    repo_color: str | None,
    tickets_enabled: bool,
    *,
    show_tickets: bool,
) -> list[Text]:
    """Build one row's cells (Rich Text, so colours survive), in `column_labels`
    order: the Ticket cell follows Author and the Status cell follows the
    PR-state cell, both present only when `show_tickets` (the columns exist) and
    blank for a row whose repo isn't Linear-enabled."""

    def cell(stem: str) -> str:
        return read_text(branch_cache(stem, wt.branch))

    num, state, ci = cell("pr-num"), cell("pr-state"), cell("pr-checks")
    comments = _comments_cell(cell("pr-comments"), cell("pr-comments-total"))
    title = cell("pr-title")
    author = cell("pr-author")
    state_icon, style = _STATE.get(state, (state, "white"))
    ticket, ticket_status = (
        _linear_cells(wt, repo_name) if tickets_enabled else (Text(""), Text(""))
    )

    cells = [
        _workspace_cell(
            wt,
            repo_color,
            muted=bool(cell("pr-muted")),
            nudge=bool(cell("pr-nudge")),
        ),
        Text(f"#{num}") if num else Text(""),
        # Author is populated by the daemon only for other-authored (coworker /
        # review) PRs — blank for my own, so the column reads "whose PR is this
        # that isn't mine".
        Text(f"@{author}", style="cyan") if author else Text(""),
    ]
    if show_tickets:
        cells.append(ticket)
    cells.append(Text(state_icon, style=style) if state else Text(""))
    if show_tickets:
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

    def __init__(self, *, show_tickets: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._show_tickets = show_tickets
        # worktree path → row capability tokens, rebuilt each `update_inventory`
        # so `current_capabilities()` can gate the footer's row keys without a
        # re-read.
        self._row_caps: dict[str, frozenset[str]] = {}
        # row key (worktree path OR header sentinel) → owning repo display name,
        # so `current_repo_name()` resolves the cursor row's repo even on a
        # group-header row (where `current_path()` is None).
        self._row_repo: dict[str, str] = {}

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns(*column_labels(show_tickets=self._show_tickets))

    def _current_row_key(self) -> str | None:
        """The raw row key under the cursor (a worktree path or a header
        sentinel), or None when the table is empty."""
        if not self.row_count:
            return None
        row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        return row_key.value

    def current_path(self) -> str | None:
        """Worktree path under the cursor, or None on an empty table or a repo
        group-header row (which carries no workspace, so row actions no-op)."""
        key = self._current_row_key()
        if key is None or key.startswith(HEADER_KEY_PREFIX):
            return None
        return key

    def current_repo_name(self) -> str | None:
        """The repo display name owning the cursor row — the header's own repo on
        a group-header row, or the worktree's repo on a worktree row. None on an
        empty table. Used to default the `n` new-workspace modal's repo picker to
        the row under the cursor even when that row is a header."""
        key = self._current_row_key()
        return self._row_repo.get(key) if key is not None else None

    def current_capabilities(self) -> frozenset[str] | None:
        """The highlighted row's capability tokens (for footer row-key gating),
        or None when the table is empty — so the footer shows the full legend
        rather than gating against an empty set. A header row returns
        `{HEADER_CAP}`, which the footer reads to hide every row-targeted key."""
        key = self._current_row_key()
        if key is None:
            return None
        return self._row_caps.get(key, frozenset())

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
        same row index so a refresh doesn't yank the selection away. Each repo
        gets a group-header row followed by its worktree rows."""
        saved = self.cursor_row
        self.clear()
        self._row_caps = {}
        self._row_repo = {}
        ncols = len(column_labels(show_tickets=self._show_tickets))
        for repo_name, repo_color, tickets_enabled, wts in inventory:
            hkey = f"{HEADER_KEY_PREFIX}{repo_name}"
            self.add_row(*_header_cells(repo_name, repo_color, ncols), key=hkey)
            self._row_caps[hkey] = frozenset({HEADER_CAP})
            self._row_repo[hkey] = repo_name
            for wt in wts:
                self.add_row(
                    *worktree_cells(
                        wt,
                        repo_name,
                        repo_color,
                        tickets_enabled,
                        show_tickets=self._show_tickets,
                    ),
                    key=str(wt.path),
                )
                self._row_caps[str(wt.path)] = row_capabilities(
                    wt, repo_name, tickets_enabled
                )
                self._row_repo[str(wt.path)] = repo_name
        if self.row_count:
            target = min(saved, self.row_count - 1)
            self.move_cursor(row=target)
            # Don't leave the cursor resting on a group header when a worktree
            # row is selectable just below — the common single-repo first render
            # would otherwise open with the header (and every row key hidden).
            key = self._current_row_key()
            if (
                key
                and key.startswith(HEADER_KEY_PREFIX)
                and target + 1 < self.row_count
            ):
                self.move_cursor(row=target + 1)
