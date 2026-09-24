# Sends and nudges — behavior spec

## The idle gate

- [send.one-line~1] Given a message carrying a real newline or the
  two-character `\n` spelling, it collapses to one line before the send —
  either form arrives in the composer as Enter, submitting a truncated
  prompt — and a plain message passes through untouched.
- [idle-gate.needs-input~1] Given native `Needs input` and no `idle=` pill,
  `nudge_if_idle` refuses the send — the state is ambiguous between parked-
  at-prompt and a pending y/n permission.
- [idle-gate.reassert~1] Given any native state short of unambiguous `Idle`,
  the fast-tick reassert writes no pill; a ref reporting no native state at
  all heals only when the screen read confirms the bare composer.
- [idle-gate.verdict~1] For every gate case, `rest_skip_reason`'s verdict
  matches whether `nudge_if_idle` would deliver — the display caller cannot
  disagree with the decision.
- [idle-pill.liveness~1] Given `CMUX_WORKSPACE_ID` absent from the
  id-carrying workspace listing — including being a mere prefix of a live id
  — the hook exits silently; a live id passes through to the pill write.

## Automatic sends

- [diff-nudge.send~1] Given pending diff notes in a worktree, the fast tick
  sends `DIFF_COMMENTS_NUDGE` to that session through `nudge_if_idle` with no
  `pref_key` — mute and snooze do not silence a note the user just wrote.
- [diff-nudge.scope~1] (untested: design rationale) The hand-over reaches
  only the session in the worktree holding the notes; it never generalises
  into a fan-out.
- [seed-queue.stale~1] Given a queued seed body older than `STALE_SECONDS`,
  the prune drops it unsent — a "you are starting a fresh task" prompt
  delivered into a session the user has since been driving is worse than
  none — while a fresh marker survives.
- [orphan.no-nudge~1] Given a worktree with no PR, the orphan refresh applies
  its pills and sends nothing — there is no orphan nudge and no config key
  for one.

## Broadcast

- [broadcast.send~1] `cockpit broadcast` reaches each workspace through
  `nudge_if_idle` with `tag="broadcast"` and no `pref_key` — no send path,
  idle check, or cache cell of its own.
- [broadcast.repo~1] Given two bare-clone repos whose paths both end in
  `.bare`, `--repo .bare` resolves to neither — the path basename is not a
  second spelling of the name, and an unknown name exits 2 listing the
  configured repos.

## Nudge prefs, snooze and wake

- [nudge-prefs.repo-key~1] Given prefs for the same PR number in two repos,
  deleting or snoozing one leaves the other's file and nudge decision
  untouched — every key carries the repo's nwo name.
- [nudge.restamp~1] Given `restamp_pref` for one PR, only that PR's snapshot
  and its own `pr-*` cells for its one worktree change — a sibling PR, a
  second worktree, and the daemon-derived cells on the same cwd come out
  byte-identical.
- [nudge.snooze-wake~1] Given a snooze whose `until` lies far in the past, it
  stays snoozed — a snooze waits on an event, never the clock.
- [nudge.wake-signature~1] `wake_signature` takes exactly `total_from_others`
  and `review_decision` — a head oid cannot perturb it without becoming a
  parameter — and its output is sensitive to both.
- [nudge.chain-snooze~1] Given `z` pressed on a chain member below the tip,
  every member's pref is written with the pressed row's direction, wake
  snapshots included — membership comes from the render's own record, so the
  keypress cannot leave a half-snoozed chain.
- [nudge-cli.split-chain~1] Given a bare `cockpit nudge snooze` on a branch
  stacked below an unsnoozed tip, the write stays per-PR and the warning
  names the tip that has to agree before the row moves.
