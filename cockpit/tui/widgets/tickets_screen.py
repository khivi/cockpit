"""Modal overlay listing tickets assigned to me that have no worktree yet.

The one cockpit surface that is *not* derived from `git worktree list`. The main
table answers "how is my work going"; this answers "what should I start", and
`enter` on a row turns the second into the first.

It is a screen rather than a mode of `WorktreeTable` because a ticket has no
worktree path — the key every row, cell writer and row action in that table is
built on. Its own screen means its own key space, and none of the table's row
caps, bands or footer gating has to learn a second meaning.

Read-only, like every other renderer: it is handed the payloads
`orchestrators/ticket_inbox.py` wrote and never fetches, never touches git, and
never writes a cell. Ticket text comes from whoever filed the ticket, so it goes
through `cache.strip_control` on the way in — the payload-derived case the flat
cells' own `read_text` can't cover.

Each org is a fold. Its header row carries a `▸`/`▾` marker and the count, and
`enter` on it opens or closes that org — the one gesture a header has, the way
`z` and `h` toggle the main table's own fold rows. Every org starts closed unless
it is the only one, since a tracker with a hundred cards assigned to you
otherwise buries the org that has three.

Dismisses with the selected ticket's spawn *source* (its URL, falling back to its
id), which is a string `spawn.detect_source` already classifies for all four
providers: a Linear or Jira URL carries its key, a Trello card URL its short
link, a GitHub issue URL its repo and number. That is why starting a ticket needs
no spawn machinery of its own.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static
from textual.widgets.data_table import CellDoesNotExist

from cockpit.lib.cache import strip_control

#: Sentinel prefix for an org header row, so `_selected` can tell one from a
#: ticket. NUL-led for the same reason `WorktreeTable.HEADER_KEY_PREFIX` is: no
#: ticket id can collide with it.
HEADER_KEY_PREFIX = "\x00org:"

#: Header and ticket rows share the Ticket column, so the hierarchy is carried
#: by rendering alone — as in the main table.
ROW_INDENT = "  "

_TICKET_MAX = 26
_TITLE_MAX = 46
_STATE_MAX = 14
_AGE_MAX = 4


def _ellipsize(text: str, limit: int) -> str:
    """Cap `text` at `limit` with a trailing `…`, like the main table's own."""
    return text if len(text) <= limit + 1 else text[:limit] + "…"


