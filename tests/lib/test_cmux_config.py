from __future__ import annotations

from pathlib import Path

import pytest

from cockpit.lib import cmux_config
from cockpit.lib.cmux_config import (
    MARKER,
    disable_native_pr_row,
    parse_jsonc,
    restore_native_pr_row,
)

JSONC = """{
  "$schema": "https://example.invalid/schema.json", // keep me
  /* block comment with "quotes" and // slashes */
  "app" : { "appearance" : "system" },
%s
}
"""


@pytest.fixture(autouse=True)
def _no_reload(monkeypatch):
    reloads: list[int] = []
    monkeypatch.setattr(cmux_config, "_reload", lambda: reloads.append(1))
    return reloads


def _show(path: Path) -> object:
    data = parse_jsonc(path.read_text())
    assert data is not None
    return data.get("sidebar", {}).get("showPullRequests", True)


def test_parse_jsonc_keeps_comment_markers_inside_strings():
    data = parse_jsonc('{"url": "https://x//y", /* c */ "a": [1,],}')
    assert data == {"url": "https://x//y", "a": [1]}


@pytest.mark.parametrize(
    "sidebar",
    [
        "",
        '  "sidebar" : {\n    "showNotificationMessage" : false\n  }',
        '  "sidebar" : {\n    "showPullRequests" : true\n  }',
        '  "sidebar" : {\n  }',
    ],
    ids=["no-sidebar", "sidebar-without-key", "key-true", "empty-sidebar"],
)
@pytest.mark.covers("pills.native-row-off~1")
def test_disable_then_restore_round_trips(tmp_path, sidebar, _no_reload):
    path = tmp_path / "cmux.json"
    original = JSONC % sidebar
    path.write_text(original)

    assert disable_native_pr_row(path) is True
    assert _show(path) is False
    assert "// keep me" in path.read_text()
    assert path.read_text().count(MARKER) == 1

    assert restore_native_pr_row(path) is True
    assert path.read_text() == original
    assert _no_reload == [1, 1]


def test_disable_is_idempotent(tmp_path):
    path = tmp_path / "cmux.json"
    path.write_text(JSONC % "")
    disable_native_pr_row(path)
    once = path.read_text()
    assert disable_native_pr_row(path) is False
    assert path.read_text() == once


@pytest.mark.covers("pills.native-row-off~1")
def test_a_user_set_false_is_never_claimed(tmp_path):
    path = tmp_path / "cmux.json"
    original = JSONC % '  "sidebar" : { "showPullRequests" : false }'
    path.write_text(original)
    assert disable_native_pr_row(path) is False
    assert restore_native_pr_row(path) is False
    assert path.read_text() == original


def test_missing_file_is_created(tmp_path):
    path = tmp_path / "cmux" / "cmux.json"
    assert disable_native_pr_row(path) is True
    assert _show(path) is False


def test_unparseable_file_is_left_alone(tmp_path, capsys):
    path = tmp_path / "cmux.json"
    path.write_text("{ not json")
    assert disable_native_pr_row(path) is False
    assert path.read_text() == "{ not json"
    assert "by hand" in capsys.readouterr().out


def test_unrecognised_layout_is_left_alone(tmp_path, capsys):
    path = tmp_path / "cmux.json"
    original = '{"sidebar": {"showPullRequests": true, "x": 1}}\n'
    path.write_text(original)
    assert disable_native_pr_row(path) is False
    assert path.read_text() == original
    assert "by hand" in capsys.readouterr().out


def test_restore_without_marker_is_a_noop(tmp_path):
    path = tmp_path / "cmux.json"
    path.write_text(JSONC % "")
    assert restore_native_pr_row(path) is False
    assert restore_native_pr_row(tmp_path / "absent.json") is False
