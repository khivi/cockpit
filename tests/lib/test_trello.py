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
    CONFIG_FIELDS,
    TRELLO_CARD_URL_RE,
    card_short_link,
    fetch_card_board,
    fetch_card_handles,
    fetch_card_lists,
    fetch_card_meta,
    fetch_my_open,
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


def test_fetch_card_handles_prefers_the_card_number():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"name": "Fix the login flow", "idShort": 122})

    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_card_handles(["aB3dZ9"], key=KEY, token=TOKEN)
    # The number is the handle a human reads off the card; the name is only the
    # fallback. Both fields ride one GET.
    assert out == {"aB3dZ9": "#122"}
    assert "fields=name%2CidShort" in captured["url"]


def test_fetch_card_handles_falls_back_to_the_card_name():
    def fake_urlopen(req, timeout=None):
        return _FakeResp({"name": "Fix the login flow"})

    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=fake_urlopen):
        out = fetch_card_handles(["aB3dZ9"], key=KEY, token=TOKEN)
    assert out == {"aB3dZ9": "Fix the login flow"}


def test_fetch_card_handles_no_creds_skips_network():
    with (
        patch("cockpit.lib.trello.urllib.request.urlopen") as urlopen,
        patch.dict("os.environ", {}, clear=True),
    ):
        out = fetch_card_handles(["aB3dZ9"])
    assert out == {"aB3dZ9": None}
    urlopen.assert_not_called()


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


def test_config_fields_declare_board_for_routing():
    # `tickets.board` is the whole of Trello's ticket→repo route and the ticket
    # inbox's scope; the provider owns its config schema, so preflight only
    # accepts the field if it's here. A list is one repo spanning several boards.
    assert ("board", "str_or_str_list") in CONFIG_FIELDS


def test_fetch_card_board_returns_the_board_name():
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"board": {"name": "Engineering"}})

    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=fake_urlopen):
        assert fetch_card_board("aB3dZ9", key=KEY, token=TOKEN) == "Engineering"
    # Only the board's *name* is requested — never the whole card payload.
    assert "board=true" in captured["url"] and "board_fields=name" in captured["url"]
    assert captured["url"].startswith("https://api.trello.com/1/cards/aB3dZ9?")


def test_fetch_card_board_no_creds_skips_network():
    with (
        patch("cockpit.lib.trello.urllib.request.urlopen") as urlopen,
        patch.dict("os.environ", {}, clear=True),
    ):
        assert fetch_card_board("aB3dZ9") is None
    urlopen.assert_not_called()


def test_fetch_card_board_blank_short_link_skips_network():
    with patch("cockpit.lib.trello.urllib.request.urlopen") as urlopen:
        assert fetch_card_board("", key=KEY, token=TOKEN) is None
    urlopen.assert_not_called()


def test_fetch_card_board_degrades_to_none_on_every_failure():
    # A 404 (unknown card), a timeout, an unparsable body, and a board-less
    # payload all collapse to None — the caller reads that as "inconclusive".
    err = urllib.error.HTTPError("u", 404, "not found", {}, BytesIO(b""))  # type: ignore[arg-type]
    for side, ret in (
        (err, None),
        (TimeoutError(), None),
        (None, _FakeResp(raw=b"not json {")),
        (None, _FakeResp({"board": {}})),
        (None, _FakeResp({})),
    ):
        kwargs = {"side_effect": side} if side is not None else {"return_value": ret}
        with patch("cockpit.lib.trello.urllib.request.urlopen", **kwargs):
            assert fetch_card_board("aB3dZ9", key=KEY, token=TOKEN) is None


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


# ────────────────────────────────────────────────────────────────────────────
# fetch_my_open — the ticket inbox's one call per Trello account
# ────────────────────────────────────────────────────────────────────────────


def _field(tickets: list[dict] | None, key: str = "id") -> list[str]:
    """`key` off each ticket, asserting the fetch was answered — `None` is the
    "couldn't ask" case and never what these cases exercise."""
    assert tickets is not None
    return [t[key] for t in tickets]


