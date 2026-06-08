---
description: "Queue a cockpit worktree + workspace teardown for the daemon."
argument-hint: "[pr|branch|slug] [--force]"
model: haiku
allowed-tools: Bash
---

# /cockpit:close

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase or skip. After Bash returns, paste its stdout verbatim.

```bash
cockpit close $ARGUMENTS
```

## Arguments (reference only)

- Positional `<query>` (optional) — PR (`#123` / `123`), branch name, or workspace slug. Defaults to the worktree at the current directory.
- `--force` — override the open-PR refusal. Also lets you close a teammate's open-PR worktree once their commits are pushed. Never overrides uncommitted changes or commits that exist only locally.

## Behaviour

1. Resolve the target: from `<query>` if given, else from `git rev-parse --show-toplevel`.
2. Inline blocker probe. Hard blockers (never `--force`-overridable): uncommitted files, and commits that exist only locally. For your own branches, "unpushed" means "not yet merged to the default branch"; for someone else's PR worktree it means only "not on that PR's remote branch", so a teammate's pushed-but-unmerged PR is not a hard blocker. Once the cached PR is MERGED the unpushed check is skipped (`git cherry` over-counts squash-merges and non-default-base merges) — but uncommitted files still hard-block. Soft blocker (overridable by `--force`): an open PR.
3. Write a close-request marker under `$COCKPIT_HOME/state/close-requests/<repo>/<ref>.json`.
4. SIGUSR1-kick the running daemon. The daemon's next cycle drains the queue through `orchestrators.teardown` — one code path for `/cockpit:close`, autoclose-on-merge, and orphan reaping.
5. A running daemon is required. If none is running, the command errors out and tells you to start one (`cockpit watch`), rather than running teardown inline — so it never dual-runs with a real daemon and a transient teardown failure stays durable in the queue.

Teardown order is invariant: workspace close (`cmux`/`limux`) → `git worktree remove` → cache delete. Pulling the cwd out from under a live Claude session breaks every Stop/PreToolUse hook with ENOENT.
