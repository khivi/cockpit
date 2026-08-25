"""`cockpit watch` as a Textual app — the daemon *is* the TUI.

It owns the pidfile (claimed before construction in `cockpit.cockpit._watch`,
released on unmount), runs the slow + fast ticks itself in thread workers,
shows live countdowns, and renders a read-only, arrow-key-navigable worktree
table. (The log pane that displayed tick output is temporarily removed; stdout
is still captured so prints can't corrupt the screen.)

Design notes (the two footguns this avoids):
  • Stdout capture installs ONE process-wide writer in `on_mount` (a thread-safe
    `queue.SimpleQueue`), not per-tick `redirect_stdout` — the slow and fast tick
    threads would otherwise race on the global stream.
  • Signals use `loop.add_signal_handler`, never `signal.signal` (which raises
    off the main thread). SIGUSR1 kicks a slow tick (how the TUI's own close
    action and `cockpit new` wake the daemon to drain their queued work);
    SIGTERM/SIGHUP ask Textual to
    exit cleanly.

The tick functions are injected as callables so this module never imports back
into `cockpit.cockpit` (avoids a circular import). They are lock-free; the app
serializes slow vs fast under its own `_tick_lock` (acquired inside the worker)
so the header can show "waiting" (blocked on the lock) distinctly from
"running", and per-tick phase gates prevent a timer from launching an
overlapping run of the same tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import queue
import signal
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult

from cockpit.lib import version
from cockpit.lib.cache import (
    branch_cache,
    cost_reporting_available,
    find_pr_payload,
    read_text,
)
from cockpit.lib.cmux import (
    BLUE,
    LOOP_ICON,
    LOOP_KEY,
    CmuxUnavailable,
    cmux,
    cmux_close_workspace_best_effort,
    nudge_if_idle,
    select_workspace,
    spawn_orphan_workspace,
    spawn_pr_workspace,
    was_self_closed,
    workspace_cwds,
    workspace_is_idle,
    workspace_names,
)
from cockpit.lib.config import (
    COCKPIT_HOME,
    CONFIG_PATH,
    ensure_state_dirs,
    load_config,
    repo_tickets,
    repos_grouped_by_org,
    reset_config_cache,
    resolve_theme,
    resolve_tui_theme,
    save_tui_theme,
)
from cockpit.lib.daemon import release_pidfile
from cockpit.lib.daemon_signal import enqueue
from cockpit.lib.events import watch_workspace_events
from cockpit.lib.gh import PR, repo_nwo
from cockpit.lib.git import Worktree, origin_head_branch, worktrees
from cockpit.lib.hidden import is_hidden, load_hidden, toggle_hidden
from cockpit.lib.nudges import (
    NudgePref,
    load_pref,
    pref_key,
    save_pref,
    wake_signature,
)
from cockpit.lib.teardown_types import TeardownRequest
from cockpit.lib.tickets import provider_for
from cockpit.lib.tool import is_cmux, resolve_tool
from cockpit.orchestrators.teardown import resolve_pr_state, worktree_state_blockers
from cockpit.tui.widgets.ask_screen import AskScreen
from cockpit.tui.widgets.config_screen import ConfigCommands, ConfigScreen
from cockpit.tui.widgets.footer_bar import FooterBar
from cockpit.tui.widgets.header_bar import HeaderBar
from cockpit.tui.widgets.new_workspace_screen import NewWorkspaceScreen
from cockpit.tui.widgets.worktree_table import HIDDEN_CAP, Inventory, WorktreeTable

_LOG_TAIL_LINES = 200

# `r` (Read PR) renders body + comments + diff into one `ConfigScreen`, whose
# body is a single `Static`. A large diff would stall the render, so the blob is
# capped — with the drop reported in the body (never a silent truncation) and
# `p` as the escape hatch to the full PR.
_READ_MAX_LINES = 2000

# `gh`'s markdown renderer (glamour) pads every line to the requested
# `GH_FORCE_TTY` width *plus* a 2-column left margin, so asking for the box
# width exactly overruns it by 2 and every prose line wraps a second time,
# orphaning a word onto its own line. Measured, not guessed: request 98 → lines
# 100 wide; request 96 → exactly 98. Two things still overflow and cannot be
# fixed by width — gh's own header line (never wrapped; its length follows the
# branch and repo names) and markdown tables (glamour renders them at natural
# width) — both merely wrap, so they stay readable.
_GLAMOUR_MARGIN = 2

# The `n` (New) action shells out via the same module dispatch the daemon's
# `_bg_spawn_pr` uses: `python -m cockpit.cli new …`. NOT `python spawn.py …` by
# path — that puts the package dir on sys.path[0], where `cockpit.py` shadows the
# `cockpit` package and intra-package imports die (`'cockpit' is not a package`).
# Detached output lands in `spawn.log`.
_SPAWN_LOG = COCKPIT_HOME / "spawn.log"


def _pr_from_payload(p: dict) -> PR:
    """Reconstruct a `PR` from a cached PR payload (`cache.write_pr_cache`'s
    inverse) so the `w` action can reuse `spawn_pr_workspace` — the daemon's own
    spawn helper — for an identical prompt and pills rather than re-deriving
    them. Lossy by design: `author` is empty for self-authored PRs (the cache
    only records a *coworker's* login) — which is exactly what `mine` reads, so
    an `f`-spawned coworker workspace gets the same review-mode seed prompt the
    daemon would have used — and fields absent from the snapshot (`body`,
    `merged_at`) fall back to defaults. The daemon re-applies live pills on its
    next tick, so any drift self-heals within a cycle."""
    return PR(
        number=int(p.get("number") or 0),
        title=str(p.get("title") or ""),
        branch=str(p.get("branch") or ""),
        url=str(p.get("url") or ""),
        author=str(p.get("author") or ""),
        is_draft=bool(p.get("isDraft")),
        review_decision=str(p.get("review") or ""),
        mergeable=str(p.get("mergeable") or ""),
        ci=str(p.get("ci") or ""),
        unaddressed=int(p.get("unaddressed") or 0),
        total_from_others=int(p.get("total") or 0),
        state=str(p.get("state") or "OPEN"),
        updated_at=str(p.get("updatedAt") or ""),
        head_oid=p.get("headRefOid"),
        mine=not p.get("author"),
    )


def _nwo_from_pr_url(url: str | None) -> str | None:
    """`owner/repo` parsed from a cached GitHub PR URL
    (`https://github.com/owner/repo/pull/N`), or None. Resolves a same-repo
    `#N` GitHub issue ref to its repo for the ticket-URL lookup — no network,
    unlike `gh.repo_nwo`."""
    import re

    m = re.match(r"https?://github\.com/([\w.-]+/[\w.-]+)/pull/\d+", url or "")
    return m.group(1) if m else None


class _QueueWriter(io.TextIOBase):
    """A thread-safe stdout/stderr stand-in: every written line goes to a queue.

    Process-global on purpose — it captures prints from both tick threads and
    from leaf modules (gh/git helpers) without touching their code.
    """

    def __init__(self, q: queue.SimpleQueue[str]) -> None:
        self._q = q

    def write(self, s: str) -> int:
        if s and s.strip():
            self._q.put(s.rstrip("\n"))
        return len(s)

    def flush(self) -> None:
        pass


class CockpitApp(App[None]):
    CSS = """
    #table { width: 1fr; height: 1fr; }
    /* Highlight the cursor row with a translucent tint rather than DataTable's
       default solid fill — a solid background forces an auto-contrast
       foreground that clobbers the repo's ANSI color painted into the
       Workspace cell (see WorktreeTable._workspace_cell). No `color:` here:
       WorktreeTable passes cursor_foreground_priority="renderable" so the
       cell's own Rich Text color always wins over this component style. */
    #table > .datatable--cursor { background: $accent 30%; }
    """

    # Add "Show config: …" to the built-in command palette (Ctrl+P).
    COMMANDS = App.COMMANDS | {ConfigCommands}

    BINDINGS = [
        ("s", "sync", "Sync now"),
        ("f", "focus_row", "Focus"),
        ("p", "open_pr", "Open PR"),
        ("t", "open_ticket", "Open ticket"),
        ("r", "read_pr", "Read PR"),
        ("a", "ask_row", "Ask"),
        ("o", "show_output", "Output"),
        ("c", "close_row", "Close"),
        ("C", "force_close_row", "Force close"),
        ("m", "mute_row", "Mute"),
        ("z", "snooze_row", "Snooze"),
        ("N", "nudge_row", "Nudge"),
        ("n", "new_workspace", "New"),
        ("h", "hide_repo", "Hide repo"),
        ("q", "quit", "Quit"),
        ("escape", "dismiss_overlay", "Back"),
    ]

    def __init__(
        self,
        *,
        slow_tick: Callable[..., None],
        fast_tick: Callable[[], None],
        slow_secs: int,
        fast_secs: int,
        self_ws: str | None = None,
    ) -> None:
        super().__init__()
        self._slow_tick = slow_tick
        self._fast_tick = fast_tick
        self._slow_secs = slow_secs
        self._fast_secs = fast_secs
        self._self_ws = self_ws
        # Tick bodies are lock-free; this serializes slow vs fast so we can tell
        # "running" (holds the lock) from "waiting" (blocked on it).
        self._tick_lock = threading.Lock()
        # Per-tick phase: "idle" | "waiting" (on the lock) | "running".
        self._slow_phase = "idle"
        self._fast_phase = "idle"
        self._fast_started = False
        self._next_slow = 0.0
        self._next_fast = 0.0
        # `cmux events` doorbell (lib/events.py): a workspace created or closed
        # out from under us wakes the fast tick immediately instead of waiting
        # out the interval. `_events_pending` coalesces events that arrive while
        # a fast tick is already running — that tick may have read workspace
        # state before they happened, so one more is owed when it lands.
        self._events_stop = threading.Event()
        self._events_pending = False
        self._log_q: queue.SimpleQueue[str] = queue.SimpleQueue()
        # Bounded on-disk tail of tick output (last N lines), rewritten on drain.
        self._log_tail: deque[str] = deque(maxlen=_LOG_TAIL_LINES)
        self._log_path = COCKPIT_HOME / "watch.log"
        self._saved_stdout: object | None = None
        self._saved_stderr: object | None = None
        # repo path → git nwo name (the PR-cache key). `repo_nwo` shells out to
        # `gh`; memoized here since a repo's nwo is stable and the TUI reads the
        # cache on every render. See `_cache_repo_name`.
        self._repo_nwo_cache: dict[str, str] = {}
        # Is the `▸ N hidden` disclosure row expanded? Session-only: the parked
        # *set* persists (`lib/hidden.py`), this peek deliberately doesn't, so a
        # restart comes back tidy.
        self._show_hidden = False

    # ---- lifecycle -------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Log pane temporarily removed — the table runs full-width. stdout is
        # still captured (below) so tick prints can't corrupt the screen.
        cfg = load_config()
        repos = cfg.get("repos", [])
        # Ticket columns + the `t` "open ticket" key appear for any provider
        # (linear OR github) — the open action routes through the row's provider
        # (`tickets.provider_for`), so it's no longer Linear-specific.
        show_tickets = any(repo_tickets(cfg, r) != "none" for r in repos)
        # The `$` column is gated on the *data*, not on config or a plan check:
        # the statusLine blob carries no subscription tier, and some plans report
        # `total_cost_usd: 0` for every session. If nothing has ever reported a
        # non-zero cost there is nothing to show, so the column never appears
        # rather than sitting permanently blank.
        show_cost = cost_reporting_available()
        yield HeaderBar(id="header")
        yield WorktreeTable(
            show_tickets=show_tickets,
            show_cost=show_cost,
            id="table",
            cursor_foreground_priority="renderable",
        )
        # Grouped footer: row keys (left) vs global keys (right). The `t` ticket
        # key shows only when some repo has a ticket provider; backend-divergent
        # keys follow `resolve_tool()` (see FooterBar.BACKEND_ACTIONS). Row keys
        # are further gated per-row by the highlighted row's capabilities
        # (`_refresh_footer_caps`): `p`/`m` need a PR, `t` needs a ticket.
        yield FooterBar(
            self.BINDINGS,
            show_tickets=show_tickets,
            backend=resolve_tool(),
            id="footer",
        )

    def on_mount(self) -> None:
        import sys

        self._saved_stdout, self._saved_stderr = sys.stdout, sys.stderr
        writer = _QueueWriter(self._log_q)
        sys.stdout = writer
        sys.stderr = writer

        self._apply_saved_theme()
        self._set_loop_pill(True)
        self._install_signal_handlers()

        self.query_one(HeaderBar).version_text = version.running_version()

        self._next_slow = time.monotonic() + self._slow_secs
        self.set_interval(1.0, self._update_countdown)
        self.set_interval(0.2, self._drain_log)
        self.set_interval(self._slow_secs, self._kick_slow)

        print(f"slow-tick: every {self._slow_secs}s")
        if self._fast_secs > 0:
            print(f"fast-tick: every {self._fast_secs}s (starts after first slow)")

        # Paint the table immediately from git + the persisted cache so the
        # worktrees show on startup, without waiting for the first (networked)
        # slow tick to finish.
        self._prime_table()

        # Slow first; the fast loop starts only once the slow tick has populated
        # the PR caches (so the first fast republish isn't a no-op).
        self._kick_slow()

        # Independent of that ordering — the doorbell only ever *kicks* the fast
        # tick, which self-guards until `_start_fast` has run.
        self._watch_events()

    def _apply_saved_theme(self) -> None:
        """Apply the persisted `tui_theme`, then persist any later palette pick.

        Textual's theme is in-memory only (resets to $TEXTUAL_THEME each launch,
        see config.TUI_THEME_DEFAULT), so we (a) set it from config on mount and
        (b) subscribe to theme-changed to write the user's Ctrl+P "Change theme"
        pick back to config.json — making the choice survive a restart. An
        unknown name falls back to the App default rather than raising (Textual
        validates `App.theme` against its registered themes). Setting the theme
        before subscribing keeps the initial apply from echoing back to disk;
        `save_tui_theme` also no-ops on an unchanged value as a backstop."""
        name = resolve_tui_theme(load_config())
        if name in self.available_themes:
            self.theme = name
        with contextlib.suppress(Exception):
            # Subscribing requires the app node to be running (true in on_mount);
            # guard so a theme that never persists can't crash startup.
            self.theme_changed_signal.subscribe(self, self._persist_theme)

    def _persist_theme(self, theme: object) -> None:
        save_tui_theme(getattr(theme, "name", str(theme)))

    def _start_fast(self) -> None:
        """Begin the fast tick loop — called on the UI thread after the first
        slow tick completes. Idempotent."""
        if self._fast_secs <= 0 or self._fast_started:
            return
        self._fast_started = True
        self._next_fast = time.monotonic() + self._fast_secs
        self.set_interval(self._fast_secs, self._kick_fast)
        self._kick_fast()

    def on_unmount(self) -> None:
        import sys

        # Stops the reader loop AND kills the `cmux events` child, which would
        # otherwise outlive the TUI (it blocks on a pipe nobody reads).
        self._events_stop.set()
        self._set_loop_pill(False)
        if self._saved_stdout is not None:
            sys.stdout = self._saved_stdout
            sys.stderr = self._saved_stderr
        release_pidfile()

    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            loop.add_signal_handler(signal.SIGUSR1, self._kick_slow)
            loop.add_signal_handler(signal.SIGTERM, self.exit)
            loop.add_signal_handler(signal.SIGHUP, self.exit)
        except (NotImplementedError, ValueError):
            # add_signal_handler is unavailable on some platforms / non-main
            # loops — the TUI still works, only external signals won't route.
            pass

    # ---- ticks -----------------------------------------------------------

    def _kick_slow(self, only_repo: str | None = None) -> None:
        # `only_repo` (a repo path) scopes the kick to one repo — a row keypress
        # refreshes just that row's repo, skipping the `gh` round-trips for every
        # other repo. The periodic interval, SIGUSR1, startup, and the `s` sync
        # key pass None for a full reconcile.
        if self._slow_phase != "idle":
            return
        self._slow_phase = "waiting"
        if only_repo is None:
            # Only a full-cycle kick resets the header countdown — the real
            # cadence is the `set_interval` timer from on_mount, which always
            # calls with only_repo=None. A repo-scoped row-action kick must not
            # desync the header from that timer.
            self._next_slow = time.monotonic() + self._slow_secs
        self._run_slow(only_repo)

    def _kick_fast(self) -> None:
        if self._fast_secs <= 0 or self._fast_phase != "idle":
            return
        self._fast_phase = "waiting"
        self._next_fast = time.monotonic() + self._fast_secs
        self._run_fast()

    @work(thread=True, group="events", exit_on_error=False)
    def _watch_events(self) -> None:
        """Long-lived `cmux events` reader — see lib/events.py. No-ops on
        limux/none. Never touches state: an event only rings `_on_workspace_event`,
        and the tick it wakes re-derives everything from scratch."""
        watch_workspace_events(
            lambda: self.call_from_thread(self._on_workspace_event),
            self._events_stop,
            lambda wsid, cwd: self.call_from_thread(
                self._on_workspace_closed, wsid, str(cwd)
            ),
        )

    def _on_workspace_event(self) -> None:
        """Doorbell (UI thread): a workspace was created or closed."""
        if self._fast_phase != "idle":
            self._events_pending = True  # re-kick when the running tick lands
            return
        self._kick_fast()

    def _on_workspace_closed(self, workspace_id: str, cwd: str) -> None:
        """The X in cmux's sidebar (UI thread) — treat it as the `c` key.

        Closing a workspace is the one close gesture a user can make from
        outside the TUI, and it means what `c` means: I'm done with this
        worktree. It routes to the same `_close_worktree` gate, so a dirty tree,
        unpushed commits, or an open PR still refuse (loudly) and the worktree
        survives — the daemon respawns its workspace next slow tick, which is
        the visible signal that nothing was torn down.

        Two filters run first, both cheap and both on the UI thread:

        - `was_self_closed` drops the closes cockpit made itself. The event says
          nothing about who closed a workspace, and `h`/park, a trailing-fold
          anchor dissolve, the dead-cwd sweep, and teardown's own trailing close
          all reach here otherwise — park in particular is documented as
          workspace-only, so reading it as this gesture would tear down every
          worktree in the parked repo.
        - `quiet=True` makes an unregistered cwd (a hand-made session, a fold
          anchor rooted at `$HOME`) a silent no-op rather than an error toast.
        """
        if was_self_closed(workspace_id):
            return
        self._close_worktree(cwd, quiet=True)

    @work(thread=True, group="slow", exit_on_error=False)
    def _run_slow(self, only_repo: str | None = None) -> None:
        try:
            with self._tick_lock:  # "waiting" until acquired, then "running"
                self._slow_phase = "running"
                # `_publish_inventory` republishes the table after each repo so a
                # finished repo surfaces while later repos are still fetching `gh`,
                # rather than all repos appearing at once when the tick returns.
                # `only_repo` scopes a row-keypress kick to that row's repo.
                self._slow_tick(self._publish_inventory, only_repo)
        except Exception as e:  # a tick must never take the daemon down
            print(f"slow-tick error: {e}")
        finally:
            self._slow_phase = "idle"
            # Each step below is independently guarded: a failure in one (e.g.
            # the very first publish) must never stop `_start_fast` from being
            # reached, or the fast-tick loop would silently never start.
            try:
                self._publish_inventory()
            except Exception as e:
                print(f"slow-tick error: publish failed: {e}")
            try:
                # First slow tick done → the PR caches exist; safe to start fast.
                self.call_from_thread(self._start_fast)
            except Exception as e:
                print(f"slow-tick error: start_fast failed: {e}")

    @work(thread=True, group="prime", exit_on_error=False)
    def _prime_table(self) -> None:
        """Render the table once at startup, off the tick path. Reads only git
        (`git worktree list`) and the persisted flat cache cells a prior daemon
        run left on disk — no network — so rows (and any cached PR/CI/Linear
        cells) appear instantly. The first slow tick refreshes them when it
        completes; this is lock-free since it never writes a cell."""
        try:
            self._publish_inventory()
        except Exception as e:  # priming must never take the daemon down
            print(f"prime error: {e}")

    @work(thread=True, group="fast", exit_on_error=False)
    def _run_fast(self) -> None:
        try:
            with self._tick_lock:  # "waiting" until acquired, then "running"
                self._fast_phase = "running"
                self._fast_tick()
        except Exception as e:
            print(f"fast-tick error: {e}")
        finally:
            self._fast_phase = "idle"
            self._publish_inventory()
            if self._events_pending:
                # Events landed mid-tick — this run may predate them, so owe one
                # more. Cleared before kicking so the next batch can re-arm.
                self._events_pending = False
                self.call_from_thread(self._kick_fast)

    # ---- ui updates ------------------------------------------------------

    @staticmethod
    def _phase_remaining(phase: str, deadline: float, now: float) -> int:
        if phase == "running":
            return -1
        if phase == "waiting":  # blocked on the tick lock
            return -3
        return max(0, int(deadline - now))

    def _update_countdown(self) -> None:
        now = time.monotonic()
        header = self.query_one(HeaderBar)
        header.slow_remaining = self._phase_remaining(
            self._slow_phase, self._next_slow, now
        )
        if self._fast_secs <= 0:
            header.fast_remaining = -2
        elif not self._fast_started:
            header.fast_remaining = -3  # waiting on the first slow tick
        else:
            header.fast_remaining = self._phase_remaining(
                self._fast_phase, self._next_fast, now
            )

    def _drain_log(self) -> None:
        # Drain queued tick output into the bounded on-disk tail — the only
        # sink now that the log pane is out of the layout (and gone entirely).
        new: list[str] = []
        while True:
            try:
                new.append(self._log_q.get_nowait())
            except queue.Empty:
                break
        if not new:
            return
        self._log_tail.extend(new)
        with contextlib.suppress(OSError):
            self._log_path.write_text("\n".join(self._log_tail) + "\n")

    def _cache_repo_name(self, repo: dict) -> str:
        """The repo's git nwo name — the key the daemon writes PR cache files
        under (`{name}__pr-N.json`, `cache._repo_slug`), NOT the config `name`
        label. The label is arbitrary/mutable (`docs/config.md`) and differs
        from the nwo whenever it's set (e.g. label "Envesya" vs repo "beta"), so
        keying the cache by the label misses every file and blanks the
        Ticket/Status cells + row actions. Memoized per path (`repo_nwo` shells
        out to `gh`, the TUI reads the cache on every render). Falls back to the
        path basename on a `gh` failure — off-GitHub repos have no PR cache, so
        the fallback never resolves anything wrong — and does NOT cache that
        fallback, so a transient failure doesn't pin the wrong key."""
        path = Path(os.path.expanduser(repo["path"]))
        key = str(path)
        cached = self._repo_nwo_cache.get(key)
        if cached is not None:
            return cached
        try:
            name = repo_nwo(path)[1]
        except RuntimeError:
            return path.name
        self._repo_nwo_cache[key] = name
        return name

    def _gather_inventory(self, workspace_paths: set[Path] | None = None) -> Inventory:
        """Enumerate worktrees per configured repo. Runs on a worker thread —
        `worktrees()` shells out to git (dirty/unlanded counts).

        A `use_worktree: false` repo works in-place on its checkout, so its one
        row is only meaningful while a workspace is open on it — without one the
        row is a branch name you can't act on (`f`/`c` have nothing to reach).
        Those rows are dropped, leaving just the repo's group header, from which
        `n` starts a workspace on demand. `workspace_paths` is the app's live
        `workspace_cwds()` read; omitted (or empty on a backend hiccup) it hides
        those rows, which is the same "start one when you need it" state.

        Repos sharing an `org` render adjacent (`repos_grouped_by_org`) — with the
        org's `sidebar_color` merged onto each member at load, an org reads as one
        block of same-tinted repos rather than a colour scattered down the table."""
        ws = workspace_paths or set()
        hidden = load_hidden()
        out: Inventory = []
        cfg = load_config()
        for repo in repos_grouped_by_org(cfg):
            path = Path(os.path.expanduser(repo["path"]))
            if not path.is_dir():
                continue
            # A parked repo is dormant — never enumerated, even while the hidden
            # section is expanded (it renders there as a bare name row, from
            # `_hidden_names`, so revealing costs no `git worktree list`).
            if str(path.resolve()) in hidden:
                continue
            try:
                wts = worktrees(path, repo.get("branch_prefix", ""))
            except (RuntimeError, OSError):
                continue
            if not repo.get("use_worktree", True):
                wts = [wt for wt in wts if wt.path.resolve() in ws]
            out.append(
                (
                    repo.get("name") or path.name,
                    self._cache_repo_name(repo),
                    repo.get("sidebar_color"),
                    repo_tickets(cfg, repo),
                    wts,
                )
            )
        return out

    @staticmethod
    def _live_workspace_paths() -> set[Path]:
        """Resolved cwds that currently have a live workspace — one
        `workspace_cwds()` read per refresh, feeding the row `"workspace"` cap.
        Degrades to empty when the backend is absent/erroring (tool=none, cmux
        hiccup), so those rows simply advertise `w` (spawn) rather than crash."""
        try:
            return {p.resolve() for p in workspace_cwds().values()}
        except CmuxUnavailable:
            return set()

    def _publish_inventory(self) -> None:
        """Re-gather worktrees and refresh the table. Safe to call from a worker
        thread (the slow tick's per-repo `on_repo_done` hook): `_gather_inventory`
        is a pure git + cache-cell read, and `call_from_thread` marshals the
        render onto the UI thread — the same two steps the tick's `finally` runs."""
        ws_paths = self._live_workspace_paths()
        inv = self._gather_inventory(ws_paths)
        self.call_from_thread(self._render_table, inv, ws_paths, self._hidden_names())

    @staticmethod
    def _hidden_names() -> set[str]:
        """Display names of the repos parked with `h` — what the table needs to
        build the disclosure row and its revealed repo rows."""
        hidden = load_hidden()
        return {
            repo.get("name") or Path(os.path.expanduser(repo["path"])).name
            for repo in load_config().get("repos", []) or []
            if str(Path(os.path.expanduser(repo["path"])).resolve()) in hidden
        }

    def _render_table(
        self,
        inventory: Inventory,
        workspace_paths: set[Path] | None = None,
        hidden_names: set[str] | None = None,
    ) -> None:
        self.query_one(WorktreeTable).update_inventory(
            inventory, workspace_paths, hidden_names, expanded=self._show_hidden
        )
        # A refresh can change the highlighted row's state (PR/ticket/mute) or
        # the row set, so re-gate the footer's row keys to the current row.
        self._refresh_footer_caps()

    def _refresh_footer_caps(self) -> None:
        """Push the highlighted row's capabilities to the footer so its row-key
        hints follow the selection: `p`/`m` only when the row has a PR, `l` only
        with a ticket, and `m` reads "Unmute" when the row's PR is muted. Cheap
        (cache-cell reads) and UI-thread only. None (no row) → the footer shows
        the full row-key set."""
        with contextlib.suppress(Exception):
            caps = self.query_one(WorktreeTable).current_capabilities()
            self.query_one(FooterBar).set_row_state(caps)

    def _repo_config_by_name(self, name: str | None) -> dict | None:
        """The full config dict for the repo whose display name is `name` (as
        `_gather_inventory` derives it). Falls back to the sole repo when only
        one is configured, so the command works even before the first render."""
        repos: list[dict] = load_config().get("repos", []) or []
        if name is not None:
            for repo in repos:
                display = (
                    repo.get("name") or Path(os.path.expanduser(repo["path"])).name
                )
                if display == name:
                    return repo
        return repos[0] if len(repos) == 1 else None

    def action_show_full_config(self) -> None:
        cfg = load_config()
        # Surface both active themes: `theme` (dark|light palette driving cmux
        # pills + starship footer) and `tui_theme` (this TUI's live Textual theme
        # — reads `self.theme`, so it reflects an unsaved palette change too).
        header = (
            f"theme     (pills / footer): {resolve_theme(cfg)}\n"
            f"tui_theme (this TUI):        {self.theme}\n\n"
        )
        self.push_screen(
            ConfigScreen("config: all", header + json.dumps(cfg, indent=2))
        )

    def action_edit_config(self) -> None:
        """Open config.json in $EDITOR — the one user-driven full-config write
        (same sanctioned exception as `save_tui_theme`). Suspends the app so a
        full-screen editor can take over, then re-validates the JSON and drops
        the config cache; a parse error keeps the daemon on its last-good
        in-memory config. Repo/interval changes apply fully on the next daemon
        start; live-read paths pick up the new repo set on the next tick."""
        import shlex
        import subprocess

        ensure_state_dirs()  # seed config.json from the example if absent
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        try:
            with self.suspend():
                subprocess.run([*shlex.split(editor), str(CONFIG_PATH)])
        except (OSError, ValueError) as e:
            self.notify(f"could not open editor: {e}", severity="error", timeout=8.0)
            return
        try:
            json.loads(CONFIG_PATH.read_text())
        except (OSError, ValueError) as e:
            self.notify(
                f"config.json has invalid JSON — not reloaded: {e}",
                severity="error",
                timeout=10.0,
            )
            return
        reset_config_cache()
        self.notify("config saved — restart cockpit to apply fully", timeout=6.0)

    def action_show_output(self) -> None:
        # Captured tick output (bounded log tail) in a dismissable overlay
        # (ConfigScreen is a generic text modal). Snapshot, not live.
        body = "\n".join(self._log_tail) or "(no tick output yet)"
        self.push_screen(ConfigScreen("slow / fast output", body))

    def action_dismiss_overlay(self) -> None:
        # Escape: close the help panel if open, else pop a modal back toward the
        # table (no-op on the base screen; modals with their own escape binding
        # handle it first). Named to avoid overriding Textual's async `action_back`.
        if self.query("HelpPanel"):
            with contextlib.suppress(Exception):
                self.action_hide_help_panel()
            return
        if len(self.screen_stack) > 1:
            self.pop_screen()

    # ---- actions ---------------------------------------------------------

    def action_sync(self) -> None:
        print("kick: manual sync — running cycle now")
        self._kick_slow()

    def _row_act(self, fn: Callable[[str], object]) -> None:
        # Shared by the action_*_row methods below: resolve the highlighted
        # row's path once and invoke `fn` on it, no-op when no row is selected.
        path = self.query_one(WorktreeTable).current_path()
        if path:
            fn(path)

    def action_focus_row(self) -> None:
        self._row_act(self._focus_worktree)

    def action_open_pr(self) -> None:
        self._row_act(self._open_pr_url)

    def action_open_ticket(self) -> None:
        self._row_act(self._open_ticket_url)

    def action_close_row(self) -> None:
        self._row_act(self._close_worktree)

    def action_force_close_row(self) -> None:
        self._row_act(lambda path: self._close_worktree(path, force=True))

    def action_mute_row(self) -> None:
        self._row_act(self._toggle_mute)

    def action_snooze_row(self) -> None:
        self._row_act(self._toggle_snooze)

    def action_nudge_row(self) -> None:
        self._row_act(self._send_nudge)

    def action_read_pr(self) -> None:
        # Width is read here, on the main thread, and passed down: it sizes
        # glamour's hard wrap to the overlay's body box (see `_read_pr`).
        width = ConfigScreen.content_width(self.size.width)
        self._row_act(lambda path: self._read_pr(path, width))

    def action_ask_row(self) -> None:
        """`a` — type a line and send it to this row's Claude session.

        The text half of `N`: same gated send (`nudge_if_idle`), but the message
        is yours rather than the canned nudge prose. The modal is pushed here on
        the main thread (`push_screen` requires it) and the resolve+send runs on
        a worker, so the git/cmux round-trips never block the UI."""
        path = self.query_one(WorktreeTable).current_path()
        if path:
            self.push_screen(AskScreen(), lambda text: self._on_ask(path, text))

    def _on_ask(self, path_str: str, text: str | None) -> None:
        # Escape / blank input dismisses with None — nothing to send.
        if text:
            self._send_ask(path_str, text)

    def action_hide_repo(self) -> None:
        """`h` — the one hide/unhide key, read off the cursor row:

        - on the `▸ N hidden` disclosure row → expand/collapse the parked repos
          (`_toggle_hidden_section`), so unhiding is reachable without a second
          keybinding to remember;
        - on a revealed parked repo's row → un-park it;
        - anywhere else → park the cursor row's whole repo.

        Park is repo-scoped, not row-scoped, so it works from a group header or
        any of its worktree rows. A parked repo goes dormant: `cycle_all` skips
        it entirely, so it costs no `gh` round-trip and gets no auto-spawn or
        nudge until un-parked. The repo stays registered in `config.json` — this
        is parking, not unregistering.

        Parking also clears the repo out of the *cmux sidebar* (`_park_workspaces`)
        — hiding the TUI row while its workspaces still sit in the sidebar would
        only move the clutter. Un-parking does not respawn them: a
        worktree-managed repo gets its workspaces back from the next slow tick's
        `_spawn_missing_workspaces`, a `use_worktree: false` one from `n`/`f`."""
        table = self.query_one(WorktreeTable)
        if HIDDEN_CAP in (table.current_capabilities() or frozenset()):
            self._toggle_hidden_section()
            return
        repo = self._repo_config_by_name(table.current_repo_name())
        if repo is None:
            self.notify("no repo under the cursor", severity="warning")
            return
        path = Path(os.path.expanduser(repo["path"]))
        name = repo.get("name") or path.name
        parked = toggle_hidden(path)
        self.notify(
            f"{name} hidden — h on the ▸ hidden row to reveal"
            if parked
            else f"{name} un-hidden"
        )
        if parked:
            # Shells out to git + cmux, so it runs on a worker; it republishes
            # the table itself when done.
            self._park_workspaces(path, repo.get("branch_prefix", ""))
            return
        # Local re-render only (git + cache cells, no network): parking must not
        # cost a `gh` round-trip, and un-parking picks up fresh data on the next
        # slow tick anyway.
        self._prime_table()

    @work(thread=True, group="park", exit_on_error=False)
    def _park_workspaces(self, repo_path: Path, branch_prefix: str) -> None:
        """Close the parked repo's cmux workspaces so it leaves the sidebar too.

        Workspace-only, like the `c` key on a primary checkout: no worktree is
        removed, no branch deleted, nothing uncommitted is touched — parking is
        not teardown, and the only thing lost is the terminal session.

        Matched by cwd against the repo's own worktrees (`git worktree list`),
        not by name — a worktree usually lives in a *sibling* directory, so a
        path-prefix test would miss it. Two workspaces are always spared: the one
        the daemon itself runs in (closing it would kill this TUI) and any that
        isn't idle, since a running agent mid-turn shouldn't be cut off. A busy
        one is reported, never silently skipped."""
        try:
            wts = worktrees(repo_path, branch_prefix)
            paths = {repo_path.resolve(), *(wt.path.resolve() for wt in wts)}
            cwds = workspace_cwds()
        except (CmuxUnavailable, RuntimeError, OSError) as e:
            print(f"park: could not enumerate workspaces for {repo_path}: {e}")
            return
        closed, busy = 0, 0
        for ref, cwd in cwds.items():
            if cwd.resolve() not in paths or ref == self._self_ws:
                continue
            if not workspace_is_idle(ref):
                busy += 1
                continue
            if cmux_close_workspace_best_effort(ref):
                closed += 1
        if busy:
            self.call_from_thread(
                self.notify,
                f"{busy} workspace(s) still running — left open",
                severity="warning",
            )
        if closed or busy:
            print(f"park {repo_path.name}: closed {closed}, kept {busy} busy")
        self._publish_inventory()

    def _toggle_hidden_section(self) -> None:
        """Expand / collapse the parked repos for this session. No key of its
        own — reached by `h` or a click on the disclosure row. Re-renders locally
        (`_prime_table`): revealing must never cost a `gh` round-trip."""
        self._show_hidden = not self._show_hidden
        self._prime_table()

    def on_worktree_table_hidden_toggle(
        self, event: WorktreeTable.HiddenToggle
    ) -> None:
        """Click on the `▸ N hidden` disclosure row → same as `h` there."""
        self._toggle_hidden_section()

    def action_new_workspace(self) -> None:
        # Spawn a worktree + workspace from the typed source (the `cockpit new`
        # path). The modal offers a repo picker (when more than one is
        # configured) so a bare branch name can be routed to any repo; it
        # defaults to the cursor row's repo, which sets spawn.py's cwd. A
        # `use_worktree: false` repo instead gets one named checkout workspace —
        # the modal prefills its name and blocks a second once one exists. A
        # parked repo stays offered but sinks to the bottom, dimmed; spawning
        # into one un-parks it (`_spawn_new`).
        cfg_repos = load_config().get("repos", []) or []
        repos = [
            (
                repo.get("name") or Path(os.path.expanduser(repo["path"])).name,
                str(Path(os.path.expanduser(repo["path"]))),
            )
            for repo in cfg_repos
        ]
        live = self._live_workspace_paths()
        parked = load_hidden()
        hidden_paths = {p for _name, p in repos if str(Path(p).resolve()) in parked}
        no_worktree_paths: set[str] = set()
        busy_paths: set[str] = set()
        for repo in cfg_repos:
            if repo.get("use_worktree", True):
                continue
            path = str(Path(os.path.expanduser(repo["path"])))
            no_worktree_paths.add(path)
            if Path(path).resolve() in live:
                busy_paths.add(path)
        # Default to the cursor row's repo — resolved by repo name so a group-
        # header row (where `current_path()` is None) still preselects its repo.
        default_repo = self._repo_config_by_name(
            self.query_one(WorktreeTable).current_repo_name()
        )
        default_path = (
            str(Path(os.path.expanduser(default_repo["path"])))
            if default_repo
            else None
        )
        self.push_screen(
            NewWorkspaceScreen(
                repos,
                default_path,
                no_worktree_paths=no_worktree_paths,
                busy_paths=busy_paths,
                hidden_paths=hidden_paths,
            ),
            self._spawn_new,
        )

    def _repo_config_by_path(self, path: str | None) -> dict | None:
        """The full config dict for the repo whose path resolves to `path`."""
        if not path:
            return None
        target = Path(path).resolve()
        repos: list[dict] = load_config().get("repos", []) or []
        for repo in repos:
            if Path(os.path.expanduser(repo["path"])).resolve() == target:
                return repo
        return None

    def _spawn_new(self, result: tuple[str, str | None] | None) -> None:
        # Modal callback (UI thread): `(source, repo_path)` or `None`/blank when
        # cancelled. The repo_path the user chose becomes spawn.py's cwd, so its
        # cwd-based discovery routes a bare name into the selected repo. For a
        # `use_worktree: false` repo the source IS a workspace name → spawn a
        # named checkout workspace (`--cwd <path> --name <name>`), no worktree.
        if not result:
            return
        import shlex

        source, cwd = result
        if not source or not source.strip():
            return
        name = source.strip()
        repo = self._repo_config_by_path(cwd)
        # Spawning into a parked repo un-parks it: the repo has to be live for
        # the slow tick `_launch_spawn` kicks to reconcile the new worktree at
        # all (`cycle_all` skips parked repos), and asking for a workspace there
        # is the plainest possible statement that it isn't dormant any more.
        # `notify` directly, not `_notify` — this is the UI thread.
        if cwd and is_hidden(cwd):
            toggle_hidden(cwd)
            label = (repo or {}).get("name") or Path(cwd).name
            self.notify(f"{label} un-hidden — creating a workspace there")
            # Local re-render so the repo leaves the `▸ N hidden` fold now
            # rather than whenever the kicked cycle finishes (same as `h`).
            self._prime_table()
        if repo is not None and not repo.get("use_worktree", True) and cwd:
            spawn_source = f"--cwd {shlex.quote(cwd)} --name {shlex.quote(name)}"
        else:
            spawn_source = name
        self._launch_spawn(spawn_source, cwd)

    def on_data_table_row_highlighted(self, event: object) -> None:
        # Arrow-key navigation moves the row cursor → re-gate the footer's row
        # keys to the newly highlighted row (cache-cell reads only, no network).
        self._refresh_footer_caps()

    def on_worktree_table_focus_request(
        self, event: WorktreeTable.FocusRequest
    ) -> None:
        # Enter / double-click focuses the row's workspace (same as `f`); the
        # table raises FocusRequest only for those, so single-click never yanks
        # cmux focus.
        self._focus_worktree(event.path)

    def on_worktree_table_new_request(self, event: WorktreeTable.NewRequest) -> None:
        # Double-click on a repo header row → same as `n`. action_new_workspace
        # defaults the modal's repo picker to the cursor row's repo, which the
        # click already moved onto the header.
        self.action_new_workspace()

    def _resolve_worktree(self, path_str: str) -> tuple[dict, Worktree] | None:
        """Map a row's worktree-path key back to its (repo config, Worktree).

        Re-derives from `git worktree list` per configured repo — inventory is
        derived, not stored, so a keypress resolves against live state."""
        target = Path(path_str).resolve()
        for repo in load_config().get("repos", []):
            rp = Path(os.path.expanduser(repo["path"]))
            if not rp.is_dir():
                continue
            repo_name = repo.get("name") or rp.name
            try:
                for wt in worktrees(rp, repo.get("branch_prefix", ""), repo_name):
                    if wt.path.resolve() == target:
                        return repo, wt
            except (RuntimeError, OSError):
                continue
        return None

    @staticmethod
    def _workspace_ref(wt: Worktree) -> str | None:
        target = wt.path.resolve()
        return next(
            (ref for ref, p in workspace_cwds().items() if p.resolve() == target),
            None,
        )

    @staticmethod
    def _workspace_ref_by_name(name: str) -> str | None:
        return next((ref for ref, n in workspace_names().items() if n == name), None)

    def _notify(self, message: str, *, severity: str = "information") -> None:
        """Toast feedback, safe from a worker thread. The log pane is removed,
        so a `print` is invisible — a notification is the only on-screen cue."""
        self.call_from_thread(self.notify, message, severity=severity, timeout=4.0)

    @work(thread=True, group="focus", exit_on_error=False)
    def _focus_worktree(self, path_str: str) -> None:
        # `f`: get me into this row's session. Focus the row's workspace,
        # spawning one first when it doesn't have one — a single "take me there"
        # verb (the former `w`/open key folds in here: focus was just spawn's
        # trailing step). On cmux it focuses; on limux (which can spawn but has
        # no select verb) it spawns and the user switches via limux's own UI. The
        # spawn reuses the daemon's exact spawn+pill helpers, so an `f`-spawned
        # workspace is indistinguishable from a daemon-spawned one; the next tick
        # adopts it by cwd (path-keyed, not pill-keyed) so it is never
        # double-spawned. Spawning is not a cache write, so the
        # daemon-is-sole-writer invariant still holds.
        backend = resolve_tool()
        if backend == "none":
            self._notify("open: no workspace backend (tool=none)", severity="warning")
            return
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            self._notify(f"open: no worktree at {path_str}", severity="error")
            return
        repo, wt = resolved
        repo_name = repo.get("name") or Path(os.path.expanduser(repo["path"])).name
        # Re-read live workspaces just before spawning to shrink the window in
        # which the slow tick could spawn the same workspace concurrently. A
        # `use_worktree: false` repo's main checkout can host several sessions all
        # rooted at the same cwd, so cwd-matching can't single out "the repo's
        # session" — its canonical session is the one named after the repo. Prefer
        # that name match there, falling back to the cwd match (and, if none, a
        # spawn).
        ref = None
        if not repo.get("use_worktree", True):
            ref = self._workspace_ref_by_name(repo_name)
        if ref is None:
            ref = self._workspace_ref(wt)
        if ref is not None:
            if backend == "cmux":
                select_workspace(ref)
                self._notify(f"focused {wt.label or wt.short}")
            else:
                self._notify(f"workspace already open: {wt.label or wt.short}")
            return
        payload = (
            find_pr_payload(wt.branch, self._cache_repo_name(repo))
            if wt.branch
            else None
        )
        if payload:
            pr = _pr_from_payload(payload)
            new_ref = spawn_pr_workspace(
                pr, wt, pref=load_pref(pref_key(self._cache_repo_name(repo), pr.number))
            )
        else:
            new_ref = spawn_orphan_workspace(wt)
        if new_ref is None:
            self._notify(f"open failed: {wt.label or wt.short}", severity="error")
            return
        if backend == "cmux":
            select_workspace(new_ref)
            self._notify(f"opened + focused {wt.label or wt.short}")
        else:
            self._notify(f"opened {wt.label or wt.short} — switch via limux")
        self.call_from_thread(
            self._kick_slow, str(Path(os.path.expanduser(repo["path"])))
        )

    def _pr_payload_for_path(self, path_str: str) -> dict | None:
        """The cached PR payload for the row at `path_str` (resolves git), or
        None when the row has no worktree or no cached PR."""
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            return None
        repo, wt = resolved
        return find_pr_payload(wt.branch, self._cache_repo_name(repo))

    @work(thread=True, group="open", exit_on_error=False)
    def _open_pr_url(self, path_str: str) -> None:
        payload = self._pr_payload_for_path(path_str)
        if not payload or not payload.get("url"):
            self._notify("no PR for this row", severity="warning")
            return
        self.call_from_thread(self.open_url, payload["url"])
        self._notify(f"opening PR #{payload.get('number')}")

    @work(thread=True, group="open", exit_on_error=False)
    def _open_ticket_url(self, path_str: str) -> None:
        # Open the row's delivered ticket — provider-neutral, routed through the
        # repo's `TicketProvider.ticket_url` (`tickets.provider_for`). GitHub
        # builds the URL deterministically from the ref + the PR's repo nwo;
        # Linear reads the exact `Linear: [ID](url)` footer link out of the PR
        # body (its canonical URL can't be hand-constructed). The cached block is
        # stored under the (historically named) `linear` key for both providers.
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            self._notify("no worktree for this row", severity="warning")
            return
        repo, wt = resolved
        provider = provider_for(load_config(), repo)
        if provider is None:
            self._notify("tickets not enabled for this repo", severity="warning")
            return
        payload = find_pr_payload(wt.branch, self._cache_repo_name(repo))
        tickets = ((payload or {}).get("ticket") or {}).get("tickets") or []
        if not payload or not tickets:
            self._notify("no ticket for this row", severity="warning")
            return
        ticket_id = str(tickets[0].get("id", ""))
        url = provider.ticket_url(
            ticket_id,
            repo_nwo=_nwo_from_pr_url(payload.get("url")),
            repo_dir=wt.path,
            pr_number=payload["number"],
        )
        if not url:
            self._notify(f"no URL for ticket {ticket_id}", severity="warning")
            return
        self.call_from_thread(self.open_url, url)
        self._notify(f"opening ticket {ticket_id}")

    @work(thread=True, group="close", exit_on_error=False)
    def _close_worktree(
        self, path_str: str, *, force: bool = False, quiet: bool = False
    ) -> None:
        # `c`: refuse on any blocker. `C` (force): override the *soft* open-PR
        # blocker only — hard blockers (uncommitted/unlanded, via
        # `worktree_state_blockers`) still refuse, so force never discards local
        # work. Teardown is enqueued + drained by the daemon (sole writer).
        #
        # `quiet` suppresses only the no-such-worktree toast, for the sidebar-X
        # path (`_on_workspace_closed`), where the closed workspace is routinely
        # not one of ours. Every refusal still toasts — the X gives no other
        # feedback, so a silent one would read as a successful close.
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            if not quiet:
                self._notify(f"close: no worktree at {path_str}", severity="error")
            return
        repo, wt = resolved
        # nwo name, not the config label — `resolve_pr_state`/teardown key the PR
        # cache by it (`find_pr_payload`, `delete_pr_caches_for_branch`), and the
        # daemon wrote those files under the nwo. A label mismatch here would
        # misresolve PR state and leave the cache files undeleted on teardown.
        repo_name = self._cache_repo_name(repo)
        repo_dir = Path(os.path.expanduser(repo["path"]))
        prefix = repo.get("branch_prefix", "")
        # Ownership picks the commit guard: ours holds until the work lands,
        # someone else's closes once their branch is pushed (review over).
        is_mine = wt.branch.startswith(prefix) if (prefix and wt.branch) else True

        # Resolve the PR state ONCE (cache first, one live `gh` fallback) so an
        # out-of-band squash/rebase merge the slow tick never cached as MERGED
        # doesn't false-flag the branch as unlanded — a HARD block `C` can't
        # override. Both the hard gate and the open-PR soft gate read this.
        state, pr_number = resolve_pr_state(wt.path, wt.branch, repo_name)
        pr_is_merged = state == "MERGED"

        # Hard blockers (dirty/unlanded) refuse even under force. A primary
        # checkout (`use_worktree: false`) relaxes the unlanded guard only while
        # it stays on its default branch (a workspace-only close — nothing
        # removed). Parked on a feature branch it's a branch teardown (checkout
        # default + `git branch -D`), so the guard must stand; pass
        # `is_primary=False` there. `default is None` (off-GitHub) can't delete,
        # so it stays workspace-only. `teardown` skips `git worktree remove`
        # either way.
        default_branch = origin_head_branch(repo_dir)
        on_default = default_branch is None or wt.branch == default_branch
        ws_only_close = wt.is_primary and on_default
        hard = worktree_state_blockers(
            wt.path,
            branch=wt.branch,
            is_mine=is_mine,
            pr_merged=pr_is_merged,
            is_primary=ws_only_close,
        )
        if hard:
            self._notify(
                f"close refused {wt.label or wt.short}: "
                + "; ".join(hard)
                + " — commit/push/merge first (C does not override this)",
                severity="warning",
            )
            return
        if not force and state == "OPEN" and pr_number is not None:
            self._notify(
                f"close refused {wt.label or wt.short}: "
                f"PR #{pr_number} is OPEN — press C to force",
                severity="warning",
            )
            return

        ref = self._workspace_ref(wt)
        names = workspace_names()
        req = TeardownRequest(
            ref=ref or wt.branch or wt.short,
            name=(names.get(ref, "") if ref else ""),
            worktree_path=wt.path,
            branch=wt.branch,
            repo_path=repo_dir,
            repo_name=repo_name,
            forced=force,
            # Delete on merge, or when tearing down a primary checkout's feature
            # branch (workspace-only closes on the default branch keep it).
            delete_branch=pr_is_merged or (wt.is_primary and not on_default),
        )
        enqueue(req)
        self._notify(f"queued {'force-' if force else ''}close: {wt.label or wt.short}")
        self.call_from_thread(self._kick_slow, str(repo_dir))

    def _resolve_row_pref(
        self, path_str: str, verb: str
    ) -> tuple[dict, Worktree, int, str, NudgePref] | None:
        # Shared prologue for the two per-PR pref keys (`m` mute, `z` snooze):
        # resolve the row's worktree, read its PR number off the daemon-written
        # `pr-num` cell, and load the pref. `verb` only names the action in the
        # failure toasts. None when the row has no worktree or no PR.
        #
        # Returns the `pref_key` alongside the number so the caller saves under
        # the same repo-scoped key this loaded — a bare number is shared with
        # every other repo's PR of that number.
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            self._notify(f"{verb}: no worktree at {path_str}", severity="error")
            return None
        repo, wt = resolved
        raw = read_text(branch_cache("pr-num", wt.branch)) if wt.branch else ""
        try:
            pr = int(raw)
        except ValueError:
            self._notify(
                f"{verb}: no PR for {wt.label or wt.short}", severity="warning"
            )
            return None
        key = pref_key(self._cache_repo_name(repo), pr)
        return repo, wt, pr, key, load_pref(key)

    @work(thread=True, group="mute", exit_on_error=False)
    def _toggle_mute(self, path_str: str) -> None:
        # Toggle the row PR's nudge-mute (full mute, no expiry — same as
        # `cockpit nudge mute`). Writes a NudgePref, NOT a cache cell, so the
        # daemon stays sole writer; the kicked slow tick republishes the
        # `pr-muted` cell + pills, so the 🔇 glyph catches up within the cycle.
        got = self._resolve_row_pref(path_str, "mute")
        if got is None:
            return
        repo, wt, pr, key, pref = got
        pref.muted = not pref.muted
        pref.until = None
        pref.reason = "muted from TUI" if pref.muted else ""
        save_pref(key, pref)
        self._notify(
            f"{'muted' if pref.muted else 'unmuted'} {wt.label or wt.short} (#{pr})"
        )
        self.call_from_thread(
            self._kick_slow, str(Path(os.path.expanduser(repo["path"])))
        )

    @work(thread=True, group="mute", exit_on_error=False)
    def _toggle_snooze(self, path_str: str) -> None:
        # Toggle the row PR's snooze: "I've read this, it's someone else's turn".
        # Silences the nudge like a mute AND sinks the row into the sidebar's
        # trailing `snoozed` fold — but expires on an *event*, not a clock. Both
        # wake snapshots (review activity + the PR's current actionable issue)
        # are read from the daemon's cached PR payload (no `gh` here), and the
        # slow tick clears the snooze as soon as the live PR disagrees with
        # either (`cycle._resolve_prefs`).
        got = self._resolve_row_pref(path_str, "snooze")
        if got is None:
            return
        repo, wt, pr, key, pref = got
        if pref.snoozed:
            pref.snoozed = False
            pref.wake_on = ""
            pref.wake_nudge = ""
            self._notify(f"woke {wt.label or wt.short} (#{pr})")
        else:
            # nwo name, not the config label — the daemon wrote the payload under
            # the nwo (`_cache_repo_name`). Keying by the label misses every file,
            # so `wake_on` would be built from an empty payload ("0|") and the very
            # next slow tick would wake the snooze it just set.
            payload = find_pr_payload(wt.branch, self._cache_repo_name(repo)) or {}
            pref.snoozed = True
            pref.wake_on = wake_signature(
                int(payload.get("total") or 0), str(payload.get("review") or "")
            )
            pref.wake_nudge = str(payload.get("nudge") or "")
            # A snooze supersedes a mute: mute wins everywhere it's read (glyph,
            # sidebar fold, `quiet`), so leaving it set would silently swallow
            # both the 💤 and its wake. Snooze is the narrower ask, so it takes
            # over — press `m` again for an indefinite mute.
            pref.muted = False
            pref.until = None
            pref.reason = ""
            self._notify(
                f"snoozed {wt.label or wt.short} (#{pr}) — wakes on a new "
                f"comment, review, or CI/conflict issue"
            )
        save_pref(key, pref)
        # ponytail: repo-scoped kick, so the sidebar's `<org> snoozed (N)` fold
        # lags by up to one slow interval. `cycle_all` builds `folds` only when
        # `only_repo is None`, so `_reconcile_review_groups` doesn't run here —
        # everything else (`pr-snoozed`, 💤, the row band, the nudge going quiet)
        # lands on this kick; only the cmux fold waits for the next full cycle.
        # Upgrade path if that bites: kick full-cycle (`_kick_slow(None)`) and
        # pay one `gh` round-trip per repo on the keypress. Do *not* build folds
        # under `only_repo` — a bucket holding no ref from the scoped repo would
        # match nothing and be dissolved by the pass's unguarded sweep, taking
        # every other org's fold down with it.
        self.call_from_thread(
            self._kick_slow, str(Path(os.path.expanduser(repo["path"])))
        )

    @work(thread=True, group="nudge", exit_on_error=False)
    def _send_nudge(self, path_str: str) -> None:
        # Manual nudge NOW (not the slow tick): a deliberate keypress overrides
        # mute + throttle (`nudge_if_idle` without pr_number) but still
        # respects its idle/parked safety gate, so it never types into a running
        # turn or a pending permission prompt. cmux-only; never writes a cache cell.
        if not is_cmux():
            self._notify("nudge requires cmux", severity="warning")
            return
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            self._notify(f"nudge: no worktree at {path_str}", severity="error")
            return
        _repo, wt = resolved
        ref = self._workspace_ref(wt)
        if ref is None:
            self._notify(
                f"nudge: no workspace for {wt.label or wt.short}", severity="warning"
            )
            return
        message = (
            "Check this PR now — CI status, unresolved review comments, and "
            "merge conflicts vs base — and address anything actionable."
        )
        if nudge_if_idle(ref, message):
            self._notify(f"nudged {wt.label or wt.short}")
        else:
            self._notify(
                f"nudge skipped {wt.label or wt.short}: not idle "
                "(busy, awaiting permission, or parked)",
                severity="warning",
            )

    @work(thread=True, group="nudge", exit_on_error=False)
    def _send_ask(self, path_str: str, text: str) -> None:
        # The text half of `N`: same gated send, user-supplied message. Routed
        # through `nudge_if_idle` rather than a raw `cmux send` so the whole
        # idle-gate story applies — a mid-turn or permission-pending session
        # refuses it instead of having the text typed into a y/n prompt. No
        # pref_key: a deliberate keypress overrides mute/snooze, exactly like
        # `N`. `nudge_if_idle` collapses the text to one line (see
        # `cmux.one_line`). Writes no cache cell.
        if not is_cmux():
            self._notify("ask requires cmux", severity="warning")
            return
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            self._notify(f"ask: no worktree at {path_str}", severity="error")
            return
        _repo, wt = resolved
        ref = self._workspace_ref(wt)
        if ref is None:
            self._notify(
                f"ask: no workspace for {wt.label or wt.short} — press f first",
                severity="warning",
            )
            return
        if nudge_if_idle(ref, text, tag="ask"):
            self._notify(f"sent to {wt.label or wt.short}")
        else:
            self._notify(
                f"ask skipped {wt.label or wt.short}: not idle "
                "(busy, awaiting permission, or parked)",
                severity="warning",
            )

    @work(thread=True, group="read", exit_on_error=False)
    def _read_pr(self, path_str: str, width: int) -> None:
        # `r`: read the row's PR — body, review comments, diff — without leaving
        # the dashboard. A keypress-time `gh` read that caches NOTHING: the
        # daemon stays the sole cache writer, and this is the same sanctioned
        # shape as `t`'s Linear branch (which reads `gh.pr_body` on the
        # keystroke). The PR *number* comes off the cached payload, so the
        # fetch is skipped entirely on a row with no PR.
        import subprocess

        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            self._notify(f"read: no worktree at {path_str}", severity="error")
            return
        repo, wt = resolved
        payload = (
            find_pr_payload(wt.branch, self._cache_repo_name(repo))
            if wt.branch
            else None
        )
        number = (payload or {}).get("number")
        if not number:
            self._notify("no PR for this row", severity="warning")
            return
        num = str(number)
        self._notify(f"reading PR #{num}…")

        # `gh` renders markdown (and colours its header) only when stdout is a
        # TTY, and we capture it — so without this the body arrives as *raw*
        # markdown, literal backticks and asterisks and all, which is strictly
        # worse than GitHub. `GH_FORCE_TTY` takes a width: glamour hard-wraps
        # and pads to it, so it is sized to the overlay's body box (a wider
        # value makes every line wrap twice). It is a no-op on `pr diff`, whose
        # output is byte-identical either way, so it is set once for both.
        env = {**os.environ, "GH_FORCE_TTY": str(max(20, width - _GLAMOUR_MARGIN))}

        def _gh(args: list[str]) -> str:
            # Run in the worktree so `gh` resolves the repo from its remote; a
            # failure is rendered inline rather than aborting the overlay, so a
            # fetchable body still shows when only the diff fails.
            try:
                proc = subprocess.run(
                    ["gh", *args],
                    cwd=wt.path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=env,
                )
            except (OSError, subprocess.SubprocessError) as e:
                return f"(gh {' '.join(args)} failed: {e})"
            if proc.returncode != 0:
                return f"(gh {' '.join(args)} failed: {proc.stderr.strip()})"
            return proc.stdout

        # One view call, not two. Under `GH_FORCE_TTY`, `--comments` renders the
        # full view *plus* the discussion, so it is a superset of a bare
        # `pr view` and asking for both would duplicate the body. (Without the
        # forced TTY the flag instead *replaces* the body with the comments,
        # which is why the piped form needed two calls and a
        # skip-the-empty-block dance.) Verified against PRs with 0 and 1
        # comments, where the two forms are byte-identical; `--comments` is
        # still the right one to send, since showing every comment is exactly
        # what the flag is for.
        # `--color always` survives into the overlay: ConfigScreen renders its
        # body through `Text.from_ansi`, so the diff arrives already coloured.
        view = _gh(["pr", "view", num, "--comments"])
        diff = _gh(["pr", "diff", num, "--color", "always"])
        lines = f"{view}\n\n{'─' * 60}\n\n{diff}".splitlines()
        if len(lines) > _READ_MAX_LINES:
            # Never a silent cap — say what was dropped and where to get it.
            dropped = len(lines) - _READ_MAX_LINES
            lines = lines[:_READ_MAX_LINES] + [
                "",
                f"… truncated {dropped} more lines — press p to open the full "
                "PR in a browser",
            ]
        self.call_from_thread(
            self.push_screen,
            ConfigScreen(f"PR #{num} — {wt.label or wt.short}", "\n".join(lines)),
        )

    @work(thread=True, group="new", exit_on_error=False)
    def _launch_spawn(self, source: str, cwd: str | None) -> None:
        # Fire `cockpit new <source>` detached via module dispatch (like the
        # daemon's `_bg_spawn_pr`) so the TUI never blocks on `git fetch` +
        # worktree add. Module dispatch, not `spawn.py` by path — see the
        # `_SPAWN_LOG` note above for why path invocation breaks imports. No
        # auto-teardown
        # to guard against: a worktree is only reaped once its PR merges, so a
        # freshly spawned research/planning worktree is safe by construction.
        # spawn.py writes no cache cell (daemon stays sole writer); the worktree
        # surfaces on the slow tick we kick below. Detached output → spawn.log.
        import shlex
        import subprocess
        import sys
        from typing import IO

        try:
            args = shlex.split(source)
        except ValueError as e:
            self._notify(f"new: bad input: {e}", severity="error")
            return
        if not args:
            return
        cmd = [sys.executable, "-m", "cockpit.cli", "new", *args]
        logfile: IO[bytes] | None = None
        try:
            logfile = open(_SPAWN_LOG, "ab")  # noqa: SIM115 — passed to a detached Popen; must outlive this scope
        except OSError:
            logfile = None
        sink: IO[bytes] | int = logfile if logfile is not None else subprocess.DEVNULL
        try:
            subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=sink,
                stderr=sink,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            self._notify(f"new: failed to launch spawn: {e}", severity="error")
            return
        finally:
            if logfile is not None:
                logfile.close()
        self._notify(f"creating: {source} — surfaces on next sync")
        # `cwd` is the chosen repo's path — scope the kick to it (None → full).
        self.call_from_thread(self._kick_slow, cwd)

    # ---- cmux loop pill --------------------------------------------------

    def _set_loop_pill(self, on: bool) -> None:
        if not self._self_ws:
            return
        try:
            if on:
                cmux(
                    "set-status",
                    LOOP_KEY,
                    LOOP_ICON,
                    "--workspace",
                    self._self_ws,
                    "--color",
                    BLUE,
                    check=False,
                )
            else:
                cmux(
                    "clear-status", LOOP_KEY, "--workspace", self._self_ws, check=False
                )
        except Exception:
            pass
