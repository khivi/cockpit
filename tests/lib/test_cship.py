"""Tests for the scripts/bin/starship shim + scripts/lib/cship.py wiring.

The shim rewrites `STARSHIP_SHELL=unknown` (which cship 1.7.1 forces) to
`sh` before exec'ing the real starship — without this, every [custom.*]
module in the cockpit footer renders empty. These tests drive the real
shim script against a fake "real starship" planted in a tmpdir, and unit-
test the PATH-injection + missing-binary error rendering in cship.py.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SHIM = REPO_ROOT / "scripts" / "bin" / "starship"
SHIM_DIR = SHIM.parent

import scripts.lib.cship as cship_mod  # noqa: E402


def _install_fake_bin(dir_: Path, name: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    f = dir_ / name
    f.write_text("#!/bin/sh\nexit 0\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return f


# ── shim: STARSHIP_SHELL rewrite ──────────────────────────────────────────


def _fake_starship(dest_dir: Path, body: str) -> Path:
    """Create an executable `starship` script at dest_dir/starship."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    fake = dest_dir / "starship"
    fake.write_text("#!/bin/sh\n" + body)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _run_shim(
    env_extra: dict, args: list[str], path_dirs: list[Path]
) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.pathsep.join(str(p) for p in path_dirs),
        **env_extra,
    }
    return subprocess.run(
        [str(SHIM), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_shim_rewrites_unknown_to_sh(tmp_path):
    _fake_starship(tmp_path / "real", 'printf "shell=%s\\n" "$STARSHIP_SHELL"\n')
    res = _run_shim(
        {"STARSHIP_SHELL": "unknown"},
        [],
        [tmp_path / "real"],
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "shell=sh"


def test_shim_passthrough_bash_untouched(tmp_path):
    _fake_starship(tmp_path / "real", 'printf "shell=%s\\n" "$STARSHIP_SHELL"\n')
    res = _run_shim(
        {"STARSHIP_SHELL": "bash"},
        [],
        [tmp_path / "real"],
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "shell=bash"


def test_shim_passthrough_unset(tmp_path):
    _fake_starship(
        tmp_path / "real",
        'if [ -z "${STARSHIP_SHELL-}" ]; then echo unset; else echo "set=$STARSHIP_SHELL"; fi\n',
    )
    res = _run_shim({}, [], [tmp_path / "real"])
    assert res.returncode == 0
    assert res.stdout.strip() == "unset"


def test_shim_self_skips_on_path(tmp_path):
    """With shim dir AND fake real on PATH, the shim must reach the fake
    exactly once — no infinite recursion. Marker file proves it."""
    marker = tmp_path / "marker"
    _fake_starship(
        tmp_path / "real",
        f"echo hit >> {marker}\n",
    )
    res = _run_shim(
        {"STARSHIP_SHELL": "unknown"},
        [],
        [SHIM_DIR, tmp_path / "real"],
    )
    assert res.returncode == 0, res.stderr
    assert marker.exists()
    assert marker.read_text().count("hit") == 1


def test_shim_passes_argv(tmp_path):
    _fake_starship(tmp_path / "real", 'printf "args=%s\\n" "$*"\n')
    res = _run_shim(
        {"STARSHIP_SHELL": "unknown"},
        ["prompt", "--status", "0", "--terminal-width", "120"],
        [tmp_path / "real"],
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "args=prompt --status 0 --terminal-width 120"


def test_shim_no_real_starship_exits_127(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    res = _run_shim({"STARSHIP_SHELL": "unknown"}, [], [empty])
    assert res.returncode == 127
    assert "starship" in res.stderr.lower()


# ── cship.py: PATH injection ──────────────────────────────────────────────


@pytest.fixture
def both_bins_installed(tmp_path, monkeypatch):
    """Plant real cship + starship executables and put them on PATH."""
    bindir = tmp_path / "bin"
    _install_fake_bin(bindir, "cship")
    _install_fake_bin(bindir, "starship")
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    return bindir


def test_invoke_cship_prepends_bin_dir_to_path(both_bins_installed, monkeypatch):
    bindir = both_bins_installed
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    captured = {}

    def fake_run(cmd, **kw):
        captured["env"] = kw["env"]
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    with patch.object(cship_mod.subprocess, "run", side_effect=fake_run):
        cship_mod.invoke_cship(b"{}", None)

    expected_prefix = f"{cship_mod.BIN_DIR}{os.pathsep}"
    assert captured["env"]["PATH"].startswith(expected_prefix)
    assert captured["env"]["PATH"].endswith(f"{bindir}:/usr/bin:/bin")


def test_invoke_cship_does_not_mutate_os_environ(both_bins_installed, monkeypatch):
    bindir = both_bins_installed
    original_path = f"{bindir}:/usr/bin:/bin"
    monkeypatch.setenv("PATH", original_path)
    with patch.object(
        cship_mod.subprocess,
        "run",
        return_value=MagicMock(returncode=0, stdout=b"", stderr=b""),
    ):
        cship_mod.invoke_cship(b"{}", None)
    assert os.environ["PATH"] == original_path


def test_invoke_cship_sets_session_id_alongside_path(both_bins_installed):
    captured = {}

    def fake_run(cmd, **kw):
        captured["env"] = kw["env"]
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    with patch.object(cship_mod.subprocess, "run", side_effect=fake_run):
        cship_mod.invoke_cship(b"{}", "sess-abc")

    assert captured["env"]["CSHIP_SESSION_ID"] == "sess-abc"
    assert str(cship_mod.BIN_DIR) in captured["env"]["PATH"]


# ── cship.py: missing-binary loud errors ──────────────────────────────────


def test_invoke_cship_errors_on_missing_cship(tmp_path, monkeypatch, capsysbinary):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    rc = cship_mod.invoke_cship(b"{}", None)
    assert rc != 0
    err = capsysbinary.readouterr().err.decode()
    assert "cship" in err and "not on PATH" in err


def test_invoke_cship_errors_on_missing_starship(tmp_path, monkeypatch, capsysbinary):
    bindir = tmp_path / "bin"
    _install_fake_bin(bindir, "cship")
    monkeypatch.setenv("PATH", str(bindir))
    rc = cship_mod.invoke_cship(b"{}", None)
    assert rc != 0
    err = capsysbinary.readouterr().err.decode()
    assert "starship" in err and "not on PATH" in err


# ── invoke_cship: subprocess plumbing ──────────────────────────────────────


def test_invoke_cship_pipes_blob_and_forwards_stdout(
    tmp_path, monkeypatch, capsysbinary
):
    """invoke_cship pipes the given blob to cship and forwards its stdout."""
    import subprocess as _sp

    bindir = tmp_path / "bin"
    _install_fake_bin(bindir, "cship")
    _install_fake_bin(bindir, "starship")
    monkeypatch.setenv("PATH", str(bindir))

    captured = {}

    def fake_run(cmd, input=None, capture_output=False, env=None):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["env"] = env
        return _sp.CompletedProcess(cmd, 0, stdout=b"styled-output\n", stderr=b"")

    monkeypatch.setattr("scripts.lib.cship.subprocess.run", fake_run)

    assert cship_mod.invoke_cship(b'{"hello":"world"}', "sess1") == 0
    assert captured["cmd"] == ["cship"]
    assert captured["input"] == b'{"hello":"world"}'
    assert captured["env"]["CSHIP_SESSION_ID"] == "sess1"
    out, _err = capsysbinary.readouterr()
    assert out == b"styled-output\n"


def test_invoke_cship_propagates_exit_code(tmp_path, monkeypatch, capsysbinary):
    import subprocess as _sp

    bindir = tmp_path / "bin"
    _install_fake_bin(bindir, "cship")
    _install_fake_bin(bindir, "starship")
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(
        "scripts.lib.cship.subprocess.run",
        lambda *a, **kw: _sp.CompletedProcess(["cship"], 17, b"", b"boom\n"),
    )

    assert cship_mod.invoke_cship(b"", None) == 17
    _out, err = capsysbinary.readouterr()
    assert err == b"boom\n"


def test_invoke_cship_no_session_id_omits_env_export(tmp_path, monkeypatch):
    """When sid is None, CSHIP_SESSION_ID must not be exported into cship's env."""
    import subprocess as _sp

    bindir = tmp_path / "bin"
    _install_fake_bin(bindir, "cship")
    _install_fake_bin(bindir, "starship")
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.delenv("CSHIP_SESSION_ID", raising=False)
    captured = {}

    def fake_run(cmd, input=None, capture_output=False, env=None):
        captured["env"] = env
        return _sp.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("scripts.lib.cship.subprocess.run", fake_run)
    cship_mod.invoke_cship(b"{}", None)
    assert "CSHIP_SESSION_ID" not in captured["env"]
