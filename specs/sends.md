# Sends and nudges — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## The idle gate

- [send.one-line~1] Every message collapses to one line via `one_line` inside
  `nudge_if_idle`, before the `dry` print — a newline arrives as Enter and would
  submit a truncated prompt.
- [idle-gate.needs-input~1] The gate blocks on native `Running` and treats
  `Needs input` as ambiguous; safe means the `idle=` pill is present or native
  is the unambiguous `Idle`.
- [idle-gate.reassert~1] `reassert_idle_pills` only ever writes a pill, is
  `dry`-gated, and trusts `Running`/`Needs input` no harder than the gate
  itself.
- [idle-gate.verdict~1] Every caller reads the refusal verdict from the one
  function (`rest_skip_reason` wrapping `_idle_skip_reason`); no call site re-
  derives the guard order.
- [idle-pill.liveness~1] The hook's liveness guard compares `CMUX_WORKSPACE_ID`
  against a listing that carries workspace ids, never the ref-and-name listing.

## Automatic sends

- [diff-nudge.send~1] The diff-comment hand-over rides `nudge_if_idle` like
  every other send; it has no second send path.
- [diff-nudge.scope~1] (untested: design rationale) The hand-over reaches only
  the session in the worktree holding the notes; it never generalises into a
  fan-out.
- [seed-queue.stale~1] A seed marker older than `STALE_SECONDS` is dropped,
  never delivered late into a session the user has been driving.
- [orphan.no-nudge~1] A worktree with no PR gets pills and a row, never a send;
  there is no orphan nudge and no config key for one.

## Broadcast

- [broadcast.send~1] `cockpit broadcast` fans out through `nudge_if_idle` with
  no `pref_key` — no send path, idle check, or cache cell of its own.
- [broadcast.repo~1] `--repo` matches the repo's one identity (`_repo_label`),
  casefolded; the path basename is not a second spelling, and an unknown name
  exits 2 listing the configured repos.

## Nudge prefs, snooze and wake

- [nudge-prefs.repo-key~1] Every `NudgePref` key carries the repo's nwo name; no
  call site invents a key from a bare PR number.
- [nudge.restamp~1] `restamp_pref` is the one row-action cache write and covers
  exactly the pref cells and snapshot fields the keypress sourced — never a cell
  the daemon derives.
- [nudge.snooze-wake~1] A snooze wakes on events — review activity, new work, a
  push to a reviewed PR — never on a clock.
- [nudge.wake-signature~1] `head_oid` stays out of `wake_signature`; the push-
  wake arm applies only to PRs that are not mine.
- [nudge.chain-snooze~1] `z` applies the pressed row's direction to every chain
  member, from the render's own record; a half-snoozed chain converges rather
  than getting a glyph.
- [nudge-cli.split-chain~1] The bare-snooze warning names the tip via
  `chain_tip`, the payload-level reading of the same `base` link; `nudge_cli.py`
  derives no chain of its own.
