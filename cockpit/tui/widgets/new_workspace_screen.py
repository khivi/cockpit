"""Modal text box for creating a new worktree + workspace.

The app's `n` action pushes this screen; on submit it dismisses with a
`(source, repo_path)` tuple, which the app feeds to `spawn.py` — a bare name
(new branch), a PR (`#N` / URL), a Linear id, or a Slack thread URL,
auto-detected by spawn.py (the same path `/cockpit:new` walks) — with
`repo_path` becoming the spawn `cwd`, so
the source resolves against the chosen repo. Empty input / escape dismisses with
`None` (no spawn).

When more than one repo is configured the screen shows a `Select` so a bare
branch name can be routed to any repo (defaulting to the cursor row's repo);
with a single repo there's nothing to pick and only a static hint is shown.

A `use_worktree: false` repo (`no_worktree_paths`) behaves differently: `n`
there creates one *named workspace on the checkout*, not a worktree. Two things
follow — the name Input prefills with the repo name (the one addressable session
per such repo; editable), and once that session exists (`busy_paths`) the repo is
labelled "(open — use f)" and a submit against it is rejected: focusing the lone
session is `f`'s job, not a second `n`. The app maps the dismissed `(source,
path)` onto `cockpit new --cwd <path> --name <source>` for these repos.

Like the rest of the TUI this screen never writes a cell: the spawn it triggers
runs detached in the app and the new worktree surfaces on the next slow tick, so
the daemon stays the sole cache writer.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Select, Static


class NewWorkspaceScreen(ModalScreen["tuple[str, str | None] | None"]):
    """A dismissable text-box overlay returning `(source, repo_path)`.

    `repos` is `[(display_name, expanded_path), ...]`; `default_path` pre-selects
    the matching repo (the cursor row's). The dismiss value is `(source, path)`
    on Enter with a non-blank source, else `None`.
    """

    DEFAULT_CSS = """
    NewWorkspaceScreen { align: center middle; }
    NewWorkspaceScreen > VerticalScroll {
        width: 80%;
        max-width: 90;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    NewWorkspaceScreen .nw-title { text-style: bold; color: $accent; margin-bottom: 1; }
    NewWorkspaceScreen .nw-hint { color: $text-muted; }
    NewWorkspaceScreen Input { margin: 1 0; }
    NewWorkspaceScreen Select { margin: 1 0; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    #: Suffix on a `use_worktree: false` repo that already has its one workspace.
    BUSY_SUFFIX = "  (open — use f)"

    def __init__(
        self,
        repos: list[tuple[str, str]] | None = None,
        default_path: str | None = None,
        *,
        no_worktree_paths: set[str] | None = None,
        busy_paths: set[str] | None = None,
    ) -> None:
        super().__init__()
        self._repos = list(repos or [])
        self._default_path = default_path
        # Only worth a picker when there's more than one repo to choose from.
        self._has_select = len(self._repos) > 1
        # `use_worktree: false` repos: `n` makes a named checkout workspace (not a
        # worktree). `busy` = one already exists → block a second (use `f`).
        self._no_worktree = set(no_worktree_paths or ())
        self._busy = set(busy_paths or ())
        self._name_by_path = {path: name for name, path in self._repos}

    def _default_name(self, path: str | None) -> str:
        """Prefill for the name Input: the repo name for a `use_worktree: false`
        repo (its single session is named after it), else blank (a worktree
        repo's source is a branch/PR/URL the user types)."""
        if path is not None and path in self._no_worktree:
            return self._name_by_path.get(path, "")
        return ""

    def _option_label(self, name: str, path: str) -> str:
        return f"{name}{self.BUSY_SUFFIX}" if path in self._busy else name

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("New workspace", classes="nw-title")
            yield Static(
                "Branch name, PR (#N or URL), Linear id, or Slack thread URL",
                classes="nw-hint",
            )
            if self._has_select:
                # Pre-select the cursor row's repo; fall back to the first if the
                # default isn't among the options (Select rejects a stray value).
                paths = {p for _name, p in self._repos}
                value = (
                    self._default_path
                    if self._default_path in paths
                    else self._repos[0][1]
                )
                yield Select(
                    [
                        (self._option_label(name, path), path)
                        for name, path in self._repos
                    ],
                    value=value,
                    allow_blank=False,
                    id="nw-repo",
                )
            elif self._repos:
                yield Static(
                    f"  (repo: {self._option_label(*self._repos[0])})",
                    classes="nw-hint",
                )
            yield Input(placeholder="fix-login  |  #1234  |  PE-1234", id="nw-input")
            yield Static("", id="nw-error", classes="nw-hint")
            yield Static("enter to create · esc to cancel", classes="nw-hint")

    def on_mount(self) -> None:
        # Typing the name is the primary action; Tab reaches the repo Select.
        self.query_one(Input).focus()
        # Seed the name from the initially-selected repo (repo name for a
        # `use_worktree: false` repo, blank otherwise).
        self.query_one("#nw-input", Input).value = self._default_name(
            self._selected_repo_path()
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        # Switching to a `use_worktree: false` repo seeds its name; switching to a
        # worktree repo leaves the box untouched (no default, and clobbering a
        # typed branch/PR would be hostile).
        if event.select.id != "nw-repo":
            return
        path = None if event.value is Select.BLANK else str(event.value)
        default = self._default_name(path)
        if default:
            self.query_one("#nw-input", Input).value = default
        self.query_one("#nw-error", Static).update("")

    def _selected_repo_path(self) -> str | None:
        if self._has_select:
            value = self.query_one("#nw-repo", Select).value
            if value is not Select.BLANK and value is not None:
                return str(value)
        return self._repos[0][1] if self._repos else None

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the box: hand back (source, repo_path); blank → None (no spawn).
        source = event.value.strip()
        if not source:
            self.dismiss(None)
            return
        path = self._selected_repo_path()
        # A `use_worktree: false` repo allows just one addressable workspace;
        # once it exists, reject a second here — `f` focuses the existing one.
        if path in self._busy:
            name = self._name_by_path.get(path, path)
            self.query_one("#nw-error", Static).update(
                f"[b]{name}[/b] already has a workspace — press f to focus it"
            )
            return
        self.dismiss((source, path))

    def action_cancel(self) -> None:
        self.dismiss(None)
