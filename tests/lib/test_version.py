"""Tests for cockpit/lib/version — the running package version."""

from __future__ import annotations

from cockpit.lib import version


def test_running_version_reads_package_metadata():
    v = version.running_version()
    assert v
    assert all(part.isdigit() for part in v.split("."))


def test_running_version_empty_when_no_metadata(monkeypatch):
    def _missing(name):
        raise version.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version.metadata, "version", _missing)
    assert version.running_version() == ""
