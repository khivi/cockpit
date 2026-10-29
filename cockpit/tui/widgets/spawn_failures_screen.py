"""Modal overlay listing background spawns that failed, with their error.

A PR whose `cockpit new --pr` child failed has no worktree, hence no table row,
so without this nothing says why. Read-only, like every renderer: it is handed
the payload `orchestrators/spawn_failures.py` wrote and never fetches, never
touches git, never writes a cell. Error and title text come from outside the
process (a PR title, a child's stderr), so it passes `cache.strip_control`.

Explicit column widths for the reason `tickets_screen` spells out: an auto column
resizes on idle while the cell render cache ignores the width. The error is
ellipsized to its column; the full text rides on the table's tooltip, which
follows the cursor row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from cockpit.lib.cache import strip_control
from cockpit.tui.widgets.tickets_screen import _age, _ellipsize

_PR_MAX = 6
_REPO_MAX = 22
_TITLE_MAX = 28
_FAILS_MAX = 5
_AGE_MAX = 4
_ERROR_MAX = 40


class SpawnFailuresScreen(ModalScreen[None]):
    """Failed background spawns. Escape dismisses; nothing else is actionable."""

    DEFAULT_CSS = """
    SpawnFailuresScreen { align: center middle; }
    SpawnFailuresScreen > Vertical {
        width: 95%;
        max-width: 140;
        height: 60%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    SpawnFailuresScreen .sf-title { text-style: bold; color: $accent; }
    SpawnFailuresScreen .sf-hint { color: $text-muted; }
    SpawnFailuresScreen DataTable { height: 1fr; margin-top: 1; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q", "cancel", "Close"),
    ]

    def __init__(self, failures: list[dict] | None = None) -> None:
        super().__init__()
        # Newest first: the one you just caused is the one you are looking for.
        self._failures = sorted(
            failures or [], key=lambda f: f.get("ts") or 0, reverse=True
        )
        self._errors: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Failed spawns", classes="sf-title")
            yield Static(self._subtitle(), classes="sf-hint")
            yield DataTable(id="sf-table", cursor_type="row", zebra_stripes=False)
            yield Static(
                "The daemon retries on its own; a row clears once the PR has a "
                "worktree or is no longer open · hover the error for its full "
                "text · esc to close",
                classes="sf-hint",
            )

    def _subtitle(self) -> str:
        n = len(self._failures)
        if not n:
            return "No background spawn has failed."
        return f"{n} background spawn{'s' if n != 1 else ''} failed, with no worktree"

    def on_mount(self) -> None:
        table = self.query_one("#sf-table", DataTable)
        for label, cap in (
            ("PR", _PR_MAX),
            ("Repo", _REPO_MAX),
            ("Title", _TITLE_MAX),
            ("Fails", _FAILS_MAX),
            ("Age", _AGE_MAX),
            ("Error", _ERROR_MAX),
        ):
            table.add_column(label, width=cap + 1, key=label)
        for i, f in enumerate(self._failures):
            key = str(i)
            error = strip_control(str(f.get("error") or ""))
            self._errors[key] = error
            ts = f.get("ts")
            age = (
                _age(datetime.fromtimestamp(float(ts), UTC).isoformat())
                if isinstance(ts, int | float)
                else ""
            )
            table.add_row(
                Text(_ellipsize(f"#{f.get('pr', '')}", _PR_MAX)),
                Text(_ellipsize(strip_control(str(f.get("repo") or "")), _REPO_MAX)),
                Text(_ellipsize(strip_control(str(f.get("title") or "")), _TITLE_MAX)),
                Text(_ellipsize(str(f.get("failures") or ""), _FAILS_MAX)),
                Text(age, style="grey62"),
                Text(_ellipsize(error, _ERROR_MAX), style="red"),
                key=key,
            )
        self._sync_tooltip(table)
        table.focus()

    def _sync_tooltip(self, table: DataTable) -> None:
        key = None
        if table.row_count:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.tooltip = self._errors.get(str(key), "") or None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._sync_tooltip(event.data_table)

    def action_cancel(self) -> None:
        self.dismiss(None)
