# Spawn and prompts — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Spawn

- [spawn.adopt-grace~1] Both paths that attach a workspace to an existing
  worktree refuse one younger than the adoption grace; an unstattable path reads
  as old enough.
- [spawn.context-flag~1] `parse_args` errors on a bare `--context`; the CLI
  never synthesizes its own context.
- [spawn.coworker-pr~1] A coworker's PR seeds the review prompt and gets no
  nudge; `PR.mine` gates both.
- [spawn.picker-parked~1] The repo picker keeps parked repos, sunk and dimmed
  but selectable; `_spawn_new` un-parks before launching, deliberately redundant
  with the spawn-side gate.
- [spawn.plan-fallback~1] When no ticket-prompt pair matches, the spawn falls
  through to the plan-only prompt with the `source` slot seeded by the bare ref.
- [spawn.review-default~1] An unset `skills.review` falls back to packaged prose
  rendered through `review_lead`, the one resolver both call sites use — never a
  named command.
- [spawn.seed-enter~1] `deliver_followup` presses no Enter on a body it could
  not confirm on screen.
- [spawn.seed-budget~1] The retry budget stays at two sends with the waiting
  carried by echo polls; a slow boot is answered by polling, not re-sends.
- [spawn.ticket-prompt~1] The ticket prompt resolves via the `(mode, provider)`
  table from `repo_tickets`, after every routing hop — never the global block or
  the cwd's repo.
- [spawn.unhide~1] `_unhide_spawn_target` is one gate after the spawn for every
  mode, keyed on `main_worktree_path(wt)`.
- [spawn.opt-out~1] `_spawn_missing_workspaces` early-returns for `use_worktree:
  false` repos, and every read spells the default as opted-in.

## Prompts

- [prompts.templates~1] Every packaged template resolves with its slots; a
  missing slot raises `KeyError`.
- [prompts.split~1] (untested: design rationale) Templates carry only static
  prose and slots; control flow lives in the Python builder, never in a `.txt`.
- [prompts.plan-untracked~1] The plan gate tells the session to write `plan.md`
  and never stage or commit it.
- [prompts.plan-unread~1] No tick, renderer, or teardown reads the plan
  artifact; it stays prose in the template, not a path the Python computes.
- [slack.no-preflight~1] No provider fetch is gated on a `claude mcp list`
  probe; the prompt's own retry-then-STOP handles an absent connector.