# The card endpoint returns ids, never names: `/members/me/cards` accepts
# `board=true` / `list=true` and silently ignores both, which is why a second
# call resolves them and why every case here routes on the URL.
_BOARD_IDS = {"Engineering": "bE", "Marketing": "bM", "Engineering (2024)": "bOld"}
_BOARDS: list[dict] = [
    {
        "id": "bE",
        "name": "Engineering",
        "closed": False,
        "lists": [{"id": "lDoing", "name": "Doing"}, {"id": "lDone", "name": "Done"}],
    },
    {
        "id": "bM",
        "name": "Marketing",
        "closed": False,
        "lists": [{"id": "lNext", "name": "Next"}],
    },
    {
        "id": "bOld",
        "name": "Engineering (2024)",
        "closed": True,
        "lists": [{"id": "lOld", "name": "Doing"}],
    },
]


def _card(short: str, board: str = "Engineering", list_id="lDoing", **over) -> dict:
    card = {
        "shortLink": short,
        "name": f"Card {short}",
        "idShort": 122,
        "shortUrl": f"https://trello.com/c/{short}",
        "dateLastActivity": "2026-09-09T10:00:00.000Z",
        "idList": list_id,
        "idBoard": _BOARD_IDS[board],
    }
    card.update(over)
    return card


def _routed(cards: object, boards: object = None, *, urls: list | None = None):
    """A urlopen side-effect that answers the cards call and the boards call."""
    payload = _BOARDS if boards is None else boards

    def fake_urlopen(req, timeout=None):
        if urls is not None:
            urls.append(req.full_url)
        if "/members/me/boards" in req.full_url:
            return _FakeResp(payload)
        return _FakeResp(cards)

    return fake_urlopen


def test_fetch_my_open_normalizes_every_field():
    with patch(
        "cockpit.lib.trello.urllib.request.urlopen",
        side_effect=_routed([_card("aB3dZ9")]),
    ):
        out = fetch_my_open(["Engineering"], key=KEY, token=TOKEN)

    assert out == [
        {
            "id": "aB3dZ9",
            "team": "Engineering",
            "title": "Card aB3dZ9",
            "state": "Doing",
            "url": "https://trello.com/c/aB3dZ9",
            "updated_at": "2026-09-09T10:00:00.000Z",
            "handle": "#122",
        }
    ]


def test_fetch_my_open_resolves_the_board_and_list_names():
    """The bug this pair of calls exists for: with the ids unresolved, every
    card came back with a blank board and a blank state, so the inbox could
    neither group them nor tell a done card from a fresh one."""
    cards = [_card("a"), _card("b", board="Marketing", list_id="lNext")]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed(cards)):
        out = fetch_my_open(None, key=KEY, token=TOKEN)
    assert [(t["team"], t["state"]) for t in out or []] == [
        ("Engineering", "Doing"),
        ("Marketing", "Next"),
    ]


def test_fetch_my_open_carries_the_card_number_as_the_handle():
    """`#122` is the only human-readable handle a card has — its id is an opaque
    short link, which stays the key everything else joins on."""
    with patch(
        "cockpit.lib.trello.urllib.request.urlopen",
        side_effect=_routed([_card("aB3dZ9", idShort=7)]),
    ):
        out = fetch_my_open(None, key=KEY, token=TOKEN)
    assert _field(out, "handle") == ["#7"]
    assert _field(out) == ["aB3dZ9"]


def test_fetch_my_open_handle_is_blank_when_the_number_is_missing():
    with patch(
        "cockpit.lib.trello.urllib.request.urlopen",
        side_effect=_routed([_card("a", idShort=None)]),
    ):
        assert _field(fetch_my_open(None, key=KEY, token=TOKEN), "handle") == [""]


