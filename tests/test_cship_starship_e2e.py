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


def test_footer_does_not_render_time_pill(footer_env):
    """The bundled config dropped the wall-clock pill — regression-guard
    against accidental reintroduction (cship picks up `[time]` from
    starship's defaults if our `format` re-references it)."""
    env, _cache, _cfg = footer_env
    res = _run_footer(env)
    assert res.returncode == 0
    out = res.stdout.decode("utf-8", errors="replace")
    assert not re.search(
        r"\d{2}:\d{2}", out
    ), f"clock pill should be gone but rendered: {out!r}"


def test_footer_renders_ratelimit_pill(footer_env):
    env, cache, _cfg = footer_env
    (cache / "rate-limit-5h").write_text("8 2026-05-21T15:00:00Z")
    res = _run_footer(env)
    assert res.returncode == 0
    out = res.stdout.decode("utf-8", errors="replace")
    assert "8%/5h" in out, f"ratelimit pill missing: {out!r}"


def test_footer_does_not_duplicate_cwd(footer_env):
    """Regression: cship.toml used to include `$cship.workspace.current_dir`
    which printed the absolute path on every render — duplicating Claude
    Code's own header (which already shows the cwd, abbreviated with `~`).
    The footer line must not contain `$HOME` or any absolute filesystem
    path."""
    env, _cache, _cfg = footer_env
    res = _run_footer(env)
    assert res.returncode == 0
    out = res.stdout.decode("utf-8", errors="replace")
    assert env["HOME"] not in out, f"footer leaks HOME path: {out!r}"
    # Strip ANSI escapes before scanning for `/`-prefixed tokens — styled
    # output legitimately contains `;` and `m` chars that aren't filesystem.
    stripped = re.sub(r"\x1b\[[0-9;]*m", "", out)
    for token in stripped.split():
        assert not token.startswith(
            "/"
        ), f"footer line contains absolute-path token {token!r}: {stripped!r}"


def test_footer_renders_session_pills_via_sid(footer_env):
    """Regression for the screenshot bug: production always pipes
    `session_id` in the JSON, so the field printers must read the
    sid-keyed cache. Earlier tests only covered the no-sid path."""
    env, cache, _cfg = footer_env
    sid = "SID-FOR-E2E"
    (cache / f"context-{sid}").write_text("42 1000000")
    (cache / f"rate-limit-5h-{sid}").write_text("9 2026-05-21T15:00:00Z")
    blob = f'{{"session_id":"{sid}"}}'.encode()
    res = _run_footer(env, stdin=blob)
    assert res.returncode == 0, res.stderr.decode()
    out = res.stdout.decode("utf-8", errors="replace")
    assert "42%/1M" in out, f"sid-keyed context pill missing: {out!r}"
    assert "9%/5h" in out, f"sid-keyed ratelimit pill missing: {out!r}"


def test_footer_session_pills_survive_session_restart(footer_env):
    """Fresh-session regression: when Claude Code restarts, the first
    statusLine ping has only `session_id` + `transcript_path` — no
    `context_window` / `rate_limits`. The pills should still render by
    falling back to the most recent prior session's cache."""
    env, cache, _cfg = footer_env
    # Prior session's cache, no cache for the new sid yet.
    (cache / "context-PRIOR").write_text("80 1000000")
    (cache / "rate-limit-5h-PRIOR").write_text("33 2026-05-21T15:00:00Z")
    blob = b'{"session_id":"FRESH-SID","transcript_path":"/tmp/x.jsonl"}'
    res = _run_footer(env, stdin=blob)
    assert res.returncode == 0, res.stderr.decode()
    out = res.stdout.decode("utf-8", errors="replace")
    assert "80%/1M" in out, f"context pill should fall back to prior session: {out!r}"
    assert "33%/5h" in out, f"ratelimit pill should fall back to prior session: {out!r}"


def test_footer_survives_iso_string_resets_at(footer_env):
    """cship 1.7.x's JSON parser rejects string `resets_at` and emits an
    empty render. `stash_from_stdin` must coerce the ISO string to epoch
    in the outgoing blob so cship parses cleanly and the rest of the
    footer (clock, context, etc.) still renders."""
    env, _cache, _cfg = footer_env
    blob = (
        b'{"session_id":"S","context_window":{"used_percentage":7,'
        b'"context_window_size":1000000},'
        b'"rate_limits":{"five_hour":{"used_percentage":4,'
        b'"resets_at":"2026-05-22T15:00:00Z"}}}'
    )
    res = _run_footer(env, stdin=blob)
    assert res.returncode == 0
    out = res.stdout.decode("utf-8", errors="replace")
    assert "7%/1M" in out, f"context pill missing (cship blackout?): {out!r}"
    assert "4%/5h" in out, f"ratelimit pill missing (cship blackout?): {out!r}"


def test_footer_golden_full_render(footer_env):
    """Catch-all visual regression: feed a realistic Claude Code blob +
    pre-warm branch caches, then assert the ANSI-stripped output
    contains every pill in the expected order on two lines.

    Touching any of these breaks the test:
      - cship.toml line wrapper schema
      - STARSHIP_SHELL shim wiring
      - sid-keyed cache lookup
      - pill ordering / line break placement
      - cwd accidentally rendering
    """
    env, cache, _cfg = footer_env

    # Force a known branch by writing branch caches keyed to one we'll
    # set GIT_DIR/HEAD for. Simpler: write under the current cwd's
    # branch, which is what `current_branch(os.getcwd())` returns when
    # the subprocess runs from this repo.
    branch = _current_branch()
    branch_key = branch.replace("/", "-")
    (cache / f"pr-state-{branch_key}").write_text("APPROVED")
    (cache / f"pr-num-{branch_key}").write_text("9999")
    (cache / f"pr-checks-{branch_key}").write_text("✓")
    (cache / f"pr-title-{branch_key}").write_text("Golden test PR title")

    blob = (
        b'{"session_id":"G","context_window":{"used_percentage":7,'
        b'"context_window_size":1000000},'
        b'"rate_limits":{"five_hour":{"used_percentage":4,'
        b'"resets_at":"2026-05-22T15:00:00Z"}}}'
    )
    res = _run_footer(env, stdin=blob)
    assert res.returncode == 0, res.stderr.decode()
    raw = res.stdout.decode("utf-8", errors="replace")
    stripped = re.sub(r"\x1b\[[0-9;]*m", "", raw).rstrip()

    lines = stripped.split("\n")
    assert len(lines) == 2, f"expected 2-line footer, got {len(lines)}: {stripped!r}"
    line1, line2 = lines

    # Line 1: session state. context + rate must appear; clock must not.
    # branch_pill / commit_age also render because the subprocess inherits
    # this repo's cwd, but we don't pin their exact text (commit age moves;
    # dirty count depends on the worktree state when the test runs).
    assert "🧠 7%/1M" in line1, f"context pill missing: {line1!r}"
    assert "⌛ 4%/5h" in line1, f"ratelimit pill missing: {line1!r}"
    assert not re.search(r"\d{2}:\d{2}", line1), f"clock pill should be gone: {line1!r}"

    # Line 2: PR identity in declared order — state → num → checks → title.
    assert re.search(
        r"APPROVED.*#9999.*✓.*Golden test PR title",
        line2,
    ), f"line 2 PR pills out of order: {line2!r}"

    # No absolute path leaks into either line.
    assert env["HOME"] not in stripped


def _current_branch() -> str:
    """Read HEAD ref for the cwd's repo (the worktree this test runs in)."""
    res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return res.stdout.strip() if res.returncode == 0 else ""


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
