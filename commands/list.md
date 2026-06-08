---
description: "Show all cockpit-managed worktrees with their PR status."
argument-hint: ""
model: haiku
allowed-tools: Bash
---

# /cockpit:list

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase or skip. After Bash returns, paste its stdout verbatim.

```bash
cockpit list
```

## Output

```text
REPO          BRANCH                          PR      CI        REVIEW                UPDATED
myrepo        feature/foo                     #123    pass      approved              2025-05-17T14:23:01
myrepo        fix/bar                         #124    fail      changes-req 💬3       2025-05-17T13:10:44
otherrepo     experiment/baz                  —       —         —                     2025-05-14T09:05:22 (no workspace)
```

Drift markers appended where they apply:

- `(no workspace)` — worktree exists, no matching workspace

`list.py` is read-only: it renders the current cache + git + workspace state
without polling GitHub.
