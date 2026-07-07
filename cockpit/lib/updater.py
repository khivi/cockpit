"""`cockpit update` — self-update the daemon end-to-end, in Python.

This logic ships *inside the wheel* (unlike `bin/`, which the wheel doesn't
bundle), so both the CLI (`cockpit update`) and the TUI's `u` self-update have
it wherever the daemon runs — no cache-hunting for a shell supervisor. It
mirrors the steps `bin/update.sh` used to own:

  1. ensure `uv` is on PATH (the daemon already runs via uv, so this is a
     belt-and-suspenders bootstrap for the standalone-invocation case)
  2. refresh the Claude Code marketplace + plugin via the `claude` CLI — this is
     what drops the new version dir into the plugin cache. Non-fatal.
  3. resolve the install source — the *newest* plugin-cache version dir, with a
     downgrade guard — and `uv tool install --force --no-cache` it. Fatal: this
     is the step that actually swaps the running daemon.
  4. re-pin the statusLine via `cockpit setup` (heals a stale worktree-venv pin).
     Non-fatal.

`--check` compares running vs latest and exits 0 (current) / 10 (available) /
1 (can't check) — the scripting interface `bin/update.sh --check` used to
expose. `--skip-install` runs only the refresh + setup steps: the bootstrap
`bin/update.sh` already did the first `uv tool install` to put `cockpit` on
PATH, so the updater must not reinstall (which would redirect to the newest
cached dir and could differ from the version just bootstrapped).

The `u` self-update runs this via a fresh `subprocess.run(["cockpit", "update"])`
from `cli.py`'s `_self_update_and_reexec` — a cooked, pre-TUI process, the exact
state this manual path runs in — then `os.execvp`s onto the new version. The
reinstall lands on disk before the exec loads it. Subprocesses run tty-detached
(`_TTY_SAFE`) so a child can't mangle the controlling terminal's job control
under the re-exec'd TUI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

from cockpit.lib import version

# `--check` exit code: an update is available. Matches the contract the former
# `bin/update.sh --check` exposed (0 = current, 10 = available, 1 = can't run).
UPDATE_AVAILABLE_EXIT = 10

_UV_INSTALL_URL = "https://astral.sh/uv/install.sh"


# Run every update subprocess detached from the controlling terminal: stdin off
# the TTY so a child can't SIGTTIN-block on a read, and its own session so it
# can't grab the TTY foreground process group. A child that mangled job control
# would otherwise leave the re-exec'd TUI stopped on SIGTTIN/SIGTTOU — the blank
# frozen-screen `u` self-update bug. (These are batch, non-interactive tools.)
class _TtySafe(TypedDict):
    stdin: int
    start_new_session: bool


_TTY_SAFE: _TtySafe = {"stdin": subprocess.DEVNULL, "start_new_session": True}


def _claude_home() -> Path:
    """Claude Code's config root, honouring `CLAUDE_CONFIG_DIR` — the same root
    the plugin cache lives under."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base)


def _ensure_uv() -> bool:
    """True if `uv` is (or becomes, after bootstrap) on PATH."""
    if shutil.which("uv"):
        return True
    print("uv not found — installing it...")
    try:
        subprocess.run(
            f"curl -LsSf {_UV_INSTALL_URL} | sh", shell=True, check=True, **_TTY_SAFE
        )
    except (subprocess.SubprocessError, OSError):
        return False
    local_bin = Path.home() / ".local" / "bin"
    os.environ["PATH"] = f"{local_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    return bool(shutil.which("uv"))


