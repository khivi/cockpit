"""Re-exec `cockpit watch` through the bundled `bin/cockpit.sh` supervisor.

`u`-driven self-update can't happen in-process — the update reinstalls the very
package the daemon runs from (`uv tool install --force`). So the TUI exits with
`RESTART_EXIT_CODE` (42) and relies on a supervising wrapper to catch that code,
run `bin/update.sh`, and relaunch. That wrapper is `bin/cockpit.sh`.

The catch: the wheel bundles only the `cockpit` package + the two manifests (see
pyproject `force-include`), NOT `bin/`. So the uv-installed `cockpit watch`
console script — the documented launch command — has no supervisor next to it,
and pressing `u` just kills the session with no update (the bug this module
fixes). `bin/cockpit.sh` does ship, in the Claude plugin cache.

The fix: when `cockpit watch` starts and isn't already under the supervisor
(`is_supervised()` false), find the newest cached `bin/cockpit.sh` and re-exec
through it. The shell loop — not Python — then owns the update+relaunch cycle,
so it works regardless of how the user launched `cockpit watch`. If any guard
fails (no cached script, not the installed binary, non-interactive), we run the
TUI inline and `u` degrades to a toast (see `CockpitApp.action_update`) instead
of silently dying.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from cockpit.lib import version

# Set to "1" by bin/cockpit.sh (and passed in the re-exec env here) so the
# relaunched `cockpit watch` runs the TUI inline rather than re-execing into the
# supervisor again — without it, cockpit.sh → cockpit watch → cockpit.sh would
# loop.
SUPERVISED_ENV = "COCKPIT_SUPERVISED"


def is_supervised() -> bool:
    """True iff this process runs under bin/cockpit.sh (the exit-42 catcher).

    The contract is the exact value `"1"` — `COCKPIT_SUPERVISED=0` means NOT
    supervised (the conventional 0/1 env idiom), unlike plain string
    truthiness, where a "0" would falsely report supervised and `u` would exit
    42 into the void."""
    return os.environ.get(SUPERVISED_ENV) == "1"


def _claude_config_dir() -> Path:
    # `or` rather than a get() default so CLAUDE_CONFIG_DIR set-but-EMPTY still
    # falls back to ~/.claude (get()'s default only applies when unset, and
    # Path("").expanduser() would silently probe the cwd).
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def _plugin_cache_root() -> Path | None:
    """`{claude}/plugins/cache/{marketplace}/{plugin}`, or None if unresolvable.

    `{claude}` honours `CLAUDE_CONFIG_DIR` (Claude Code's config-dir override),
    defaulting to `~/.claude`.
    """
    marketplace = version.marketplace_name()
    plugin = version.plugin_name()
    if not marketplace or not plugin:
        return None
    root = _claude_config_dir() / "plugins" / "cache" / marketplace / plugin
    return root if root.is_dir() else None


def supervisor_script() -> Path | None:
    """Path to the newest cached `bin/cockpit.sh`, or None if none is installed.

    The cache holds version-named dirs (`.../cockpit/0.27.104/bin/cockpit.sh`);
    pick the highest by `version.parse_version` — the same ordering the update
    check uses — so the re-exec and `is_newer()` can't disagree.
    """
    root = _plugin_cache_root()
    if not root:
        return None
    candidates = [p for p in root.glob("*/bin/cockpit.sh") if p.is_file()]
    if not candidates:
        return None
    # p.parent is `<ver>/bin`; p.parent.parent.name is the version dir name.
    return max(candidates, key=lambda p: version.parse_version(p.parent.parent.name))


def _is_interactive() -> bool:
    return sys.stdout.isatty()


def _is_installed_invocation() -> bool:
    """True iff this process IS the PATH-installed `cockpit` console script.

    The re-exec hands execution to whatever cockpit.sh resolves — `cockpit` on
    PATH (the uv-tool install). That's only a no-op swap when it's the same
    thing the user launched. A dev running `uv run cockpit watch` from a
    worktree (argv[0] = the worktree venv's script) or `python -m cockpit.cli
    watch` (argv[0] = the module file) must NOT be silently exec-swapped for
    the installed wheel — their local code would never run, with no indication.
    """
    exe = shutil.which("cockpit")
    if not exe:
        return False
    try:
        return Path(sys.argv[0]).resolve() == Path(exe).resolve()
    except OSError:
        return False


def reexec_through_supervisor(extra_args: list[str]) -> None:
    """Re-exec `watch` through `bin/cockpit.sh` unless a guard says not to.

    Returns (no re-exec — caller runs the TUI inline) when: already supervised;
    the first extra arg is cockpit.sh's reserved `update` verb (forwarding it
    would exec bin/update.sh instead of launching watch — fall through to
    watch's argparse, which rejects it, the pre-supervisor behavior); not
    interactive (non-TTY watch exits 2 anyway); not the PATH-installed binary
    (re-exec would silently swap a dev's local code for the installed wheel);
    or no cached `cockpit.sh` is found. Otherwise replaces this process with
    the shell supervisor, which owns the update+relaunch loop.

    `extra_args` are forwarded as cockpit.sh's args (it appends them to its
    `cockpit watch` call).
    """
    if is_supervised():
        return
    if extra_args and extra_args[0] == "update":
        return
    if not _is_interactive():
        return
    if not _is_installed_invocation():
        return
    script = supervisor_script()
    if not script:
        return
    # The marker rides the exec'd environment only (execvpe, not a mutation of
    # os.environ) — nothing to undo on failure, and even an older cached
    # cockpit.sh that predates the export-in-the-wrapper change can't loop.
    env = {**os.environ, SUPERVISED_ENV: "1"}
    try:
        os.execvpe("bash", ["bash", str(script), *extra_args], env)
    except OSError:
        # bash missing/unrunnable: don't take down watch — fall back to inline.
        # os.environ was never touched, so `_watch` correctly reports
        # unsupervised and `u` warns instead of exiting into the void.
        return
