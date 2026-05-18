---
description: "Remove a cockpit worktree + cmux workspace + PR cache."
argument-hint: "<pr|branch|slug> [--force]"
model: haiku
allowed-tools: Bash
---

# /cockpit:close

Tear down a cockpit-managed workspace + its worktree + its PR cache, after safety checks. Shares its workspace resolver with `/cockpit:focus`.

## Arguments

- Positional `<query>` — PR (`#123` / `123`), branch name, or workspace slug.
- `--force` — bypass refusal on uncommitted changes, unpushed commits, or open PR.

## Behaviour

1. `resolve_workspace()` matches the query.
2. Safety checks (skipped under `--force`):
   - uncommitted files in the worktree (`git status --porcelain`)
   - unpushed commits relative to `@{upstream}`
   - PR state is `OPEN` (per cached snapshot)
3. `cmux close-workspace --workspace <ref>` then `git worktree remove`.
4. `delete_pr_caches_for_branch()` clears `~/.config/cockpit/cache/<repo>__pr-*.json` entries matching the branch.

Workspace-only mode (no matching worktree) skips git + cache steps and only closes cmux.

## Implementation

```bash
exec python3 ${CLAUDE_PLUGIN_ROOT}/scripts/close.py "$@"
```
