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


_OLD = "2020-01-01T00:00:00Z"  # always buckets to "earlier"


def test_bucket() -> None:
    from datetime import timedelta

    now = release_notes.datetime.now().astimezone()
    today = now.date()

    def at(days: int) -> str:
        return (now - timedelta(days=days)).isoformat()

    assert release_notes._bucket(at(0), today) == "today"
    assert release_notes._bucket(at(1), today) == "yesterday"
    assert release_notes._bucket(at(60), today) == "earlier"
    assert release_notes._bucket("garbage", today) == "earlier"


def test_notes_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: "o/r")
    monkeypatch.setattr(release_notes.version, "running_version", lambda: "1.2.35")
    seen: dict[str, int] = {}

    def fake_raw(repo: str, limit: int, page: int = 1) -> list[tuple[str, str]]:
        seen["limit"] = limit
        return [("feat: a", _OLD), ("fix: b", _OLD), ("feat: c", _OLD)]

    monkeypatch.setattr(release_notes, "_raw_entries", fake_raw)
    result = release_notes.notes("1.2.32")  # gap of 3
    assert result is not None
    title, items = result
    assert seen["limit"] == 3
    assert title == "1.2.32 → 1.2.35"
    assert ("feat: a", "earlier") in items


def test_notes_recent_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: "o/r")
    monkeypatch.setattr(release_notes.version, "running_version", lambda: "1.2.35")
    monkeypatch.setattr(
        release_notes, "_raw_entries", lambda r, n, page=1: [("feat: a", _OLD)]
    )
    result = release_notes.notes(None)  # the `r` key
    assert result is not None
    assert result[0] == "recent changes (v1.2.35)"


def test_notes_empty_on_no_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: None)
    assert release_notes.notes(None) is None


def test_notes_empty_on_no_subjects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: "o/r")
    monkeypatch.setattr(release_notes.version, "running_version", lambda: "1.2.35")
    monkeypatch.setattr(release_notes, "_raw_entries", lambda r, n, page=1: [])
    assert release_notes.notes(None) is None


def test_entries_drops_bump_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Done:
        stdout = (
            f"feat: real\t{_OLD}\n"
            f"chore: bump version to 1.2.3\t{_OLD}\n"
            f"fix: also real\t{_OLD}\n"
        )

    monkeypatch.setattr(release_notes.subprocess, "run", lambda *a, **k: _Done())
    entries = release_notes._entries("o/r", 10)
    assert [s for s, _ in entries] == ["feat: real", "fix: also real"]
    assert all(bucket == "earlier" for _, bucket in entries)


def test_recent_page_full_page_not_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: "o/r")
    seen: dict[str, int] = {}

    def fake_raw(repo: str, per_page: int, page: int) -> list[tuple[str, str]]:
        seen["per_page"], seen["page"] = per_page, page
        return [(f"feat: {i}", _OLD) for i in range(per_page)]  # exactly per_page

    monkeypatch.setattr(release_notes, "_raw_entries", fake_raw)
    items, exhausted = release_notes.recent_page(2, per_page=5)
    assert (seen["per_page"], seen["page"]) == (5, 2)
    assert len(items) == 5 and exhausted is False
    assert items[0] == ("feat: 0", "earlier")


def test_recent_page_short_page_is_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: "o/r")
    # A short page ends history; the bump-noise filter still drops a line, but
    # `exhausted` keys off the raw count, not the filtered one.
    monkeypatch.setattr(
        release_notes,
        "_raw_entries",
        lambda r, p, pg: [
            ("feat: a", _OLD),
            ("chore: bump version to 1.0.0", _OLD),
            ("fix: b", _OLD),
        ],
    )
    items, exhausted = release_notes.recent_page(1, per_page=15)
    assert [s for s, _ in items] == ["feat: a", "fix: b"] and exhausted is True


def test_recent_page_empty_on_no_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "install_repo", lambda: None)
    assert release_notes.recent_page(1) == ([], True)


def test_recent_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notes.version, "running_version", lambda: "1.2.35")
    assert release_notes.recent_title() == "recent changes (v1.2.35)"
