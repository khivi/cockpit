#!/usr/bin/env python3
"""`/cockpit:focus` — switch cmux focus to a workspace by PR / branch / slug."""

from __future__ import annotations

import sys
from pathlib import Path

from cockpit.lib.cmux import (
    cmux,
    require_workspace_binary,
    resolve_workspace,
)
from cockpit.lib.config import discover_repo
from cockpit.lib.tool import is_cmux, resolve_tool


def main() -> int:
    require_workspace_binary()

    if not is_cmux():
        print(
            f"ERROR: focus requires cmux; current tool is {resolve_tool()}",
            file=sys.stderr,
        )
        return 1

    if len(sys.argv) != 2:
        print("usage: focus.py <pr|branch|slug>", file=sys.stderr)
        return 2

    repo_cfg = discover_repo()
    repo_dir = Path(repo_cfg["path"]).expanduser() if repo_cfg else Path.cwd()

    try:
        match = resolve_workspace(sys.argv[1], repo_dir)
    except LookupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    cmux("focus", "--workspace", match.ref, check=False)
    print(f"focused workspace {match.name or match.ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
