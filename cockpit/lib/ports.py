"""Listening TCP processes running from inside a worktree, for the close warning.

A dev server outlives the session that started it and is reparented to
launchd, so a process-tree walk loses it on exactly the case that matters. The
process's cwd survives, so ownership is cwd containment.

Read-only: it reports, never kills.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Listener:
    pid: int
    command: str
    port: str


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


def listeners_under(root: Path) -> list[Listener] | None:
    """Listening sockets whose process cwd is `root` or below it.

    None means lsof could not be asked; [] means nothing is listening there.
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
    root = root.resolve()
    found: list[Listener] = []
    for p in _fields(cwd_out):
        pid = p["p"][0]
        cwd = Path(p.get("n", [""])[0])
        if pid not in sockets or not cwd.is_absolute():
            continue
        if cwd != root and root not in cwd.parents:
            continue
        command, ports = sockets[pid]
        found.extend(Listener(int(pid), command, port) for port in ports)
    return found


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