def _age(updated_at: str, *, now: datetime | None = None) -> str:
    """`2d` / `6h` / `14m` from an ISO-8601 timestamp, or "" when unparsable.

    Every provider stamps a different flavour (Linear and GitHub use `Z`, Jira a
    `+0000` offset, Trello `Z` again), so the parse is deliberately forgiving and
    a failure costs an empty cell rather than a missing row.
    """
    if not updated_at:
        return ""
    text = updated_at.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    delta = (now or datetime.now(UTC)) - stamp
    minutes = int(delta.total_seconds() // 60)
    if minutes < 0:
        return ""
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 60 * 24:
        return f"{minutes // 60}h"
    return f"{minutes // (60 * 24)}d"


def ticket_source(ticket: dict) -> str:
    """The string `cockpit new` should be handed to start this ticket.

    The URL when there is one: it is the only form that routes a GitHub issue
    (whose `owner/repo#N` id `detect_source` does not classify) and a Trello card
    (whose board a bare short link can't name). The bare id is the fallback, and
    for Linear and Jira the two are equivalent — a URL and a bare key resolve to
    the same mode with the same key extracted.
    """
    return str(ticket.get("url") or "") or str(ticket.get("id") or "")


class TicketsScreen(ModalScreen["str | None"]):
    """Ticket inbox overlay. Dismisses with a spawn source, or None."""

    DEFAULT_CSS = """
    TicketsScreen { align: center middle; }
    TicketsScreen > Vertical {
        width: 90%;
        max-width: 110;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    TicketsScreen .tk-title { text-style: bold; color: $accent; }
    TicketsScreen .tk-hint { color: $text-muted; }
    TicketsScreen DataTable { height: 1fr; margin-top: 1; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q", "cancel", "Close"),
        Binding("t", "open_ticket", "Open in browser"),
    ]

    def __init__(self, buckets: dict[str, list[dict]] | None = None) -> None:
        super().__init__()
        # Bucket order is the caller's; inside a bucket the payload is already
        # newest-first, which is the order `ticket_inbox._dedup` established.
        self._buckets = {k: list(v) for k, v in (buckets or {}).items()}
        self._by_key: dict[str, dict] = {}
        # Every org starts folded, like the sidebar's two trailing piles: a
        # tracker with a hundred cards assigned to you would otherwise bury the
        # org that has three. A lone org is expanded, since folding the only
        # thing on screen leaves a list with nothing in it.
        self._open: set[str] = set(self._buckets) if len(self._buckets) == 1 else set()

    def _count(self) -> int:
        return sum(len(v) for v in self._buckets.values())

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Tickets", classes="tk-title")
            yield Static(self._subtitle(), classes="tk-hint", id="tk-subtitle")
            yield DataTable(id="tk-table", cursor_type="row", zebra_stripes=False)
            yield Static(
                "enter opens an org, or starts the highlighted ticket · "
                "t opens it in the browser · esc to close",
                classes="tk-hint",
            )

    def _subtitle(self) -> str:
        count = self._count()
        if not count:
            # Distinguishing "nothing assigned" from "no provider configured" is
            # the collector's job, not this screen's — it only ever sees written
            # payloads, and a bucket that couldn't be fetched was never written.
            return "Nothing assigned to you that doesn't already have a worktree."
        return f"{count} assigned to you, with no worktree yet"

    def on_mount(self) -> None:
        table = self.query_one("#tk-table", DataTable)
        # Explicit widths, not `DataTable`'s auto-sizing. An auto column is
        # widened from the cells in `_update_dimensions`, which runs on idle —
        # so a fold that adds the first long title paints at the *old* width and
        # the narrow render is cached per cell. Only the row under the mouse
        # re-rendered (hover is part of the cache key), which is exactly how it
        # showed up: every Title clipped to `Title`'s own label, one full row
        # following the pointer. Each cell is ellipsized to the same cap, +1
        # since `_ellipsize` leaves a string one over the limit alone.
        for label, cap in (
            ("Ticket", _TICKET_MAX),
            ("Title", _TITLE_MAX),
            ("State", _STATE_MAX),
            ("Age", _AGE_MAX),
        ):
            table.add_column(label, width=cap + 1, key=label)
        self._rebuild()
        table.focus()

    def _rebuild(self, *, cursor_key: str | None = None) -> None:
        """Repaint every row for the current fold state.

        `DataTable` has no per-row visibility, so a fold is a rebuild. Cheap
        enough: the inbox is a list of tickets assigned to one person, and it is
        only ever repainted on a keypress.
        """
        table = self.query_one("#tk-table", DataTable)
        table.clear()
        self._by_key.clear()
        for bucket, tickets in self._buckets.items():
            marker = "▾" if bucket in self._open else "▸"
            # The count is ellipsized around rather than off: it is the whole
            # reason a folded header is readable at all.
            count = f" ({len(tickets)})"
            name = _ellipsize(strip_control(bucket), _TICKET_MAX - 2 - len(count))
            table.add_row(
                Text(f"{marker} {name}{count}", style="bold"),
                Text(""),
                Text(""),
                Text(""),
                key=f"{HEADER_KEY_PREFIX}{bucket}",
            )
            if bucket not in self._open:
                continue
            for ticket in tickets:
                self._add_ticket(table, bucket, ticket)
        if cursor_key is not None:
            self._move_cursor_to(table, cursor_key)

    def _move_cursor_to(self, table: DataTable, key: str) -> None:
        """Park the cursor back on `key` after a rebuild — the row the user just
        toggled, which has moved by however many rows the fold added or removed.
        """
        with contextlib.suppress(CellDoesNotExist, KeyError):
            table.move_cursor(row=table.get_row_index(key))

    def _add_ticket(self, table: DataTable, bucket: str, ticket: dict) -> None:
        tid = strip_control(str(ticket.get("id") or ""))
        if not tid:
            return
        # Bucket-qualified: one ticket can legitimately sit under two orgs when
        # they share a credential and a key, and a duplicate row key would make
        # the second one silently replace the first.
        key = f"{bucket}\x00{tid}"
        self._by_key[key] = ticket
        # A Trello id is an opaque short link (`6rm3JJPY`); the number on the
        # card (`#122`) is what a human reads off it, so the provider carries it
        # as `handle` and the id stays the key everything else joins on.
        handle = strip_control(str(ticket.get("handle") or "")) or tid
        table.add_row(
            Text(f"{ROW_INDENT}{_ellipsize(handle, _TICKET_MAX - len(ROW_INDENT))}"),
            Text(_ellipsize(strip_control(str(ticket.get("title") or "")), _TITLE_MAX)),
            Text(
                _ellipsize(strip_control(str(ticket.get("state") or "")), _STATE_MAX),
                style="grey62",
            ),
            Text(_age(str(ticket.get("updated_at") or "")), style="grey62"),
            key=key,
        )

    def _cursor_key(self) -> str | None:
        """The cursor row's key, or None when the table is empty."""
        table = self.query_one("#tk-table", DataTable)
        if not table.row_count:
            return None
        try:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except (CellDoesNotExist, IndexError):
            return None
        return str(key) if key else None

    def _selected(self) -> dict | None:
        """The cursor row's ticket, or None on a header (or an empty table)."""
        key = self._cursor_key()
        return self._by_key.get(key) if key else None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter on a ticket starts it; on an org header it opens or folds that
        # org, the one gesture the header row has — as `z` and `h` do on the
        # main table's own fold rows.
        key = self._cursor_key() or ""
        if key.startswith(HEADER_KEY_PREFIX):
            bucket = key.removeprefix(HEADER_KEY_PREFIX)
            if bucket in self._open:
                self._open.discard(bucket)
            else:
                self._open.add(bucket)
            self._rebuild(cursor_key=key)
            return
        ticket = self._selected()
        if ticket is not None:
            self.dismiss(ticket_source(ticket))

    def action_open_ticket(self) -> None:
        ticket = self._selected()
        url = str((ticket or {}).get("url") or "")
        if url:
            self.app.open_url(url)

    def action_cancel(self) -> None:
        self.dismiss(None)
