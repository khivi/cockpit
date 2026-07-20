"""cockpit — unified console entry point (`[project.scripts] cockpit`).

A thin subcommand dispatcher over the per-feature modules. Each subcommand
lazily imports only the module it needs, so the latency-sensitive render path
(`cockpit starship <field>`, `cockpit statusline`) never pays to import the
heavy reconcile/spawn modules.

Subcommands:
  watch                 long-running daemon (Textual TUI)
  setup                 (re)install the statusLine config + Claude Code hooks
  statusline            Claude Code statusLine shim (reads stdin → renders)
  starship <field>      starship field printer / `warm`
  idle-pill <phase>     Claude Code hook shim → cmux idle pill (stop/prompt/…)
  new    [args]         create a worktree + workspace
  close  [args]         queue a worktree + workspace teardown for the daemon
  nudge  [args]         manage nudge mutes
"""

from __future__ import annotations

import sys

_SUBCOMMANDS = (
    "watch",
    "setup",
    "statusline",
    "starship",
    "idle-pill",
    "new",
    "close",
    "nudge",
)


def _usage() -> str:
    return (
        "usage: cockpit <" + " | ".join(_SUBCOMMANDS) + "> [args]"
        "  (no subcommand defaults to watch)"
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(_usage())
        return 0
    if argv and argv[0] in ("-V", "--version"):
        from cockpit.lib.version import running_version

        print(f"cockpit {running_version()}")
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

        return daemon_main([f"--{sub}", *rest])

    # statusline + starship are the hot render path — route straight to the
    # leaf module, skipping the daemon preflight that setup/starship never run.
    if sub == "statusline":
        from cockpit.statusline import main as statusline_main

        return statusline_main()
    if sub == "starship":
        from cockpit.starship import main as starship_main

        return starship_main(["cockpit-starship", *rest])

    # idle-pill is a Claude Code hook shim — exec the packaged shell script with
    # the phase arg (stop/prompt/loop-set/loop-clear). Kept off the render path
    # and never fatal: a Claude hook must not break a session.
    if sub == "idle-pill":
        import os
        from pathlib import Path

        script = Path(__file__).resolve().parent / "hooks" / "cmux-idle-pill.sh"
        if not script.exists():
            print(f"cockpit: idle-pill script missing at {script}", file=sys.stderr)
            return 0
        # Run via `bash` rather than execv'ing the script directly: a wheel does
        # not reliably preserve the file's exec bit, so relying on it would break
        # the hook on a fresh brew install.
        os.execvp("bash", ["bash", str(script), *rest])
        return 0  # type: ignore[unreachable]  # execvp replaces the process

    if sub == "close":
        from cockpit.close import main as close_main

        return close_main(rest)

    if sub == "nudge":
        from cockpit.lib.nudge_cli import main as nudge_main

        return nudge_main(rest)

    if sub == "new":
        from cockpit.spawn import main as spawn_main

        return spawn_main(rest)

    print(f"cockpit: unknown subcommand {sub!r}\n{_usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
