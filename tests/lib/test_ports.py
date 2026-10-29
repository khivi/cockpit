import subprocess
import sys
import time
from pathlib import Path

import pytest

from cockpit.lib import ports

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="lsof flags are macOS")


@pytest.fixture
def server(tmp_path: Path):
    wt = tmp_path / "wt" / "web"
    wt.mkdir(parents=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", "0", "--bind", "127.0.0.1"],
        cwd=wt,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield proc, tmp_path
    finally:
        proc.kill()
        proc.wait()


def _wait_for(root: Path, pid: int) -> list[ports.Listener]:
    for _ in range(50):
        found = [x for x in ports.listeners_under(root) or [] if x.pid == pid]
        if found:
            return found
        time.sleep(0.1)
    return []


@pytest.mark.covers("teardown.port-warning~1")
def test_a_server_started_below_the_worktree_is_attributed_to_it(server):
    proc, tmp = server
    found = _wait_for(tmp / "wt", proc.pid)
    assert found and found[0].port.isdigit()
    warning = ports.close_warning(tmp / "wt")
    assert warning and f"pid {proc.pid}" in warning


@pytest.mark.covers("teardown.port-warning~1")
def test_a_sibling_worktree_is_not_blamed(server, tmp_path):
    proc, tmp = server
    assert _wait_for(tmp / "wt", proc.pid)
    (tmp / "wt-2").mkdir()
    assert all(x.pid != proc.pid for x in ports.listeners_under(tmp / "wt-2") or [])


@pytest.mark.covers("teardown.port-warning~1")
def test_no_lsof_means_could_not_ask_and_no_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert ports.listeners_under(tmp_path) is None
    assert ports.close_warning(tmp_path) is None
