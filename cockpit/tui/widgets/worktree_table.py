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
action no-ops there. The Author column (just before Title, since it's rarely
populated) shows the PR author's login prefixed with `@`, populated by the daemon
only for other-authored PRs (coworker / review PRs) and blank for my own. The
Dirty column (headed with the
`✎` modifications glyph rather than the word "Dirty") reads the same
daemon-written `git-status` cell the footer does (`●S ✎M ✚U`). The Ticket and
Status columns are added only when some configured repo is Linear-enabled
(`show_tickets`); Ticket shows the delivered Linear ticket id(s) and Status shows
one workflow-state *icon* per ticket (headed with the `📍` glyph rather than the
word "Status", mapped from the state name via `_linear_status_icon`), both from
the cached per-PR block.

Columns are grouped by domain so the eye doesn't hop between GitHub and ticket
data: the local dirty column sits right after `PR` #, then the rest of the GitHub
cluster (review-state / CI / comments), then the ticket cluster (Ticket id /
status), then the rarely-populated `Author`, and finally the long `Title` at the
end. Every
icon-headed column carries a hover tooltip (`watch_hover_coordinate`) — hovering
the header shows what the column means; hovering a value cell shows the decoded
value (PR review-state name, ticket workflow state, CI verdict) — so the glyphs
stay legible without a legend.

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

from pathlib import Path

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.message import Message
from textual.widgets import DataTable
from textual.widgets.data_table import CellDoesNotExist

from cockpit.lib.cache import branch_cache, cwd_cache, find_pr_payload, read_text
from cockpit.lib.cmux import DEVDONE_ICON
from cockpit.lib.colors import CMUX_COLOR_ANSI
from cockpit.lib.constants import MAIN_BRANCHES
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
# below. Sits right after the `Ticket` id column (the ticket cluster), not next
# to the PR-state column, so ticket data stays grouped away from GitHub data.
_STATUS_ICON = "📍"

# Linear workflow-state *name* (case-insensitive substring) → (icon, style).
# Matched top-to-bottom so the more specific names win over their bare
# fallbacks ("dev done" before "done", "in review" before a bare match). State
# names are arbitrary per team, so this is a heuristic over Linear's common
# vocabulary — the same name-substring approach `_linear_cells` already uses for
# the status colour. An unrecognised state falls back to a neutral ◎.
#
# These deliberately share NO glyph with the PR-state column (`_STATE` /
# `_PR_STATE_ICON`): a "workflow position" family (squares + tools) rather than
# PR's "review verdict" family (circles + checks). Even though the two columns
# now live in separate clusters, keeping the vocabularies disjoint means a stray
# glance never confuses a ticket state for a PR state (both would else use
# 🔵/👀/✅/⛔).
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


# (repo display name, cache key/nwo, sidebar_color, tickets provider, worktrees).
# The provider is `repo_tickets(...)` verbatim ("none" when disabled) — Trello
# renders card titles, every other provider its id. Display name → header +
# `_row_repo`; cache key → `find_pr_payload` (the daemon writes PR cache under the
# git nwo, which differs from the config label when that label is set). See
# `app._cache_repo_name`.
Inventory = list[tuple[str, str, str | None, str, list[Worktree]]]

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
    """Column headers in display order, grouped by domain. The local `✎` dirty
    column sits right after `PR` #, then the rest of the GitHub cluster — the `🔀`
    review-state, `CI`, and `💬` comments. The ticket cluster — `Ticket` id then
    its `📍` workflow-state — follows, present only when some configured repo has a
    ticket provider (Linear or GitHub, `show_tickets`). Then `Author` (blank for
    self-authored, the coworker login on a review PR — rarely populated, so parked
    near the end), and finally the long `Title`."""
    cols = ["Workspace", "PR", _DIRTY_ICON, _APPROVAL_ICON, "CI", "💬"]
    if show_tickets:
        cols += ["Ticket", _STATUS_ICON]
    cols += ["Author", "Title"]
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


def _ticket_display_id(t: dict, provider: str) -> str:
    """The human-facing ticket handle. Trello ids are opaque short links, so it
    prefers the cached card title (id fallback); every other provider's id
    (PE-1234, #123, PROJ-45) is itself the meaningful handle."""
    if provider == "trello":
        return str(t.get("title") or t.get("id", "?"))
    return str(t.get("id", "?"))


