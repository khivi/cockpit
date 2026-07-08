"""Tests for cockpit/lib/trello.py — card-URL / footer parsing and the REST
surfaces (`fetch_card_lists`, `fetch_myself`, `fetch_card_meta`, `move_card`).

The Trello card *body* is fetched by Claude via the Trello MCP from the spawned
workspace; see `test_spawn.py` for the spawn-side dispatch. The daemon's direct
Trello calls are exercised below with a mocked `urlopen` (the Jira leaf test's
pattern) — degrade-never-raise on every failure path, and key+token query-auth.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

from cockpit.lib.trello import (
    TRELLO_CARD_URL_RE,
    card_short_link,
    fetch_card_lists,
    fetch_card_meta,
    fetch_myself,
    move_card,
    parse_trello_footer_links,
    parse_trello_footers,
    trello_seed,
)

# ────────────────────────────────────────────────────────────────────────────
# URL / footer parsing — the strict delivery signal
# ────────────────────────────────────────────────────────────────────────────


def test_card_short_link_extracts_and_tolerates_tail():
    assert card_short_link("https://trello.com/c/aB3dZ9") == "aB3dZ9"
    assert card_short_link("https://trello.com/c/aB3dZ9/42-fix-oauth") == "aB3dZ9"
    assert card_short_link("https://trello.com/c/aB3dZ9?x=1#f") == "aB3dZ9"
    assert card_short_link("https://example.com/nope") is None


def test_short_link_is_case_sensitive():
    # A Trello short link is case-sensitive — never upper/lower-cased.
    assert card_short_link("https://trello.com/c/AbCdEf") == "AbCdEf"


def test_trello_seed_stable_across_tail_query_fragment():
    # The seed is the (lowercased) short link — invariant to slug/query/fragment,
    # so re-spawning the same card is idempotent.
    base = "https://trello.com/c/aB3dZ9"
    seed = trello_seed(base)
    assert seed == "ab3dz9"
    assert trello_seed(base + "/7-some-slug") == seed
    assert trello_seed(base + "?filter=x") == seed
    assert trello_seed(base + "#comment") == seed


def test_url_re_matches_client_and_archives_shapes():
    assert TRELLO_CARD_URL_RE.match("https://trello.com/c/aB3dZ9")
    assert TRELLO_CARD_URL_RE.match("http://trello.com/c/aB3dZ9/1-x")
    assert not TRELLO_CARD_URL_RE.match(
        "https://trello.com/b/aB3dZ9"
    )  # board, not card


def test_parse_footers_dedups_keeps_case():
    body = (
        "Some description.\n"
        "Trello: [Fix OAuth](https://trello.com/c/aB3dZ9)\n"
        "Trello: [Other](https://trello.com/c/Zz00Yy)\n"
        "Trello: [dupe](https://trello.com/c/aB3dZ9/2-again)\n"
    )
    assert parse_trello_footers(body) == ["aB3dZ9", "Zz00Yy"]


def test_parse_footers_requires_line_anchored_footer():
    # A bare mention / non-anchored line is NOT a delivery footer.
    assert parse_trello_footers("see https://trello.com/c/aB3dZ9 for context") == []
    assert parse_trello_footers("x Trello: [t](https://trello.com/c/aB3dZ9)") == []


def test_parse_footers_empty():
    assert parse_trello_footers("") == []
    assert parse_trello_footers("no footer here") == []


def test_parse_footer_links_keeps_url():
    body = "Trello: [t](https://trello.com/c/aB3dZ9/5-fix)"
    assert parse_trello_footer_links(body) == [
        ("aB3dZ9", "https://trello.com/c/aB3dZ9/5-fix")
    ]


def test_parse_footer_links_empty_without_link():
    assert parse_trello_footer_links("Trello: aB3dZ9 no link") == []
    assert parse_trello_footer_links("") == []


# ────────────────────────────────────────────────────────────────────────────
# REST surfaces — mocked urlopen
# ────────────────────────────────────────────────────────────────────────────

KEY = "key_xxx"
TOKEN = "tok_xxx"


class _FakeResp:
    """Minimal context-manager stand-in for urlopen's return."""

    def __init__(self, data: object = None, *, raw: bytes | None = None):
        self._body = raw if raw is not None else json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_fetch_card_lists_happy_path_and_query_auth():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"list": {"name": "Doing"}})

    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_card_lists(["aB3dZ9"], key=KEY, token=TOKEN)
    assert out == {"aB3dZ9": "Doing"}
    # key + token ride as query params (Trello's scheme), not a header.
    assert "key=key_xxx" in captured["url"] and "token=tok_xxx" in captured["url"]
    assert captured["url"].startswith("https://api.trello.com/1/cards/aB3dZ9?")


