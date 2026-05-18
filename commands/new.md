---
description: "Create a git worktree + cmux workspace for a new branch or existing PR."
argument-hint: "<branch|PR-number|github-url> [--pr <num>] [--repo <name>]"
allowed-tools: Bash
---

# /cockpit:new

Spawn a fresh worktree (sibling of the main repo) plus a cmux workspace with `claude` pre-running.

## Arguments

- `<branch|PR-number|github-url>` — positional, auto-detected:
  - GitHub PR URL (`https://github.com/.../pull/N`) → PR mode
  - Bare number (`123` or `#123`) → PR mode
  - Anything else → branch (local, remote, or new — git resolves)
- `--pr <num>` — explicit PR mode; overrides positional detection.
- `--branch <name>` — explicit branch; overrides positional detection.
- `--repo <name>` — target a configured repo by `name` from `~/.config/cockpit/config.json`. Skips cwd-based discovery; useful when invoking from outside the repo's tree.
- `--claude-prompt <str>` — first-turn prompt for claude. Defaults to a plan-only prompt for PR input; defaults to none (bare `claude`) for branch input.

## Behaviour

1. Pick the managed repo. If `--repo <name>` is set, match the entry in `~/.config/cockpit/config.json` by `name`. Otherwise, resolve the main worktree from cwd via `git worktree list --porcelain` and match its path. If no entry matches, `spawn.py` calls `lib.registry.register_cwd()` inline to append the current repo.
2. Derive the short slug from the branch name (last segment after `/`, slugified, capped at 30 chars).
3. Compute worktree path: `<dirname(main-repo)>/<short>` — append `-2`, `-3`, … if taken.
4. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spawn.py "$@"`. Creates the worktree if missing, spawns the cmux workspace via `cmux new-workspace --name <short> --cwd <wt-path> --command 'claude [prompt]' --focus false`.
5. If the branch already has a worktree and a workspace with the same short, attach (idempotent) instead of erroring.
6. Print one line: `workspace <short> spawned at <wt-path> on <branch>` — or `attached existing workspace …` on idempotent reattach.

## Implementation

```bash
exec python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spawn.py "$@"
```

## Examples

```text
/cockpit:new fix-login
/cockpit:new 12345                       # PR mode (numeric arg)
/cockpit:new https://github.com/org/repo/pull/12345  # PR mode (URL)
/cockpit:new fix-login --pr 12345        # explicit PR mode with custom local branch
/cockpit:new fix-login --repo myapp      # target a specific configured repo by name
```
