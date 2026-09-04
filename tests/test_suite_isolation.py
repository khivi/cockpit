"""The suite's own guardrails — `tests/conftest.py`'s `_no_live_backend` and
`_isolate_cockpit_home`.

These exist because a refactor moved a mock boundary and four xdist workers each
spawned a real cmux workspace on the author's machine and sent it cockpit's
orphan-nudge prompt. The same run was also renaming live workspaces and
workspace groups. Every other test in this repo asserts what cockpit does; these
assert what the *test suite* cannot do, which is the only category of bug whose
blast radius is the developer's laptop rather than a failing assertion.

They are deliberately written against the guards' observable behaviour rather
than their implementation, so a rewrite of the fixtures still has to keep the
property.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _REAL_COCKPIT_HOME


def test_execing_the_real_cmux_raises():
    with pytest.raises(RuntimeError, match="blocked"):
        subprocess.Popen(["cmux", "list-workspaces"])


def test_execing_the_real_cockpit_cli_raises():
    """`_bg_spawn_pr` launches a detached `python -m cockpit.cli new`, which is
    a whole real spawn against the real machine — not a backend call, so the
    binary-name check alone would miss it."""
    with pytest.raises(RuntimeError, match="blocked"):
        subprocess.Popen(["python", "-m", "cockpit.cli", "new", "khivi/x"])


def test_subprocess_run_is_covered_too():
    """`lib.run` and `subprocess.run` both bottom out in `Popen`, which is why
    the guard sits there rather than on each wrapper."""
    with pytest.raises(RuntimeError, match="blocked"):
        subprocess.run(["cmux", "--help"], capture_output=True)


def test_git_still_runs():
    """Filtered by argv, not blanket-blocked: the leaf tests shell out to real
    git and gh on tmp_path, and a guard that broke those would be reverted."""
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 0


def test_the_backend_reads_as_absent():
    """`resolve_tool()`'s `auto` resolves to `none`, so cmux-facing code
    degrades through the gates `tool: none` already validates instead of
    reading whatever sidebar the developer happens to have open."""
    from cockpit.lib.tool import resolve_tool

    assert shutil.which("cmux") is None
    assert resolve_tool() == "none"


def test_a_fake_backend_under_tmp_is_allowed(tmp_path, monkeypatch):
    """A test that builds its own backend is doing the right thing —
    `tests/lib/test_events.py` drives the real stream logic that way. The guard
    keys on where the executable resolves, not on its name."""
    fake = tmp_path / "cmux"
    fake.write_text("#!/bin/sh\necho ok\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    assert shutil.which("cmux") == str(fake)
    # Both spellings: the resolved path, and the bare name `lib.events` spawns.
    assert subprocess.run([str(fake)], capture_output=True).returncode == 0
    assert subprocess.run(["cmux"], capture_output=True).returncode == 0


def test_cockpit_home_is_not_the_developers():
    from cockpit.lib.config import COCKPIT_HOME

    assert Path(COCKPIT_HOME).resolve() != _REAL_COCKPIT_HOME


def test_writing_into_the_real_cockpit_home_raises():
    """The second layer: a test that rebuilds the path by hand, or a module
    that captured it at import, still cannot land a byte in it."""
    from cockpit.lib import config

    with pytest.raises(RuntimeError, match="blocked"):
        config._atomic_write_text(_REAL_COCKPIT_HOME / "config.json", "{}")
