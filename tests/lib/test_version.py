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


def _at_version(monkeypatch, v):
    monkeypatch.setattr(version, "running_version", lambda: v)


def _marker():
    # Read at call time, not import: the autouse runtime-dir fixture patches the
    # config module attribute, which is exactly what `upgraded_version` reads.
    return version.config.COCKPIT_RUNTIME_DIR / version._SEEN_FILE


def test_first_ever_run_stamps_but_announces_nothing(monkeypatch):
    # A fresh install is not an upgrade — "here's what changed" is a lie about a
    # version you have never run. It must still stamp, or the NEXT launch (an
    # ordinary restart) would read as an upgrade.
    _at_version(monkeypatch, "2.24.2")
    assert version.upgraded_version() == ""
    assert _marker().read_text().strip() == "2.24.2"


def test_an_unchanged_version_announces_nothing(monkeypatch):
    _at_version(monkeypatch, "2.24.2")
    version.upgraded_version()
    for _ in range(3):
        assert version.upgraded_version() == ""


def test_a_changed_version_announces_once_and_restamps(monkeypatch):
    _at_version(monkeypatch, "2.24.2")
    version.upgraded_version()

    _at_version(monkeypatch, "2.25.0")
    assert version.upgraded_version() == "2.25.0"
    # Once per upgrade: the restamp is what closes it.
    assert version.upgraded_version() == ""
    assert _marker().read_text().strip() == "2.25.0"


def test_an_unresolvable_version_is_never_stamped(monkeypatch):
    # Stamping "" would make the next run, on a real version, read as an upgrade
    # — so a source tree with no metadata must write nothing at all.
    _at_version(monkeypatch, "")
    assert version.upgraded_version() == ""
    assert not _marker().exists()


def test_an_unwritable_runtime_dir_fails_open(monkeypatch, tmp_path):
    # The marker is a nicety; it runs on the startup path and must not be able
    # to take the TUI down. A failed write costs a repeated prompt, not a crash.
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("")
    monkeypatch.setattr(version.config, "COCKPIT_RUNTIME_DIR", blocked)
    _at_version(monkeypatch, "2.24.2")
    assert version.upgraded_version() == ""
