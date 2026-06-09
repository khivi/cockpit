"""Tests for cockpit/lib/slack.py — Slack-permalink classification + seeding.

Pure functions only (the module deliberately has no API/subprocess surface —
the spawned Claude reads the thread via the Slack MCP, and there is no
`claude mcp list` probe). So these assert URL recognition and the stable-seed
normalization that keeps the codename branch idempotent.
"""

from __future__ import annotations

from cockpit.lib.slack import is_slack_url, slack_seed

_ARCHIVES = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
_CLIENT = "https://app.slack.com/client/T01234567/C0123ABC"


# ── is_slack_url ───────────────────────────────────────────────────────────


def test_archives_permalink_recognized():
    assert is_slack_url(_ARCHIVES)


def test_archives_permalink_with_query_recognized():
    assert is_slack_url(_ARCHIVES + "?thread_ts=1700000000.123456&cid=C0123ABC")


def test_client_deep_link_recognized():
    assert is_slack_url(_CLIENT)


def test_client_thread_deep_link_recognized():
    assert is_slack_url(_CLIENT + "/thread/C0123ABC-1700000000.123456")


def test_http_scheme_also_recognized():
    assert is_slack_url(_ARCHIVES.replace("https://", "http://"))


def test_plain_branch_name_not_a_url():
    assert not is_slack_url("khivi/slack-feature")


def test_non_slack_https_url_not_recognized():
    assert not is_slack_url("https://github.com/owner/repo/pull/42")


def test_slack_homepage_without_message_not_recognized():
    # The marketing/homepage URL carries no thread identity — not a source.
    assert not is_slack_url("https://acme.slack.com/")


# ── slack_seed (stable identity) ───────────────────────────────────────────


def test_archives_seed_is_channel_and_ts():
    assert slack_seed(_ARCHIVES) == "c0123abc/1700000000123456"


def test_archives_seed_ignores_query_params():
    a = slack_seed(_ARCHIVES)
    b = slack_seed(_ARCHIVES + "?thread_ts=1700000000.123456&cid=C0123ABC")
    assert a == b


def test_archives_seed_is_case_insensitive_on_channel():
    upper = "https://acme.slack.com/archives/C0123ABC/p1700000000123456"
    lower = "https://acme.slack.com/archives/c0123abc/p1700000000123456"
    assert slack_seed(upper) == slack_seed(lower)


def test_client_seed_is_team_and_channel():
    assert slack_seed(_CLIENT) == "t01234567/c0123abc"


def test_seed_falls_back_to_stripped_url_when_unparsed():
    # Defensive: a shape detect_source never routes here still yields a stable,
    # query-stripped seed rather than raising.
    weird = "https://acme.slack.com/something/else?x=1#frag"
    assert slack_seed(weird) == "https://acme.slack.com/something/else"
