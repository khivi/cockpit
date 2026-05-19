---
description: "Create a git worktree + cmux workspace for a new branch or existing PR."
argument-hint: "<branch|PR|url> | --branch <n> | --pr <n> | --name <n> | --cwd <path> | --skill <n> [--repo <n>] [--claude-prompt <s>]"
model: haiku
allowed-tools: Bash
---

# /cockpit:new

Spawn a fresh worktree (sibling of the main repo) plus a cmux workspace with `claude` pre-running.

## Arguments

Exactly one *positional or input-flag* source is required. Mixing the positional with `--branch`/`--pr`/`--name`/`--skill` is an error.

**Positional** (auto-detected; mutually exclusive with `--branch`/`--pr`/`--name`/`--skill`):

- GitHub PR URL (`https://github.com/.../pull/N`) → PR mode
- `#`-prefixed PR number (`#123`) → PR mode. A bare integer (`123`) is treated as a branch — use `#123` or `--pr 123` for PRs
- Anything else → branch (local, remote, or new — git resolves at worktree time). If an **open PR exists** for the resolved branch on GitHub, its number/title/url is printed to stderr and the plan-only prompt is used (override with `--claude-prompt`)

**Input flags** (mutually exclusive with the positional; `--branch`/`--pr` may combine with each other and with `--name`):

- `--branch <name>` — explicit branch name. Combined with `--pr`, fetches the PR under this local name instead of the PR's head ref
- `--pr <num>` — fetches `pull/<num>/head`; local branch defaults to the PR's head ref unless `--branch` overrides
- `--name <short>` — workspace short name. Alone, it also seeds the new branch name. When omitted, the short name is slugified from the branch tail
- `--cwd <path>` — spawn workspace in an arbitrary directory (created if missing); no worktree or repo required. Mutex with `--branch`/`--pr`/`--skill`
- `--skill <name>` — spawn a workspace running a skill. Resolves against `~/.claude/skills/<name>/skill.md` first (global skills always win), then `<repo>/.claude/skills/<name>/skill.md`. Workspace cwd defaults to `$HOME` (global) or the repo path (repo skill); an explicit `--repo` overrides cwd to that repo's path even when the global skill wins. No worktree, no branch. Mutex with `--branch`/`--pr`/`--cwd`

**Modifiers** (always combinable with any input source above):

- `--repo <name>` — universal override on repo discovery; targets a configured repo by `name` from `~/.config/cockpit/config.json`. Combinable with any input source. No-op under `--cwd` (no repo lookup occurs). With `--skill`, sets the workspace cwd to the configured repo's path — even when the global skill wins resolution; if the global skill is absent, it also serves as the repo-skill lookup location. **For non-skill inputs, if neither `--repo` nor cwd-based discovery resolves a repo, `/cockpit:new` errors out** — auto-registration via `register_cwd` has been removed
- `--claude-prompt <str>` — first-turn prompt for claude; overrides the default. Defaults: PR-context plan-only prompt for PR input (positional PR or `--pr`); branch plan-only prompt for branch input (positional branch, `--branch`, `--name` alone) — if an open PR exists on that branch, the PR-context variant is used instead; `/<name>` for `--skill`; bare `claude` for `--cwd`

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
/cockpit:new --skill <skill-name>                    # global skill workspace at $HOME
/cockpit:new --skill <skill-name> --repo myrepo      # global or repo skill, workspace cwd = myrepo
```
