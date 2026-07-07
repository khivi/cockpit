"""Release-notes scoping logic — the gh fetch itself needs network/auth, so we
test the version-gap heuristic and the rendering/gating around a stubbed fetch."""

from __future__ import annotations

import pytest

from cockpit.lib import release_notes

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
