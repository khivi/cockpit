---
description: "Create a git worktree + cmux workspace for a new branch or existing PR."
argument-hint: "<branch|PR|url> | --branch <n> | --pr <n> | --name <n> | --cwd <path>"
model: haiku
allowed-tools: Bash
---

# /cockpit:new

Spawn a fresh worktree (sibling of the main repo) plus a cmux workspace with `claude` pre-running.

## Arguments

Exactly one input source is required. Mixing positional with `--branch`/`--pr`/`--name` is an error.

**Positional** (auto-detected, mutually exclusive with `--branch`/`--pr`/`--name`):

- GitHub PR URL (`https://github.com/.../pull/N`) → PR mode
- Bare number (`123` or `#123`) → PR mode
- Anything else → branch (local, remote, or new — git resolves)

**Explicit flags** (mutually exclusive with positional):

- `--branch <name>` — explicit branch name
- `--pr <num>` — explicit PR; fetches `pull/<num>/head` into a local branch
- `--name <short>` — new branch (bypasses PR detection); also sets workspace name

**Modifiers** (combinable with any input source above):

- `--repo <name>` — target a configured repo by `name` from `~/.config/cockpit/config.json` instead of cwd discovery
- `--claude-prompt <str>` — first-turn prompt for claude. Auto-generated plan-only prompt for PR input; bare `claude` for branch input

**Alternative mode** (mutually exclusive with `--branch`/`--pr`/`--repo`):

- `--cwd <path>` — spawn workspace in an arbitrary directory (created if missing), no worktree or repo required

## Behaviour

1. Pick the managed repo via cwd discovery or `--repo`. If unmatched, auto-registers via `lib.registry.register_cwd()`.
2. Derive the short slug from the branch tail (slugified, capped at 30 chars).
3. Compute worktree path: `<dirname(main-repo)>/<short>` — append `-2`, `-3`, … if taken.
4. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spawn.py "$@"`. Creates the worktree if missing, spawns via `cmux new-workspace --name <short> --cwd <wt-path> --command 'claude [prompt]' --focus false`.
5. Idempotent: existing worktree + workspace → attach instead of error.
6. Prints: `workspace <short> spawned at <wt-path> on <branch>` or `attached existing workspace …`.

## Implementation

```bash
exec python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spawn.py "$@"
```

## Examples

```text
/cockpit:new fix-login                               # new or existing local/remote branch
/cockpit:new 12345                                   # PR by number
/cockpit:new https://github.com/org/repo/pull/12345  # PR by URL
/cockpit:new --branch fix-login --pr 12345           # PR fetched under custom local name
/cockpit:new --name fix-login                        # new branch, workspace named fix-login
/cockpit:new --repo myapp fix-login                  # target a specific configured repo
/cockpit:new --cwd ~/scratch/spike                   # arbitrary dir, no repo
```
