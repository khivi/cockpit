"""cockpit — unified console entry point (`[project.scripts] cockpit`).

A thin subcommand dispatcher over the per-feature modules. Each subcommand
lazily imports only the module it needs, so the latency-sensitive render path
(`cockpit starship <field>`, `cockpit statusline`) never pays to import the
heavy reconcile/spawn modules.

Subcommands:
  watch                 long-running daemon (Textual TUI)
  setup                 (re)install the cship/starship statusLine config
  statusline            Claude Code statusLine shim (reads stdin → renders)
  starship <field>      starship field printer / `warm`
  new    [args]         create a worktree + workspace
  close  [args]         queue a worktree + workspace teardown for the daemon
  nudge  [args]         manage nudge mutes
  update [--check]      self-update the daemon (or just report availability)
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

_SUBCOMMANDS = (
    "watch",
    "setup",
    "statusline",
    "starship",
    "new",
    "close",
    "nudge",
    "update",
)


def _running_as_installed_cockpit() -> bool:
    """True iff this process was launched as the PATH-installed `cockpit`
    console script (not a dev's `uv run cockpit watch` from a worktree venv).

    Guards the `u`-triggered self-update + re-exec: auto-swapping a dev's
    worktree session for the released wheel would be wrong, so when the launched
    binary differs from `shutil.which("cockpit")` we decline."""
    which = shutil.which("cockpit")
    if not which:
        return False
    try:
        return Path(which).resolve() == Path(sys.argv[0]).resolve()
    except OSError:
        return False


def _usage() -> str:
    return (
        "usage: cockpit <" + " | ".join(_SUBCOMMANDS) + "> [args]"
        "  (no subcommand defaults to watch)"
    )


def _run_with_argv(prog: str, rest: list[str], main_fn: Callable[[], int]) -> int:
    """Run a module `main()` that parses sys.argv, with argv reshaped to its own
    args (prog name + rest, no subcommand token), restoring argv afterward."""
    saved = sys.argv
    sys.argv = [prog, *rest]
    try:
        return main_fn()
    finally:
        sys.argv = saved


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(_usage())
        return 0
    # Bare `cockpit` defaults to `watch` — the daemon TUI is the primary entry
    # point; every other subcommand is a hook/render shim or one-off.
    if not argv:
        argv = ["watch"]
    sub, rest = argv[0], argv[1:]

    # watch / setup share cockpit.cockpit's argparse (require_git/gh +
    # preflight live there); translate the subcommand back to its flag.
    if sub in ("watch", "setup"):
        from cockpit.cockpit import main as daemon_main

        rc = daemon_main([f"--{sub}", *rest])
        # `u` in the TUI exits watch with RESTART_EXIT_CODE. Run the update in a
        # fresh subprocess (not in-process, where the just-torn-down Textual
        # would share fds/signals with the terminal-touching update), then
        # re-exec a fresh `cockpit watch` on the new version.
        if sub == "watch":
            from cockpit.tui.app import RESTART_EXIT_CODE

            if rc == RESTART_EXIT_CODE:
                return _self_update_and_reexec(rest)
        return rc

    # statusline + starship are the hot render path — route straight to the
    # leaf module, skipping the daemon preflight that setup/starship never run.
    if sub == "statusline":
        from cockpit.statusline import main as statusline_main

        return statusline_main()
    if sub == "starship":
        from cockpit.starship import main as starship_main

        return starship_main(["cockpit-starship", *rest])

    if sub == "close":
        from cockpit.close import main as close_main

        return close_main(rest)

    if sub == "nudge":
        from cockpit.lib.nudge_cli import main as nudge_main

        return nudge_main(rest)

    # new parses sys.argv internally; reshape it so its argparse sees only its
    # own args (prog name + rest, no subcommand token).
    if sub == "new":
        from cockpit.spawn import main as spawn_main

        return _run_with_argv("cockpit-new", rest, spawn_main)

    if sub == "update":
        from cockpit.lib.updater import run_update

        return run_update(
            skip_install="--skip-install" in rest,
            check_only="--check" in rest,
        )

    print(f"cockpit: unknown subcommand {sub!r}\n{_usage()}", file=sys.stderr)
    return 2


def _self_update_and_reexec(watch_args: list[str]) -> int:
    """The TUI exited via `u`. Run the update as a fresh `cockpit update`
    subprocess — a cooked, pre-TUI process, the exact state manual `cockpit
    update` runs in — so the heavy, terminal-touching update work never runs in
    this process that just tore Textual down, then re-exec onto the new version.

    Declines (returning 0) when not running as the installed `cockpit` — a dev's
    `uv run` session must not be auto-swapped for the released wheel."""
    if not _running_as_installed_cockpit():
        print(
            "update available — run `cockpit update`, or press `u` from an "
            "installed `cockpit watch` to self-update.",
            file=sys.stderr,
        )
        return 0
    import subprocess
    import time

    from cockpit.lib.updater import UPDATE_SKIPPED_NOOP_EXIT

    rc = subprocess.run(["cockpit", "update"]).returncode
    if rc == UPDATE_SKIPPED_NOOP_EXIT:
        # The local plugin cache had nothing newer than the running version —
        # not the same claim as "up to date" (the header's indicator compares
        # against GitHub directly, which can be ahead of the local cache after
        # a network hiccup or propagation lag). Relaunch anyway (the user quit
        # the TUI; leaving them at a shell is worse) but explain, and hold the
        # message on screen for a moment — this is a cooked, pre-exec terminal.
        print(
            "cockpit: plugin cache not yet refreshed; nothing new to install. "
            "The header may still show an update — retry `u` shortly.",
            file=sys.stderr,
        )
        time.sleep(2)
    elif rc != 0:
        print(
            "cockpit: update failed; not relaunching. Re-run `cockpit watch`.",
            file=sys.stderr,
        )
        return 1
    # The update's subprocesses can leave the controlling terminal's foreground
    # process group dangling; re-assert ours before re-exec so the new Textual
    # isn't stopped on SIGTTIN/SIGTTOU (blank frozen screen).
    _restore_terminal_foreground()
    # Replace this process with the just-installed cockpit; execvp never returns
    # on success (the trailing return is reached only if stubbed in tests).
    try:
        os.execvp("cockpit", ["cockpit", "watch", *watch_args])
    except OSError as exc:
        print(
            f"cockpit: relaunch failed ({exc}); update installed — "
            "re-run `cockpit watch`.",
            file=sys.stderr,
        )
        return 1
    return 0  # type: ignore[unreachable]


def _restore_terminal_foreground() -> None:
    """Re-assert this process group as the controlling terminal's foreground
    before re-exec'ing the TUI. A no-op unless something (an update subprocess)
    left the foreground pgrp elsewhere; without it the re-exec'd Textual reads
    stdin from the background and is stopped on SIGTTIN. No-op without a TTY."""
    import signal

    try:
        if not sys.stdin.isatty():
            return
    except (OSError, ValueError):
        return
    prev = signal.getsignal(signal.SIGTTOU)
    try:
        # tcsetpgrp from a background pgrp raises SIGTTOU at us; ignore it for
        # the reclaim so we don't stop ourselves doing it.
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(sys.stdin.fileno(), os.getpgrp())
    except (OSError, ValueError):
        pass
    finally:
        with contextlib.suppress(OSError, ValueError, TypeError):
            signal.signal(signal.SIGTTOU, prev)


if __name__ == "__main__":
    sys.exit(main())
