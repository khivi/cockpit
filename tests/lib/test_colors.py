"""Theme-aware neutral greys in lib.colors.

colors.py resolves the `theme` from config.json at IMPORT time (mirroring how
it reads $NO_COLOR), so every test reloads the module after planting config.
Only the neutral greys (slate/shadow + bold variants) flip between dark and
light; every saturated hue is background-agnostic and must stay put.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Callable

import pytest

import cockpit.lib.colors as colors_mod


@pytest.fixture(autouse=True)
def _reset_colors_module():
    """Each test reloads colors against a planted config, mutating the shared
    module object. Restore it to the default (dark) state afterward so other
    test modules that read colors via the live module object aren't polluted by
    collection order."""
    yield
    prev = os.environ.get("COCKPIT_HOME")
    os.environ["COCKPIT_HOME"] = "/nonexistent-cockpit-home-reset"
    try:
        importlib.reload(colors_mod)
    finally:
        if prev is None:
            os.environ.pop("COCKPIT_HOME", None)
        else:
            os.environ["COCKPIT_HOME"] = prev


def _reload_with(tmp_path, monkeypatch, cfg: dict | None):
    """Reload lib.colors with $COCKPIT_HOME pointed at a config holding `cfg`
    (or no config file at all when cfg is None)."""
    monkeypatch.delenv("NO_COLOR", raising=False)  # ensure codes are emitted
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    if cfg is not None:
        (tmp_path / "config.json").write_text(json.dumps(cfg))
    return importlib.reload(colors_mod)


def _code(colorizer: Callable[[str], str]) -> str:
    """Extract the SGR parameter string from a colorizer's output."""
    wrapped = colorizer("X")
    return wrapped[wrapped.index("[") + 1 : wrapped.index("m")]


def test_dark_theme_uses_light_tuned_greys(tmp_path, monkeypatch):
    c = _reload_with(tmp_path, monkeypatch, {"theme": "dark"})
    assert _code(c.slate) == "38;5;243"
    assert _code(c.shadow) == "38;5;240"
    assert _code(c.bold_slate) == "1;38;5;243"
    assert _code(c.bold_shadow) == "1;38;5;240"


def test_light_theme_darkens_greys(tmp_path, monkeypatch):
    c = _reload_with(tmp_path, monkeypatch, {"theme": "light"})
    assert _code(c.slate) == "38;5;236"
    assert _code(c.shadow) == "38;5;238"
    assert _code(c.bold_slate) == "1;38;5;236"
    assert _code(c.bold_shadow) == "1;38;5;238"


def test_saturated_hues_are_theme_invariant(tmp_path, monkeypatch):
    hue_names = (
        "orange",
        "azure",
        "crimson",
        "leaf",
        "amber",
        "bold_violet",
        "bold_ruby",
    )
    dark = _reload_with(tmp_path, monkeypatch, {"theme": "dark"})
    hues_dark = {n: _code(getattr(dark, n)) for n in hue_names}
    light = _reload_with(tmp_path, monkeypatch, {"theme": "light"})
    for name, code in hues_dark.items():
        assert _code(getattr(light, name)) == code, f"{name} changed with theme"


def test_missing_config_defaults_to_dark(tmp_path, monkeypatch):
    c = _reload_with(tmp_path, monkeypatch, None)  # no config.json
    assert _code(c.slate) == "38;5;243"


def test_unknown_theme_defaults_to_dark(tmp_path, monkeypatch):
    c = _reload_with(tmp_path, monkeypatch, {"theme": "solarized"})
    assert _code(c.slate) == "38;5;243"


def test_corrupt_config_defaults_to_dark(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text("{ not json")
    c = importlib.reload(colors_mod)
    assert _code(c.slate) == "38;5;243"


# ── CMUX_COLOR_ANSI (sidebar_color → log-echo colorizers) ────────────────────

_CMUX_COLOR_NAMES = {
    "Red",
    "Crimson",
    "Orange",
    "Amber",
    "Olive",
    "Green",
    "Teal",
    "Aqua",
    "Blue",
    "Navy",
    "Indigo",
    "Purple",
    "Magenta",
    "Rose",
    "Brown",
    "Charcoal",
}


def test_cmux_color_ansi_covers_all_cmux_names(tmp_path, monkeypatch):
    c = _reload_with(tmp_path, monkeypatch, {"theme": "dark"})
    assert set(c.CMUX_COLOR_ANSI) == _CMUX_COLOR_NAMES


def test_cmux_color_ansi_values_are_bold_256(tmp_path, monkeypatch):
    c = _reload_with(tmp_path, monkeypatch, {"theme": "dark"})
    for name, colorizer in c.CMUX_COLOR_ANSI.items():
        assert _code(colorizer).startswith("1;38;5;"), name


def test_cmux_color_ansi_is_theme_invariant(tmp_path, monkeypatch):
    dark = _reload_with(tmp_path, monkeypatch, {"theme": "dark"})
    codes_dark = {n: _code(f) for n, f in dark.CMUX_COLOR_ANSI.items()}
    light = _reload_with(tmp_path, monkeypatch, {"theme": "light"})
    for name, code in codes_dark.items():
        assert _code(light.CMUX_COLOR_ANSI[name]) == code, f"{name} changed with theme"


def test_cmux_color_ansi_respects_no_color(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    c = importlib.reload(colors_mod)
    # NO_COLOR makes every colorizer the identity — no escape codes emitted.
    assert c.CMUX_COLOR_ANSI["Teal"]("repo") == "repo"
