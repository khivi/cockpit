"""Release-notes scoping logic — the gh fetch itself needs network/auth, so we
test the version-gap heuristic and the rendering/gating around a stubbed fetch."""

from __future__ import annotations

import pytest

from cockpit.lib import release_notes


@pytest.mark.parametrize(
    "prev,current,expected",
    [
        ("1.2.30", "1.2.35", 5),  # patch delta == PR count
        ("1.2.30", "1.2.30", release_notes._RECENT),  # no movement (caller guards)
        ("1.2.0", "1.3.0", release_notes._RECENT),  # minor bump → window fallback
        ("1.2.30", "1.2.99", release_notes._MAX),  # capped
        ("1.2", "1.2.5", release_notes._RECENT),  # malformed prev → window
    ],
)
def test_gap(prev: str, current: str, expected: int) -> None:
    assert release_notes._gap(prev, current) == expected


def test_notes_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: "o/r")
    monkeypatch.setattr(release_notes.version, "running_version", lambda: "1.2.35")
    seen: dict[str, int] = {}

    def fake_subjects(repo: str, limit: int) -> list[str]:
        seen["limit"] = limit
        return ["feat: a", "fix: b", "feat: c"]

    monkeypatch.setattr(release_notes, "_subjects", fake_subjects)
    body = release_notes.notes("1.2.32")  # gap of 3
    assert seen["limit"] == 3
    assert body.startswith("1.2.32 → 1.2.35")
    assert "• feat: a" in body


def test_notes_recent_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: "o/r")
    monkeypatch.setattr(release_notes.version, "running_version", lambda: "1.2.35")
    monkeypatch.setattr(release_notes, "_subjects", lambda r, n: ["feat: a"])
    body = release_notes.notes(None)  # the `r` key
    assert body.startswith("recent changes (v1.2.35)")


def test_notes_empty_on_no_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: None)
    assert release_notes.notes(None) == ""


def test_notes_empty_on_no_subjects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: "o/r")
    monkeypatch.setattr(release_notes.version, "running_version", lambda: "1.2.35")
    monkeypatch.setattr(release_notes, "_subjects", lambda r, n: [])
    assert release_notes.notes(None) == ""


def test_subjects_drops_bump_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Done:
        stdout = "feat: real\nchore: bump version to 1.2.3\nfix: also real\n"

    monkeypatch.setattr(release_notes.subprocess, "run", lambda *a, **k: _Done())
    assert release_notes._subjects("o/r", 10) == ["feat: real", "fix: also real"]