def test_fetch_my_open_asks_only_for_open_cards_and_every_board():
    urls: list[str] = []
    with patch(
        "cockpit.lib.trello.urllib.request.urlopen",
        side_effect=_routed([_card("a"), _card("b")], urls=urls),
    ):
        out = fetch_my_open(None, key=KEY, token=TOKEN)

    assert len(urls) == 2
    cards_url = next(u for u in urls if "/members/me/cards" in u)
    boards_url = next(u for u in urls if "/members/me/boards" in u)
    assert "filter=open" in cards_url
    # A card I'm a member of can sit on a closed board or in an archived list;
    # an unresolved name is indistinguishable from having no state at all.
    assert "filter=all" in boards_url
    assert "lists=all" in boards_url
    assert _field(out) == ["a", "b"]


def test_fetch_my_open_filters_boards_client_side():
    """Trello has no board-scoped variant of this endpoint, so the filter can't
    ride the request."""
    cards = [_card("a", board="Engineering"), _card("b", board="Marketing")]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed(cards)):
        out = fetch_my_open(["engineering"], key=KEY, token=TOKEN)  # casefolded
    assert _field(out) == ["a"]


def test_fetch_my_open_without_boards_keeps_every_card():
    cards = [_card("a", board="Engineering"), _card("b", board="Marketing")]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed(cards)):
        assert _field(fetch_my_open([], key=KEY, token=TOKEN)) == ["a", "b"]


def test_fetch_my_open_sorts_newest_first():
    cards = [
        _card("old", dateLastActivity="2026-09-01T00:00:00.000Z"),
        _card("new", dateLastActivity="2026-09-09T00:00:00.000Z"),
    ]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed(cards)):
        out = fetch_my_open(None, key=KEY, token=TOKEN)
    assert _field(out) == ["new", "old"]


def test_fetch_my_open_skips_a_card_with_no_short_link():
    cards = [_card("a"), {"name": "orphan"}, "not-a-dict"]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed(cards)):
        assert _field(fetch_my_open(None, key=KEY, token=TOKEN)) == ["a"]


def test_fetch_my_open_unknown_board_or_list_leaves_the_name_blank():
    """A card on a board the boards call didn't return — never a dropped row."""
    cards = [{**_card("a"), "idBoard": "gone", "idList": "gone"}]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed(cards)):
        out = fetch_my_open(None, key=KEY, token=TOKEN)
    assert [(t["team"], t["state"]) for t in out or []] == [("", "")]


def test_fetch_my_open_drops_cards_on_an_archived_board():
    """Archiving a board leaves every card on it open, so one retired board
    arrives as dozens of live-looking cards — whatever list they sit in."""
    cards = [_card("a"), _card("old", board="Engineering (2024)", list_id="lOld")]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed(cards)):
        assert _field(fetch_my_open(None, key=KEY, token=TOKEN)) == ["a"]


def test_fetch_my_open_keeps_a_card_whose_board_is_unknown():
    """Unknown is not archived — a board the lookup didn't return keeps its
    card, with a blank name, exactly as before."""
    cards = [{**_card("a"), "idBoard": "gone"}]
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed(cards)):
        assert _field(fetch_my_open(None, key=KEY, token=TOKEN)) == ["a"]


def test_fetch_my_open_failure_is_none_not_empty():
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=TimeoutError()):
        assert fetch_my_open(None, key=KEY, token=TOKEN) is None


def test_fetch_my_open_is_none_when_only_the_names_fail():
    """Half an answer is not an answer: unresolved names would read as a set of
    boardless, stateless cards rather than as a failed fetch."""
    with patch(
        "cockpit.lib.trello.urllib.request.urlopen",
        side_effect=_routed([_card("a")], boards="not-a-list"),
    ):
        assert fetch_my_open(None, key=KEY, token=TOKEN) is None


def test_fetch_my_open_answered_with_nothing_is_empty_not_none():
    with patch("cockpit.lib.trello.urllib.request.urlopen", side_effect=_routed([])):
        assert fetch_my_open(None, key=KEY, token=TOKEN) == []


def test_fetch_my_open_unset_creds_is_empty_and_skips_network():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("cockpit.lib.trello.urllib.request.urlopen") as urlopen,
    ):
        assert fetch_my_open(None) == []
    urlopen.assert_not_called()
