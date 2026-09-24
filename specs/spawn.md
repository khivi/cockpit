# Spawn and prompts — behavior spec

## Spawn

- [spawn.adopt-grace~1] Given an un-stat-able worktree path, its age reads as
  infinite — old enough to adopt — so a stat failure never silently mutes the
  callers; a worktree younger than the grace is refused by both paths that
  attach a workspace to an existing worktree.
- [spawn.context-flag~1] Given a bare `--context` with no text, the spawn
  exits 2 naming the flag and creates nothing — an unexpanded flag means the
  substitution didn't happen, and the CLI never synthesizes its own context.
- [spawn.coworker-pr~1] Given the same failing-CI PR tracked once as mine and
  once as a coworker's, only mine is nudged — pills and tracking are
  identical, and the send is gated on `PR.mine`.
- [spawn.picker-parked~1] Given a parked repo, the new-workspace picker still
  offers it — sunk below the live repos and labelled hidden — and picking it
  un-parks the repo on the same gesture.
- [spawn.plan-fallback~1] Given a source no `(mode, provider)` pair matches —
  a GitHub issue URL with no ticket provider configured — the fallback
  plan-only prompt carries the ref (`o/r#42`) in its Source slot rather than
  seeding a bare branch name.
- [spawn.review-default~1] Given no configured review command, a coworker-PR
  seed leads with cockpit's packaged review prose — never a command cockpit
  doesn't ship.
- [spawn.seed-enter~1] Given a body the composer never echoes, delivery
  reports the failure instead of submitting — no Enter is ever pressed on an
  unconfirmed composer.
- [spawn.seed-budget~1] The same failed delivery re-types the body exactly
  once before giving up — the waiting lives in the echo polls, and a slow
  boot is answered by polling, never a third send.
- [spawn.ticket-prompt~1] Given a repo declaring a Linear team key with no
  global `tickets` block at all, its bare ticket id still routes to it — the
  routing gate asks the matched candidates about their provider, never the
  global block.
- [spawn.unhide~1] Given a spawn into a parked repo by any mode — `--pr`, an
  existing branch, a named branch, a ticket ref — the one shared gate
  un-parks it, keyed on the resolved repo root rather than the new worktree's
  own path.
- [spawn.opt-out~2] Given a `use_worktree: false` repo with an open PR that
  would otherwise be created, `_spawn_missing_workspaces` spawns nothing —
  no background create, no PR workspace, no orphan workspace.

## Prompts

- [prompts.templates~1] Every packaged template renders with its declared
  slots leaving no placeholder behind, and a missing slot raises `KeyError`
  rather than surviving as a stray brace.
- [prompts.split~1] (untested: design rationale) Templates carry only static
  prose and slots; control flow lives in the Python builder, never in a
  `.txt`.
- [prompts.plan-untracked~1] Both plan-gate spellings tell the session to
  write `plan.md` AND carry the never-stage line — cockpit's `.gitignore`
  does not travel to the repos it spawns into, so the prose is the only
  thing holding a tracked plan out of a squash merge there.
- [prompts.plan-unread~1] No Python source under cockpit/ names the plan
  artifact, so no tick, renderer, or teardown can come to depend on a file a
  session might not have written.
- [slack.no-preflight~1] No call under cockpit/ shells `claude mcp list` —
  tree-wide and provider-neutral, including the Linear spawn path at runtime;
  each provider prompt carries its own retry-then-STOP step instead.
