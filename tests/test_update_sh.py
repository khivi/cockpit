"""Behavioural tests for bin/update.sh — now a first-install bootstrap only.

The marketplace/plugin-refresh + cache-redirect logic moved into the in-wheel
Python updater (`cockpit/lib/updater.py`, tested in tests/test_updater.py). What
remains in shell is the bootstrap that runs *before* `cockpit` is on PATH:

  1. bootstrap `uv` if missing (curl must stay dormant when uv is present)
  2. `uv tool install --force --no-cache <this checkout>` — `--no-cache` is
     load-bearing (uv keys its build cache on the source path, so a version-only
     bump otherwise re-serves the stale wheel)
  3. hand off to `cockpit update --skip-install` for the refresh + setup steps

We shadow `uv` and `cockpit` on PATH with shims that record argv, then assert
those three. `repo_root` resolves from the script's own location (following
symlinks), so the install targets this repo.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.fixtures import make_shim_on_path

UPDATE_SH = Path(__file__).resolve().parent.parent / "bin" / "update.sh"


def _run(script: Path = UPDATE_SH) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def _lines(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def test_installs_cockpit_command_from_repo_root(tmp_path, monkeypatch):
    uv_log = make_shim_on_path(tmp_path, monkeypatch, "uv")
    make_shim_on_path(tmp_path, monkeypatch, "cockpit")

    result = _run()
    assert result.returncode == 0, result.stderr

    repo_root = UPDATE_SH.resolve().parent.parent
    assert any(
        line == f"tool install --force --no-cache {repo_root}"
        for line in _lines(uv_log)
    )


def test_reinstall_passes_no_cache(tmp_path, monkeypatch):
    # --no-cache is required, not cosmetic: uv keys its build cache on the source
    # path, so a version-only bump (the common case) leaves the key unchanged and
    # a plain --force re-serves the stale wheel.
    uv_log = make_shim_on_path(tmp_path, monkeypatch, "uv")
    make_shim_on_path(tmp_path, monkeypatch, "cockpit")

    result = _run()
    assert result.returncode == 0, result.stderr

    install_lines = [line for line in _lines(uv_log) if line.startswith("tool install")]
    assert install_lines, "uv tool install never ran"
    assert all("--no-cache" in line for line in install_lines), install_lines


def test_hands_off_to_cockpit_update_skip_install(tmp_path, monkeypatch):
    # After the bootstrap install, the rest (marketplace/plugin refresh + setup)
    # is delegated to the Python updater via `cockpit update --skip-install`.
    make_shim_on_path(tmp_path, monkeypatch, "uv")
    cockpit_log = make_shim_on_path(tmp_path, monkeypatch, "cockpit")

    result = _run()
    assert result.returncode == 0, result.stderr
    assert "update --skip-install" in _lines(cockpit_log)


def test_does_not_bootstrap_uv_when_present(tmp_path, monkeypatch):
    # The uv bootstrap must stay dormant when uv is already on PATH: curl is
    # never invoked.
    make_shim_on_path(tmp_path, monkeypatch, "uv")
    make_shim_on_path(tmp_path, monkeypatch, "cockpit")
    curl_log = make_shim_on_path(tmp_path, monkeypatch, "curl")

    result = _run()
    assert result.returncode == 0, result.stderr
    assert not curl_log.exists(), "curl bootstrap fired despite uv being present"


def test_resolves_through_symlink(tmp_path, monkeypatch):
    # Invoked via a symlink (e.g. ~/bin/update.sh -> the plugin dir), repo_root
    # must follow the link to the real script location, so the install targets
    # the real checkout — not the symlink's dir.
    link_dir = tmp_path / "userbin"
    link_dir.mkdir()
    link = link_dir / "update.sh"
    link.symlink_to(UPDATE_SH)

    uv_log = make_shim_on_path(tmp_path, monkeypatch, "uv")
    make_shim_on_path(tmp_path, monkeypatch, "cockpit")

    result = _run(link)
    assert result.returncode == 0, result.stderr

    repo_root = UPDATE_SH.resolve().parent.parent
    assert any(
        line == f"tool install --force --no-cache {repo_root}"
        for line in _lines(uv_log)
    )
