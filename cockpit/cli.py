"""cockpit — unified console entry point (`[project.scripts] cockpit`).

A thin subcommand dispatcher over the per-feature modules. Each subcommand
lazily imports only the module it needs, so the latency-sensitive render path
(`cockpit starship <field>`, `cockpit statusline`) never pays to import the
heavy reconcile/spawn modules.

Subcommands:
  watch                 long-running daemon (Textual TUI)
  footer                (re)install the cship/starship statusLine config
  statusline            Claude Code statusLine shim (reads stdin → renders)
  starship <field>      starship field printer / `warm`
  sync                  kick a running daemon, else run one cycle inline
  close  [args]         tear down a worktree + workspace
  new    [args]         create a worktree + workspace
  focus  <ref>          switch workspace focus
  list                  render the cached worktree + PR table
  nudge  [args]         manage nudge mutes
  repos                 list configured repos
"""

from __future__ import annotations

import sys
from collections.abc import Callable

_SUBCOMMANDS = (
    "watch",
    "footer",
    "statusline",
    "starship",
    "sync",
    "close",
    "new",
    "focus",
    "list",
    "nudge",
    "repos",
)


def _usage() -> str:
    return "usage: cockpit <" + " | ".join(_SUBCOMMANDS) + "> [args]"


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
    if not argv or argv[0] in ("-h", "--help"):
        print(_usage())
        return 0 if argv else 2
    sub, rest = argv[0], argv[1:]

    # watch / footer share cockpit.cockpit's argparse (require_git/gh +
    # preflight live there); translate the subcommand back to its flag.
    if sub in ("watch", "footer"):
        from cockpit.cockpit import main as daemon_main

        return daemon_main([f"--{sub}", *rest])

    # statusline + starship are the hot render path — route straight to the
    # leaf module, skipping the daemon preflight that footer/starship never run.
    if sub == "statusline":
        from cockpit.footer import main as statusline_main

        return statusline_main()
    if sub == "starship":
        from cockpit.starship import main as starship_main

        return starship_main(["cockpit-starship", *rest])

    if sub == "sync":
        from cockpit.sync import main as sync_main

        return sync_main()
    if sub == "list":
        from cockpit.list import main as list_main

        return list_main()
    if sub == "repos":
        from cockpit.repos import main as repos_main

        return repos_main()
    if sub == "nudge":
        from cockpit.lib.nudge_cli import main as nudge_main

        return nudge_main(rest)

    # close / new / focus parse sys.argv internally; reshape it so their
    # argparse sees only their own args (prog name + rest, no subcommand token).
    if sub == "close":
        from cockpit.close import main as close_main

        return _run_with_argv("cockpit-close", rest, close_main)
    if sub == "new":
        from cockpit.spawn import main as spawn_main

        return _run_with_argv("cockpit-new", rest, spawn_main)
    if sub == "focus":
        from cockpit.focus import main as focus_main

        return _run_with_argv("cockpit-focus", rest, focus_main)

    print(f"cockpit: unknown subcommand {sub!r}\n{_usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
