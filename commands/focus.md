---
description: "Switch cmux focus to a cockpit workspace by PR, branch, or slug."
argument-hint: "<pr|branch|slug>"
model: haiku
allowed-tools: Bash
---

# /cockpit:focus

Resolve the workspace via `lib.cmux.resolve_workspace` and switch cmux focus to it. Read-only on git/disk.

## Arguments

- Positional `<query>` — one of:
  - PR number (`#123` or `123`) — looked up in `~/.config/cockpit/cache/`
  - Branch name (exact match against `git worktree list`)
  - Workspace slug (exact match against `cmux list-workspaces`)

## Behaviour

1. `discover_repo()` resolves the managed repo from cwd.
2. `resolve_workspace()` matches the query in priority order: PR → branch → slug.
3. `cmux focus --workspace <ref>` switches focus.

Errors on no match or ambiguous match — never closes or modifies state.

## Implementation

```bash
exec python3 ${CLAUDE_PLUGIN_ROOT}/scripts/focus.py "$@"
```
