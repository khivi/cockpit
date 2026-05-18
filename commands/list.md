---
description: "Show all cockpit-managed worktrees with their PR status."
allowed-tools: Bash
---

# /cockpit:list

Read `git worktree list` for every managed repo, cross-reference `cmux list-workspaces`, and overlay PR status from `~/.config/cockpit/cache/<repo>__pr-*.json`.

## Output

```text
REPO          BRANCH              PR     CI       REVIEW              UPDATED
myrepo        feature/foo         #123   pass     approved            2m ago
myrepo        fix/bar             #124   fail     changes-req 💬3     1h ago
otherrepo     experiment/baz      —      —        —                   3d ago  (no PR)
```

Drift markers appended where they apply:

- `(no workspace)` — worktree exists, no matching cmux workspace

## Implementation

```bash
exec ${CLAUDE_PLUGIN_ROOT}/scripts/list.py
```

`list.py` is read-only: it renders the current cache + git + cmux state
without polling GitHub.
