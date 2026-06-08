"""Behavioural tests for bin/update.sh.

The script shells out to `claude` and `uv`. We shadow both on PATH with shims
that record argv (and, for one test, fail on demand), then assert:

  1. `claude plugin update` is given the fully-qualified `<plugin>@<marketplace>`
     id — a bare name yields `Plugin "cockpit" not found` and was the bug.
  2. A plugin-refresh failure does NOT (under `set -e`) abort before the
     `uv tool install --force` reinstall, which is what swaps the running daemon.

`repo_root` resolves from the script's own location, so the script reads this
repo's real manifests (name `cockpit` / `khivi-cockpit`) — no faking needed.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from tests.fixtures import make_shim_on_path

UPDATE_SH = Path(__file__).resolve().parent.parent / "bin" / "update.sh"


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(UPDATE_SH)],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def _lines(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def test_plugin_update_uses_qualified_id(tmp_path, monkeypatch):
    claude_log = make_shim_on_path(tmp_path, monkeypatch, "claude")
    uv_log = make_shim_on_path(tmp_path, monkeypatch, "uv")

    result = _run()
    assert result.returncode == 0, result.stderr

    claude_calls = _lines(claude_log)
    # Marketplace refresh takes the bare marketplace name...
    assert "plugin marketplace update khivi-cockpit" in claude_calls
    # ...but the plugin update needs the qualified <plugin>@<marketplace> id.
    assert "plugin update cockpit@khivi-cockpit" in claude_calls
    assert "plugin update cockpit" not in claude_calls

    # The uv reinstall still runs, targeting repo_root.
    repo_root = UPDATE_SH.resolve().parent.parent
    assert any(line == f"tool install --force {repo_root}" for line in _lines(uv_log))


def test_plugin_refresh_failure_does_not_block_uv_reinstall(tmp_path, monkeypatch):
    # Custom claude shim: logs argv, but exits non-zero on `plugin update` to
    # simulate the failure that previously (under `set -e`) aborted the script
    # before the uv reinstall.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    claude_log = tmp_path / "claude.log"
    claude = bin_dir / "claude"
    claude.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{claude_log}"\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "update" ]; then exit 1; fi\n'
        "exit 0\n"
    )
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    uv_log = make_shim_on_path(tmp_path, monkeypatch, "uv")

    result = _run()
    assert result.returncode == 0, result.stderr

    # The plugin update was attempted (and failed)...
    assert "plugin update cockpit@khivi-cockpit" in _lines(claude_log)
    # ...yet the uv reinstall still ran.
    repo_root = UPDATE_SH.resolve().parent.parent
    assert any(line == f"tool install --force {repo_root}" for line in _lines(uv_log))
