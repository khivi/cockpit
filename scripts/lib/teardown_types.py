"""Neutral data type shared between `lib.daemon_signal` and `orchestrators.teardown`.

Lives in `lib/` (not `orchestrators/`) so `lib.daemon_signal` can import it
without a reverse `lib → orchestrators` dependency. The orchestrator
re-exports it for backward compat with callers using
`from scripts.orchestrators.teardown import TeardownRequest`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TeardownRequest:
    """Inputs for a single workspace teardown.

    `worktree_path` / `branch` / `repo_path` / `repo_name` are all optional
    because the `close_gone_cwd_workspaces` path has only `ref` to work with.
    """

    ref: str
    name: str = ""
    worktree_path: Path | None = None
    branch: str | None = None
    repo_path: Path | None = None
    repo_name: str | None = None
    forced: bool = False
