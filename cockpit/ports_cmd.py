"""`cockpit ports` — who holds the TCP ports a repo declares it needs.

Read-only and on demand: one lsof pass, one `docker ps`, no daemon. A holder is
named as a docker container, a worktree of any configured repo, or a bare
process (an ssh tunnel shows as `ssh (pid N)`).

Exits 0 when every declared port is free, 1 when any is held, and 2 when the
answer is unknown: no configured repo here, no ports declared, or lsof could not
be asked. "Couldn't ask" is never reported as free.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cockpit.broadcast import _repo_label
from cockpit.lib import ports
from cockpit.lib.config import discover_repo, load_config
from cockpit.lib.git import worktrees_basic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cockpit ports",
        description="Show who holds each port a repo declares in config `ports`.",
    )
    parser.add_argument(
        "--all", action="store_true", help="every configured repo, not just the cwd's"
    )
    return parser


def _worktree_labels(repos: list[dict]) -> dict[Path, str]:
    labels: dict[Path, str] = {}
    for repo in repos:
        name = _repo_label(repo)
        try:
            wts = worktrees_basic(
                Path(repo["path"]).expanduser(), repo.get("branch_prefix", ""), name
            )
        except RuntimeError:
            continue
        for wt in wts:
            labels[wt.path.resolve()] = f"{name}/{wt.label}"
    return labels


def render(
    targets: list[dict],
    listeners: list[ports.Listener],
    worktrees: dict[Path, str],
    docker: dict[str, str],
) -> tuple[list[str], bool]:
    lines: list[str] = []
    held = False
    for repo in targets:
        lines.append(_repo_label(repo))
        for port in repo.get("ports", []):
            found = ports.holders(port, listeners, worktrees, docker)
            held |= bool(found)
            lines.append(
                f"  :{port}  " + ("held by " + ", ".join(found) if found else "free")
            )
    return lines, held


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repos = load_config().get("repos", [])
    if args.all:
        targets = [r for r in repos if r.get("ports")]
    else:
        here = discover_repo()
        if here is None:
            print("cockpit ports: the cwd is in no configured repo", file=sys.stderr)
            return 2
        targets = [here] if here.get("ports") else []
    if not targets:
        print('cockpit ports: no repo declares "ports" in config.json', file=sys.stderr)
        return 2
    listeners = ports.all_listeners()
    if listeners is None:
        print(
            "cockpit ports: lsof could not be asked; holders unknown", file=sys.stderr
        )
        return 2
    lines, held = render(
        targets, listeners, _worktree_labels(repos), ports.docker_published()
    )
    print("\n".join(lines))
    return 1 if held else 0
