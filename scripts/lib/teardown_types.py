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

    `delete_branch` opts the request into deleting the local branch ref (via
    `git branch -D`) after the worktree is removed. Off by default; callers set
    it only once they've confirmed the branch is merged and carries no
    post-merge local commits. `teardown` still refuses to delete the default
    branch regardless of this flag.
    """

    ref: str
    name: str = ""
    worktree_path: Path | None = None
    branch: str | None = None
    repo_path: Path | None = None
    repo_name: str | None = None
    forced: bool = False
    delete_branch: bool = False
