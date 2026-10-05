# Sidebar — behavior spec

## Trailing folds

- [folds.anchor~1] The reconcile re-anchors a fold onto a workspace that owns a
  live shell. The reconcile closes the husk through the self-close funnel. It
  never uses a raw close.
- [folds.born-collapsed~1] The reconcile creates a trailing fold collapsed. It
  re-parks an already-standing fold without a collapse re-assert. A fold that
  the user expanded stays expanded.
- [folds.collapse-readback~1] A fold that the user expanded stays expanded for
  the life of the session. The cycle never re-collapses a fold that it did not
  just create. Asserted structurally: `is_collapsed` appears nowhere in
  `cycle.py`.
- [folds.partial~1] Given a healthy repo cycle, `ReviewFolds.partial` stays
  False. A repo that fails to report sets it. The flag suspends the dissolve
  loop. Re-park, rename and re-member still run.
- [folds.restore~1] Given a standing record and a stray same-icon group live,
  the fast-tick restore issues no ungroup, close, rename or member change. It
  can only create.
- [folds.restore-reads~1] Given a failed group read (`None`), the restore gives
  up. It never flattens the failure to an empty list. It never duplicates a
  group that is still on screen.
- [folds.snoozed-toggle~2] The snoozed fold row advertises exactly two keys
  that act on the fold itself: `z` and `A`. It also advertises the global set.
  It hides workspace-targeted row keys and `h`.
- [folds.order~1] Reversing the trailing-folds tuple flips the emitted sidebar
  order. Pass order alone decides it. There is no rank field.
- [folds.review-derived~1] (untested: design rationale) The daemon derives "this is
  a review" from `PR.mine` every cycle. It never persists the mark.
- [folds.mystery~1] (untested: process rule) The fast-tick fold restore
  repairs the loss. Whatever destroys the folds is still unidentified. No
  change may claim otherwise.

## Stacks

- [stacks.anchor~1] `create_workspace_group` returns the swapped-in keepalive
  anchor. It never returns the husk that `create` returned.
- [stacks.header~1] A chain of three renders as one group named `<tip> (N)`.
  The tip leads the member list. The member order matches the order the table
  renders the chain in.
- [stacks.snoozed~1] Given a chain whose tip is snoozed, the whole chain joins
  the snoozed pile as one contiguous run, tip first. The chain gives up its
  own group.
- [stacks.nesting~1] Four PRs stacked in a line render with the tip at the
  head. The three PRs below render at one shared indent. The render never
  cascades one indent per level.
- [stacks.derived~1] (untested: design rationale) The daemon derives chains
  from `PR.base` every cycle. It never runs `gh stack view`. It never
  persists a stack.

## Pills

- [pills.pr-exclusive~1] Given a MERGED PR, cmux renders only the `pr` pill.
  The `state` pill stays suppressed. Its trailing glyph carries CI.
- [pills.wip-suppression~1] Given a PR that is approved, conflicted,
  mid-rebase and dirty at once, the pills are exactly rebase, conflict,
  approved and pr. The `wip` pill steps aside.
- [pills.pr-coverage~1] (untested: design rationale) The `pr` pill reaches
  only tracked workspaces. The daemon spawns no pill for an untracked
  workspace.

## Sidebar tags

- [sidebar-tag.cwd~1] Given `--cwd` alone, with no repo determined and a tag
  configured, the workspace name is the bare directory name. The spawn never
  guesses a tag from the directory where the user stood.
- [sidebar-tag.fold-header~1] The fold tag reads the org block's own
  declaration. `🛡️ {repo}` yields the bare `🛡️`. A literal org tag stays whole.
  The fold tag never reads a member's expanded tag.
- [sidebar-tag.separator~1] A tag that ends in a glyph takes a plain space
  (`🎛️ dot`). A tag that ends in text keeps the separator. The rule keys on the
  last character. The `{repo}`-expanded form stays parted.
- [sidebar-tag.token~1] `tag_workspace_name` treats `{repo}` as ordinary text.
  The load step expands the token exactly once.
- [sidebar-tag.sites~1] (untested: design rationale) Two sites apply the
  tag: the daemon's `workspace_name` half and `spawn.py`'s `ws_name` half.
  There is no third naming site.

## cmux events

- [events.cursor-file~1] The resume cursor is cmux's bookmark. It never becomes
  stored inventory. No renderer reads anything derived from it. Asserted
  structurally: `events.py` imports nothing from the cache module.
- [events.doorbell~1] Given a workspace event, the fast tick runs once,
  immediately. The slow tick count stays untouched. The event is a trigger and
  never a decision.
- [events.self-close~1] Closing a gone-cwd workspace goes through the funnel
  that records the self-close. The resulting closed event never reads as the
  user's sidebar ✕.
- [events.sidebar-x~1] Given the ✕ on a workspace that cockpit did not close,
  the close routes to the refusing gate with force off. The X is one
  unmodified click. It never maps onto `C`.