def _linear_cells(wt: Worktree, repo_name: str, provider: str) -> tuple[Text, Text]:
    """Delivered ticket id(s) and workflow state(s) from the cached per-PR block,
    as two cells. The Ticket cell is the comma-joined id(s) — except Trello, whose
    ids are opaque short links, so it joins the cached card title(s) (id fallback).
    The Status cell is one workflow-state *icon* per ticket (space-joined), each
    tinted by its own `_linear_status_icon` style. Both blank when there are no
    delivered tickets."""
    payload = find_pr_payload(wt.branch, repo_name) or {}
    tickets = (payload.get("ticket") or {}).get("tickets") or []
    if not tickets:
        return Text(""), Text("")
    ids = ", ".join(_ticket_display_id(t, provider) for t in tickets)
    icons = []
    for t in tickets:
        state = t.get("state")
        if not state:
            # Provider is configured and the PR delivered this ticket, but the
            # fetch couldn't resolve a state (unreachable / missing creds /
            # unknown id — every provider degrades a failed fetch to None). Flag
            # it red rather than the neutral ◎, which reads as "known but
            # unmapped". A successful fetch always yields a non-empty name.
            icons.append(Text("!", style="bold red"))
            continue
        icon, style = _linear_status_icon(str(state))
        icons.append(Text(icon, style=style))
    return Text(ids, style="magenta"), Text(" ").join(icons)


def row_capabilities(
    wt: Worktree,
    repo_name: str,
    tickets_provider: str,
    *,
    has_workspace: bool = False,
) -> frozenset[str]:
    """The highlighted-row capability tokens the footer gates its row keys on.
    Read from the same daemon-written cells the cells render from (no network),
    except ``"workspace"``, which reflects live cmux/limux state passed in by the
    app (`has_workspace`) — a single `workspace_cwds()` read per inventory
    refresh, cached here so per-keystroke footer gating stays a pure set lookup:

      * ``"pr"``        — a PR is cached for the branch (`pr-num`), so `p`/`m` apply;
      * ``"ticket"``    — the repo has a provider and the PR delivers a ticket, so
        `t` applies;
      * ``"muted"``     — the PR's nudges are muted (`pr-muted`), so `m` reads
        "Unmute";
      * ``"workspace"`` — the row has a live workspace, so `N` (nudge) applies
        (`f` shows regardless — it focuses an existing session or spawns one);
      * ``"primary"``   — the row is a `use_worktree: false` primary checkout
        sitting on a **main branch** (`master`/`main`); it can't be torn down as
        a worktree and the branch survives, so `c`/`C` reduce to a workspace-only
        close (which the footer hides when there's no workspace). A primary
        checkout parked on a *feature* branch does NOT get this cap: `c`/`C`
        there tear the branch down (checkout default + `git branch -D`), so they
        stay advertised even with no workspace — same as a feature row. The
        `MAIN_BRANCHES` test is a cheap, call-free heuristic for "on the default
        branch"; a miss only mis-hides a footer hint, never affecting teardown's
        own authoritative (`origin_head_branch`) guards.
    """
    caps: set[str] = set()
    if read_text(branch_cache("pr-num", wt.branch)):
        caps.add("pr")
    if read_text(branch_cache("pr-muted", wt.branch)):
        caps.add("muted")
    if tickets_provider != "none" and (
        (find_pr_payload(wt.branch, repo_name) or {}).get("ticket") or {}
    ).get("tickets"):
        caps.add("ticket")
    if has_workspace:
        caps.add("workspace")
    if wt.is_primary and wt.branch in MAIN_BRANCHES:
        caps.add("primary")
    return frozenset(caps)


def worktree_cells(
    wt: Worktree,
    repo_name: str,
    repo_color: str | None,
    tickets_provider: str,
    *,
    show_tickets: bool,
) -> list[Text]:
    """Build one row's cells (Rich Text, so colours survive), in `column_labels`
    order: PR, Dirty, then the rest of the GitHub cluster (state / CI / comments),
    then the ticket cluster (Ticket / Status) when `show_tickets` (blank for a row
    whose repo has no ticket provider, `tickets_provider == "none"`), then Author
    and Title."""

    def cell(stem: str) -> str:
        return read_text(branch_cache(stem, wt.branch))

    num, state, ci = cell("pr-num"), cell("pr-state"), cell("pr-checks")
    comments = _comments_cell(cell("pr-comments"), cell("pr-comments-total"))
    title = cell("pr-title")
    author = cell("pr-author")
    state_icon, style = _STATE.get(state, (state, "white"))
    ticket, ticket_status = (
        _linear_cells(wt, repo_name, tickets_provider)
        if tickets_provider != "none"
        else (Text(""), Text(""))
    )

    cells = [
        _workspace_cell(
            wt,
            repo_color,
            muted=bool(cell("pr-muted")),
            nudge=bool(cell("pr-nudge")),
        ),
        Text(f"#{num}") if num else Text(""),
        _dirty_cell(wt),
        Text(state_icon, style=style) if state else Text(""),
        Text(ci, style=_CI_STYLE.get(ci, "white")) if ci else Text(""),
        comments,
    ]
    if show_tickets:
        cells += [ticket, ticket_status]
    cells += [
        # Author is populated by the daemon only for other-authored (coworker /
        # review) PRs — blank for my own, so the column reads "whose PR is this
        # that isn't mine". Rarely populated → parked just before Title.
        Text(f"@{author}", style="cyan") if author else Text(""),
        Text((title[:48] + "…") if len(title) > 49 else title, style="grey62"),
    ]
    return cells


