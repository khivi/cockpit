"""Listening TCP processes: who runs from a worktree, and who holds a declared port.

A dev server outlives the session that started it and is reparented to
launchd, so a process-tree walk loses it on exactly the case that matters. The
process's cwd survives, so ownership is cwd containment.

Read-only: it reports, never kills.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Listener:
    pid: int
    command: str
    port: str
    cwd: Path | None = None


def _lsof(args: list[str], *, partial_ok: bool = False) -> str | None:
    try:
        proc = subprocess.run(
            ["lsof", *args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # lsof exits 1 both for "nothing matched" and for errors (including a pid it
    # may not inspect); only empty output with exit 1 is a clean "nothing".
    if proc.returncode == 0 or (
        proc.returncode == 1 and (partial_ok or not proc.stdout.strip())
    ):
        return proc.stdout
    return None


def _fields(out: str) -> list[dict[str, list[str]]]:
    """`-F` output grouped per process: `{"p": [pid], "c": [cmd], "n": [...]}`."""
    procs: list[dict[str, list[str]]] = []
    for line in out.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            procs.append({})
        if procs:
            procs[-1].setdefault(tag, []).append(value)
    return procs


def all_listeners() -> list[Listener] | None:
    """Every listening socket, with its process cwd when lsof may read it.

    None means lsof could not be asked; [] means nothing is listening.
    """
    out = _lsof(["-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"])
    if out is None:
        return None
    sockets = {
        p["p"][0]: (
            p.get("c", ["?"])[0],
            sorted({n.rsplit(":", 1)[-1] for n in p.get("n", [])}),
        )
        for p in _fields(out)
    }
    if not sockets:
        return []
    cwd_out = _lsof(
        ["-a", "-d", "cwd", "-p", ",".join(sockets), "-Fpn"], partial_ok=True
    )
    if cwd_out is None:
        return None
    cwds: dict[str, Path] = {}
    for p in _fields(cwd_out):
        cwd = Path(p.get("n", [""])[0])
        if cwd.is_absolute():
            cwds[p["p"][0]] = cwd
    return [
        Listener(int(pid), command, port, cwds.get(pid))
        for pid, (command, ports) in sockets.items()
        for port in ports
    ]


def _within(path: Path | None, root: Path) -> bool:
    return path is not None and (path == root or root in path.parents)


def listeners_under(root: Path) -> list[Listener] | None:
    """Listening sockets whose process cwd is `root` or below it.

    None means lsof could not be asked; [] means nothing is listening there.
    """
    found = all_listeners()
    if found is None:
        return None
    root = root.resolve()
    return [x for x in found if _within(x.cwd, root)]


_PUBLISHED = re.compile(r":(\d+)->")


def docker_published() -> dict[str, str]:
    """Host port -> container name for every running container's published ports.

    Docker Desktop and OrbStack listen through a backend proxy, so lsof alone
    names the proxy, never the container. {} when docker is absent or fails.
    """
    if not shutil.which("docker"):
        return {}
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        name, _, published = line.partition("\t")
        for port in _PUBLISHED.findall(published):
            out.setdefault(port, name)
    return out


def holders(
    port: int,
    listeners: list[Listener],
    worktrees: dict[Path, str],
    docker: dict[str, str],
) -> list[str]:
    """Who holds `port`: a docker container, a worktree by label, or a process."""
    key = str(port)
    if key in docker:
        return [f"docker {docker[key]}"]
    deepest_first = sorted(worktrees.items(), key=lambda kv: -len(kv[0].parts))
    labels: list[str] = []
    for x in listeners:
        if x.port != key:
            continue
        owner = next((lbl for p, lbl in deepest_first if _within(x.cwd, p)), None)
        if owner is not None:
            label = f"worktree {owner} ({x.command}, pid {x.pid})"
        else:
            label = f"{x.command} (pid {x.pid})"
        if label not in labels:
            labels.append(label)
    return labels


def describe(listeners: list[Listener]) -> str:
    return ", ".join(f":{x.port} {x.command} (pid {x.pid})" for x in listeners)


def close_warning(worktree: Path) -> str | None:
    """The line `c` and `cockpit close` show when a server would be left behind."""
    listeners = listeners_under(worktree)
    if not listeners:
        return None
    return "still listening from this worktree, close does not stop it: " + describe(
        listeners
    )
