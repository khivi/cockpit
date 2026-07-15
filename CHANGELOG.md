# Changelog

All notable changes to this project are documented here, in the style of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The version number auto-bumps a patch on every merge to `main` (see
`.githooks/version-bump.py`), so a per-version list would mostly be noise.
This file instead records notable, human-readable changes grouped by kind,
not every version bump.

## Recent history

### Added

- Ticket providers for Trello, Jira, and GitHub Issues, alongside Linear, via
  a unified `tickets` config object (#231, #223, replacing per-provider
  flags)
- `review_prs` gating: skip coworker PRs from Dependabot and non-collaborator
  (external/fork) authors by default, opt-in via `dependabot` /
  `review_external` (#232, #242)
- `cockpit close` CLI and `/cockpit:close` command as manual teardown entry
  points alongside the TUI's `c`/`C` keys (#207)
- Configurable `review_command` for auto-spawned review workspaces (#206)
- Startup warning when a repo's configured base branch doesn't resolve
  against `origin` (#244)
- Red `!` indicator in the status column for an unresolved ticket state
  (#243)
- Worktree table rows grouped under per-repo header rows (#233)

### Changed

- `w` (open workspace) folded into `f` (focus), which now spawns a workspace
  first if the row has none; `in_place` config renamed to `use_worktree`
  (inverted polarity); `n` (new workspace) routes per repo type (#245)
- Sidebar workspace names drop the `[repo]` prefix, relying on `sidebar_color`
  tint to convey which repo a workspace belongs to (#235)
- Footer ahead-count is based on the PR's base branch, with a configurable
  remote (#246)
- Ticket-opening is provider-neutral, with a dynamic per-row footer instead
  of a fixed key hint (#203); the key itself moved from `l` to `t` (#204)

### Fixed

- Self-update (`u`) runs in a subprocess, avoiding a TTY hang (#239)
- Workspaces are deduplicated by worktree path instead of by a name that can
  collide (#234)
- Highlighted dashboard row keeps its repo color (#240)
- Branch refs are reaped from a fresh worktree read instead of a stale cycle
  snapshot (#230)
- Manual close recognizes squash and rebase merges, not just fast-forward
  merges (#205)
- A `use_worktree: false` workspace is named after the repo, not `master`
  (#249)
- Cockpit's own workspace is excluded from cwd-based workspace matching
  (#248)
- Cross-session fallback dropped from the statusline context pill, which was
  showing stale data (#198)

## Adding entries

When you land a notable PR, add a line under the matching heading above
(`Added` / `Changed` / `Fixed`). Routine `chore`/`ci`/`build`/`test`/
docs-only commits and automatic version bumps don't need an entry.
