# Changelog

## [1.17.0](https://github.com/khivi/cockpit/compare/v1.16.0...v1.17.0) (2026-08-21)


### Features

* **sidebar:** sink a snoozed stacked-PR chain to the bottom ([#337](https://github.com/khivi/cockpit/issues/337)) ([660f64c](https://github.com/khivi/cockpit/commit/660f64c0f32e37f2bf57ca670e85f8dd53c33218))


### Bug Fixes

* **tui:** show the h/Hide hint only on repo rows ([#334](https://github.com/khivi/cockpit/issues/334)) ([f7a49d9](https://github.com/khivi/cockpit/commit/f7a49d9f8a734b1731e9352a5aded75b218882a9))

## [1.16.0](https://github.com/khivi/cockpit/compare/v1.15.0...v1.16.0) (2026-08-20)


### Features

* **spawn:** fold --context-text into an optional-value --context ([#332](https://github.com/khivi/cockpit/issues/332)) ([7580ed4](https://github.com/khivi/cockpit/commit/7580ed4225009f3d6e161e7bf92cb41301ed2ced))

## [1.15.0](https://github.com/khivi/cockpit/compare/v1.14.0...v1.15.0) (2026-08-20)


### Features

* **setup:** ship /cockpit-broadcast as a bundled slash command ([#328](https://github.com/khivi/cockpit/issues/328)) ([a2ae576](https://github.com/khivi/cockpit/commit/a2ae57648bb6f2022bb8b23e9a21fa8723fb06d9))

## [1.14.0](https://github.com/khivi/cockpit/compare/v1.13.0...v1.14.0) (2026-08-20)


### Features

* **tui:** wake the fast tick on cmux workspace events ([#325](https://github.com/khivi/cockpit/issues/325)) ([32205e7](https://github.com/khivi/cockpit/commit/32205e7b56eceba9c9cae0541ed2e2279623b8f0))

## [1.13.0](https://github.com/khivi/cockpit/compare/v1.12.0...v1.13.0) (2026-08-20)


### Features

* **cli:** add cockpit broadcast to send text to every idle session ([#321](https://github.com/khivi/cockpit/issues/321)) ([805fcc8](https://github.com/khivi/cockpit/commit/805fcc858d62bbb837be02ee78064aba4017eb40))
* **preflight:** gate startup on the cmux verbs and capabilities cockpit needs ([#324](https://github.com/khivi/cockpit/issues/324)) ([4cfeb67](https://github.com/khivi/cockpit/commit/4cfeb6767e7a0fb6101ab939f411587224029435))


### Bug Fixes

* **nudge:** key prefs per repo and resolve the snooze payload by nwo ([#323](https://github.com/khivi/cockpit/issues/323)) ([e4fa850](https://github.com/khivi/cockpit/commit/e4fa85047026de640dd4f88aa90fce0c7f8758d7))

## [1.12.0](https://github.com/khivi/cockpit/compare/v1.11.0...v1.12.0) (2026-08-20)


### Features

* **tui:** align row status glyphs and let a snooze supersede a mute ([#319](https://github.com/khivi/cockpit/issues/319)) ([883abc5](https://github.com/khivi/cockpit/commit/883abc5f1b8d4dff76e8dea23c632e967d5b508c))

## [1.11.0](https://github.com/khivi/cockpit/compare/v1.10.1...v1.11.0) (2026-08-20)


### Features

* **tui:** sink reviews and snoozed rows below the active queue ([#317](https://github.com/khivi/cockpit/issues/317)) ([bb7e64c](https://github.com/khivi/cockpit/commit/bb7e64c84698f97ea8cc250820981df5e0415b6c))

## [1.10.1](https://github.com/khivi/cockpit/compare/v1.10.0...v1.10.1) (2026-08-20)


### Bug Fixes

* **tui:** brighten the snoozed-row icon tint ([#315](https://github.com/khivi/cockpit/issues/315)) ([6e7d77a](https://github.com/khivi/cockpit/commit/6e7d77a3966116d8faa5dd1b1a810c71e255937a))

## [1.10.0](https://github.com/khivi/cockpit/compare/v1.9.0...v1.10.0) (2026-08-20)


### Features

* **sidebar:** key the coworker-review fold by org and fold a lone review ([#310](https://github.com/khivi/cockpit/issues/310)) ([a72abdd](https://github.com/khivi/cockpit/commit/a72abddf8d924001dc70670d32d984f05eef0f60))
* **tui:** snooze a PR until someone comments or approves ([#312](https://github.com/khivi/cockpit/issues/312)) ([65fc93a](https://github.com/khivi/cockpit/commit/65fc93a05987178f9a4140aaf62dc984da002c49))

## [1.9.0](https://github.com/khivi/cockpit/compare/v1.8.0...v1.9.0) (2026-08-20)


### Features

* **tickets:** route a ticket to its repo by project/keys/board ([#304](https://github.com/khivi/cockpit/issues/304)) ([6ee4e98](https://github.com/khivi/cockpit/commit/6ee4e985c5eb9faf56a51919d85e27713f9c1e21))
* **tui:** make the repo grouping read as a hierarchy ([#307](https://github.com/khivi/cockpit/issues/307)) ([c9ff131](https://github.com/khivi/cockpit/commit/c9ff131267fc13d3010f51377b44b2ed3662fcf2))


### Documentation

* PyPI trusted-publishing setup + recovery steps ([#271](https://github.com/khivi/cockpit/issues/271)) ([92244e1](https://github.com/khivi/cockpit/commit/92244e1aa4c937c2e4d8a81d783fbe2e77eb8f75))

## [1.8.0](https://github.com/khivi/cockpit/compare/v1.7.1...v1.8.0) (2026-08-13)


### Features

* **tickets:** resolve provider credentials per org, keep them out of spawned sessions ([#299](https://github.com/khivi/cockpit/issues/299)) ([a5c9ea0](https://github.com/khivi/cockpit/commit/a5c9ea0b28b2533ec28a0a825bff7b6f3a9387e5))


### Bug Fixes

* **release:** match release-please's tag format to tag.yml's ([#302](https://github.com/khivi/cockpit/issues/302)) ([0d1649c](https://github.com/khivi/cockpit/commit/0d1649ccf46efc0e7183054818d6e4f1ccf3ecc0))

## Changelog

All notable changes to this project are documented here, in the style of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The version in `pyproject.toml` is bumped and tagged `v<version>` at release
time (the brew formula pins that tag), so a per-version list would mostly be
noise. This file instead records notable, human-readable changes grouped by
kind, not every version bump.

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

- Distribution moved from a Claude Code plugin + uv-tool to a Homebrew formula
  (`brew tap khivi/cockpit && brew install cockpit`); `cockpit setup` now writes
  the statusLine **and** the Claude Code hooks into `~/.claude/settings.json`.
  The in-TUI self-update (`u`), the `/cockpit:*` slash commands, and the
  plugin/marketplace are gone — `brew upgrade` handles updates. Existing
  plugin users: see [`MIGRATION.md`](MIGRATION.md).
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
