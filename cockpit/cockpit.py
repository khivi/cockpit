"""cockpit: the reconcile-loop daemon for PRs ↔ cmux workspaces.

Per cycle, for every repo registered in $COCKPIT_HOME/config.json:
  1. fetch relevant PRs (mine + coworker-PRs with local worktrees)
  2. refresh status pills on existing tracked workspaces
  3. spawn workspaces for PRs that have a worktree but no workspace
  4. close duplicate workspaces (same name, or same worktree under different name)
  5. close workspaces whose branch's PR is no longer open
  6. mark orphan worktrees (mine, no PR) with an orphan pill
  7. write a PR cache snapshot under $COCKPIT_HOME/cache
  8. autoclean merged worktrees + workspaces (clean + no unpushed only)

Modes:
  --watch         long-running daemon (Textual TUI); SIGUSR1 kicks a cycle
  --setup         re-run statusLine setup, then exit

Sibling entry points (each script does one job):
  cockpit/statusline.py   statusLine shim — pipes Claude Code's stdin to cship
  cockpit/spawn.py    `/cockpit:new` — create worktree + workspace

Failure policy: each cycle MUST exit 0 even on GitHub API errors. Errors go to
stderr (visible in the watch TUI log); the next cycle retries.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

from cockpit.lib.cache import (
    republish_pr_caches_from_disk,
    write_git_state_cache,
)
from cockpit.lib.cmux import (
    CmuxUnavailable,
    reconcile_workspace_names,
    workspace_state,
)
from cockpit.lib.config import (
    ensure_state_dirs,
    install_cship_default_config,
    install_cship_statusline_if_configured,
    install_starship_default_config,
    load_config,
)
from cockpit.lib.gh import gh_self_user, require_gh
from cockpit.lib.git import require_git, worktrees
from cockpit.lib.preflight import preflight
from cockpit.orchestrators.cycle import cycle_all

DEFAULT_SLOW_POLL_SECS = 300
DEFAULT_FAST_POLL_SECS = 30
MIN_POLL_SECS = 5

# Slow + fast tick bodies are lock-free; the only caller is the TUI
# (`cockpit.tui.app`), which serializes them under its own lock so it can tell
# "running" (holds the lock) from "waiting" (blocked on it) for the header.


def _build_state() -> dict:
    return {
        "self_user": None,
        "pr_cache": {},
        "pill_state": {},
    }


def _once_with(
    state: dict,
    on_repo_done: Callable[[], None] | None = None,
    only_repo: str | None = None,
) -> None:
    cfg = load_config()
    self_user = state.get("self_user") or gh_self_user()
    state["self_user"] = self_user
    cycle_all(
        cfg,
        self_user,
        dry=False,
        pr_cache=state["pr_cache"],
        pill_state=state["pill_state"],
        on_repo_done=on_repo_done,
        only_repo=only_repo,
    )


def _fast_tick(state: dict) -> None:
    """Cheap, local-only refresh: write git-state cells for every worktree
    of every registered repo, reconcile workspace names to their worktree dir,
    then re-publish PR flat cells from the persistent JSON snapshots.
    Network-free (cmux/git are local); safe to run at a tight cadence.

    The slow tick already does all three after fetching `gh` data; the fast
    tick fills the 300s gap between slow ticks so:
      • `git checkout` reflects in the footer within ~30s instead of ~300s
      • a workspace whose name drifted recovers within ~30s
      • PR flat cells repopulate within ~30s after an OS tmpdir wipe
        (cells live under `$TMPDIR/cockpit-cache/`; JSON survives under
        `$COCKPIT_HOME/cache/`)

    Lock-free: the TUI serializes this against the slow tick under its own lock
    (both write the same cache cells).
    """
    cfg = load_config()
    # Names/cwds are a local (non-network) cmux query; fetch once and reuse
    # across repos. A backend hiccup degrades to no rename, never a crash.
    try:
        names, cwds = workspace_state()
    except CmuxUnavailable:
        names, cwds = {}, {}
    for repo_entry in cfg.get("repos", []):
        repo_path = Path(os.path.expanduser(repo_entry["path"]))
        if not repo_path.is_dir():
            continue
        try:
            wts = worktrees(
                repo_path,
                repo_entry.get("branch_prefix", ""),
                repo_entry.get("name", ""),
            )
        except (RuntimeError, OSError):
            continue
        for wt in wts:
            write_git_state_cache(wt.path, wt.repo_name)
        if cwds:
            reconcile_workspace_names(names, cwds, wts)
    republish_pr_caches_from_disk()


def _watch(state: dict, watch_secs: int, fast_secs: int) -> int:
    """Launch the Textual TUI daemon. TUI-only: requires a terminal.

    The pidfile is claimed *before* the app starts (a half-initialised Textual
    terminal on a pidfile collision is worse than a clean message) and released
    by the app on unmount. The slow/fast tick functions are injected so the TUI
    package never imports back into this module.
    """
    if not sys.stdout.isatty():
        print(
            "cockpit watch requires a terminal (TTY); run it in a terminal "
            "or cmux/tmux tab.",
            file=sys.stderr,
        )
        return 2

    from cockpit.lib.daemon import claim_pidfile
    from cockpit.tui.app import RESTART_EXIT_CODE, CockpitApp

    claim_pidfile()  # exits 1 if a live daemon already holds it
    self_ws = os.environ.get("CMUX_WORKSPACE_ID")

    def _slow(
        on_repo_done: Callable[[], None] | None = None,
        only_repo: str | None = None,
    ) -> None:
        _once_with(state, on_repo_done, only_repo)

    app = CockpitApp(
        slow_tick=_slow,
        fast_tick=lambda: _fast_tick(state),
        slow_secs=watch_secs,
        fast_secs=fast_secs,
        self_ws=self_ws,
    )
    app.run()
    # `u` exits with RESTART_EXIT_CODE so cli.py runs the updater and re-execs;
    # a clean quit / SIGTERM leaves return_code at 0.
    rc = app.return_code or 0
    if rc == RESTART_EXIT_CODE:
        # Let cli.py run the updater and os.execvp — that replaces the process
        # image (killing any threads), so it never hits the interpreter-exit
        # join below.
        return rc
    # A slow tick worker runs `gh`/`git` in a non-daemon executor thread. If `q`
    # lands mid-tick, Textual restores the terminal and app.run() returns while
    # that thread is still blocked in the subprocess — then a normal return hangs
    # at interpreter exit, where concurrent.futures' atexit handler joins it
    # (the "q, then it hangs until ^C" bug). Nothing lives in memory (cache writes
    # already hit disk atomically; the pidfile was released on_unmount), so exit
    # hard and skip that join.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


def _statusline_command() -> str:
    return f"{sys.executable} -m cockpit.cli statusline"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--watch",
        action="store_true",
        help="Run as a daemon. Tick rates come from config "
        "(slow_poll_interval_seconds + fast_poll_interval_seconds).",
    )
    g.add_argument(
        "--setup",
        action="store_true",
        help="Re-run statusLine setup only (cship.toml + starship.toml + statusLine), then exit.",
    )
    args = p.parse_args(argv)

    require_git()
    require_gh()

    ensure_state_dirs()
    preflight(load_config())

    if args.setup:
        install_cship_default_config()
        install_starship_default_config()
        install_cship_statusline_if_configured(_statusline_command())
        return 0

    if args.watch:
        cfg = load_config()
        slow_secs = int(cfg.get("slow_poll_interval_seconds", DEFAULT_SLOW_POLL_SECS))
        fast_secs = int(cfg.get("fast_poll_interval_seconds", DEFAULT_FAST_POLL_SECS))
        if slow_secs < MIN_POLL_SECS:
            print(
                f"config slow_poll_interval_seconds must be >= {MIN_POLL_SECS}",
                file=sys.stderr,
            )
            return 2
        if fast_secs < 0 or (0 < fast_secs < MIN_POLL_SECS):
            print(
                f"config fast_poll_interval_seconds must be 0 (disable) or >= {MIN_POLL_SECS}",
                file=sys.stderr,
            )
            return 2
        state = _build_state()
        return _watch(state, slow_secs, fast_secs)
    # The mutually-exclusive group is required, so setup/watch are the only
    # paths; both return above.
    return 0


if __name__ == "__main__":
    sys.exit(main())