# ── Hover tooltips ──────────────────────────────────────────────────────────
# The icon-headed columns are cryptic at a glance, so every column carries a
# hover hint (`WorktreeTable.watch_hover_coordinate`). Hovering the *header*
# shows what the column means (`_HEADER_TOOLTIPS`, keyed by the column label);
# hovering a *value cell* shows the decoded value (`row_tooltips`, e.g. the PR
# review-state name or the ticket's workflow state), falling back to the column
# meaning for the self-evident text columns.

_HEADER_TOOLTIPS: dict[str, str] = {
    "Workspace": "Workspace / branch name",
    "PR": "Pull-request number",
    "Author": "PR author (blank when it's mine)",
    _APPROVAL_ICON: "PR review state",
    "CI": "CI checks",
    "💬": "Unaddressed review comments (unaddressed / total)",
    "Ticket": "Delivered ticket id(s)",
    _STATUS_ICON: "Ticket workflow state",
    _DIRTY_ICON: "Uncommitted changes (staged / modified / untracked)",
    "Title": "PR title",
}

# Raw `pr-state` enum → the phrase shown when hovering a PR-state (🔀) cell.
_STATE_LABEL: dict[str, str] = {
    "APPROVED": "Approved",
    "OPEN": "Open",
    "DRAFT": "Draft",
    "REVIEW_REQUIRED": "Review required",
    "CHANGES_REQUESTED": "Changes requested",
    "MERGED": "Merged",
    "CLOSED": "Closed",
}

# CI glyph → phrase shown when hovering a CI cell.
_CI_LABEL: dict[str, str] = {
    "✓": "CI passing",
    "✗": "CI failing",
    "•": "CI running",
    "?": "CI status unknown",
}


def _comments_tooltip(unaddressed_raw: str, total_raw: str) -> str | None:
    """Hover text for the 💬 cell — mirrors `_comments_cell`'s parse but spells
    the ratio out in words. None when nothing is unaddressed (no cell shown)."""
    try:
        unaddressed = int(unaddressed_raw or 0)
        total = int(total_raw or 0)
    except ValueError:
        return None
    if unaddressed <= 0:
        return None
    if total > unaddressed:
        return f"{unaddressed} of {total} review threads unaddressed"
    return f"{unaddressed} unaddressed review thread(s)"


def _dirty_tooltip(wt: Worktree) -> str | None:
    """Hover text for the ✎ cell — the same `git-status` counts spelled out
    (`1 staged, 2 modified, 3 untracked`). None when clean or unpopulated."""
    parts = read_text(cwd_cache("git-status", wt.path)).split()
    if len(parts) != 3:
        return None
    try:
        staged, unstaged, untracked = (int(p) for p in parts)
    except ValueError:
        return None
    segs = []
    if staged:
        segs.append(f"{staged} staged")
    if unstaged:
        segs.append(f"{unstaged} modified")
    if untracked:
        segs.append(f"{untracked} untracked")
    return ", ".join(segs) or None


def _ticket_status_tooltip(wt: Worktree, repo_name: str, provider: str) -> str | None:
    """Hover text for the 📍 cell — each delivered ticket's `id: state` (the
    workflow-state name the icon abstracts away). Uses the same display handle as
    the Ticket cell (`_ticket_display_id`), so Trello shows the card title rather
    than its opaque short link. None with no delivered tickets."""
    payload = find_pr_payload(wt.branch, repo_name) or {}
    tickets = (payload.get("ticket") or {}).get("tickets") or []
    if not tickets:
        return None
    parts = []
    for t in tickets:
        tid = _ticket_display_id(t, provider)
        state = t.get("state")
        parts.append(f"{tid}: {state}" if state else f"{tid}: state unavailable")
    return "; ".join(parts)


