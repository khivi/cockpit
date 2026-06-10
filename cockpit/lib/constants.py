"""Cross-cutting constants shared by leaves and orchestrators.

Lives in `lib` so a leaf (`cmux.py`) and an orchestrator (`cycle.py`) can both
import the same value without a leaf→orchestrator dependency. Keep this module
import-light — no project imports — so anything may depend on it.
"""

from __future__ import annotations

# Branch names treated as a repo's trunk. A worktree on one of these is never a
# feature branch: its `label` derivation always collapses to the branch name
# itself, so the rename/pill paths exempt it (see `cmux.reconcile_workspace_names`
# and `cycle._refresh_*`). `git._BASE_BRANCH_SEG_RE` matches the same set as a
# regex for prefix-stripping.
MAIN_BRANCHES = {"master", "main"}
