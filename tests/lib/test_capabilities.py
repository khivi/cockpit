"""Tests for cockpit/lib/capabilities.py — the cmux verb + capability probe.

The `cmux()` wrapper is mocked rather than driven against a real cmux: these
cases are precisely the ones a healthy local cmux can't produce (a build too
old for `cmux capabilities`, a missing `send-key`).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cockpit.lib.capabilities import (
    REQUIRED_CAPABILITIES,
    REQUIRED_VERBS,
    BackendProbe,
    has_capability,
    parse_capabilities,
    parse_verbs,
    probe,
)

HELP = """cmux - control cmux via Unix socket

Usage:
  cmux [global-options] <command> [options]

Commands:
  capabilities
  list-workspaces [--window <id|ref|index>]
  new-workspace [--name <title>] [--cwd <path>]

  send [--workspace <id|ref|index>] <text>
  send-key [--workspace <id|ref|index>] <key>
  list-status [--workspace <id|ref|index>]
  next-window | previous-window | last-window [--window <id|ref|index>]
  markdown [open] <path> (open markdown file in formatted viewer panel)
  help

Environment:
  CMUX_WORKSPACE_ID   Auto-set in cmux terminals. Used as default --workspace.
"""


def CAPS(ids) -> str:
    """A `cmux capabilities` payload offering exactly `ids`."""
    return json.dumps(
        {"access_mode": "cmuxOnly", "capabilities": list(ids), "methods": ["x"]}
    )


ALL_CAPS = CAPS(REQUIRED_CAPABILITIES)


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """`probe` is process-cached — clear it around every case so one test's
    fake cmux can't leak into the next."""
    probe.cache_clear()
    yield
    probe.cache_clear()


def _fake_cmux(help_text: str = HELP, caps: str = ALL_CAPS, browser: str = "disabled"):
    def _run(*args: str, check: bool = True) -> str:
        return {
            "--help": help_text,
            "capabilities": caps,
            "browser-status": browser,
        }.get(args[0], "")

    return _run


# `browser-status` must be in the Commands: section for `probe` to run it —
# the verb is only invoked when advertised.
HELP_WITH_BROWSER = HELP.replace(
    "  capabilities\n", "  capabilities\n  browser-status\n"
)
HELP_WITHOUT_DIFF = HELP_WITH_BROWSER
HELP_WITH_DIFF = HELP_WITH_BROWSER.replace(
    "  capabilities\n",
    "  capabilities\n  diff [patch-file|-] [--layout <split|unified>]\n",
)


def test_parse_verbs_reads_the_commands_section():
    verbs = parse_verbs(HELP)
    assert set(REQUIRED_VERBS) <= verbs
    assert "capabilities" in verbs


def test_parse_verbs_splits_alternations_and_stops_at_the_next_section():
    verbs = parse_verbs(HELP)
    assert {"next-window", "previous-window", "last-window"} <= verbs
    # Prose in parens and flag/arg placeholders are not verbs, and the trailing
    # `Environment:` block's indented lines must not leak in.
    assert "markdown" in verbs
    assert not {"open", "<path>", "[--workspace", "CMUX_WORKSPACE_ID"} & verbs


def test_parse_capabilities_tolerates_junk():
    assert parse_capabilities(ALL_CAPS) == frozenset(REQUIRED_CAPABILITIES)
    assert parse_capabilities("") == frozenset()
    assert parse_capabilities("not json") == frozenset()
    assert parse_capabilities("[1, 2]") == frozenset()
    assert parse_capabilities('{"capabilities": null}') == frozenset()
    assert parse_capabilities('{"capabilities": ["a", 7]}') == frozenset({"a"})


def test_probe_is_empty_when_the_backend_is_not_cmux():
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=False),
        patch("cockpit.lib.cmux.cmux") as fake,
    ):
        found = probe()
    assert found == BackendProbe(frozenset(), frozenset())
    fake.assert_not_called()


def test_probe_reports_a_healthy_cmux_as_complete():
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch("cockpit.lib.cmux.cmux", side_effect=_fake_cmux()),
    ):
        found = probe()
    assert found.supports_capabilities
    assert found.missing_verbs() == ()
    assert found.missing_capabilities() == ()


def test_probe_reports_a_missing_verb():
    lean = HELP.replace("  send-key [--workspace <id|ref|index>] <key>\n", "")
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch("cockpit.lib.cmux.cmux", side_effect=_fake_cmux(help_text=lean)),
    ):
        found = probe()
    assert found.missing_verbs() == ("send-key",)