def _refresh_plugin() -> None:
    """Refresh the marketplace + plugin via the `claude` CLI. Non-fatal: the uv
    reinstall is what swaps the daemon, so a refresh hiccup must not abort it.

    The marketplace update takes the bare marketplace name; the plugin update
    needs the fully-qualified `<plugin>@<marketplace>` id (a bare name yields
    `Plugin "cockpit" not found`). Both names come from the bundled manifests so
    they never drift."""
    if not shutil.which("claude"):
        print(
            "claude CLI not found — update the plugin from inside Claude Code "
            "with /plugin.",
            file=sys.stderr,
        )
        return
    market = version.marketplace_name()
    plugin = version.plugin_name()
    if not (market and plugin):
        return
    print(f"refreshing marketplace {market}...")
    try:
        subprocess.run(
            ["claude", "plugin", "marketplace", "update", market],
            check=False,
            **_TTY_SAFE,
        )
    except OSError:
        print("marketplace refresh failed; continuing.", file=sys.stderr)
    print(f"updating plugin {plugin}@{market}...")
    try:
        subprocess.run(
            ["claude", "plugin", "update", f"{plugin}@{market}"],
            check=False,
            **_TTY_SAFE,
        )
    except OSError:
        print("plugin refresh failed; continuing.", file=sys.stderr)


def newest_cache_dir() -> Path | None:
    """The newest version dir under `<claude>/plugins/cache/<market>/<plugin>/`,
    or None when the cache is absent/empty. "Newest" is by numeric version sort
    (`version.parse_version`) — lexical sort would wrongly rank `0.27.9` above
    `0.27.10`."""
    market = version.marketplace_name()
    plugin = version.plugin_name()
    if not (market and plugin):
        return None
    cache_root = _claude_home() / "plugins" / "cache" / market / plugin
    if not cache_root.is_dir():
        return None
    dirs = [d for d in cache_root.iterdir() if d.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda d: version.parse_version(d.name))


def _do_install() -> int:
    """Reinstall the daemon from the newest cached version dir. Returns 0 on
    success (or a deliberate skip), non-zero on a fatal failure."""
    src = newest_cache_dir()
    if src is None:
        print(
            "error: no plugin-cache version dir under "
            f"{_claude_home()}/plugins/cache — run /plugin update (or the "
            "bundled bin/update.sh) first.",
            file=sys.stderr,
        )
        return 1
    # Downgrade guard: if the running install is already >= the newest cached
    # dir (e.g. the `claude plugin update` refresh above failed to drop a newer
    # one), skip the reinstall — otherwise every `u` would roll the daemon back.
    running = version.running_version()
    if running and not version.is_newer(src.name, running):
        print(
            f"already at {running} (newest cached: {src.name}); "
            "skipping reinstall to avoid a downgrade."
        )
        return 0
    print(f"(re)installing the cockpit command from {src}...")
    # --no-cache is load-bearing: the wheel version is read from plugin.json at
    # build time, but uv keys its build cache on the source *path*, so a
    # version-only bump leaves the key unchanged and a plain --force re-serves
    # the stale wheel. --no-cache forces a real rebuild.
    try:
        subprocess.run(
            ["uv", "tool", "install", "--force", "--no-cache", str(src)],
            check=True,
            **_TTY_SAFE,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"error: uv tool install failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_setup() -> None:
    """Re-pin the statusLine config through the freshly-installed `cockpit`
    console script. Non-fatal — setup is a no-op unless `use_cship` is on, and a
    hiccup must not fail an otherwise-successful update."""
    cockpit = shutil.which("cockpit")
    if not cockpit:
        return
    print("re-pinning footer config (cockpit setup)...")
    try:
        subprocess.run([cockpit, "setup"], check=False, **_TTY_SAFE)
    except OSError:
        print("cockpit setup failed; footer config left as-is.", file=sys.stderr)


def _check() -> int:
    running = version.running_version()
    latest = version.latest_version()
    if latest and version.is_newer(latest, running):
        print(f"update available: {running or '?'} -> {latest}")
        return UPDATE_AVAILABLE_EXIT
    print(f"up to date ({running or 'unknown'})")
    return 0


def run_update(skip_install: bool = False, check_only: bool = False) -> int:
    """Run the update flow. See module docstring for the step breakdown.

    Returns a process exit code: 0 on success, 1 on a fatal failure, or (for
    `check_only`) 10 when an update is available."""
    if check_only:
        return _check()
    if not _ensure_uv():
        print("error: uv is unavailable — can't update.", file=sys.stderr)
        return 1
    _refresh_plugin()
    if not skip_install:
        rc = _do_install()
        if rc != 0:
            return rc
    _run_setup()
    print("\ndone. restart Claude Code and 'cockpit watch' to apply.")
    return 0
