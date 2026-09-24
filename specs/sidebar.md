# Sidebar — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Trailing folds

- [folds.anchor~1] A fold is re-anchored onto a workspace that owns a live
  shell — the command-less anchor `create` spawns has no terminal surface and
  dies silently — and the husk is closed through the self-close funnel, never
  a raw close.
- [folds.born-collapsed~1] A trailing fold is created collapsed; an
  already-standing fold is re-parked without any collapse re-assert, so one
  the user expanded stays expanded.
- [folds.collapse-readback~1] `is_collapsed` appears nowhere in `cycle.py` —
  reading it back to "correct" a fold would slam shut one the user
  deliberately expanded.
- [folds.partial~1] Given a healthy repo cycle, `ReviewFolds.partial` stays
  False; a repo that fails to report sets it, which suspends the dissolve
  loop while re-park, rename and re-member still run.
- [folds.restore~1] Given a standing record and a stray same-icon group live,
  the fast-tick restore issues no ungroup, close, rename or member change —
  it can only create.
- [folds.restore-reads~1] Given the group read fails (`None`), the restore
  gives up — flattening the failure to an empty list would read as "every
  fold is gone" and duplicate each group still on screen.
- [folds.snoozed-toggle~2] The snoozed fold row advertises exactly the two
  keys that act on the fold itself — `z` and `A` — plus the global set;
  workspace-targeted row keys and `h` are hidden there.
- [folds.order~1] Reversing the trailing-folds tuple flips the emitted
  sidebar order — pass order is the only thing deciding it; there is no rank
  field.
- [folds.review-derived~1] (untested: design rationale) "This is a review" is
  derived every cycle from `PR.mine`, never persisted.
- [folds.mystery~1] (untested: process rule) The fast-tick fold restore
  repairs the loss; whatever destroys the folds is still unidentified, and no
  change may claim otherwise.

## Stacks

- [stacks.anchor~1] `create_workspace_group` returns the swapped-in keepalive
  anchor — never the husk `create` returned.
- [stacks.header~1] A chain of three renders as one group named `<tip> (N)`
  with the tip leading the member list — the same order the table renders the
  chain in.
- [stacks.snoozed~1] Given a chain whose tip is snoozed, the whole chain
  joins the snoozed pile as one contiguous run, tip first, giving up its own
  group — a workspace lives in exactly one group.
- [stacks.nesting~1] Four PRs stacked in a line render as the tip at the head
  and all three below at one shared indent — never a per-level cascade.
- [stacks.derived~1] (untested: design rationale) Chains are derived from
  `PR.base` every cycle — no `gh stack view`, no persisted stack.

## Pills

- [pills.pr-exclusive~1] Given a MERGED PR, cmux renders only the `pr` pill —
  `state` stays suppressed, since the pill already says merged and its
  trailing glyph carries CI.
- [pills.wip-suppression~1] Given a PR approved, conflicted, mid-rebase and
  dirty at once, the pills are exactly rebase, conflict, approved, pr — `wip`
  steps aside so the approval clears cmux's three visible rows.
- [pills.pr-coverage~1] (untested: design rationale) The `pr` pill reaches
  only tracked workspaces; no pill is spawned for untracked ones.

## Sidebar tags

- [sidebar-tag.cwd~1] Given `--cwd` alone — no repo determined — and a
  configured tag, the workspace name is the bare directory name; no tag is
  guessed from wherever the user was standing.
- [sidebar-tag.fold-header~1] The fold tag reads the org block's own
  declaration — `🛡️ {repo}` yields the bare `🛡️`, and a literal org tag stays
  whole — never a member's expanded tag, which would name the whole org's
  pile after one repo.
- [sidebar-tag.separator~1] A tag ending in a glyph takes a plain space
  (`🎛️ dot`), while a tag ending in text keeps the separator — keyed on the
  last character, so the `{repo}`-expanded form stays parted.
- [sidebar-tag.token~1] `tag_workspace_name` treats `{repo}` as ordinary
  text — the token is expanded exactly once, at load time, which is what lets
  an org declare it.
- [sidebar-tag.sites~1] (untested: design rationale) The tag is applied by
  the daemon's `workspace_name` half and `spawn.py`'s `ws_name` half — no
  third naming site.

## cmux events

- [events.cursor-file~1] `events.py` imports nothing from the cache module —
  the resume cursor is cmux's bookmark and never routes through flat-cell
  machinery.
- [events.doorbell~1] Given a workspace event, the fast tick runs once,
  immediately, and the slow tick count is untouched — a trigger, never a
  decision.
- [events.self-close~1] Closing a gone-cwd workspace goes through the funnel
  that records the self-close, so the resulting closed event cannot be
  mistaken for the user's sidebar ✕.
- [events.sidebar-x~1] Given the ✕ on a workspace cockpit did not close
  itself, the close routes to the refusing gate with force off — the X is
  one unmodified click and never maps onto `C`.
