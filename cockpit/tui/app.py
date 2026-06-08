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
    off the main thread). SIGUSR1 kicks a slow tick (how `cockpit close`/`new`
    wake the daemon to drain their queued work); SIGTERM/SIGHUP ask Textual to
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
from textual.widgets import Footer

from cockpit.lib import version
from cockpit.lib.cache import find_pr_payload
from cockpit.lib.cmux import (
    BLUE,
    LOOP_ICON,
    LOOP_KEY,
    cmux,
    workspace_cwds,
    workspace_names,
)
from cockpit.lib.config import COCKPIT_HOME, load_config
from cockpit.lib.daemon import release_pidfile
from cockpit.lib.daemon_signal import enqueue
from cockpit.lib.git import Worktree, worktrees
from cockpit.lib.teardown_types import TeardownRequest
from cockpit.lib.tool import is_cmux
from cockpit.orchestrators.teardown import probe_blockers
from cockpit.tui.widgets.header_bar import HeaderBar
from cockpit.tui.widgets.log_pane import LogPane
from cockpit.tui.widgets.worktree_table import WorktreeTable

_UPDATE_CHECK_SECONDS = 3600
_LOG_TAIL_LINES = 200

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
        ("f", "focus_row", "Focus"),
        ("c", "close_row", "Close"),
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
        # Bounded on-disk tail of tick output (last N lines), rewritten on drain.
        self._log_tail: deque[str] = deque(maxlen=_LOG_TAIL_LINES)
        self._log_path = COCKPIT_HOME / "watch.log"
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
        # Drain queued tick output into the bounded on-disk tail (last N lines)
        # and any mounted LogPane. The pane is temporarily out of the layout, so
        # the file is currently the only place this output lands.
        new: list[str] = []
        while True:
            try:
                new.append(self._log_q.get_nowait())
            except queue.Empty:
                break
        if not new:
            return
        self._log_tail.extend(new)
        for pane in self.query(LogPane):
            for line in new:
                pane.append(line)
        with contextlib.suppress(OSError):
            self._log_path.write_text("\n".join(self._log_tail) + "\n")

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

    def action_focus_row(self) -> None:
        path = self.query_one(WorktreeTable).current_path()
        if path:
            self._focus_worktree(path)

    def action_close_row(self) -> None:
        path = self.query_one(WorktreeTable).current_path()
        if path:
            self._close_worktree(path)

    def _resolve_worktree(self, path_str: str) -> tuple[dict, Worktree] | None:
        """Map a row's worktree-path key back to its (repo config, Worktree).

        Re-derives from `git worktree list` per configured repo — inventory is
        derived, not stored, so a keypress resolves against live state."""
        target = Path(path_str).resolve()
        for repo in load_config().get("repos", []):
            rp = Path(os.path.expanduser(repo["path"]))
            if not rp.is_dir():
                continue
            try:
                for wt in worktrees(rp, repo.get("branch_prefix", "")):
                    if wt.path.resolve() == target:
                        return repo, wt
            except (RuntimeError, OSError):
                continue
        return None

    @staticmethod
    def _workspace_ref(wt: Worktree) -> str | None:
        """The cmux workspace ref rooted at this worktree (cwd→path), or None."""
        target = wt.path.resolve()
        return next(
            (ref for ref, p in workspace_cwds().items() if p.resolve() == target),
            None,
        )

    def _notify(self, message: str, *, severity: str = "information") -> None:
        """Toast feedback, safe from a worker thread. The log pane is removed,
        so a `print` is invisible — a notification is the only on-screen cue."""
        self.call_from_thread(self.notify, message, severity=severity, timeout=4.0)

    @work(thread=True, group="focus", exit_on_error=False)
    def _focus_worktree(self, path_str: str) -> None:
        # Shells out to git/cmux — runs off the UI thread. cmux-only (limux has
        # no focus verb), matching `cockpit focus`.
        if not is_cmux():
            self._notify("focus requires cmux", severity="warning")
            return
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            self._notify(f"focus: no worktree at {path_str}", severity="error")
            return
        _repo, wt = resolved
        ref = self._workspace_ref(wt)
        if ref is None:
            self._notify(
                f"focus: no workspace for {wt.label or wt.short}", severity="warning"
            )
            return
        cmux("focus", "--workspace", ref, check=False)
        self._notify(f"focused {wt.label or wt.short}")

    @work(thread=True, group="close", exit_on_error=False)
    def _close_worktree(self, path_str: str) -> None:
        # Mirrors `cockpit close` (no --force): refuse on any blocker, else
        # enqueue a teardown marker and kick the slow tick to drain it. Never
        # force-closes — dirty/unpushed/open-PR work is protected; the shell
        # `cockpit:close --force` is the override path.
        resolved = self._resolve_worktree(path_str)
        if resolved is None:
            self._notify(f"close: no worktree at {path_str}", severity="error")
            return
        repo, wt = resolved
        repo_name = repo.get("name") or Path(os.path.expanduser(repo["path"])).name
        repo_dir = Path(os.path.expanduser(repo["path"]))
        prefix = repo.get("branch_prefix", "")
        is_mine = wt.branch.startswith(prefix) if (prefix and wt.branch) else True

        blockers = probe_blockers(wt.path, wt.branch, repo_name, is_mine=is_mine)
        if blockers:
            self._notify(
                f"close refused {wt.label or wt.short}: "
                + "; ".join(blockers)
                + " — use `cockpit:close --force`",
                severity="warning",
            )
            return

        ref = self._workspace_ref(wt)
        names = workspace_names()
        payload = find_pr_payload(wt.branch, repo_name=repo_name) if wt.branch else None
        pr_is_merged = (
            payload is not None and str(payload.get("state", "")).upper() == "MERGED"
        )
        req = TeardownRequest(
            ref=ref or wt.branch or wt.short,
            name=(names.get(ref, "") if ref else ""),
            worktree_path=wt.path,
            branch=wt.branch,
            repo_path=repo_dir,
            repo_name=repo_name,
            forced=False,
            delete_branch=pr_is_merged,
        )
        enqueue(req)
        self._notify(f"queued close: {wt.label or wt.short}")
        self.call_from_thread(self._kick_slow)

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
