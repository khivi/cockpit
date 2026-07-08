"""Tests for cockpit/lib/updater — the in-wheel `cockpit update` flow.

The updater shells out to `claude`, `uv`, and `cockpit` and reads the plugin
cache off disk. We mock `subprocess.run`/`shutil.which` at the updater's own
boundary (orchestrator style — the leaves are covered elsewhere) and build a
real plugin-cache tree on `tmp_path`, then assert the step ordering, the
manifest-derived ids, the newest-dir pick, and the downgrade/skip guards.

`version.marketplace_name()`/`plugin_name()` read this repo's real manifests
(`khivi-cockpit` / `cockpit`), so the cache tree uses those names verbatim.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from cockpit.lib import updater

_MARKET = "khivi-cockpit"
_PLUGIN = "cockpit"


class FakeRun:
    """Records every subprocess.run call; optionally fails matching commands."""

    def __init__(self, fail_on: Callable[[list], bool] | None = None) -> None:
        self.calls: list[list] = []
        self._fail_on = fail_on or (lambda cmd: False)

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self._fail_on(cmd):
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    def ran(self, *needles: str) -> bool:
        """True if some recorded list-form call contains all `needles` as items."""
        for cmd in self.calls:
            if isinstance(cmd, list) and all(n in cmd for n in needles):
                return True
        return False


def _which_all(name: str) -> str | None:
    return f"/usr/bin/{name}"


def _make_cache(tmp_path, monkeypatch, versions: list[str]):
    """Build `<tmp>/plugins/cache/<market>/<plugin>/<ver>/` dirs and point
    CLAUDE_CONFIG_DIR at the tmp root. Returns the cache root."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    root = tmp_path / "plugins" / "cache" / _MARKET / _PLUGIN
    for v in versions:
        (root / v).mkdir(parents=True)
    return root


# --- --check ---------------------------------------------------------------


def test_check_reports_update_available(monkeypatch, capsys):
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.1.0")
    monkeypatch.setattr(updater.version, "latest_version", lambda: "0.2.0")
    assert updater.run_update(check_only=True) == updater.UPDATE_AVAILABLE_EXIT
    assert "update available" in capsys.readouterr().out


def test_check_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.2.0")
    monkeypatch.setattr(updater.version, "latest_version", lambda: "0.2.0")
    assert updater.run_update(check_only=True) == 0
    assert "up to date" in capsys.readouterr().out


