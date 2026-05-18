---
name: cockpit
model: haiku
description: "Manage cmux workspaces backed by git worktrees aligned to GitHub PRs. TRIGGER when the user says: start a new feature branch / new PR worktree, what is the status of my PRs, list my open PRs, refresh PR status, switch/focus to a PR's workspace, close/tear down a worktree. DO NOT TRIGGER for: general git questions, building unrelated features, or anything that does not involve worktree↔workspace↔PR alignment."
allowed-tools: Bash
effort: low
context: fork
---

# cockpit

Keeps three things in lockstep for every PR: **git worktree ↔ cmux workspace ↔ GitHub PR**. State lives under `~/.config/cockpit/` (config + per-PR cache); active work is derived from `git worktree list`, `cmux list-workspaces`, and `gh`.

## Routing

| User intent | Command |
|---|---|
| Start a new feature branch / PR worktree | `/cockpit:new <branch-or-pr>` |
| What are the statuses of my open PRs? | `/cockpit:list` |
| Refresh PR status / I just pushed | `/cockpit:sync` |
| Switch to a PR's workspace | `/cockpit:focus <pr\|branch\|slug>` |
| Tear down a worktree + workspace | `/cockpit:close <pr\|branch\|slug> [--force]` |

`new`/`focus` are idempotent — re-invoking `/cockpit:new foo` or `/cockpit:focus foo` attaches to the existing worktree/workspace instead of erroring. `close` refuses on dirty/unpushed/open-PR state unless `--force`. `focus` and `close` share `lib.cmux.resolve_workspace`, which matches PR → branch → slug.
