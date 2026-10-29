from pathlib import Path

import pytest

from cockpit import cli, ports_cmd
from cockpit.lib import ports
from cockpit.lib.preflight import validate_config

REPO = {"name": "beta", "path": "/nonexistent/beta", "ports": [3306, 6379]}


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(ports_cmd, "load_config", lambda: {"repos": [REPO]})
    monkeypatch.setattr(ports_cmd, "discover_repo", lambda: REPO)
    monkeypatch.setattr(ports.shutil, "which", lambda _name: None)

    def set_listeners(found):
        monkeypatch.setattr(ports_cmd.ports, "all_listeners", lambda: found)

    return set_listeners


@pytest.mark.covers("ports.exit~1")
def test_all_free_exits_zero(wired, capsys):
    wired([])
    assert cli.main(["ports"]) == 0
    assert ":3306  free" in capsys.readouterr().out


@pytest.mark.covers("ports.exit~1", "ports.holder~1")
def test_a_held_port_names_its_holder_and_exits_one(wired, capsys):
    wired([ports.Listener(42, "ssh", "3306", Path("/"))])
    assert cli.main(["ports"]) == 1
    out = capsys.readouterr().out
    assert ":3306  held by ssh (pid 42)" in out
    assert ":6379  free" in out


@pytest.mark.covers("ports.exit~1")
def test_lsof_unavailable_exits_two_and_claims_nothing_free(wired, capsys):
    wired(None)
    assert cli.main(["ports"]) == 2
    assert "free" not in capsys.readouterr().out


def test_no_declared_ports_exits_two(monkeypatch):
    bare = {"name": "x", "path": "/nonexistent/x"}
    monkeypatch.setattr(ports_cmd, "load_config", lambda: {"repos": [bare]})
    monkeypatch.setattr(ports_cmd, "discover_repo", lambda: bare)
    assert cli.main(["ports"]) == 2
    assert cli.main(["ports", "--all"]) == 2


@pytest.mark.covers("ports.config~1")
@pytest.mark.parametrize("bad", ["3306", [0], [70000], [True], ["3306"]])
def test_bad_ports_fail_validation(bad):
    with pytest.raises(SystemExit):
        validate_config({"repos": [{"name": "r", "path": "/p", "ports": bad}]})


def test_good_ports_pass_validation():
    validate_config({"repos": [{"name": "r", "path": "/p", "ports": [3306]}]})


def test_a_repo_path_that_is_not_git_is_skipped(tmp_path):
    assert ports_cmd._worktree_labels([{"name": "x", "path": str(tmp_path)}]) == {}