def test_fetch_card_lists_no_creds_skips_network():
    with (
        patch("cockpit.lib.trello.urllib.request.urlopen") as urlopen,
        patch.dict("os.environ", {}, clear=True),
    ):
        out = fetch_card_lists(["aB3dZ9"])
    assert out == {"aB3dZ9": None}
    urlopen.assert_not_called()


def test_fetch_card_lists_failure_isolated_per_card():
    def fake_urlopen(req, timeout=None):
        if "BADCARD" in req.full_url:
            raise TimeoutError()
        return _FakeResp({"list": {"name": "Done"}})

    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_card_lists(["okCard", "BADCARD"], key=KEY, token=TOKEN)
    assert out == {"okCard": "Done", "BADCARD": None}


def test_fetch_card_lists_malformed_json_is_none():
    # A 200 with an unparsable body must degrade like any other failure, not
    # raise json.JSONDecodeError out of `_request`.
    with patch(
        "cockpit.lib.trello.urllib.request.urlopen",
        return_value=_FakeResp(raw=b"not json {"),
    ):
        out = fetch_card_lists(["aB3dZ9"], key=KEY, token=TOKEN)
    assert out == {"aB3dZ9": None}


def test_fetch_card_lists_http_error_is_none():
    err = urllib.error.HTTPError("u", 401, "unauthorized", {}, BytesIO(b""))  # type: ignore[arg-type]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=err):
        out = fetch_card_lists(["aB3dZ9"], key=KEY, token=TOKEN)
    assert out == {"aB3dZ9": None}


def test_fetch_myself_returns_member_id():
    with patch(
        "cockpit.lib.trello.urllib.request.urlopen",
        return_value=_FakeResp({"id": "mem-123"}),
    ):
        assert fetch_myself(key=KEY, token=TOKEN) == "mem-123"


def test_fetch_myself_no_creds_is_none():
    with (
        patch("cockpit.lib.trello.urllib.request.urlopen") as urlopen,
        patch.dict("os.environ", {}, clear=True),
    ):
        assert fetch_myself() is None
    urlopen.assert_not_called()


def test_fetch_card_meta_list_board_members():
    payload = {
        "idBoard": "b1",
        "idMembers": ["m1", "m2"],
        "list": {"name": "Doing"},
    }
    with patch(
        "cockpit.lib.trello.urllib.request.urlopen", return_value=_FakeResp(payload)
    ):
        meta = fetch_card_meta("aB3dZ9", key=KEY, token=TOKEN)
    assert meta == {"list": "Doing", "board": "b1", "members": ["m1", "m2"]}


def test_fetch_card_meta_missing_card_is_none():
    with patch("cockpit.lib.trello.urllib.request.urlopen", return_value=_FakeResp({})):
        assert fetch_card_meta("aB3dZ9", key=KEY, token=TOKEN) is None


def test_move_card_resolves_list_and_puts_idlist():
    calls: list[tuple[str, str]] = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.method, req.full_url))
        if req.method == "GET" and "/boards/" in req.full_url:
            return _FakeResp(
                [{"id": "l1", "name": "Doing"}, {"id": "l2", "name": "Done"}]
            )
        if req.method == "GET":
            return _FakeResp({"idBoard": "b1"})
        # PUT — the move
        assert "idList=l2" in req.full_url
        return _FakeResp({"id": "aB3dZ9"})

    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=fake_urlopen):
        ok = move_card("aB3dZ9", "done", key=KEY, token=TOKEN)  # case-insensitive
    assert ok is True
    assert [m for m, _ in calls] == ["GET", "GET", "PUT"]


def test_move_card_no_matching_list_is_false():
    def fake_urlopen(req, timeout=None):
        if "/boards/" in req.full_url:
            return _FakeResp([{"id": "l1", "name": "Doing"}])
        return _FakeResp({"idBoard": "b1"})

    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=fake_urlopen):
        assert move_card("aB3dZ9", "Done", key=KEY, token=TOKEN) is False


def test_move_card_get_failure_is_false():
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=TimeoutError()):
        assert move_card("aB3dZ9", "Done", key=KEY, token=TOKEN) is False


def test_move_card_no_creds_is_false():
    with (
        patch("cockpit.lib.trello.urllib.request.urlopen") as urlopen,
        patch.dict("os.environ", {}, clear=True),
    ):
        assert move_card("aB3dZ9", "Done") is False
    urlopen.assert_not_called()
