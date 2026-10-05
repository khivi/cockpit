# Spawn and prompts — behavior spec

## Spawn

- [spawn.adopt-grace~1] Given an un-stat-able worktree path, its age reads as
  infinite. The worktree counts as old enough to adopt. A stat failure never
  silently mutes the callers. Both paths that attach a workspace to an existing
  worktree refuse a worktree younger than the grace.
- [spawn.context-flag~1] Given a bare `--context` with no text, the spawn
  exits 2 and names the flag. It creates nothing. The CLI never synthesizes its
  own context.
- [spawn.coworker-pr~1] Given the same failing-CI PR tracked once as mine and
  once as a coworker's, only mine is nudged. Pills and tracking are identical.
  The send gates on `PR.mine`.
- [spawn.picker-parked~1] Given a parked repo, the new-workspace picker still
  offers it. The picker sinks it below the live repos and labels it hidden.
  Picking it un-parks the repo on the same gesture.
- [spawn.plan-fallback~1] Given a source that no `(mode, provider)` pair
  matches, the fallback plan-only prompt carries the ref (`o/r#42`) in its
  Source slot. One such source is a GitHub issue URL with no ticket provider
  configured. The prompt never seeds a bare branch name.
- [spawn.review-default~1] Given no configured review command, a coworker-PR
  seed leads with cockpit's packaged review prose. The seed never names a
  command cockpit does not ship.
- [spawn.seed-enter~1] Given a body the composer never echoes, delivery
  reports the failure. Delivery never submits the body. No Enter is ever
  pressed on an unconfirmed composer.
- [spawn.seed-budget~1] The same failed delivery re-types the body exactly
  once before giving up. The waiting lives in the echo polls. Polling answers a
  slow boot, never a third send.
- [spawn.seed-garbled~1] Given a submitted body that the session received
  altered, delivery reports it. The composer echo confirms only the body's
  first characters. Delivery reads loss past them from Claude Code's own
  transcript, never from the screen. Nothing is re-queued. An absent record is
  never read as corruption. A caller that passes no worktree forgoes the check.
- [spawn.ticket-prompt~1] Given a repo that declares a Linear team key and no
  global `tickets` block at all, its bare ticket id still routes to it. The
  routing gate asks the matched candidates about their provider. It never asks
  the global block.
- [spawn.unhide~1] Given a spawn into a parked repo by any mode (`--pr`, an
  existing branch, a named branch, a ticket ref), one shared gate un-parks it.
  The gate keys on the resolved repo root. It never keys on the new worktree's
  own path.
- [spawn.opt-out~2] Given a `use_worktree: false` repo with an open PR that a
  default repo would spawn for, `_spawn_missing_workspaces` spawns nothing. It
  makes no background create, no PR workspace and no orphan workspace.

## Prompts

- [prompts.templates~1] Every packaged template renders with its declared
  slots and leaves no placeholder behind. A missing slot raises `KeyError`. It
  never survives as a stray brace.
- [prompts.split~1] (untested: design rationale) Templates carry only static
  prose and slots. Control flow lives in the Python builder. A `.txt` never
  carries control flow.
- [prompts.plan-untracked~1] Both plan-gate spellings tell the session to
  write `plan.md`. Both spellings carry the never-stage line.
- [prompts.ticket-identity~1] Given a repo with a resolved ticket provider, the
  first-turn prompt states which tracker it files against. It states which
  team, project or board it files into. It names no credential env var.
- [prompts.ticket-scope-derived~1] The scope the prompt states comes off the
  provider strategy. It is never a `keys` read. A Trello repo scoped by its
  board states the board. A repo with no provider states nothing. A spawn that
  determined no repo at all states nothing.
- [prompts.ticket-mcp-server~1] Given a repo that declares an MCP server name,
  the first-turn prompt names that server. It never names the provider's
  connector generically. Given no server name, the prompt states the connector
  generically and names no server.
- [prompts.plan-unread~1] No tick, renderer or teardown depends on the plan
  artifact. Asserted structurally: no Python source under cockpit/ names it.
- [slack.no-preflight~2] No provider's spawn gates its fetch on probing for an
  MCP connector. Each provider prompt carries its own retry-then-STOP step.
  Asserted structurally: the Linear spawn path shells no `claude mcp list` at
  runtime. Tree-wide, the only source that shells it is the ticket check's own
  leaf. No spawn, tick or gate imports that leaf.
