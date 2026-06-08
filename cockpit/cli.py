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
  nudge  [args]         manage nudge mutes
"""

from __future__ import annotations

import sys
from collections.abc import Callable

_SUBCOMMANDS = (
    "watch",
    "setup",
    "statusline",
    "starship",
    "new",
    "nudge",
)


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

        return daemon_main([f"--{sub}", *rest])

    # statusline + starship are the hot render path — route straight to the
    # leaf module, skipping the daemon preflight that setup/starship never run.
    if sub == "statusline":
        from cockpit.statusline import main as statusline_main

        return statusline_main()
    if sub == "starship":
        from cockpit.starship import main as starship_main

        return starship_main(["cockpit-starship", *rest])

    if sub == "nudge":
        from cockpit.lib.nudge_cli import main as nudge_main

        return nudge_main(rest)

    # new parses sys.argv internally; reshape it so its argparse sees only its
    # own args (prog name + rest, no subcommand token).
    if sub == "new":
        from cockpit.spawn import main as spawn_main

        return _run_with_argv("cockpit-new", rest, spawn_main)

    print(f"cockpit: unknown subcommand {sub!r}\n{_usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
