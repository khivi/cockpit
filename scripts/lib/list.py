"""Read-only `--list` output: every worktree across all managed repos, with
cached PR status and a `(no workspace)` drift marker when applicable.

Reads cache + git + cmux only; never polls GitHub.
"""

from __future__ import annotations

import os
from pathlib import Path

from .cache import find_pr_payload
from .cmux import workspace_names
from .config import load_config
from .git import worktrees


def render_list() -> int:
    cfg = load_config()
    cmux_set = set(workspace_names().values())
    print(f"{'REPO':<14}{'BRANCH':<32}{'PR':<8}{'CI':<10}{'REVIEW':<22}UPDATED")
    for r in cfg.get("repos", []):
        path = Path(os.path.expanduser(r["path"]))
        name = r.get("name") or path.name
        if not path.is_dir():
            print(f"{name:<14}(repo path missing)")
            continue
        try:
            wts = worktrees(path)
        except RuntimeError:
            continue
        for wt in wts:
            branch = wt.branch or "(detached)"
            pr_payload = find_pr_payload(branch, repo_name=name)
            drift = "" if wt.short in cmux_set else " (no workspace)"
            if pr_payload:
                review = str(pr_payload["review"]).lower()
                unaddressed = pr_payload.get("unaddressed") or 0
                if unaddressed:
                    review = (
                        f"{review} 💬{unaddressed}"
                        if review and review != "none"
                        else f"💬{unaddressed}"
                    )
                print(
                    f"{name:<14}{branch:<32}#{pr_payload['number']:<7}"
                    f"{pr_payload['ci']:<10}"
                    f"{review:<22}"
                    f"{pr_payload.get('updatedAt', '')}{drift}"
                )
            else:
                print(f"{name:<14}{branch:<32}{'—':<8}{'—':<10}{'—':<22}—{drift}")
    return 0
