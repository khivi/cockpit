# Sends and nudges — behavior spec

## The idle gate

- [send.one-line~1] Given a message with a real newline or the two-character
  `\n` spelling, the send path collapses it to one line before the send. A
  plain message passes through untouched.
- [idle-gate.needs-input~1] Given native `Needs input` and no `idle=` pill,
  `nudge_if_idle` refuses the send.
- [idle-gate.reassert~2] Given any native state short of unambiguous `Idle`,
  the fast-tick reassert writes no pill. Given a ref with no native state, the
  reassert writes the pill only when two conditions hold. The transcript shows
  no tool call in flight. The screen confirms the bare composer. The screen
  alone is never evidence of rest.
- [idle-gate.verdict~1] For every gate case, the verdict of
  `rest_skip_reason` matches whether `nudge_if_idle` would deliver. The display
  caller never disagrees with the decision.
- [idle-pill.liveness~1] Given `CMUX_WORKSPACE_ID` absent from the id-carrying
  workspace listing, the hook exits silently. A mere prefix of a live id counts
  as absent. A live id passes through to the pill write.

## Automatic sends

- [diff-nudge.send~1] Given pending diff notes in a worktree, the fast tick
  sends `DIFF_COMMENTS_NUDGE` to that session through `nudge_if_idle` with no
  `pref_key`. Mute and snooze do not silence the send.
- [diff-nudge.scope~1] (untested: design rationale) The hand-over reaches only
  the session in the worktree that holds the notes. It never generalises into a
  fan-out.
- [seed-queue.stale~1] Given a queued seed body older than `STALE_SECONDS`,
  the prune drops it unsent. A fresh marker survives.
- [orphan.no-nudge~1] Given a worktree with no PR, the orphan refresh applies
  its pills and sends nothing. No orphan nudge exists. No config key for one
  exists.

## Broadcast

- [broadcast.send~1] `cockpit broadcast` reaches each workspace through
  `nudge_if_idle` with `tag="broadcast"` and no `pref_key`. It has no send
  path, idle check, or cache cell of its own.
- [broadcast.repo~1] Given two bare-clone repos whose paths both end in
  `.bare`, `--repo .bare` resolves to neither. The path basename is not a
  second spelling of the name. An unknown name exits 2 and lists the configured
  repos.

## Nudge prefs, snooze and wake

- [nudge-prefs.repo-key~1] Given prefs for the same PR number in two repos,
  deleting or snoozing one leaves the other's file and nudge decision
  untouched. Every key carries the nwo name of the repo.
- [nudge.restamp~1] Given `restamp_pref` for one PR, only that PR's snapshot
  and its own `pr-*` cells for its one worktree change. A sibling PR, a second
  worktree, and the daemon-derived cells on the same cwd stay byte-identical.
- [nudge.snooze-wake~1] Given a snooze whose `until` lies far in the past, it
  stays snoozed. A snooze waits on an event and never on the clock.
- [nudge.wake-signature~1] `wake_signature` takes exactly `total_from_others`
  and `review_decision`. A head oid cannot change it without becoming a
  parameter. Its output is sensitive to both parameters.
- [nudge.chain-snooze~1] Given `z` pressed on a chain member below the tip,
  the app writes the pref of every member with the direction of the pressed
  row. The write includes wake snapshots. Membership comes from the render's
  own record. The keypress never leaves a half-snoozed chain.
- [nudge-cli.split-chain~1] Given a bare `cockpit nudge snooze` on a branch
  stacked below an unsnoozed tip, the write stays per-PR. The warning names the
  tip that has to agree before the row moves.
