---
description: "Queue a cockpit worktree + workspace teardown for the daemon (defaults to the current workspace)."
argument-hint: "[branch|slug|path] [--force] [--dry-run]"
model: haiku
allowed-tools: Bash
---

# /cockpit:close

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase or skip. After Bash returns, paste its stdout verbatim.

```bash
cockpit close $ARGUMENTS
```

## Arguments (reference only)

- Positional `[branch|slug|path]` (optional) — the worktree to close, by branch name, sidebar label, dir basename, or path. Defaults to the worktree at the current directory, so a bare `/cockpit:close` closes the workspace you're in.
- `--force` — override the soft open-PR refusal. Also lets you close a teammate's open-PR worktree once their commits are pushed. Never overrides uncommitted changes or commits that exist only locally.
- `--dry-run` — report the resolved target and any blockers without enqueuing.

## Behaviour

1. Resolve the target: from the positional query if given, else from the current directory's worktree root.
2. Inline blocker probe (same gating as the TUI's `c`/`C` row actions). Hard blockers (never `--force`-overridable): uncommitted files, and commits that exist only locally. For your own branches "unpushed" means "not yet merged to the default branch"; for someone else's PR worktree it means only "not on that PR's remote branch", so a teammate's pushed-but-unmerged PR is not a hard blocker. Once the PR is MERGED (resolved cache-first with one live `gh` fallback, so out-of-band squash/rebase merges count) the unpushed check is skipped — but uncommitted files still hard-block. Soft blocker (overridable by `--force`): an open PR.
3. Write a durable close-request marker under `$COCKPIT_HOME/state/close-requests/<repo>/<ref>.json`.
4. SIGUSR1-kick the running daemon. Its next cycle drains the queue through `orchestrators.teardown` — one teardown code path shared with autoclose-on-merge and orphan reaping.
5. If no daemon is running, the marker stays queued (drained on the next `cockpit watch` start) and the command says so. Teardown never runs inline, so it can't dual-run with a real daemon and a transient failure stays durable in the queue.

Teardown order is invariant: workspace close (`cmux`/`limux`) → `git worktree remove` → cache delete. Pulling the cwd out from under a live Claude session breaks every Stop/PreToolUse hook with ENOENT.
