"""Shared test helpers for spinning up an isolated cockpit environment.

Used by `tests/lib/test_config.py` (lib-level: config + statusLine seeding)
and `tests/test_cockpit.py` (entry-point: `cockpit.main(...)` CLI dispatch).
Both need the same setup (fake $HOME, COCKPIT_HOME, optional cship-on-PATH
stub) — this module is the single source of truth.
"""

from __future__ import annotations


def expected_starship(cockpit_config) -> str:
    """The bundled starship.toml after __COCKPIT_STARSHIP__ placeholder substitution.

    `install_starship_default_config()` rewrites the placeholder to the
    resolved absolute path of `scripts/starship.py` before writing to
    ~/.config/starship.toml — assertions about installed content must
    match that substituted output, not the in-repo source.
    """
    return cockpit_config.STARSHIP_DEFAULT_TOML.read_text().replace(
        cockpit_config.STARSHIP_PLACEHOLDER, str(cockpit_config.STARSHIP_PY)
    )


def setup_cockpit_config(tmp_path, monkeypatch, cfg: dict):
    """Stand up an isolated cockpit config + fake $HOME, return reloaded module."""
    import importlib
    import json as _json

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / "config.json").write_text(_json.dumps(cfg))

    import scripts.lib.config as cockpit_config

    importlib.reload(cockpit_config)
    return cockpit_config


def fake_cship_on_path(tmp_path, monkeypatch, present: bool) -> None:
    """Pin $PATH to a tmp bin dir, optionally containing an executable
    `cship` shim, so `shutil.which("cship")` resolves deterministically
    against the real filesystem instead of being mocked."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if present:
        shim = bin_dir / "cship"
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
