"""Navigable worktree table — a DataTable with a row cursor (arrow keys).

Strictly a renderer: it only *reads* the same flat cache cells starship reads
(`pr-*` by branch) plus the per-PR JSON for Linear. It never writes a cell,
preserving the daemon-is-sole-writer invariant. Rows are keyed by worktree path
so the app's `f`/`c` keybindings can resolve the cursor row (`current_path`)
back to its workspace for focus / close.

Repos are grouped under a per-repo *header row* (`HEADER_KEY_PREFIX`, the repo
name trailed by a dim `─` rule out to `_RULE_WIDTH`, tinted with the repo's
`sidebar_color`), so same-named worktrees (every repo's `master`) are
disambiguated structurally by which header they sit under — no `repo/label`
prefix needed. The worktree rows below each header keep the same `sidebar_color`
tint on their label (matching the cmux sidebar) and hang under it behind
`ROW_INDENT`: header and row both write into the Workspace column, so without
the rule + indent the two read as siblings rather than parent/child. Header rows
carry no workspace, so `current_path()` returns None on them and every row
action no-ops there. Within a repo, a *stacked* chain of PRs renders as one
group: its tip heads the run and every PR it is stacked on lists under it,
indented one level behind a `└` (`_stack_rows`, off the daemon-written
`pr-base` cell) — the table's own rendering of the same chain the cmux sidebar
folds into a group. Rows within a repo then sink into three bands (`_row_band`):
my live queue, then coworkers' PRs I'm reviewing, then the ones I've snoozed —
the table's rendering of the trailing folds the sidebar parks at the bottom, so
a pile I've already said "not my turn" to can't bury the row that wants me. The
snoozed band goes one step further and collapses behind a per-repo `▸ N snoozed`
disclosure row (`_split_snoozed`, `z` to open it): sinking a row I've explicitly
deferred still spends a full line on it, and reclaiming those lines is the whole
point of snoozing.

The Author column (just before Title, since it's rarely populated) shows the PR
author's login prefixed with `@`, populated by the daemon only for other-authored
PRs (coworker / review PRs) and blank for my own. The Dirty column (headed with the
`✎` modifications glyph rather than the word "Dirty") reads the same
daemon-written `git-status` cell the footer does (`●S ✎M ✚U`). The Ticket and
Status columns are added only when some configured repo is Linear-enabled
(`show_tickets`); Ticket shows the delivered Linear ticket id(s) and Status shows
one workflow-state *icon* per ticket (headed with the `📍` glyph rather than the
word "Status", mapped from the state name via `_linear_status_icon`), both from
the cached per-PR block. The trailing `$` column is the total USD every Claude
Code session rooted at that worktree has spent (`wt-cost`), present only when
Claude Code reports real spend on this machine (`show_cost`) and blank on a row
that has cost nothing — the one column about the cost of the work rather than
about the PR.

Every cell that names something on the web is an OSC 8 terminal *hyperlink*
(`_cell_links`): the GitHub cluster to the PR (CI to its checks page), the ticket
cluster to the tracker, `Author` to that login's profile. The terminal owns the
gesture and the affordance, so cockpit only supplies the destination — and,
because a link is invisible until hovered, names it again in the tooltip. The
ticket link is read from the daemon-written `url` (`cycle._stamp_ticket_urls`),
never resolved here: three of the four providers can only find it in the PR body,
which is a `gh` call this module may not make.

Columns are grouped by domain so the eye doesn't hop between GitHub and ticket
data: the local dirty column sits right after `PR` #, then the rest of the GitHub
cluster (review-state / CI / comments), then the ticket cluster (Ticket id /
status), then the rarely-populated `Author`, and finally the long `Title` at the
end. Every
icon-headed column carries a hover tooltip (`watch_hover_coordinate`) — hovering
the header shows what the column means; hovering a value cell shows the decoded
value (PR review-state name, ticket workflow state, CI verdict) — so the glyphs
stay legible without a legend.

A muted PR (nudges silenced via `m` / `cockpit nudge`) prefixes its workspace
name with the 🔇 glyph, read from the daemon-written `pr-muted` cell — the same
snapshot starship reads, so the table never diverges from the sidebar. An
unmuted PR with an actionable nudge condition (failing CI / unresolved threads /
conflicts on an OPEN PR) instead shows the 🔔 glyph, read from the `pr-nudge`
cell — `PR.nudge_issue`, the same value the slow tick's nudge decision uses, so
the bell can't disagree with whether a nudge would fire. Mute wins over 🔔 (a
muted PR fires no nudge); the bell clears automatically when CI goes green /
threads resolve / the PR merges. Snooze carries **no** glyph: a snoozed row only
ever renders inside its repo's `▾ N snoozed` fold, which says it for the whole
group, so the slot stays free for the 🔇 of a row that is both.

The glyph sits in a **fixed-width slot** (`_STATUS_SLOT`) that every row pays for,
glyphed or not — the labels of a belled row, a muted row and a quiet one then
start at the same column, so the Workspace column reads as one list instead of a
ragged one.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import ClassVar

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.binding import Binding, BindingType
from textual.coordinate import Coordinate
from textual.message import Message
from textual.widgets import DataTable
from textual.widgets.data_table import CellDoesNotExist, RowDoesNotExist

from cockpit.lib.cache import (
    cwd_cache,
    find_pr_payload,
    read_text,
    read_worktree_cost,
    ticket_display,
)
from cockpit.lib.cmux import DEVDONE_ICON
from cockpit.lib.colors import CMUX_COLOR_ANSI
from cockpit.lib.constants import MAIN_BRANCHES
from cockpit.lib.git import Worktree
from cockpit.lib.stacks import stack_order
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

# Ticket workflow-state *name* (case-insensitive substring) → (icon, style).
# Matched top-to-bottom so the more specific names win over their bare
# fallbacks ("dev done" before "done", "in review" before a bare match). The
# name is whatever the tracker was configured with — a Linear state, a GitHub
# label or open/closed, a Jira status, a Trello *list* — so this is a heuristic
# over the vocabulary those four have in common, not any one provider's
# enumeration, and it grows by adding the spelling that missed. An unrecognised
# state falls back to a neutral ◎.
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
    ("ongoing", "🚧", "cyan"),
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
# renders card numbers, every other provider its id. Display name → header +
# `_row_repo`; cache key → `find_pr_payload` (the daemon writes PR cache under the
# git nwo, which differs from the config label when that label is set). See
# `app._cache_repo_name`.
Inventory = list[tuple[str, str, str | None, str, list[Worktree]]]

# Row-key prefix marking a repo *group header* row (repo name, no workspace).
# Real worktree keys are absolute filesystem paths, so this NUL-led sentinel
# can never collide with one. `current_path()` returns None on these rows so
# every row action no-ops there.
HEADER_KEY_PREFIX = "\x00hdr:"

# Row key for the disclosure line standing in for the hidden (parked) repos —
# `▸ N repos hidden` collapsed, `▾ N repos hidden` expanded, with one dim repo row per parked
# repo underneath while expanded. Nested under `HEADER_KEY_PREFIX` so
# `current_path()` returns None on it and the cursor-skip loop treats it like any
# other header for free.
HIDDEN_ROW_KEY = f"{HEADER_KEY_PREFIX}\x00hidden"

# Row key for a repo's `▸ N snoozed` disclosure row — the collapsed form of the
# band-2 rows (`_split_snoozed`). Its own NUL-led sentinel rather than a nesting
# under `HEADER_KEY_PREFIX`: `current_path()` skips every sentinel alike, but the
# cursor-skip loop must *not* hop off this one. It sits inside its repo's block,
# it is the row the cursor lands on after a `z`, and `z` on it opens the fold.
SNOOZED_KEY_PREFIX = "\x00snz:"


def snoozed_row_key(repo_name: str) -> str:
    """Row key of `repo_name`'s snoozed disclosure row. One per repo — the fold
    is per repo, matching the table's own grouping (the sidebar's equivalent
    folds are per *org*; the table has no org row)."""
    return f"{SNOOZED_KEY_PREFIX}{repo_name}"


def _is_sentinel(key: str) -> bool:
    """Is this row key one of the synthetic non-worktree rows (group header,
    hidden disclosure, snoozed disclosure)? Real keys are absolute filesystem
    paths, so the leading NUL is the whole test."""
    return key.startswith("\x00")


# Capability sentinel handed to the footer when the highlighted row is a group
# header: it hides every row-targeted key (nothing to act on) while keeping the
# global keys (`n`/New, `s`/Sync, …). See `FooterBar._skip`.
HEADER_CAP = "header"

# Caps carrying the hidden-section state to the footer, so one key (`h`) can
# read as three verbs without the footer tracking any state of its own:
# `hiddenrow` (+ `expanded`) on the disclosure row → Reveal / Collapse;
# `parked` on a revealed repo's row → Unhide.
HIDDEN_CAP = "hiddenrow"
EXPANDED_CAP = "expanded"
PARKED_CAP = "parked"

# Cap marking the `▸ N snoozed` disclosure row, so `z` reads as Expand/Collapse
# there (+ `EXPANDED_CAP`) instead of Snooze/Wake, and every other row key hides.
# Deliberately NOT paired with `HEADER_CAP`: that hides *every* row key including
# `z` itself, and it would advertise `h`/Hide — which parks the whole repo — on a
# row that reads as one section of it.
SNOOZED_CAP = "snoozedrow"

# "This row stands for a repo that HAS a snoozed fold" — on the repo group header
# and on the `▸ N snoozed` disclosure row itself, never on a worktree row. `A`
# asks the whole fold, so it is advertised exactly where the fold is what the
# cursor row means: the same rule `h` follows for a repo-level action, which is
# why "Hide" never appears beside a single worktree. A repo with nothing snoozed
# carries no fold and so never advertises the key.
FOLD_CAP = "snoozedfold"

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

# Pending cmux diff-viewer comments — local only, never GitHub, so it gets its
# own glyph rather than folding into `💬` (GitHub review threads). 📝 sits in
# the local cluster beside `_DIRTY_ICON` rather than the GitHub cluster.
_DIFF_COMMENT_ICON = "📝"

# Worktree rows hang under their repo's header row. Both write into the same
# Workspace column, so this indent is the only thing that reads as nesting.
ROW_INDENT = "   "

# Extra indent for a stacked-PR row, on top of `ROW_INDENT` — the `└ ` spine then
# sits proud of its chain tip without stepping the whole column right.
_STACK_INDENT = "  "

# Display width of the status-glyph slot every row reserves: the widest glyph
# (🔇/🔔, 2 cells each) plus its trailing space. A row with no glyph pays the
# same width in blanks, so every label in the column starts at one column.
_STATUS_SLOT = 3

# Display width the header's trailing `─` rule fills. Sized to cover a *typical*
# widest row — `ROW_INDENT` (3) + `_STATUS_SLOT` (3) + `_LABEL_MAX` (22) + an
# ellipsis (1) — and deliberately NOT the absolute worst case, a stacked row,
# which overhangs by exactly its `_STACK_INDENT` + `└ ` spine (4). Covering that
# would pin the column four columns wider on every render for the rows that
# aren't stacked, and Workspace is competing with Title for width; a rule that
# stops a little short still reads as a rule.
# `test_a_typical_widest_row_fits_the_header_rule` holds the arithmetic — adjust
# one constant and it fails.
_RULE_WIDTH = 29
_LABEL_MAX = 22

# Ceiling on the Ticket cell — several delivered ids comma-joined, or a Trello
# card name where the card's number didn't resolve, run long enough to push the
# trailing Title column off a normal-width terminal. The full value stays on the
# hover tooltip.
_TICKET_MAX = 18


def _ellipsize(text: str, limit: int) -> str:
    """Cap `text` at `limit` characters with a trailing `…`. One character over
    the limit passes through whole — swapping a single character for an ellipsis
    saves no width and loses information."""
    return text if len(text) <= limit + 1 else text[:limit] + "…"


def column_labels(*, show_tickets: bool, show_cost: bool = False) -> tuple[str, ...]:
    """Column headers in display order, grouped by domain. The local cluster —
    `✎` dirty then `📝` pending diff-viewer comments — sits right after `PR` #,
    then the GitHub cluster — the `🔀` review-state, `CI`, and `💬` comments.
    `📝` and `💬` look alike but are never the same thing: `💬` is GitHub review
    threads, `📝` is comments left in cmux's local diff viewer that haven't been
    sent anywhere yet (`a` sends them). The ticket cluster — `Ticket` id then
    its `📍` workflow-state — follows, present only when some configured repo has a
    ticket provider (Linear or GitHub, `show_tickets`). Then `Author` (blank for
    self-authored, the coworker login on a review PR — rarely populated, so parked
    near the end), and finally the long `Title`.

    `$` trails everything (`show_cost`). It's the only column not about the PR,
    so it doesn't belong in any of the clusters above, and it reads as a column
    of numbers whatever the Title beside it does."""
    cols = [
        "Workspace",
        "PR",
        _DIRTY_ICON,
        _DIFF_COMMENT_ICON,
        _APPROVAL_ICON,
        "CI",
        "💬",
    ]
    if show_tickets:
        cols += ["Ticket", _STATUS_ICON]
    cols += ["Author", "Title"]
    if show_cost:
        cols += ["$"]
    return tuple(cols)


def _full_label(wt: Worktree) -> str:
    """The untruncated workspace label — the branch-derived `label`, falling back
    to the dir basename."""
    return wt.label or wt.short


def _display_label(wt: Worktree) -> str:
    """`_full_label` capped at `_LABEL_MAX` so one long branch name can't starve
    the trailing Title column. `row_tooltips` surfaces the full name on hover."""
    return _ellipsize(_full_label(wt), _LABEL_MAX)


# The band a snoozed row sinks into — the last one, and the one the table
# renders collapsed behind the repo's `▸ N snoozed` disclosure row.
_BAND_SNOOZED = 2


def _row_band(wt: Worktree) -> int:
    """Which trailing band a row sinks into: 0 my live queue, 1 a coworker's PR
    I'm reviewing, 2 one I've snoozed (which then folds away — `_split_snoozed`).

    Both discriminators are the same daemon-written flat cells the row renders
    from (`pr-snoozed`; `pr-author`, which carries a login only for a coworker's
    PR) — no network, nothing stored. Snooze outranks review so a coworker PR
    I've already read sinks past the ones I haven't, matching the sidebar, which
    parks its snoozed fold under its reviews fold (and a sunk stack chain under
    both — `cycle._reconcile_review_groups`). Mute is deliberately *not* a band: it
    means "stop nudging me about a PR I'm working on", not "not my turn"."""
    if read_text(cwd_cache("pr-snoozed", wt.path)):
        return _BAND_SNOOZED
    if read_text(cwd_cache("pr-author", wt.path)):
        return 1
    return 0


def _chains(wts: list[Worktree]) -> list[list[tuple[int, int]]]:
    """One repo's worktrees grouped into stacked-PR chains and sorted into
    `_row_band` order — the shared half of `_stack_rows` and `_split_snoozed`.

    Each chain is a list of `(index into wts, depth)`, its tip first. Chains are
    the unit of every ordering decision below: a chain bands by its **tip** and
    moves whole, so a snoozed tip sinks (and folds) its whole chain while a
    snooze on a member below the tip moves nothing."""
    chains: list[list[tuple[int, int]]] = []
    # `stack_order` links a branch to its base by name, but the cell holding the
    # base is keyed by worktree. Within one repo a branch names exactly one
    # worktree, so the map is total — it is only across repos that a branch name
    # is ambiguous, and a chain never spans repos.
    path_of = {wt.branch: wt.path for wt in wts}
    for i, depth in stack_order(
        [wt.branch for wt in wts],
        lambda branch: read_text(cwd_cache("pr-base", path_of[branch]))
        if branch in path_of
        else "",
    ):
        if depth == 0 or not chains:
            chains.append([(i, depth)])
        else:
            chains[-1].append((i, depth))
    chains.sort(key=lambda chain: _row_band(wts[chain[0][0]]))
    return chains


def _split_snoozed(
    wts: list[Worktree],
) -> tuple[list[tuple[Worktree, int]], list[tuple[Worktree, int]]]:
    """`(live rows, snoozed rows)` for one repo, both in `_stack_rows` order.

    The snoozed half is band 2 — the rows the table collapses behind the repo's
    `▸ N snoozed` disclosure row. Split at *chain* granularity, never per row:
    the fold takes or leaves a whole stack, so a chain can't be torn in half by
    its tip folding away from its members."""
    chains = _chains(wts)
    live: list[tuple[Worktree, int]] = []
    snoozed: list[tuple[Worktree, int]] = []
    for chain in chains:
        into = snoozed if _row_band(wts[chain[0][0]]) == _BAND_SNOOZED else live
        into.extend((wts[i], depth) for i, depth in chain)
    return live, snoozed


def _stack_rows(wts: list[Worktree]) -> list[tuple[Worktree, int]]:
    """One repo's worktrees in render order, each with its stacked-PR depth.

    A stacked PR is one whose base branch is another PR's head, so the chain is
    read straight off the daemon-written `pr-base` cells — no network, nothing
    stored, the same derivation `lib.stacks.find_stacks` runs on the daemon
    side for the cmux sidebar fold. A chain sorts contiguously under its tip at
    one level of indent; every other worktree keeps its `git worktree list`
    order at depth 0.

    Rows then sink into `_row_band` order — my live queue, then reviews, then
    snoozed — mirroring the trailing folds the sidebar already parks at the
    bottom (`cycle._reconcile_review_groups`). The sort is **stable**, so within
    a band the stack/`git worktree list` order above is untouched, and it bands
    a chain by its **tip** (the depth-0 row heading the group), never by its
    deepest member: a snoozed *tip* sinks its whole chain rather than splitting
    it, while a snooze on a member *below* the tip moves nothing — one snoozed
    dependency must not bury the active stack sitting on top of it. Contiguity
    under the tip is what keeps the table and the sidebar reading as the same
    stack. The sidebar sinks the same chain by the same tip rule, just by a
    different mechanism: its snoozed *pile* excludes every ref already folded
    into a stack group, so the whole group is moved to the bottom instead
    (`cycle._reconcile_sidebar_groups`).

    This is every row in one list; `update_inventory` renders the trailing
    snoozed band collapsed, so it goes through `_split_snoozed` instead."""
    return [(wt, depth) for half in _split_snoozed(wts) for wt, depth in half]


def _header_cells(
    repo_name: str, repo_color: str | None, ncols: int, *, hidden: bool = False
) -> list[Text]:
    """A repo group-header row: `<repo> ────…` in the Workspace column (bold,
    tinted with the repo's cmux colour when set), the rest blank. `ncols` is the
    live column count so the blank tail matches whatever `show_tickets` produced.

    The trailing rule fills the column out to `_RULE_WIDTH`, so the header reads
    as a break between groups rather than as another row — the worktree rows
    below it are the ones carrying `ROW_INDENT`. (The old `▸` prefix also read as
    a disclosure triangle, which a repo header isn't: only the `▸ N repos hidden` row
    expands.)

    `hidden` renders a *parked* repo revealed by expanding the hidden row: the
    rule and the repo's colour are both dropped for a dim `(hidden)` suffix, so a
    revealed repo reads as temporarily on screen rather than back in rotation."""
    if hidden:
        label = f"▸ {repo_name}"
        return [
            Text.assemble((label, "dim"), (" (hidden)", "dim italic")),
            *(Text("") for _ in range(ncols - 1)),
        ]
    colorizer = CMUX_COLOR_ANSI.get(repo_color or "")
    if colorizer is not None:
        head = Text.from_ansi(colorizer(repo_name))
        head.stylize("bold")
    else:
        head = Text(repo_name, style="bold")
    # The rule stays dim whatever the repo's tint, so it reads as structure
    # rather than as more of the repo's colour. A name wider than the column
    # still gets a stub of one, so every header carries the same signal.
    rule = "─" * max(3, _RULE_WIDTH - len(repo_name) - 1)
    head = Text.assemble(head, " ", (rule, "dim"))
    return [head, *(Text("") for _ in range(ncols - 1))]


def _disclosure_row(head: Text, tail: Text, labels: tuple[str, ...]) -> list[Text]:
    """A disclosure row: `head` in the Workspace column, `tail` in the wide
    `Title` column, blanks everywhere else.

    Indexed off `labels` (the live `column_labels`) rather than written as
    "everything up to the last column, then the tail": `Title` is **not** last —
    `show_cost` appends `$` after it — so a `ncols - 1` tail lands in the numeric
    column, leaves Title blank, and (DataTable auto-sizes to content) widens `$`
    for every row in the table."""
    cells = [Text("") for _ in labels]
    cells[0] = head
    cells[labels.index("Title")] = tail
    return cells


def _hidden_cells(
    names: list[str], columns: tuple[str, ...], *, expanded: bool
) -> list[Text]:
    """The disclosure line for the parked repos: a count behind a `▸`/`▾` triangle
    in the Workspace column (so it can't stretch that column) and, while
    collapsed, the repo names in the wide Title column. Both dim — it's a
    reminder, not a row. Expanded, the names render as their own rows below, so
    the tail is empty; the key lives in the footer, not in the table.

    The count names its unit ("5 repos hidden", not "5 hidden") because this row
    sits directly under a repo's `▸ N snoozed` row, whose count is *worktree
    rows* — two adjacent bare numbers counting different things read as the same
    kind of thing. `h` parks a whole repo, so saying so is what distinguishes
    them."""
    noun = "repo" if len(names) == 1 else "repos"
    return _disclosure_row(
        Text(f"{'▾' if expanded else '▸'} {len(names)} {noun} hidden", style="dim"),
        Text("" if expanded else " · ".join(names), style="dim"),
        columns,
    )


def _snoozed_cells(
    labels: list[str], columns: tuple[str, ...], *, expanded: bool
) -> list[Text]:
    """One repo's snoozed disclosure row: a count behind a `▸`/`▾` triangle in the
    Workspace column and, while collapsed, the folded workspace names in the wide
    Title column. Both dim — the whole band is "not my turn", so it must not
    compete with the live rows above it.

    Indented like a worktree row (`ROW_INDENT`), because that is what it stands
    in for: it hangs under the repo's header, not beside it."""
    return _disclosure_row(
        Text(
            f"{ROW_INDENT}{'▾' if expanded else '▸'} {len(labels)} snoozed",
            style="dim",
        ),
        Text("" if expanded else " · ".join(labels), style="dim"),
        columns,
    )


def _status_glyph(*, muted: bool, snoozed: bool, nudge: bool) -> Text:
    """The row's one status glyph, padded to `_STATUS_SLOT` cells — blanks when
    the row carries none, so every label starts at the same column.

    Snooze paints **nothing** but still takes precedence over the bell, which is
    the whole subtlety here. It has no glyph of its own because a snoozed row
    renders only inside its repo's `▾ N snoozed` fold, which says it once for the
    group — but `pr-nudge` is `PR.nudge_issue` and is never blanked for a snoozed
    PR, so a snooze that later goes CI-red would otherwise light a 🔔 that
    `should_nudge` will never ring (it gates on `quiet = muted or snoozed`).
    Dropping the glyph must not drop the suppression. That leaves the slot free
    for the 🔇 of a row that is muted *and* snoozed, the one thing left worth
    saying inside the fold — mute wins there, matching `row_tooltips`' order."""
    if muted:
        glyph, style = ICON_PR_MUTED, "yellow"
    elif snoozed:
        return Text(" " * _STATUS_SLOT)
    elif nudge:
        glyph, style = ICON_PR_NUDGE, "yellow"
    else:
        return Text(" " * _STATUS_SLOT)
    return Text.assemble((glyph, style), " " * (_STATUS_SLOT - cell_len(glyph)))


def _workspace_cell(
    wt: Worktree,
    repo_color: str | None,
    *,
    muted: bool,
    nudge: bool,
    snoozed: bool = False,
    depth: int = 0,
) -> Text:
    """The workspace name, tinted with the repo's cmux colour when set and
    prefixed with one status glyph: 🔇 when the PR's nudges are muted, else 🔔
    when the PR has an actionable, unsilenced nudge condition (failing CI /
    unresolved threads / conflicts on an OPEN PR — the `pr-nudge` cell). Mute
    silences the nudge, so the two can't coexist. A snooze silences it too, so it
    suppresses the bell as well — but paints no glyph of its own, since the row
    sitting inside the repo's `▾ N snoozed` fold already says it (`_status_glyph`).

    The slot is `_STATUS_SLOT` cells wide whichever glyph lands in it, and a row
    with none pays it in blanks — otherwise a quiet row's label would sit three
    columns left of its belled neighbour's and the column would read ragged.

    Every row hangs under its repo's header behind `ROW_INDENT` — header and row
    share the Workspace column, so the indent is what makes the grouping read as
    nesting instead of as two rows at the same level.

    `depth` is the row's place in a stacked-PR chain: 0 for an unstacked row or
    the stack's tip (which heads the group), 1 for a PR the tip is stacked on,
    indented a further `_STACK_INDENT` behind a `└`. `stack_order` never returns
    a deeper level — a stack nests exactly once, so the whole chain reads as one
    group.

    Same-named worktrees across repos are disambiguated by their group-header
    row, not a `repo/` prefix, so the label renders bare."""
    label = _display_label(wt)
    colorizer = CMUX_COLOR_ANSI.get(repo_color or "")
    if colorizer is not None:
        # Reuse the exact cmux colorizer (the source of truth) → parse its ANSI.
        cell = Text.from_ansi(colorizer(label))
    else:
        cell = Text(label, style="bold")
    cell = Text.assemble(_status_glyph(muted=muted, snoozed=snoozed, nudge=nudge), cell)
    if depth:
        cell = Text.assemble((f"{_STACK_INDENT}└ ", "dim"), cell)
    return Text.assemble(ROW_INDENT, cell)


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


def _diff_comments_cell(raw: str) -> Text:
    """The 📝 column: pending (undelivered) cmux diff-viewer comments, from the
    daemon-written `diff-comments` cell. Local only — never reaches GitHub, and
    a different count than `💬`'s GitHub review threads. Blank at zero. `a`
    delivers them and re-reads the store live at send time, so this is a cue
    that something's waiting, not the authority on what will actually send."""
    try:
        count = int(raw or 0)
    except ValueError:
        return Text("")
    return Text(str(count), style="cyan") if count > 0 else Text("")


def _comments_cell(unaddressed_raw: str, total_raw: str) -> Text:
    """The 💬 column: unaddressed review-thread count, with a `/total` denominator
    when there are addressed threads too.

    Reads the daemon-written `pr-comments` (unaddressed) and `pr-comments-total`
    (threads opened by others) cells. Renders:
      - blank only when no thread from others exists at all;
      - `0/T` (green) when every thread has been handled — the count is still
        worth seeing, and green reads as "nothing owed";
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
        return Text(f"0/{total}", style="green") if total > 0 else Text("")
    label = f"{unaddressed}/{total}" if total > unaddressed else str(unaddressed)
    return Text(label, style="red")


def _tickets_of(payload: dict | None) -> list[dict]:
    """The delivered ticket entries of a cached PR snapshot (`[]` when the PR
    delivers none, or there's no snapshot yet)."""
    return ((payload or {}).get("ticket") or {}).get("tickets") or []


def _ticket_ids(payload: dict | None, provider: str) -> str:
    """The untruncated Ticket-cell text: the delivered id(s), comma-joined —
    except Trello, whose ids are opaque short links, so `ticket_display` hands
    back the cached card number(s) (id fallback). Empty with no delivered
    tickets. Shared by the cell and its hover tooltip so the two can't drift."""
    return ", ".join(
        ticket_display(t, provider, missing="?") for t in _tickets_of(payload)
    )


def _linear_cells(payload: dict | None, provider: str) -> tuple[Text, Text]:
    """Delivered ticket id(s) and workflow state(s) from the cached per-PR block,
    as two cells. The Ticket cell is `_ticket_ids` capped at `_TICKET_MAX` (the
    full text lands on the hover tooltip). The Status cell is one workflow-state
    *icon* per ticket (space-joined), each tinted by its own
    `_linear_status_icon` style. Both blank when there are no delivered
    tickets."""
    tickets = _tickets_of(payload)
    if not tickets:
        return Text(""), Text("")
    ids = _ellipsize(_ticket_ids(payload, provider), _TICKET_MAX)
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
      * ``"snoozed"``   — the PR is snoozed (`pr-snoozed`), so `z` reads "Wake";
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
    if read_text(cwd_cache("pr-num", wt.path)):
        caps.add("pr")
    if read_text(cwd_cache("pr-muted", wt.path)):
        caps.add("muted")
    if read_text(cwd_cache("pr-snoozed", wt.path)):
        caps.add("snoozed")
    if tickets_provider != "none" and (
        (find_pr_payload(wt.branch, repo_name) or {}).get("ticket") or {}
    ).get("tickets"):
        caps.add("ticket")
    if has_workspace:
        caps.add("workspace")
    if wt.is_primary and wt.branch in MAIN_BRANCHES:
        caps.add("primary")
    return frozenset(caps)


def _ticket_link(payload: dict | None) -> str:
    """The delivered ticket's web URL, straight off the daemon-written block
    (`cycle._stamp_ticket_urls`). Empty when the PR delivers no ticket, or when
    the daemon couldn't resolve one (a missing footer link, or a cache written
    before the field existed — the next slow cycle stamps it).

    The **first** ticket, matching what `t` opens: a PR delivering several
    renders them comma-joined in one cell, and one cell carries one link.
    Reading the cached string is the whole point — three of the four providers
    can only find their URL in the PR body, which is a `gh` call a renderer
    isn't allowed to make."""
    tickets = _tickets_of(payload)
    return str((tickets[0].get("url") if tickets else "") or "")


def _cell_links(payload: dict | None, author: str) -> dict[str, str]:
    """Column label → the URL that column's cell becomes a terminal hyperlink to
    (`_apply_links`). Keyed by label rather than index so a column reorder in
    `column_labels` can't silently point a link at the wrong cell.

    One rule: a cell that stands for something on the web links to it. The
    GitHub cluster all points at the PR — except `CI`, which points at its
    checks page, because a red ✗ is the one glyph you click *through* rather than
    just at — the ticket cluster at the tracker, and `Author` at that person's
    GitHub profile. `Workspace` (whose gesture is already double-click → focus),
    the local `✎` dirty count, and `$` name nothing remote and stay unlinked.

    Every URL here is either cached or pure string work; nothing in this module
    may resolve one by asking git, gh or a tracker."""
    links: dict[str, str] = {}
    pr_url = str((payload or {}).get("url") or "")
    if pr_url:
        links["PR"] = links[_APPROVAL_ICON] = links["💬"] = links["Title"] = pr_url
        links["CI"] = f"{pr_url}/checks"
    if author:
        links["Author"] = f"https://github.com/{author}"
    ticket_url = _ticket_link(payload)
    if ticket_url:
        links["Ticket"] = links[_STATUS_ICON] = ticket_url
    return links


def _apply_links(
    cells: list[Text], labels: tuple[str, ...], links: dict[str, str]
) -> None:
    """Turn each linked cell into an OSC 8 terminal hyperlink, in place.

    `Text.stylize` *adds* a span rather than replacing the cell's style, so the
    CI verdict's red and the ticket id's magenta survive — the link rides
    alongside them. Textual passes the link through to the terminal verbatim
    (`Strip.render_style`), so the terminal owns the gesture (⌘/ctrl-click) and
    the underline-on-hover; cockpit paints no underline of its own, which would
    put a rule under most of every row.

    An **empty** cell is skipped: a hyperlink over blank padding is a click
    target with nothing in it, and the columns most often blank (Author, Ticket,
    CI on a PR with no checks) sit right beside ones that aren't."""
    for i, label in enumerate(labels):
        url = links.get(label)
        if url and cells[i].plain.strip():
            cells[i].stylize(f"link {url}")


def _cost_cell(wt: Worktree) -> Text:
    """The `$` cell: total spend across every Claude Code session in this
    worktree, off the daemon-written `wt-cost` cell.

    Blank — never `$0.00` — for a worktree that has cost nothing, because the
    cell is also empty when Claude Code simply never reported (no statusLine, a
    session that predates the cache, a plan that writes zeros). A row can't tell
    those apart, so it says nothing rather than claiming the work was free.
    Under a dollar the cents matter and the row doesn't, so it renders dim."""
    total = read_worktree_cost(wt.path)
    if total <= 0:
        return Text("")
    if total < 1:
        return Text(f"${total:.2f}", style="grey42")
    return Text(f"${total:.0f}", style="grey62")


def worktree_cells(
    wt: Worktree,
    repo_name: str,
    repo_color: str | None,
    tickets_provider: str,
    *,
    show_tickets: bool,
    show_cost: bool = False,
    depth: int = 0,
) -> list[Text]:
    """Build one row's cells (Rich Text, so colours survive), in `column_labels`
    order: PR, Dirty, then the rest of the GitHub cluster (state / CI / comments),
    then the ticket cluster (Ticket / Status) when `show_tickets` (blank for a row
    whose repo has no ticket provider, `tickets_provider == "none"`), then Author
    and Title, and finally `$` when `show_cost`.

    Every cell naming something on the web ends up an OSC 8 terminal hyperlink
    (`_cell_links` / `_apply_links`) — the PR number, its state, CI, comments and
    title to the PR; the ticket cells to the tracker; the author to their
    profile.

    `depth` indents the Workspace cell when the row is stacked on another PR
    (see `_stack_rows`)."""

    def cell(stem: str) -> str:
        return read_text(cwd_cache(stem, wt.path))

    # One snapshot read per row, handed to the ticket cells and the links rather
    # than re-globbed by each of them.
    payload = find_pr_payload(wt.branch, repo_name)
    num, state, ci = cell("pr-num"), cell("pr-state"), cell("pr-checks")
    comments = _comments_cell(cell("pr-comments"), cell("pr-comments-total"))
    title = cell("pr-title")
    author = cell("pr-author")
    state_icon, style = _STATE.get(state, (state, "white"))
    ticket, ticket_status = (
        _linear_cells(payload, tickets_provider)
        if tickets_provider != "none"
        else (Text(""), Text(""))
    )

    cells = [
        _workspace_cell(
            wt,
            repo_color,
            muted=bool(cell("pr-muted")),
            nudge=bool(cell("pr-nudge")),
            snoozed=bool(cell("pr-snoozed")),
            depth=depth,
        ),
        Text(f"#{num}") if num else Text(""),
        _dirty_cell(wt),
        _diff_comments_cell(cell("diff-comments")),
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
        Text(_ellipsize(title, 48), style="grey62"),
    ]
    if show_cost:
        cells += [_cost_cell(wt)]
    _apply_links(
        cells,
        column_labels(show_tickets=show_tickets, show_cost=show_cost),
        _cell_links(payload, author),
    )
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
    _DIFF_COMMENT_ICON: "Pending diff-viewer comments (local only — a sends them)",
    "Title": "PR title",
    "$": "Total Claude Code spend in this worktree",
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
    the ratio out in words. None when the cell is blank."""
    try:
        unaddressed = int(unaddressed_raw or 0)
        total = int(total_raw or 0)
    except ValueError:
        return None
    if unaddressed <= 0:
        if total <= 0:
            return None
        return f"all {total} review thread(s) addressed"
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


def _diff_comments_tooltip(raw: str) -> str | None:
    """Hover text for the 📝 cell. None when the cell is blank."""
    try:
        count = int(raw or 0)
    except ValueError:
        return None
    if count <= 0:
        return None
    plural = "" if count == 1 else "s"
    return f"{count} pending diff comment{plural} — press a to send"


def _ticket_status_tooltip(payload: dict | None, provider: str) -> str | None:
    """Hover text for the 📍 cell — each delivered ticket's `id: state` (the
    workflow-state name the icon abstracts away). Uses the same display handle as
    the Ticket cell (`ticket_display`), so Trello shows the card number rather
    than its opaque short link. None with no delivered tickets."""
    tickets = _tickets_of(payload)
    if not tickets:
        return None
    parts = []
    for t in tickets:
        tid = ticket_display(t, provider, missing="?")
        state = t.get("state")
        parts.append(f"{tid}: {state}" if state else f"{tid}: state unavailable")
    return "; ".join(parts)


def row_tooltips(
    wt: Worktree,
    repo_name: str,
    tickets_provider: str,
    *,
    show_tickets: bool,
    show_cost: bool = False,
) -> list[str | None]:
    """Per-cell hover hints for one worktree row, aligned to `column_labels`
    order. Three jobs: decode the cryptic value columns (workspace glyph, PR
    state, CI, comments, ticket state, dirty); give back whatever the column caps
    truncated (`_LABEL_MAX` / `_TICKET_MAX`) — a clipped cell has to stay
    readable *somewhere*; and name the **destination** of every cell that carries
    a hyperlink (`_cell_links`). That last one is what makes the links
    discoverable at all: an OSC 8 link is invisible until the pointer is over it
    and the modifier is down, so the hover text is where a cell admits it goes
    somewhere. The URL is shown rather than a "⌘-click to open" instruction
    because the modifier is the *terminal's* choice, not cockpit's — and the
    destination is worth reading on its own (which tracker, whose profile).

    The self-evident, unlinked, untruncated text columns are None and fall back
    to the column meaning on hover."""

    def cell(stem: str) -> str:
        return read_text(cwd_cache(stem, wt.path))

    payload = find_pr_payload(wt.branch, repo_name)

    if cell("pr-muted"):
        workspace: str | None = "Nudges muted"
    elif cell("pr-snoozed"):
        workspace = "Snoozed until a new comment or review"
    elif cell("pr-nudge"):
        workspace = "Nudge pending (CI / threads / conflicts)"
    else:
        workspace = None
    full_label = _full_label(wt)
    if full_label != _display_label(wt):
        workspace = f"{full_label} — {workspace}" if workspace else full_label

    tips: list[str | None] = [
        workspace,
        None,  # PR #
        _dirty_tooltip(wt),
        _diff_comments_tooltip(cell("diff-comments")),
        _STATE_LABEL.get(cell("pr-state")),
        _CI_LABEL.get(cell("pr-checks")),
        _comments_tooltip(cell("pr-comments"), cell("pr-comments-total")),
    ]
    if show_tickets:
        tips += [
            # Ticket id — self-evident, so only worth a hint when `_TICKET_MAX`
            # clipped it (several delivered ids, or a Trello card name).
            _ticket_ids(payload, tickets_provider) or None
            if tickets_provider != "none"
            else None,
            _ticket_status_tooltip(payload, tickets_provider)
            if tickets_provider != "none"
            else None,
        ]
    tips += [None, None]  # Author, Title
    if show_cost:
        # The cell rounds whole dollars away; the hint is where the exact
        # figure lives. Blank cell → no hint, so hovering falls back to the
        # column meaning rather than asserting "$0.00".
        total = read_worktree_cost(wt.path)
        tips += [f"${total:.2f} across all sessions here" if total > 0 else None]
    labels = column_labels(show_tickets=show_tickets, show_cost=show_cost)
    for label, url in _cell_links(payload, cell("pr-author")).items():
        i = labels.index(label)
        tips[i] = f"{tips[i]} — {url}" if tips[i] else url
    return tips


class WorktreeTable(DataTable):
    DEFAULT_CSS = """
    WorktreeTable { width: 1fr; height: 1fr; }
    """

    # Override DataTable's Enter→select_cursor so Enter raises FocusRequest
    # instead of a RowSelected (which a *single* click also raises — we don't
    # want single click to focus). Double-click is handled in `on_click`.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "request_focus", "Focus", show=False)
    ]

    class FocusRequest(Message):
        """User asked to focus a row's workspace (Enter or double-click)."""

        def __init__(self, path: str) -> None:
            self.path = path
            super().__init__()

    class NewRequest(Message):
        """User double-clicked a repo group-header row → open the new-workspace
        modal for that repo (a header has no workspace to focus, so its primary
        action is `n`)."""

    class HiddenToggle(Message):
        """User opened the `▸ N repos hidden` disclosure row → expand/collapse the
        parked repos. Raised by a *single* click (unlike the double-click every
        other row needs: expanding is free and reversible, and a disclosure
        triangle that needs a double-click doesn't read as one) and by Enter,
        which every other row spends on Focus."""

    class SnoozedToggle(Message):
        """User opened a repo's `▸ N snoozed` disclosure row → expand/collapse
        that repo's snoozed rows. Same single-click / Enter affordance as
        `HiddenToggle`, for the same reason."""

        def __init__(self, repo_name: str) -> None:
            self.repo_name = repo_name
            super().__init__()

    def __init__(
        self, *, show_tickets: bool = False, show_cost: bool = False, **kwargs: object
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._show_tickets = show_tickets
        self._show_cost = show_cost
        # worktree path → row capability tokens, rebuilt each `update_inventory`
        # so `current_capabilities()` can gate the footer's row keys without a
        # re-read.
        self._row_caps: dict[str, frozenset[str]] = {}
        # row key (worktree path OR header sentinel) → owning repo display name,
        # so `current_repo_name()` resolves the cursor row's repo even on a
        # group-header row (where `current_path()` is None).
        self._row_repo: dict[str, str] = {}
        # repo display name → its configured `sidebar_color`, recorded here so
        # the cursor-row readout can tint itself without a `load_config()` read
        # on every arrow key.
        self._repo_color: dict[str, str | None] = {}
        # worktree path → per-column hover tooltip (aligned to `column_labels`),
        # so `watch_hover_coordinate` decodes a value cell without re-reading the
        # cache on every mouse move. Header rows carry none (fall back to the
        # column meaning).
        self._cell_tooltips: dict[str, list[str | None]] = {}
        # repo display name → the worktree paths its `▸ N snoozed` fold holds,
        # recorded whether or not the fold is open. `A` (ask the fold) has to
        # reach those rows from the collapsed disclosure row, where they have no
        # rows of their own to read a path off — and re-deriving them in the app
        # would be a second authority on fold membership, disagreeing with what
        # is on screen the moment `_split_snoozed`'s tip rule changes.
        self._snoozed_paths: dict[str, list[str]] = {}

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns(
            *column_labels(show_tickets=self._show_tickets, show_cost=self._show_cost)
        )

    def _current_row_key(self) -> str | None:
        """The raw row key under the cursor (a worktree path or a header
        sentinel), or None when the table is empty."""
        if not self.row_count:
            return None
        row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        return row_key.value

    def current_path(self) -> str | None:
        """Worktree path under the cursor, or None on an empty table or any
        synthetic row — a repo group header, the hidden disclosure row, a repo's
        snoozed disclosure row — none of which carries a workspace, so row
        actions no-op there."""
        key = self._current_row_key()
        if key is None or _is_sentinel(key):
            return None
        return key

    def move_cursor_to_key(self, key: str) -> bool:
        """Put the row cursor on `key`, reporting whether that row exists. Used
        after a `z` so the keypress lands somewhere meaningful: the row it
        snoozed has just folded away, and restoring the cursor by *index* (what
        `update_inventory` does) would leave it on an unrelated worktree."""
        try:
            self.move_cursor(row=self.get_row_index(key))
        except RowDoesNotExist:
            return False
        return True

    def current_repo_name(self) -> str | None:
        """The repo display name owning the cursor row — the header's own repo on
        a group-header row, or the worktree's repo on a worktree row. None on an
        empty table. Used to default the `n` new-workspace modal's repo picker to
        the row under the cursor even when that row is a header."""
        key = self._current_row_key()
        return self._row_repo.get(key) if key is not None else None

    def current_repo_color(self) -> str | None:
        """The cursor row's repo `sidebar_color`, or None when it has none (a
        parked repo's revealed header drops its tint, so it reads as None here
        too — matching what `_header_cells` renders)."""
        name = self.current_repo_name()
        return self._repo_color.get(name) if name is not None else None

    def snoozed_paths(self, repo_name: str | None) -> list[str]:
        """The worktree paths inside `repo_name`'s snoozed fold, open or shut.

        `A` fans out over exactly the rows the `▸ N snoozed` disclosure row
        stands for, so it reads the membership the render itself recorded rather
        than re-deriving it — the fold takes a stack whole (`_split_snoozed`
        partitions at chain granularity), and a second derivation would sooner
        or later disagree with what the row's own count says."""
        if repo_name is None:
            return []
        return list(self._snoozed_paths.get(repo_name, ()))

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
        labels = column_labels(
            show_tickets=self._show_tickets, show_cost=self._show_cost
        )
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

    def _disclosure_message(self, key: str | None) -> Message | None:
        """The expand/collapse message for a disclosure row, or None for any
        other row. Shared by Enter and the single click — the two rows whose
        entire purpose is "open me" would otherwise each need saying twice."""
        if key == HIDDEN_ROW_KEY:
            return self.HiddenToggle()
        if key is not None and key.startswith(SNOOZED_KEY_PREFIX):
            return self.SnoozedToggle(key[len(SNOOZED_KEY_PREFIX) :])
        return None

    def action_request_focus(self) -> None:
        # Enter on a disclosure row expands/collapses it, same as its key (`h` /
        # `z`) and a single click. `current_path()` is None there (a sentinel
        # key), so without this the rows that exist to be opened would be the
        # ones Enter ignored.
        opened = self._disclosure_message(self._current_row_key())
        if opened is not None:
            self.post_message(opened)
            return
        path = self.current_path()
        if path:
            self.post_message(self.FocusRequest(path))

    def on_click(self, event: events.Click) -> None:
        # Resolve the clicked row from the event, NOT the cursor: Textual's MRO
        # walk takes each class's `_on_click` *or* `on_click`, and WorktreeTable
        # defines only the latter — so this runs BEFORE `DataTable._on_click`
        # moves the row cursor. Reading the cursor here tests the *previously*
        # selected row, which is why a single click on the disclosure row did
        # nothing until a second click (by then the first had moved the cursor).
        # Move it ourselves; DataTable repeats the same assignment a moment
        # later, idempotently.
        style = getattr(event, "style", None)
        row = style.meta.get("row") if style is not None else None
        if isinstance(row, int) and row >= 0:
            self.cursor_coordinate = Coordinate(row, self.cursor_coordinate.column)
        # Double-click focuses; a single click only moves the cursor — except on
        # a disclosure row, the rows a single click acts on.
        opened = self._disclosure_message(self._current_row_key())
        if opened is not None:
            self.post_message(opened)
            return
        if getattr(event, "chain", 1) >= 2:
            path = self.current_path()
            if path:
                self.post_message(self.FocusRequest(path))
            elif self._current_row_key() is not None:
                # Double-clicked a repo header row (no path) → open new-workspace.
                self.post_message(self.NewRequest())

    def _add_worktree_row(
        self,
        wt: Worktree,
        depth: int,
        *,
        repo_name: str,
        cache_key: str,
        repo_color: str | None,
        tickets_provider: str,
        workspace_paths: set[Path],
    ) -> None:
        """Append one worktree row and its bookkeeping (caps, owning repo, hover
        tooltips). Called twice per repo — once for the live rows, once for the
        snoozed ones a `▾ N snoozed` fold has revealed."""
        key = str(wt.path)
        self.add_row(
            *worktree_cells(
                wt,
                cache_key,
                repo_color,
                tickets_provider,
                show_tickets=self._show_tickets,
                show_cost=self._show_cost,
                depth=depth,
            ),
            key=key,
        )
        self._row_caps[key] = row_capabilities(
            wt,
            cache_key,
            tickets_provider,
            has_workspace=wt.path.resolve() in workspace_paths,
        )
        self._row_repo[key] = repo_name
        self._cell_tooltips[key] = row_tooltips(
            wt,
            cache_key,
            tickets_provider,
            show_tickets=self._show_tickets,
            show_cost=self._show_cost,
        )

    def update_inventory(
        self,
        inventory: Inventory,
        workspace_paths: set[Path] | None = None,
        hidden_repos: set[str] | None = None,
        expanded: bool = False,
        expanded_snoozed: set[str] | None = None,
    ) -> None:
        """Rebuild rows from the worktree inventory, keeping the cursor on the
        same row index so a refresh doesn't yank the selection away. Each repo
        gets a group-header row followed by its worktree rows.

        `workspace_paths` is the set of resolved cwds that currently have a live
        workspace (from the app's per-refresh `workspace_cwds()` read); a row
        whose path is in it gets the `"workspace"` cap.

        `hidden_repos` is the display names of every repo the user parked with
        `h`. A parked repo is dormant, so it carries no worktrees in the
        inventory: they render as name-only rows under the trailing `▸ N repos hidden`
        disclosure row, and only while `expanded`. Pressing `h` on one of those
        rows un-parks it — the whole hide/unhide loop lives on one key.

        `expanded_snoozed` is the display names of the repos whose `▸ N snoozed`
        fold the user opened with `z` — session-only app state, like
        `expanded`."""
        ws = workspace_paths or set()
        parked = hidden_repos or set()
        open_snoozed = expanded_snoozed or set()
        saved = self.cursor_row
        self.clear()
        self._row_caps = {}
        self._row_repo = {}
        self._repo_color = {}
        self._cell_tooltips = {}
        self._snoozed_paths = {}
        columns = column_labels(
            show_tickets=self._show_tickets, show_cost=self._show_cost
        )
        ncols = len(columns)
        for repo_name, cache_key, repo_color, tickets_provider, wts in inventory:
            self._repo_color[repo_name] = repo_color
            hkey = f"{HEADER_KEY_PREFIX}{repo_name}"
            self.add_row(
                *_header_cells(
                    repo_name, repo_color, ncols, hidden=repo_name in parked
                ),
                key=hkey,
            )
            self._row_caps[hkey] = frozenset({HEADER_CAP})
            self._row_repo[hkey] = repo_name

            add_worktree_row = partial(
                self._add_worktree_row,
                repo_name=repo_name,
                cache_key=cache_key,
                repo_color=repo_color,
                tickets_provider=tickets_provider,
                workspace_paths=ws,
            )
            live, snoozed = _split_snoozed(wts)
            for wt, depth in live:
                add_worktree_row(wt, depth)
            if snoozed:
                # The snoozed band, collapsed behind one disclosure row per repo.
                # It trails the repo's live rows, where the band already sank it.
                open_here = repo_name in open_snoozed
                skey = snoozed_row_key(repo_name)
                self.add_row(
                    *_snoozed_cells(
                        [_full_label(wt) for wt, _ in snoozed],
                        columns,
                        expanded=open_here,
                    ),
                    key=skey,
                )
                caps = {SNOOZED_CAP, FOLD_CAP} | (
                    {EXPANDED_CAP} if open_here else set()
                )
                self._row_caps[skey] = frozenset(caps)
                self._row_repo[skey] = repo_name
                self._snoozed_paths[repo_name] = [str(wt.path) for wt, _ in snoozed]
                # The header stands for the same fold, so `A` is reachable —
                # and advertised — from it too, without scrolling down to the
                # disclosure row. Restamped rather than set at add_row time
                # because whether a fold exists is only known once
                # `_split_snoozed` has run.
                self._row_caps[hkey] = frozenset({HEADER_CAP, FOLD_CAP})
                if open_here:
                    for wt, depth in snoozed:
                        add_worktree_row(wt, depth)
        shown = {repo_name for repo_name, *_ in inventory}
        collapsed = sorted(parked - shown)
        # First row of the hidden section (or the row count when there is none) —
        # the cursor-skip loop below stops here, so expanding the disclosure row
        # doesn't slide the cursor down through every revealed repo.
        hidden_start = self.row_count
        if collapsed:
            self.add_row(
                *_hidden_cells(collapsed, columns, expanded=expanded),
                key=HIDDEN_ROW_KEY,
            )
            caps = {HEADER_CAP, HIDDEN_CAP} | ({EXPANDED_CAP} if expanded else set())
            self._row_caps[HIDDEN_ROW_KEY] = frozenset(caps)
            if expanded:
                for repo_name in collapsed:
                    hkey = f"{HEADER_KEY_PREFIX}{repo_name}"
                    self.add_row(
                        *_header_cells(repo_name, None, ncols, hidden=True), key=hkey
                    )
                    self._row_caps[hkey] = frozenset({HEADER_CAP, PARKED_CAP})
                    self._row_repo[hkey] = repo_name
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
                key and key.startswith(HEADER_KEY_PREFIX) and target + 1 < hidden_start
            ):
                target += 1
                self.move_cursor(row=target)
                key = self._current_row_key()
