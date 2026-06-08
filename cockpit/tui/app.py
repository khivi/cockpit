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
    off the main thread). SIGUSR1 kicks a slow tick (keeps `/cockpit:sync`
    working); SIGTERM/SIGHUP ask Textual to exit cleanly.

The tick functions are injected as callables so this module never imports back
into `cockpit.cockpit` (avoids a circular import). They are lock-free; the app
serializes slow vs fast under its own `_tick_lock` (acquired inside the worker)
so the header can show "waiting" (blocked on the lock) distinctly from
"running", and per-tick phase gates prevent a timer from launching an
overlapping run of the same tick.
"""

from __future__ import annotations

import asyncio
import io
import os
import queue
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Footer

from cockpit.lib import version
from cockpit.lib.cmux import BLUE, LOOP_ICON, LOOP_KEY, cmux
from cockpit.lib.config import load_config
from cockpit.lib.daemon import release_pidfile
from cockpit.lib.git import Worktree, worktrees
from cockpit.tui.widgets.header_bar import HeaderBar
from cockpit.tui.widgets.log_pane import LogPane
from cockpit.tui.widgets.worktree_table import WorktreeTable

_UPDATE_CHECK_SECONDS = 3600

# (repo display name, sidebar_color, linear-enabled, worktrees)
Inventory = list[tuple[str, str | None, bool, list[Worktree]]]


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
    """

    BINDINGS = [
        ("s", "sync", "Sync now"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        slow_tick: Callable[[], None],
        fast_tick: Callable[[], None],
        slow_secs: int,
        fast_secs: int,
        dry: bool = False,
        self_ws: str | None = None,
    ) -> None:
        super().__init__()
        self._slow_tick = slow_tick
        self._fast_tick = fast_tick
        self._slow_secs = slow_secs
        self._fast_secs = fast_secs
        self._dry = dry
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
        self._log_q: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._saved_stdout: object | None = None
        self._saved_stderr: object | None = None

    # ---- lifecycle -------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Log pane temporarily removed — the table runs full-width. stdout is
        # still captured (below) so tick prints can't corrupt the screen.
        show_linear = any(r.get("linear_keys") for r in load_config().get("repos", []))
        yield HeaderBar(id="header")
        yield WorktreeTable(show_linear=show_linear, id="table")
        yield Footer()

    def on_mount(self) -> None:
        import sys

        self._saved_stdout, self._saved_stderr = sys.stdout, sys.stderr
        writer = _QueueWriter(self._log_q)
        sys.stdout = writer
        sys.stderr = writer

        self._set_loop_pill(True)
        self._install_signal_handlers()

        self._next_slow = time.monotonic() + self._slow_secs
        self.set_interval(1.0, self._update_countdown)
        self.set_interval(0.2, self._drain_log)
        self.set_interval(self._slow_secs, self._kick_slow)
        self.set_interval(_UPDATE_CHECK_SECONDS, self._check_update)

        print(f"slow-tick: every {self._slow_secs}s")
        if self._fast_secs > 0:
            print(f"fast-tick: every {self._fast_secs}s (starts after first slow)")

        # Slow first; the fast loop starts only once the slow tick has populated
        # the PR caches (so the first fast republish isn't a no-op).
        self._check_update()
        self._kick_slow()

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

    def _kick_slow(self) -> None:
        if self._slow_phase != "idle":
            return
        self._slow_phase = "waiting"
        self._next_slow = time.monotonic() + self._slow_secs
        self._run_slow()

    def _kick_fast(self) -> None:
        if self._fast_secs <= 0 or self._fast_phase != "idle":
            return
        self._fast_phase = "waiting"
        self._next_fast = time.monotonic() + self._fast_secs
        self._run_fast()

    @work(thread=True, group="slow", exit_on_error=False)
    def _run_slow(self) -> None:
        try:
            with self._tick_lock:  # "waiting" until acquired, then "running"
                self._slow_phase = "running"
                self._slow_tick()
        except Exception as e:  # a tick must never take the daemon down
            print(f"slow-tick error: {e}")
        finally:
            self._slow_phase = "idle"
            inv = self._gather_inventory()
            self.call_from_thread(self._render_table, inv)
            # First slow tick done → the PR caches exist; safe to start fast.
            self.call_from_thread(self._start_fast)

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
            inv = self._gather_inventory()
            self.call_from_thread(self._render_table, inv)

    @work(thread=True, group="update", exit_on_error=False)
    def _check_update(self) -> None:
        if not load_config().get("check_update", True):
            return
        running = version.running_version()
        latest = version.latest_version()
        if latest and version.is_newer(latest, running):
            self.call_from_thread(self._set_update, f"{running} → {latest}")

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
        # The log pane is temporarily out of the layout; drain (and discard) so
        # the queue stays bounded and re-adding a LogPane restores display.
        panes = list(self.query(LogPane))
        while True:
            try:
                line = self._log_q.get_nowait()
            except queue.Empty:
                return
            for pane in panes:
                pane.append(line)

    def _set_update(self, text: str) -> None:
        self.query_one(HeaderBar).update_text = text

    def _gather_inventory(self) -> Inventory:
        """Enumerate worktrees per configured repo. Runs on a worker thread —
        `worktrees()` shells out to git (dirty/unpushed counts)."""
        out: Inventory = []
        for repo in load_config().get("repos", []):
            path = Path(os.path.expanduser(repo["path"]))
            if not path.is_dir():
                continue
            try:
                wts = worktrees(path, repo.get("branch_prefix", ""))
            except (RuntimeError, OSError):
                continue
            out.append(
                (
                    repo.get("name") or path.name,
                    repo.get("sidebar_color"),
                    bool(repo.get("linear_keys")),
                    wts,
                )
            )
        return out

    def _render_table(self, inventory: Inventory) -> None:
        self.query_one(WorktreeTable).update_inventory(inventory)

    # ---- actions ---------------------------------------------------------

    def action_sync(self) -> None:
        print("kick: manual sync — running cycle now")
        self._kick_slow()

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