def test_probe_treats_a_missing_capabilities_verb_as_too_old():
    # Not "this cmux has no capabilities" — it can't be asked at all, so the
    # verb is never run.
    old = HELP.replace("  capabilities\n", "")
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch("cockpit.lib.cmux.cmux", side_effect=_fake_cmux(help_text=old)) as fake,
    ):
        found = probe()
    assert not found.supports_capabilities
    assert found.capabilities == frozenset()
    assert [call.args[0] for call in fake.call_args_list] == ["--help"]


def test_probe_reports_missing_capabilities():
    partial = CAPS(["workspace.read_state.v1"])
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch("cockpit.lib.cmux.cmux", side_effect=_fake_cmux(caps=partial)),
    ):
        found = probe()
    assert found.supports_capabilities
    assert "workspace.read_state.v1" not in found.missing_capabilities()
    assert "events.v1" in found.missing_capabilities()


def test_probe_shells_out_once_per_process():
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch("cockpit.lib.cmux.cmux", side_effect=_fake_cmux()) as fake,
    ):
        probe()
        probe()
    # `browser-status` is not advertised by the bare HELP fixture, so `probe`
    # skips it: two calls. The three-call case is pinned separately below, so
    # this assertion can't quietly stop counting a probe that grew a third.
    assert fake.call_count == 2  # one --help + one capabilities, not four


def test_has_capability_gates_on_the_negotiated_list():
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch(
            "cockpit.lib.cmux.cmux", side_effect=_fake_cmux(caps=CAPS(["events.v1"]))
        ),
    ):
        assert has_capability("events.v1")
        assert not has_capability("workspace.groups.v1")


def test_required_capabilities_only_name_tiers_cockpit_actually_has():
    """Both were required for features cockpit never built — see capabilities.py."""
    assert "terminal.replay.v1" not in REQUIRED_CAPABILITIES
    assert "notification.feed.v1" not in REQUIRED_CAPABILITIES
    assert "workspace.groups.v1" in REQUIRED_CAPABILITIES
    assert "workspace-group" not in REQUIRED_VERBS


# ── browser-status → the `d` diff viewer gate ────────────────────────────────


def test_probe_reads_browser_status_when_the_verb_exists():
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch(
            "cockpit.lib.cmux.cmux",
            side_effect=_fake_cmux(HELP_WITH_BROWSER, browser="enabled"),
        ) as fake,
    ):
        found = probe()
        probe()
    assert found.browser_enabled is True
    # Three subprocesses ONCE, not per call — the lru_cache still holds.
    assert fake.call_count == 3


def test_probe_skips_browser_status_when_the_verb_is_absent():
    """An older cmux without the verb must not be shelled out to for it, and
    reads as browser-off — erring toward hiding an optional key."""
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch("cockpit.lib.cmux.cmux", side_effect=_fake_cmux()) as fake,
    ):
        found = probe()
    assert found.browser_enabled is False
    assert "browser-status" not in [c.args[0] for c in fake.call_args_list]


def test_probe_treats_anything_but_enabled_as_off():
    """Regression guard: a cmux printing `Browser: enabled` (or anything but the
    bare word) must not be read as on — the `d` key would then be advertised and
    fail at press time."""
    for reply in ("disabled", "Browser: enabled", "", "ENABLED\n"):
        probe.cache_clear()
        with (
            patch("cockpit.lib.tool.is_cmux", return_value=True),
            patch(
                "cockpit.lib.cmux.cmux",
                side_effect=_fake_cmux(HELP_WITH_BROWSER, browser=reply),
            ),
        ):
            found = probe()
        expected = reply.strip().lower() == "enabled"
        assert found.browser_enabled is expected, reply


def test_has_diff_viewer_needs_both_the_verb_and_a_live_browser():
    from cockpit.lib.capabilities import BackendProbe

    both = BackendProbe(frozenset({"diff"}), frozenset(), browser_enabled=True)
    assert both.has_diff_viewer is True
    assert BackendProbe(frozenset({"diff"}), frozenset()).has_diff_viewer is False
    assert (
        BackendProbe(frozenset(), frozenset(), browser_enabled=True).has_diff_viewer
        is False
    )


def test_diff_viewer_available_reflects_the_probe():
    from cockpit.lib.capabilities import diff_viewer_available

    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch(
            "cockpit.lib.cmux.cmux",
            side_effect=_fake_cmux(HELP_WITH_DIFF, browser="enabled"),
        ),
    ):
        assert diff_viewer_available() is True
    probe.cache_clear()
    with (
        patch("cockpit.lib.tool.is_cmux", return_value=True),
        patch(
            "cockpit.lib.cmux.cmux",
            side_effect=_fake_cmux(HELP_WITH_DIFF, browser="disabled"),
        ),
    ):
        assert diff_viewer_available() is False
