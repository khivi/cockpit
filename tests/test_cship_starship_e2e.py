"""End-to-end tests against the real cship+starship binaries.

Catches integration regressions our unit tests don't — e.g. the `[cship]/
lines` wrapper schema change in #62, and the `STARSHIP_SHELL=unknown`
collapse that this branch fixes. Module-level skip means CI (no binaries)
passes cleanly; the laptop hosts the actual signal.

Each test isolates HOME / XDG_CONFIG_HOME / TMPDIR into a tmpdir so the
user's real cship / starship configs and cockpit-cache are never touched.
The bundled scripts/defaults/{cship,starship}.toml configs are copied in
verbatim, with the `__COCKPIT_STARSHIP__` placeholder substituted exactly
like `install_starship_default_config()` does at install time.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("cship") is None or shutil.which("starship") is None,
    reason="cship or starship binary not installed",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
FOOTER_PY = SCRIPTS / "footer.py"
STARSHIP_PY = SCRIPTS / "starship.py"
SHIM_DIR = SCRIPTS / "bin"
DEFAULTS = SCRIPTS / "defaults"
PLACEHOLDER = "__COCKPIT_STARSHIP__"


@pytest.fixture
def footer_env(tmp_path):
    """Return (env, cache_dir, config_dir) with all paths isolated to tmp."""
    home = tmp_path / "home"
    config_dir = home / ".config"
    config_dir.mkdir(parents=True)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    cache_dir = tmpdir / "cockpit-cache"
    cache_dir.mkdir()

    # Substitute __COCKPIT_STARSHIP__ → absolute path to scripts/starship.py,
    # mirroring install_starship_default_config().
    starship_toml = (
        (DEFAULTS / "starship.toml").read_text().replace(PLACEHOLDER, str(STARSHIP_PY))
    )
    (config_dir / "starship.toml").write_text(starship_toml)
    shutil.copy(DEFAULTS / "cship.toml", config_dir / "cship.toml")

    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_dir),
        "TMPDIR": str(tmpdir),
        "STARSHIP_CONFIG": str(config_dir / "starship.toml"),
        # Keep the host's PATH so cship + starship binaries resolve.
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    return env, cache_dir, config_dir


def _run_footer(env: dict, stdin: bytes = b"{}") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(FOOTER_PY)],
        input=stdin,
        env=env,
        capture_output=True,
        timeout=15,
    )


def test_footer_smoke_renders(footer_env):
    env, _cache, _cfg = footer_env
    res = _run_footer(env)
    assert res.returncode == 0, res.stderr.decode()
    assert res.stdout, f"empty footer; stderr={res.stderr!r}"


def test_footer_renders_context_pill(footer_env):
    """Regression-guard for #62 ([cship]/lines wrapper) AND the
    STARSHIP_SHELL=unknown collapse this branch fixes. If either is
    broken, [custom.context] silently disappears."""
    env, cache, _cfg = footer_env
    (cache / "context").write_text("42 1000000")
    res = _run_footer(env)
    assert res.returncode == 0, res.stderr.decode()
    out = res.stdout.decode("utf-8", errors="replace")
    assert "42%/1M" in out, f"context pill missing from footer: {out!r}"


def test_footer_renders_time_module(footer_env):
    env, _cache, _cfg = footer_env
    res = _run_footer(env)
    assert res.returncode == 0
    out = res.stdout.decode("utf-8", errors="replace")
    assert re.search(r"\d{2}:\d{2}", out), f"no HH:MM clock in footer: {out!r}"


def test_footer_renders_ratelimit_pill(footer_env):
    env, cache, _cfg = footer_env
    (cache / "rate-limit-5h").write_text("8 2026-05-21T15:00:00Z")
    res = _run_footer(env)
    assert res.returncode == 0
    out = res.stdout.decode("utf-8", errors="replace")
    assert "8%/5h" in out, f"ratelimit pill missing: {out!r}"


def test_shim_is_load_bearing_for_custom_modules(footer_env):
    """Run cship directly twice — once with the shim on PATH, once
    without. With the shim, `STARSHIP_SHELL=unknown` is rewritten to
    `sh` and [custom.context] renders. Without it, the pill disappears.
    This is the test that would have caught the current bug."""
    env, cache, _cfg = footer_env
    (cache / "context").write_text("42 1000000")

    # Inputs cship expects on stdin: any JSON blob is fine; the cache
    # file is what feeds [custom.context].
    blob = b'{"session_id":null}'

    env_no_shim = {**env, "STARSHIP_SHELL": "unknown"}
    res_no_shim = subprocess.run(
        ["cship"],
        input=blob,
        env=env_no_shim,
        capture_output=True,
        timeout=15,
    )

    env_with_shim = {
        **env,
        "STARSHIP_SHELL": "unknown",
        "PATH": f"{SHIM_DIR}{os.pathsep}{env['PATH']}",
    }
    res_with_shim = subprocess.run(
        ["cship"],
        input=blob,
        env=env_with_shim,
        capture_output=True,
        timeout=15,
    )

    out_no_shim = res_no_shim.stdout.decode("utf-8", errors="replace")
    out_with_shim = res_with_shim.stdout.decode("utf-8", errors="replace")

    assert (
        "42%/1M" not in out_no_shim
    ), f"Expected context pill MISSING without shim, but it rendered: {out_no_shim!r}"
    assert (
        "42%/1M" in out_with_shim
    ), f"Expected context pill PRESENT with shim, but missing: {out_with_shim!r}"
