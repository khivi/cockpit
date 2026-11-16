"""Modal overlay for `d` ("Day"): where to start today, what changed since
yesterday, and counts for what sits outside the table.

Read-only, like every renderer: the app gathers every row on a worker and hands
them over; the screen never fetches, touches git or writes. Titles, commit
subjects and session replies come from outside the process, so they pass
`cache.strip_control`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from cockpit.lib.brief import Change, repo_and_number
from cockpit.lib.cache import strip_control

_NOTHING = Text("Nothing.", style="grey62")


def _by_repo(rows: list[tuple[str, str, str]]) -> Text:
    """`(key, title, detail)` PR rows → one block per repo."""
    grouped = []
    for key, title, detail in rows:
        repo, number = repo_and_number(key)
        grouped.append((repo, f"#{number}", detail, title))
    return _grouped(grouped)


def _grouped(rows: list[tuple[str, str, str, str]]) -> Text:
    """`(repo, handle, detail, trailing)` rows → one block per repo."""
    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for repo, handle, detail, trailing in rows:
        groups[repo].append((handle, detail, trailing))
    out = Text()
    for repo in sorted(groups):
        out.append(f"{strip_control(repo)}\n", style="bold")
        for handle, detail, trailing in groups[repo]:
            out.append(f"  {strip_control(handle)} ", style="cyan")
            out.append(strip_control(detail), style="yellow")
            out.append(f"  {strip_control(trailing)}\n", style="grey62")
    return out


def _tickets_text(rows: list[tuple[str, str]]) -> Text:
    out = Text()
    for tid, what in rows:
        out.append(f"  {strip_control(tid)} ", style="cyan")
        out.append(f"{strip_control(what)}\n", style="yellow")
    return out


def _start_text(start: list[tuple[str, str, str]], more: int) -> Text:
    out = Text()
    for i, (_kind, subject, reason) in enumerate(start, 1):
        out.append(f"{i}. ", style="bold")
        out.append(strip_control(reason), style="yellow")
        out.append(f"  {strip_control(subject)}\n")
    if more:
        out.append(f"+{more} more\n", style="grey62")
    return out


class BriefScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    BriefScreen { align: center middle; }
    BriefScreen > Vertical {
        width: 95%;
        max-width: 120;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    BriefScreen .br-title { text-style: bold; color: $accent; }
    BriefScreen .br-head { text-style: bold; margin-top: 1; }
    BriefScreen .br-sub { text-style: italic; margin-top: 1; }
    BriefScreen .br-hint { color: $text-muted; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,d", "cancel", "Close"),
    ]

    def __init__(
        self,
        *,
        since: str | None,
        start: list[tuple[str, str, str]],
        more: int = 0,
        changes: list[Change],
        commits: list[tuple[str, str, str, str]] | None = None,
        sessions: list[tuple[str, str, str, str]] | None = None,
        tickets: list[tuple[str, str]] | None = None,
        elsewhere: list[str],
    ) -> None:
        super().__init__()
        self._since = since
        self._start = start
        self._more = more
        self._changes = changes
        self._commits = commits or []
        self._sessions = sessions or []
        self._tickets = tickets or []
        self._elsewhere = elsewhere

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Day", classes="br-title")
            with VerticalScroll():
                yield Static("Start here", classes="br-head")
                yield Static(
                    _start_text(self._start, self._more)
                    if self._start
                    else Text("Nothing needs you.", style="grey62")
                )
                if self._since is None:
                    yield Static("Overnight", classes="br-head")
                    yield Static(
                        Text(
                            "No baseline yet — changes show from tomorrow.",
                            style="grey62",
                        )
                    )
                else:
                    yield Static(f"Since {self._since}", classes="br-head")
                    sections = (
                        (
                            "PRs",
                            self._changes,
                            lambda: _by_repo(
                                [(c.key, c.title, c.what) for c in self._changes]
                            ),
                        ),
                        ("Commits", self._commits, lambda: _grouped(self._commits)),
                        ("Sessions", self._sessions, lambda: _grouped(self._sessions)),
                        (
                            "Tickets",
                            self._tickets,
                            lambda: _tickets_text(self._tickets),
                        ),
                    )
                    shown = [(name, render) for name, rows, render in sections if rows]
                    for name, render in shown:
                        yield Static(name, classes="br-sub")
                        yield Static(render())
                    if not shown:
                        yield Static(_NOTHING)
                if self._elsewhere:
                    yield Static("Elsewhere", classes="br-head")
                    yield Static("\n".join(self._elsewhere))
            yield Static("esc to close", classes="br-hint")

    def action_cancel(self) -> None:
        self.dismiss(None)