def test_check_does_not_shell_out(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(updater.subprocess, "run", fake)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.2.0")
    monkeypatch.setattr(updater.version, "latest_version", lambda: None)
    updater.run_update(check_only=True)
    assert fake.calls == []


# --- newest_cache_dir ------------------------------------------------------


def test_newest_cache_dir_picks_numeric_max(tmp_path, monkeypatch):
    _make_cache(tmp_path, monkeypatch, ["0.27.9", "0.27.10", "0.27.100"])
    newest = updater.newest_cache_dir()
    assert newest is not None
    assert newest.name == "0.27.100"  # numeric, not lexical (would be 0.27.9)


def test_newest_cache_dir_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert updater.newest_cache_dir() is None


# --- full update -----------------------------------------------------------


def test_full_update_refreshes_installs_newest_and_runs_setup(tmp_path, monkeypatch):
    root = _make_cache(tmp_path, monkeypatch, ["0.27.90", "0.27.91"])
    fake = FakeRun()
    monkeypatch.setattr(updater.subprocess, "run", fake)
    monkeypatch.setattr(updater.shutil, "which", _which_all)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.90")

    assert updater.run_update() == 0

    # Marketplace + plugin refresh with the manifest-derived ids.
    assert fake.ran("plugin", "marketplace", "update", _MARKET)
    assert fake.ran("plugin", "update", f"{_PLUGIN}@{_MARKET}")
    # Reinstalled from the NEWEST cached dir, with --force --no-cache.
    assert fake.ran(
        "uv", "tool", "install", "--force", "--no-cache", str(root / "0.27.91")
    )
    assert not fake.ran(
        "uv", "tool", "install", "--force", "--no-cache", str(root / "0.27.90")
    )
    # Footer re-pinned through the installed console script.
    assert fake.ran("/usr/bin/cockpit", "setup")


def test_update_subprocesses_are_tty_detached(tmp_path, monkeypatch):
    # Every update subprocess must run off the controlling TTY (stdin=DEVNULL,
    # own session) so a child can't grab the foreground pgrp / block on a read
    # and leave the re-exec'd TUI stopped — the blank frozen-screen `u` bug.
    _make_cache(tmp_path, monkeypatch, ["0.27.91"])
    seen: list[dict] = []

    def capture(cmd, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(updater.subprocess, "run", capture)
    monkeypatch.setattr(updater.shutil, "which", _which_all)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.90")

    assert updater.run_update() == 0
    assert seen  # sanity: subprocesses actually ran
    for kw in seen:
        assert kw.get("stdin") is subprocess.DEVNULL
        assert kw.get("start_new_session") is True


def test_install_failure_is_fatal(tmp_path, monkeypatch):
    _make_cache(tmp_path, monkeypatch, ["0.27.91"])
    fake = FakeRun(fail_on=lambda cmd: "install" in cmd and "uv" in cmd)
    monkeypatch.setattr(updater.subprocess, "run", fake)
    monkeypatch.setattr(updater.shutil, "which", _which_all)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.90")

    assert updater.run_update() == 1
    # Setup must NOT run after a fatal install failure.
    assert not fake.ran("/usr/bin/cockpit", "setup")


def test_plugin_refresh_failure_does_not_block_install(tmp_path, monkeypatch):
    # `claude plugin update` exits non-zero, but the updater calls it check=False
    # (recorded as a no-raise here) — the reinstall must still run.
    root = _make_cache(tmp_path, monkeypatch, ["0.27.91"])
    fake = FakeRun()
    monkeypatch.setattr(updater.subprocess, "run", fake)
    monkeypatch.setattr(updater.shutil, "which", _which_all)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.90")

    assert updater.run_update() == 0
    assert fake.ran(
        "uv", "tool", "install", "--force", "--no-cache", str(root / "0.27.91")
    )


def test_refresh_uses_list_form_with_check_false(tmp_path, monkeypatch):
    # The claude calls must pass check=False so a refresh failure can't abort.
    _make_cache(tmp_path, monkeypatch, ["0.27.91"])
    seen: list[dict] = []

    def run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["claude", "plugin"]:
            seen.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(updater.subprocess, "run", run)
    monkeypatch.setattr(updater.shutil, "which", _which_all)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.90")
    updater.run_update()
    assert seen and all(kw.get("check") is False for kw in seen)


# --- guards ----------------------------------------------------------------


def test_downgrade_guard_skips_install(tmp_path, monkeypatch):
    # Running install already >= newest cached dir → skip the reinstall (else
    # every `u` would roll the daemon back).
    _make_cache(tmp_path, monkeypatch, ["0.27.91"])
    fake = FakeRun()
    monkeypatch.setattr(updater.subprocess, "run", fake)
    monkeypatch.setattr(updater.shutil, "which", _which_all)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.91")

    assert updater.run_update() == updater.UPDATE_SKIPPED_NOOP_EXIT
    assert not fake.ran("uv", "tool", "install")


def test_no_cache_dir_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))  # no cache tree
    fake = FakeRun()
    monkeypatch.setattr(updater.subprocess, "run", fake)
    monkeypatch.setattr(updater.shutil, "which", _which_all)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.90")

    assert updater.run_update() == 1
    assert not fake.ran("uv", "tool", "install")


def test_skip_install_runs_refresh_and_setup_only(tmp_path, monkeypatch):
    # The bootstrap handoff: bin/update.sh already installed, so --skip-install
    # runs refresh + setup but never reinstalls (even if a newer cache exists).
    _make_cache(tmp_path, monkeypatch, ["9.9.9"])
    fake = FakeRun()
    monkeypatch.setattr(updater.subprocess, "run", fake)
    monkeypatch.setattr(updater.shutil, "which", _which_all)
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.90")

    assert updater.run_update(skip_install=True) == 0
    assert not fake.ran("uv", "tool", "install")
    assert fake.ran("plugin", "update", f"{_PLUGIN}@{_MARKET}")
    assert fake.ran("/usr/bin/cockpit", "setup")


def test_refresh_skipped_without_claude(tmp_path, monkeypatch):
    # No `claude` on PATH: skip the refresh, but still install + setup.
    root = _make_cache(tmp_path, monkeypatch, ["0.27.91"])
    fake = FakeRun()
    monkeypatch.setattr(updater.subprocess, "run", fake)
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: None if name == "claude" else f"/usr/bin/{name}",
    )
    monkeypatch.setattr(updater.version, "running_version", lambda: "0.27.90")

    assert updater.run_update() == 0
    assert not fake.ran("plugin", "update", f"{_PLUGIN}@{_MARKET}")
    assert fake.ran(
        "uv", "tool", "install", "--force", "--no-cache", str(root / "0.27.91")
    )
