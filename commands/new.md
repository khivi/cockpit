---
description: "Create a git worktree + cmux workspace for a new branch or existing PR."
argument-hint: "<branch-name|pr-number> [--base <branch>] [--pr <num>]"
allowed-tools: Bash
---

# /cockpit:new

Spawn a fresh worktree (sibling of the main repo) plus a cmux workspace with `claude` pre-running.

## Arguments

- `<branch-name>` or `<pr-number>` — required. Numeric input is treated as a PR number; everything else is a branch slug.
- `--base <branch>` — base to branch off (default: the repo's `gh repo view --json defaultBranchRef`).
- `--pr <num>` — explicit PR mode; fetches `pull/<num>/head` into a local branch.

## Behaviour

1. Detect the managed repo: resolve the main worktree from cwd via `git worktree list --porcelain`, then match its path against entries in `~/.config/cockpit/config.json`. If no entry matches, `spawn.py` calls `lib.registry.register_cwd()` inline to append the current repo (uses `gh api user` for the branch prefix + `gh repo view` for the default base).
2. Derive the short slug from the branch name (last segment after `/`, slugified, capped at 30 chars).
3. Compute worktree path: `<dirname(main-repo)>/<short>` — append `-2`, `-3`, … if taken.
4. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spawn.py --branch <branch> --path <worktree-path> --short <short>` (add `--pr <num>` in PR mode). Creates the worktree if missing, spawns the cmux workspace via `cmux new-workspace --name <short> --cwd <wt-path> --command 'claude' --focus false`.
5. If the branch already has a worktree and a workspace with the same short, attach (idempotent) instead of erroring.
6. Print one line: `workspace <short> spawned at <wt-path> on <branch>` — or `attached existing workspace …` on idempotent reattach.

## Implementation

```bash
exec python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spawn.py "$@"
```

## Examples

```text
/cockpit:new fix-login
/cockpit:new 12345                 # PR mode (numeric arg)
/cockpit:new hotfix --base release-1.2
/cockpit:new fix-login --pr 12345  # explicit PR mode with custom local branch
```
