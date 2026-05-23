#!/usr/bin/env python3
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
  --watch [SECS]  long-running daemon; SIGUSR1 kicks an immediate cycle
  --once          run exactly one cycle and exit

Sibling entry points (each script does one job):
  scripts/footer.py   statusLine shim — pipes Claude Code's stdin to cship
  scripts/list.py     `/cockpit:list` table
  scripts/sync.py     USR1-kick the daemon, else fall back to `cockpit.py --once`
  scripts/spawn.py    `/cockpit:new` — create worktree + workspace

Failure policy: each cycle MUST exit 0 even on GitHub API errors. Errors go to
stderr (visible in the --watch terminal); the next cycle retries.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.cmux import (  # noqa: E402
    BLUE,
    LOOP_ICON,
    LOOP_KEY,
    cmux,
)
from scripts.lib.colors import green  # noqa: E402
from scripts.lib.config import (  # noqa: E402
    ensure_state_dirs,
    load_config,
    install_cship_default_config,
    install_cship_statusline_if_configured,
    install_starship_default_config,
)
from scripts.lib.daemon import run_watcher  # noqa: E402
from scripts.lib.gh import gh_self_user  # noqa: E402
from scripts.lib.preflight import preflight  # noqa: E402
from scripts.orchestrators.cycle import cycle_all  # noqa: E402

DEFAULT_POLL_SECS = 300
MIN_POLL_SECS = 5


def _build_state(args: argparse.Namespace) -> dict:
    return {
        "self_user": None,
        "keep_stale": args.keep_stale,
        "no_spawn": args.no_spawn,
        "dry": args.dry_run,
        "verbose": args.verbose,
        "pr_cache": {},
        "nudge_state": {},
        "pill_state": {},
    }


def _once_with(state: dict) -> None:
    cfg = load_config()
    self_user = state.get("self_user") or gh_self_user()
    state["self_user"] = self_user
    cycle_all(
        cfg,
        self_user,
        keep_stale=state["keep_stale"],
        no_spawn=state["no_spawn"],
        dry=state["dry"],
        pr_cache=state["pr_cache"],
        nudge_state=state["nudge_state"],
        pill_state=state["pill_state"],
        verbose=state["verbose"],
    )


def _watch(state: dict, watch_secs: int) -> None:
    self_ws = os.environ.get("CMUX_WORKSPACE_ID")
    show_loop_pill = bool(self_ws) and not state["dry"]

    def on_start() -> None:
        if show_loop_pill and self_ws is not None:
            cmux(
                "set-status",
                LOOP_KEY,
                LOOP_ICON,
                "--workspace",
                self_ws,
                "--color",
                BLUE,
                check=False,
            )

    def on_stop() -> None:
        if show_loop_pill and self_ws is not None:
            cmux("clear-status", LOOP_KEY, "--workspace", self_ws, check=False)

    def on_wake() -> None:
        state["nudge_state"].clear()
        print(f"{green('kick:')} SIGUSR1 — running cycle now", flush=True)

    run_watcher(
        lambda: _once_with(state),
        watch_secs,
        on_start=on_start,
        on_stop=on_stop,
        on_wake=on_wake,
    )


def _statusline_command() -> str:
    return f"{sys.executable} {Path(__file__).resolve().parent / 'footer.py'}"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--watch",
        nargs="?",
        const=-1,
        type=int,
        metavar="SECS",
        help="Run as a daemon. With no arg, use config.poll_interval_seconds.",
    )
    g.add_argument("--once", action="store_true")
    g.add_argument(
        "--footer",
        action="store_true",
        help="Re-run footer setup only (cship.toml + starship.toml + statusLine), then exit.",
    )
    p.add_argument("--keep-stale", action="store_true")
    p.add_argument("--no-spawn", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    ensure_state_dirs()
    preflight(load_config())

    if args.footer:
        install_cship_default_config()
        install_starship_default_config()
        install_cship_statusline_if_configured(_statusline_command())
        return 0

    if args.watch is not None:
        cfg = load_config()
        secs = (
            args.watch
            if args.watch and args.watch > 0
            else cfg.get("poll_interval_seconds", DEFAULT_POLL_SECS)
        )
        if secs < MIN_POLL_SECS:
            print(f"--watch SECS must be >= {MIN_POLL_SECS}", file=sys.stderr)
            return 2
        state = _build_state(args)
        _watch(state, secs)
        return 0
    state = _build_state(args)
    _once_with(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
