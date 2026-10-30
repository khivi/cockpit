"""Modal overlay for `b`: what needs you, what changed since yesterday, and
counts for what sits outside the table.

Read-only, like every renderer: the app hands it payload-derived rows and it
never fetches, touches git or writes. Titles come from outside the process, so
they pass `cache.strip_control`.
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


def _by_repo(rows: list[tuple[str, str, str]]) -> Text:
    """`(key, title, detail)` rows → one block per repo."""
    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for key, title, detail in rows:
        repo, number = repo_and_number(key)
        groups[repo].append((number, strip_control(title), detail))
    out = Text()
    for repo in sorted(groups):
        out.append(f"{repo}\n", style="bold")
        for number, title, detail in groups[repo]:
            out.append(f"  #{number} ", style="cyan")
            out.append(f"{detail}", style="yellow")
            out.append(f"  {title}\n", style="grey62")
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
    BriefScreen .br-hint { color: $text-muted; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,b", "cancel", "Close"),
    ]

    def __init__(
        self,
        *,
        since: str | None,
        needs: list[tuple[str, dict]],
        changes: list[Change],
        elsewhere: list[str],
    ) -> None:
        super().__init__()
        self._since = since
        self._needs = needs
        self._changes = changes
        self._elsewhere = elsewhere

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Brief", classes="br-title")
            with VerticalScroll():
                yield Static("Needs you", classes="br-head")
                yield Static(
                    _by_repo(
                        [
                            (k, str(p.get("title") or ""), str(p.get("nudge")))
                            for k, p in self._needs
                        ]
                    )
                    if self._needs
                    else Text("Nothing.", style="grey62")
                )
                if self._since is None:
                    yield Static("Changes", classes="br-head")
                    yield Static(
                        Text(
                            "No baseline yet — changes show from tomorrow.",
                            style="grey62",
                        )
                    )
                else:
                    yield Static(f"Changed since {self._since}", classes="br-head")
                    yield Static(
                        _by_repo([(c.key, c.title, c.what) for c in self._changes])
                        if self._changes
                        else Text("Nothing.", style="grey62")
                    )
                if self._elsewhere:
                    yield Static("Elsewhere", classes="br-head")
                    yield Static("\n".join(self._elsewhere))
            yield Static("esc to close", classes="br-hint")

    def action_cancel(self) -> None:
        self.dismiss(None)
