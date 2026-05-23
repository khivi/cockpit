"""Tests for scripts/cockpit.py CLI dispatch.

`--footer` seeds statusLine + starship/cship configs; `--once` / `--watch` do
NOT touch those files. Pipeline tests live in tests/orchestrators/test_cycle.py.
"""

from __future__ import annotations

import importlib
import json as _json

from tests.asserts import expected_starship as _expected_starship
from tests.fixtures import (
    make_bin_on_path as _make_bin_on_path,
    setup_cockpit_config as _setup_cockpit_config,
)


def test_cli_footer_flag_runs_only_footer_setup(tmp_path, monkeypatch):
    """`--footer` installs cship.toml + starship.toml + statusLine and exits."""
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import scripts.cockpit as cockpit

    importlib.reload(cockpit)

    def _explode(*_a, **_kw):
        raise AssertionError("--footer must not trigger a reconcile cycle")

    monkeypatch.setattr(cockpit, "gh_self_user", _explode)
    monkeypatch.setattr(cockpit, "cycle_all", _explode)

    assert cockpit.main(["--footer"]) == 0

    cship_toml = tmp_path / "xdg" / "cship.toml"
    assert cship_toml.exists()
    assert cship_toml.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()

    starship_toml = tmp_path / "xdg" / "starship.toml"
    assert starship_toml.exists()
    assert starship_toml.read_text() == _expected_starship(cockpit_config)

    settings = _json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["statusLine"]["type"] == "command"
    assert settings["statusLine"]["command"].endswith("/footer.py")


def test_cli_once_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--once` is pure reconcile — never seeds either toml or writes statusLine."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import scripts.cockpit as cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_once_with", lambda _s: None)

    assert cockpit.main(["--once"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_cli_watch_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--watch` is pure reconcile — never seeds either toml or writes statusLine."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cship", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import scripts.cockpit as cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_watch", lambda _s, _secs: None)

    assert cockpit.main(["--watch", "60"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_cli_once_exits_when_use_cship_and_cship_missing(tmp_path, monkeypatch, capsys):
    """Preflight runs on every cockpit invocation: `use_cship: true` without
    cship on PATH must hard-fail `--once` (and `--watch`, `--footer`) — same
    contract as the standalone `lib.preflight.preflight()` test suite."""
    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "starship")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import scripts.cockpit as cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_once_with", lambda _s: None)

    import pytest

    with pytest.raises(SystemExit) as exc:
        cockpit.main(["--once"])
    assert exc.value.code == 2
    assert "`cship`" in capsys.readouterr().err
