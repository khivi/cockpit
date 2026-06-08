"""Behavioural tests for bin/cockpit.sh's `update` passthrough.

`cockpit.sh update [args]` must delegate to the sibling bin/update.sh (exec,
forwarding trailing args) instead of launching the TUI supervisor loop. We copy
the real cockpit.sh into a tmp `bin/` next to a shim update.sh that records its
argv, then assert the routing — without running a real update or the TUI.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

COCKPIT_SH = Path(__file__).resolve().parent.parent / "bin" / "cockpit.sh"


def _fake_bin(tmp_path: Path) -> tuple[Path, Path]:
    """Plant a copy of cockpit.sh and a logging update.sh shim in tmp/bin.
    Returns (cockpit.sh path, update.log path)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(COCKPIT_SH, bin_dir / "cockpit.sh")
    update_log = tmp_path / "update.log"
    update = bin_dir / "update.sh"
    update.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{update_log}"\n')
    update.chmod(update.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir / "cockpit.sh", update_log


def test_update_subcommand_delegates_to_update_sh(tmp_path):
    cockpit_sh, update_log = _fake_bin(tmp_path)

    result = subprocess.run(
        ["bash", str(cockpit_sh), "update", "--check"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    # Delegated to update.sh with the trailing arg, never touched `cockpit watch`.
    assert update_log.read_text().splitlines() == ["--check"]


def test_bare_update_delegates_with_no_args(tmp_path):
    cockpit_sh, update_log = _fake_bin(tmp_path)

    result = subprocess.run(
        ["bash", str(cockpit_sh), "update"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    assert update_log.read_text().splitlines() == [""]


def test_update_resolves_through_symlink(tmp_path):
    # Invoked via a symlink, cockpit.sh still finds its sibling update.sh.
    cockpit_sh, update_log = _fake_bin(tmp_path)
    link = tmp_path / "cockpit-watch"
    link.symlink_to(cockpit_sh)

    result = subprocess.run(
        ["bash", str(link), "update", "--check"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    assert update_log.read_text().splitlines() == ["--check"]
