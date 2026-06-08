"""Behavioural tests for bin/update.sh.

The script shells out to `claude` and `uv`. We shadow both on PATH with shims
that record argv (and, for one test, fail on demand), then assert:

  1. `claude plugin update` is given the fully-qualified `<plugin>@<marketplace>`
     id — a bare name yields `Plugin "cockpit" not found` and was the bug.
  2. A plugin-refresh failure does NOT (under `set -e`) abort before the
     `uv tool install --force` reinstall, which is what swaps the running daemon.
  3. The uv bootstrap (absorbed from the former `install.sh`) stays dormant when
     `uv` is already on PATH — `curl` must not be invoked.
  4. Run from inside the plugin cache, the daemon is reinstalled from the NEWEST
     cached version dir (the one a prior `/plugin update` drops), not the dir the
     script was launched from. A dev checkout still installs its own `repo_root`.

`repo_root` resolves from the script's own location, so the script reads this
repo's real manifests (name `cockpit` / `khivi-cockpit`) — no faking needed.
"""

from __future__ import annotations

import os
import shutil
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


def test_does_not_bootstrap_uv_when_present(tmp_path, monkeypatch):
    # The uv bootstrap (absorbed from the former install.sh) must stay dormant
    # when uv is already on PATH: curl is never invoked.
    make_shim_on_path(tmp_path, monkeypatch, "claude")
    make_shim_on_path(tmp_path, monkeypatch, "uv")
    curl_log = make_shim_on_path(tmp_path, monkeypatch, "curl")

    result = _run()
    assert result.returncode == 0, result.stderr
    assert not curl_log.exists(), "curl bootstrap fired despite uv being present"


def test_dev_checkout_installs_repo_root(tmp_path, monkeypatch):
    # repo_root (the dev checkout, this repo) is NOT under the plugin cache, so
    # the daemon installs from repo_root itself — the local-iteration path.
    make_shim_on_path(tmp_path, monkeypatch, "claude")
    uv_log = make_shim_on_path(tmp_path, monkeypatch, "uv")

    result = _run()
    assert result.returncode == 0, result.stderr

    repo_root = UPDATE_SH.resolve().parent.parent
    assert any(line == f"tool install --force {repo_root}" for line in _lines(uv_log))


def test_installs_newest_cached_version_from_plugin_cache(tmp_path, monkeypatch):
    # Installed-plugin context: HOME points at a tmp tree whose plugin cache
    # holds two version dirs. update.sh runs from the OLDER one but must
    # reinstall the daemon from the NEWER one — the dir a prior `/plugin update`
    # creates. Each version dir carries the manifests + script the SUT reads.
    real_repo = UPDATE_SH.resolve().parent.parent
    fake_home = tmp_path / "home"
    cache = fake_home / ".claude" / "plugins" / "cache" / "khivi-cockpit" / "cockpit"
    old_dir = cache / "0.27.90"
    new_dir = cache / "0.27.91"
    for d in (old_dir, new_dir):
        (d / "bin").mkdir(parents=True)
        shutil.copy(UPDATE_SH, d / "bin" / "update.sh")
        shutil.copytree(real_repo / ".claude-plugin", d / ".claude-plugin")

    claude_log = make_shim_on_path(tmp_path, monkeypatch, "claude")
    uv_log = make_shim_on_path(tmp_path, monkeypatch, "uv")
    monkeypatch.setenv("HOME", str(fake_home))

    result = subprocess.run(
        ["bash", str(old_dir / "bin" / "update.sh")],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr

    # Reinstalled from the newer dir, not the one it ran from.
    assert any(line == f"tool install --force {new_dir}" for line in _lines(uv_log))
    assert not any(line == f"tool install --force {old_dir}" for line in _lines(uv_log))
    # Plugin refresh still ran against the manifest-derived ids.
    assert "plugin update cockpit@khivi-cockpit" in _lines(claude_log)


def test_resolves_through_symlink(tmp_path, monkeypatch):
    # Invoked via a symlink (e.g. ~/bin/update.sh -> the plugin dir), repo_root
    # must follow the link to the real script location, so the plugin-cache
    # auto-detect still picks the newest version dir.
    real_repo = UPDATE_SH.resolve().parent.parent
    fake_home = tmp_path / "home"
    cache = fake_home / ".claude" / "plugins" / "cache" / "khivi-cockpit" / "cockpit"
    old_dir = cache / "0.27.90"
    new_dir = cache / "0.27.91"
    for d in (old_dir, new_dir):
        (d / "bin").mkdir(parents=True)
        shutil.copy(UPDATE_SH, d / "bin" / "update.sh")
        shutil.copytree(real_repo / ".claude-plugin", d / ".claude-plugin")

    link_dir = tmp_path / "userbin"
    link_dir.mkdir()
    link = link_dir / "update.sh"
    link.symlink_to(old_dir / "bin" / "update.sh")

    uv_log = make_shim_on_path(tmp_path, monkeypatch, "uv")
    make_shim_on_path(tmp_path, monkeypatch, "claude")
    monkeypatch.setenv("HOME", str(fake_home))

    result = subprocess.run(
        ["bash", str(link)],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr

    # Followed the symlink into the cache, then auto-detected the newest dir.
    assert any(line == f"tool install --force {new_dir}" for line in _lines(uv_log))
