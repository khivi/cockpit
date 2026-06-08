"""Tests for cockpit/lib/version — running/latest version + comparator.

`running_version`/`install_repo` read the bundled manifests against tmp files
(leaf-on-disk style). `latest_version` shells out to `gh`, so its network/auth
path is exercised by patching `subprocess.run` — the real GitHub round-trip is
neither reproducible nor offline-safe.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from cockpit.lib import version


def _write_plugin(path, ver):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "cockpit", "version": ver}))


# --- _read_version / running_version ---------------------------------------


def test_running_version_reads_bundled_manifest():
    # The repo's own plugin.json must parse to a dotted version string.
    v = version.running_version()
    assert v
    assert all(part.isdigit() for part in v.split("."))


def test_read_version_valid(tmp_path):
    p = tmp_path / "plugin.json"
    _write_plugin(p, "1.2.3")
    assert version._read_version(p) == "1.2.3"


def test_read_version_missing_file(tmp_path):
    assert version._read_version(tmp_path / "nope.json") == ""


def test_read_version_malformed_json(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text("{not json")
    assert version._read_version(p) == ""


def test_read_version_absent_key(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({"name": "cockpit"}))
    assert version._read_version(p) == ""


# --- install_repo ----------------------------------------------------------


def test_install_repo_reads_marketplace_source():
    # The bundled marketplace.json points at the GitHub install source.
    assert version.install_repo() == "khivi/cockpit"


def test_install_repo_missing_marketplace(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "_MARKETPLACE_JSON", tmp_path / "nope.json")
    assert version.install_repo() is None


def test_install_repo_no_github_source(monkeypatch, tmp_path):
    p = tmp_path / "marketplace.json"
    p.write_text(json.dumps({"plugins": [{"source": {"source": "local"}}]}))
    monkeypatch.setattr(version, "_MARKETPLACE_JSON", p)
    assert version.install_repo() is None


# --- is_newer / _parse -----------------------------------------------------


@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("0.27.80", "0.27.74", True),
        ("0.28.0", "0.27.74", True),
        ("1.0.0", "0.99.99", True),
        ("0.27.74", "0.27.74", False),
        ("0.27.73", "0.27.74", False),
        ("0.27.9", "0.27.74", False),  # 9 < 74, not lexical
        ("0.27.74", "0.27.9", True),
        ("", "0.27.74", False),
        ("0.27.80", "", False),
    ],
)
def test_is_newer(candidate, current, expected):
    assert version.is_newer(candidate, current) is expected


def test_parse_non_numeric_chunks_default_zero():
    assert version._parse("1.2.x") == (1, 2, 0)


# --- latest_version --------------------------------------------------------


def _fake_run(stdout):
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return _run


def test_latest_version_parses_gh_raw_output(monkeypatch):
    monkeypatch.setattr(version, "install_repo", lambda: "khivi/cockpit")
    payload = json.dumps({"name": "cockpit", "version": "0.27.80"})
    with patch.object(subprocess, "run", _fake_run(payload)):
        assert version.latest_version() == "0.27.80"


def test_latest_version_none_when_no_repo(monkeypatch):
    monkeypatch.setattr(version, "install_repo", lambda: None)
    assert version.latest_version() is None


def test_latest_version_none_on_gh_failure(monkeypatch):
    monkeypatch.setattr(version, "install_repo", lambda: "khivi/cockpit")

    def _boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "gh")

    with patch.object(subprocess, "run", _boom):
        assert version.latest_version() is None


def test_latest_version_none_on_malformed_payload(monkeypatch):
    monkeypatch.setattr(version, "install_repo", lambda: "khivi/cockpit")
    with patch.object(subprocess, "run", _fake_run("{not json")):
        assert version.latest_version() is None


def test_latest_version_none_when_version_absent(monkeypatch):
    monkeypatch.setattr(version, "install_repo", lambda: "khivi/cockpit")
    with patch.object(subprocess, "run", _fake_run(json.dumps({"name": "x"}))):
        assert version.latest_version() is None
