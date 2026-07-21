"""Tests for cockpit/lib/version — the running package version."""

from __future__ import annotations

import re

from cockpit.lib import version


def test_running_version_reads_package_metadata():
    v = version.running_version()
    assert v
    # X.Y.Z numeric prefix; tolerate a PEP 440 pre-release suffix (e.g. 1.2.3rc1).
    assert re.match(r"^\d+\.\d+\.\d+", v)


def _no_metadata(monkeypatch):
    def _missing(name):
        raise version.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version.metadata, "version", _missing)


def test_running_version_falls_back_to_pyproject_without_metadata(monkeypatch):
    # Source checkout / isolated venv with no installed metadata → read pyproject.
    _no_metadata(monkeypatch)
    v = version.running_version()
    assert v
    assert re.match(r"^\d+\.\d+\.\d+", v)


def test_running_version_empty_when_no_source(monkeypatch, tmp_path):
    # Neither metadata nor a readable pyproject resolves → "".
    _no_metadata(monkeypatch)
    monkeypatch.setattr(version, "_PYPROJECT", tmp_path / "does-not-exist.toml")
    assert version.running_version() == ""
