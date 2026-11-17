"""Modal overlay for `d` ("Day"): where to start today, what changed since
yesterday, and counts for what sits outside the table.

Read-only, like every renderer: the app gathers every row on a worker and hands
them over; the screen never fetches, touches git or writes. A click on a linked
row posts `Follow` and the app acts on it. Titles, commit
subjects and session replies come from outside the process, so they pass
`cache.strip_control`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import ClassVar

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Static

from cockpit.lib.brief import GONE, Change, repo_and_number
from cockpit.lib.cache import strip_control

_NOTHING = Text("Nothing.", style="grey62")
_COMMITS_PER_REPO = 5

#: Maps a row key to the click style for it, or None when the row has no target.
Link = Callable[[str], Style | None]


def _no_link(_key: str) -> Style | None:
    return None


def _styled(base: str, link: Style | None) -> Style:
    return Style.parse(base) + link if link else Style.parse(base)


def _by_repo(changes: list[Change], link: Link = _no_link) -> Text:
    """PR changes → one block per repo, with gone PRs folded into one count."""
    groups: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    gone: dict[str, int] = defaultdict(int)
    for c in changes:
        repo, number = repo_and_number(c.key)
        if c.what == GONE:
            gone[repo] += 1
        else:
            groups[repo].append((c.key, f"#{number}", c.what, c.title))
    out = Text()
    for repo in sorted(groups.keys() | gone.keys()):
        out.append(f"{strip_control(repo)}\n", style="bold")
        for key, handle, detail, title in groups[repo]:
            _row(out, handle, detail, title, link(key))
        if gone[repo]:
            out.append(f"  {gone[repo]} {GONE}\n", style="grey62")
    return out


def _grouped(
    rows: list[tuple[str, str, str, str]],
    link: Link = _no_link,
    cap: int | None = None,
) -> Text:
    """`(repo, handle, detail, trailing)` rows → one block per repo, the first
    `cap` of each. A row's link key is `<repo> <handle>`."""
    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for repo, handle, detail, trailing in rows:
        groups[repo].append((handle, detail, trailing))
    out = Text()
    for repo in sorted(groups):
        out.append(f"{strip_control(repo)}\n", style="bold")
        for handle, detail, trailing in groups[repo][:cap]:
            _row(out, handle, detail, trailing, link(f"{repo} {handle}"))
        if cap is not None and len(groups[repo]) > cap:
            out.append(f"  +{len(groups[repo]) - cap} more\n", style="grey62")
    return out


def _row(
    out: Text, handle: str, detail: str, trailing: str, link: Style | None
) -> None:
    out.append(f"  {strip_control(handle)} ", style=_styled("cyan", link))
    out.append(strip_control(detail), style="yellow")
    out.append(f"  {strip_control(trailing)}\n", style="grey62")


def _tickets_text(rows: list[tuple[str, str, str]], link: Link = _no_link) -> Text:
    out = Text()
    for tid, title, what in rows:
        out.append(f"  {strip_control(tid)} ", style=_styled("cyan", link(tid)))
        out.append(strip_control(what), style="yellow")
        out.append(f"  {strip_control(title)}\n")
    return out


def _start_text(
    start: list[tuple[str, str, str]], more: int, link: Link = _no_link
) -> Text:
    out = Text()
    for i, (_kind, subject, reason) in enumerate(start, 1):
        out.append(f"{i}. ", style="bold")
        out.append(strip_control(reason), style="yellow")
        out.append(f"  {strip_control(subject)}\n", style=_styled("", link(subject)))
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
    BriefScreen Collapsible { padding-bottom: 0; }
    BriefScreen .br-hint { color: $text-muted; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,d", "cancel", "Close"),
    ]

    class Follow(Message):
        """A linked row was clicked. `target` is `focus:<worktree path>` or
        `start:<ticket source>`."""

        def __init__(self, target: str) -> None:
            super().__init__()
            self.target = target

    def __init__(
        self,
        *,
        since: str | None,
        start: list[tuple[str, str, str]],
        more: int = 0,
        changes: list[Change],
        commits: list[tuple[str, str, str, str]] | None = None,
        sessions: list[tuple[str, str, str, str]] | None = None,
        tickets: list[tuple[str, str, str]] | None = None,
        elsewhere: list[str],
        links: dict[str, str] | None = None,
    ) -> None:
        """`links` maps a row key to its `Follow` target: a Start-here subject,
        a PR cache key, `<repo> <worktree label>` for a session, or a ticket id."""
        super().__init__()
        self._since = since
        self._start = start
        self._more = more
        self._changes = changes
        self._commits = commits or []
        self._sessions = sessions or []
        self._tickets = tickets or []
        self._elsewhere = elsewhere
        self._links = links or {}
        self._targets: list[str] = []

    def _link(self, key: str) -> Style | None:
        target = self._links.get(key)
        if not target:
            return None
        self._targets.append(target)
        return Style(meta={"@click": f"screen.follow({len(self._targets) - 1})"})

    def compose(self) -> ComposeResult:
        link = self._link
        with Vertical():
            yield Static("Day", classes="br-title")
            with VerticalScroll():
                yield Static("Start here", classes="br-head")
                yield Static(
                    _start_text(self._start, self._more, link)
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
                        ("PRs", self._changes, lambda: _by_repo(self._changes, link)),
                        (
                            "Commits",
                            self._commits,
                            lambda: _grouped(self._commits, cap=_COMMITS_PER_REPO),
                        ),
                        (
                            "Sessions",
                            self._sessions,
                            lambda: _grouped(self._sessions, link),
                        ),
                        (
                            "Tickets",
                            self._tickets,
                            lambda: _tickets_text(self._tickets, link),
                        ),
                    )
                    shown = [
                        (name, rows, render) for name, rows, render in sections if rows
                    ]
                    for name, rows, render in shown:
                        with Collapsible(title=f"{name} ({len(rows)})"):
                            yield Static(render())
                    if not shown:
                        yield Static(_NOTHING)
                if self._elsewhere:
                    yield Static("Elsewhere", classes="br-head")
                    yield Static("\n".join(self._elsewhere))
            yield Static("click a row to open it · esc to close", classes="br-hint")

    def action_follow(self, index: int) -> None:
        if 0 <= index < len(self._targets):
            self.post_message(self.Follow(self._targets[index]))

    def action_cancel(self) -> None:
        self.dismiss(None)
