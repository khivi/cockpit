"""Tests for cockpit/lib/mcp.py — the one probe cockpit makes.

A leaf over `subprocess.run`, so these drive the real parse against real
`claude mcp list` output shapes rather than stubbing the parse. The property
that matters is the three-way answer: a health word, "the listing did not name
it", and "the probe could not be read" must stay apart, since collapsing the
last two is the paid-for bug a pre-flight gate shipped.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from cockpit.lib.mcp import list_mcp_servers

_REAL_OUTPUT = """Checking MCP server health…

claude.ai Slack: https://mcp.slack.com/mcp - ✔ Connected
claude.ai Monarch: https://api.monarch.com/mcp - ! Needs authentication
playwright: playwright-mcp --browser chromium --headless - ✔ Connected
linear-vectorwave: https://mcp.linear.app/mcp (HTTP) - ✔ Connected
"""


def _ran(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(["claude"], returncode, stdout, "")


@pytest.mark.covers("ticket-check.mcp-probe~1")
def test_parses_every_line_shape_claude_prints():
    with patch("cockpit.lib.mcp.subprocess.run", return_value=_ran(_REAL_OUTPUT)):
        got = list_mcp_servers()
    assert got == {
        "claude.ai Slack": "connected",
        "claude.ai Monarch": "needs authentication",
        "playwright": "connected",
        "linear-vectorwave": "connected",
    }


@pytest.mark.covers("ticket-check.mcp-probe~1")
def test_an_unhealthy_server_keeps_its_own_wording():
    """Passed through rather than matched against a literal set, so a wording
    change degrades to an odd status rather than to a missing server."""
    out = "thing: https://x/mcp - ✗ Something New Went Wrong\n"
    with patch("cockpit.lib.mcp.subprocess.run", return_value=_ran(out)):
        assert list_mcp_servers() == {"thing": "something new went wrong"}


@pytest.mark.covers("ticket-check.mcp-probe-never-gates~1")
def test_couldnt_run_is_none_and_never_an_empty_listing():
    """`{}` would read as "you have no MCP servers", which is how a probe
    silently disabled the feature it was meant to protect."""
    with patch("cockpit.lib.mcp.subprocess.run", side_effect=FileNotFoundError):
        assert list_mcp_servers() is None
    with patch(
        "cockpit.lib.mcp.subprocess.run",
        side_effect=subprocess.TimeoutExpired("claude", 20),
    ):
        assert list_mcp_servers() is None
    with patch("cockpit.lib.mcp.subprocess.run", return_value=_ran("x", returncode=1)):
        assert list_mcp_servers() is None
    with patch(
        "cockpit.lib.mcp.subprocess.run",
        return_value=_ran("No MCP servers configured. Run `claude mcp add`\n"),
    ):
        assert list_mcp_servers() is None


@pytest.mark.covers("ticket-check.mcp-probe~1")
def test_the_probe_runs_in_the_repos_cwd():
    """MCP scope is partly per project: a server in the repo's own .mcp.json
    is invisible from the daemon's own directory."""
    with patch("cockpit.lib.mcp.subprocess.run", return_value=_ran("")) as run:
        list_mcp_servers(repo_dir="/tmp/widgets")
    assert run.call_args.args[0] == ["claude", "mcp", "list"]
    assert run.call_args.kwargs["cwd"] == "/tmp/widgets"
