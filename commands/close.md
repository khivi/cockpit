---
description: "Queue a cockpit worktree + cmux workspace teardown for the daemon."
argument-hint: "[pr|branch|slug] [--force]"
model: haiku
allowed-tools: Bash
---

# /cockpit:close

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase or skip. After Bash returns, paste its stdout verbatim.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/close.py "$@"
```

## Arguments (reference only)

- Positional `<query>` (optional) — PR (`#123` / `123`), branch name, or workspace slug. Defaults to the worktree at the current directory.
- `--force` — bypass refusal on uncommitted changes, unpushed commits, or open PR.

## Behaviour

1. Resolve the target: from `<query>` if given, else from `git rev-parse --show-toplevel`.
2. Inline blocker probe (skipped under `--force`): uncommitted files, unpushed commits, open PR. Fast refusal with a clear message.
3. Write a close-request marker under `$COCKPIT_HOME/state/close-requests/<repo>/<ref>.json`.
4. SIGUSR1-kick the running daemon. The daemon's next cycle drains the queue through `orchestrators.teardown` — one code path for `/cockpit:close`, autoclose-on-merge, and orphan reaping.
5. If no daemon is running, run teardown inline against the same request (and pop the marker on success) so the user always sees results immediately.

Teardown order is invariant: cmux close → `git worktree remove` → cache delete. Pulling the cwd out from under a live Claude session breaks every Stop/PreToolUse hook with ENOENT.