def row_tooltips(
    wt: Worktree,
    repo_name: str,
    tickets_provider: str,
    *,
    show_tickets: bool,
) -> list[str | None]:
    """Per-cell hover hints for one worktree row, aligned to `column_labels`
    order. Only the cryptic value columns decode (workspace glyph, PR state, CI,
    comments, ticket state, dirty); the self-evident text columns are None and
    fall back to the column meaning on hover."""

    def cell(stem: str) -> str:
        return read_text(branch_cache(stem, wt.branch))

    if cell("pr-muted"):
        workspace: str | None = "Nudges muted"
    elif cell("pr-nudge"):
        workspace = "Nudge pending (CI / threads / conflicts)"
    else:
        workspace = None

    tips: list[str | None] = [
        workspace,
        None,  # PR #
        _dirty_tooltip(wt),
        _STATE_LABEL.get(cell("pr-state")),
        _CI_LABEL.get(cell("pr-checks")),
        _comments_tooltip(cell("pr-comments"), cell("pr-comments-total")),
    ]
    if show_tickets:
        tips += [
            None,  # Ticket id (self-evident)
            _ticket_status_tooltip(wt, repo_name, tickets_provider)
            if tickets_provider != "none"
            else None,
        ]
    tips += [None, None]  # Author, Title
    return tips


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

    class NewRequest(Message):
        """User double-clicked a repo group-header row → open the new-workspace
        modal for that repo (a header has no workspace to focus, so its primary
        action is `n`)."""

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
        # worktree path → per-column hover tooltip (aligned to `column_labels`),
        # so `watch_hover_coordinate` decodes a value cell without re-reading the
        # cache on every mouse move. Header rows carry none (fall back to the
        # column meaning).
        self._cell_tooltips: dict[str, list[str | None]] = {}

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

    def watch_hover_coordinate(self, old: Coordinate, value: Coordinate) -> None:
        # Keep DataTable's own hover-highlight refresh, then point the widget
        # tooltip at whatever the mouse is over. `_on_mouse_move` sets
        # `hover_coordinate` to `(row, column)` for a body cell and `(-1, column)`
        # for the column header, so one watcher covers both.
        super().watch_hover_coordinate(old, value)
        self.tooltip = self._tooltip_for(value)

    def _tooltip_for(self, coord: Coordinate) -> str | None:
        """Hover hint for a coordinate: the column meaning on the header row
        (`coord.row < 0`), else the decoded value cell (falling back to the
        column meaning for the self-evident text columns)."""
        labels = column_labels(show_tickets=self._show_tickets)
        col = coord.column
        if not 0 <= col < len(labels):
            return None
        header = _HEADER_TOOLTIPS.get(labels[col])
        if coord.row < 0:
            return header
        try:
            row_key, _ = self.coordinate_to_cell_key(coord)
        except CellDoesNotExist:
            return header
        tips = self._cell_tooltips.get(row_key.value or "")
        if tips and col < len(tips) and tips[col]:
            return tips[col]
        return header

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
            elif self._current_row_key() is not None:
                # Double-clicked a repo header row (no path) → open new-workspace.
                self.post_message(self.NewRequest())

    def update_inventory(
        self, inventory: Inventory, workspace_paths: set[Path] | None = None
    ) -> None:
        """Rebuild rows from the worktree inventory, keeping the cursor on the
        same row index so a refresh doesn't yank the selection away. Each repo
        gets a group-header row followed by its worktree rows.

        `workspace_paths` is the set of resolved cwds that currently have a live
        workspace (from the app's per-refresh `workspace_cwds()` read); a row
        whose path is in it gets the `"workspace"` cap."""
        ws = workspace_paths or set()
        saved = self.cursor_row
        self.clear()
        self._row_caps = {}
        self._row_repo = {}
        self._cell_tooltips = {}
        ncols = len(column_labels(show_tickets=self._show_tickets))
        for repo_name, cache_key, repo_color, tickets_provider, wts in inventory:
            hkey = f"{HEADER_KEY_PREFIX}{repo_name}"
            self.add_row(*_header_cells(repo_name, repo_color, ncols), key=hkey)
            self._row_caps[hkey] = frozenset({HEADER_CAP})
            self._row_repo[hkey] = repo_name
            for wt in wts:
                self.add_row(
                    *worktree_cells(
                        wt,
                        cache_key,
                        repo_color,
                        tickets_provider,
                        show_tickets=self._show_tickets,
                    ),
                    key=str(wt.path),
                )
                self._row_caps[str(wt.path)] = row_capabilities(
                    wt,
                    cache_key,
                    tickets_provider,
                    has_workspace=wt.path.resolve() in ws,
                )
                self._row_repo[str(wt.path)] = repo_name
                self._cell_tooltips[str(wt.path)] = row_tooltips(
                    wt,
                    cache_key,
                    tickets_provider,
                    show_tickets=self._show_tickets,
                )
        if self.row_count:
            target = min(saved, self.row_count - 1)
            self.move_cursor(row=target)
            # Don't leave the cursor resting on a group header when a worktree
            # row is selectable just below — the common single-repo first render
            # would otherwise open with the header (and every row key hidden).
            # Consecutive headers (e.g. an empty repo followed by another
            # repo's header) need more than one hop, so keep advancing until
            # the cursor is off every header or the rows run out.
            key = self._current_row_key()
            while (
                key
                and key.startswith(HEADER_KEY_PREFIX)
                and target + 1 < self.row_count
            ):
                target += 1
                self.move_cursor(row=target)
                key = self._current_row_key()
