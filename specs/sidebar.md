# Sidebar — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Trailing folds

- [folds.anchor.owns-a-live-shell~1] A group anchor owns a live shell: an anchor
  spawned with no command gets no terminal surface and dies silently, so
  `_durable_anchor` never leaves one standing.
- [folds.collapse.create-time-only~1] Trailing folds are born collapsed at create
  time only; no per-cycle re-assert slams an expanded fold shut.
- [folds.collapse.no-read-back~1] `is_collapsed` is never read back to "correct" a
  fold.
- [folds.partial.keyed-on-completeness~1] `ReviewFolds.partial` suspends the
  dissolve loop when any repo failed to report, while re-park, rename and
  re-member still run; it keys on cycle completeness, never on a bucket being
  empty.
- [folds.restore.create-only~1] `restore_trailing_folds` can only create — no
  dissolve, rename, member change or re-park.
- [folds.restore.separate-read-functions~1] The restore pass reads
  `read_workspace_groups`, which keeps a failed read distinct from an empty
  answer; the flattening `list_workspace_groups` stays a separate function.
- [folds.snoozed-toggle.z-key-only~1] The snoozed fold row toggles on `z`, Enter,
  and a single click — never `h` or a new key.
- [folds.trailing.pass-order-not-rank~1] Fold order comes from the pass order of
  `_TRAILING_FOLDS`, not a rank field.
- [folds.review.never-persist-marker~1] (untested: design rationale) "This is a
  review" is derived every cycle from `PR.mine`, never persisted.
- [folds.mystery.not-closed~1] (untested: process rule) The fast-tick fold restore
  repairs the loss; whatever destroys the folds is still unidentified, and no
  change may claim otherwise.

## Stacks

- [stacks.anchor.durable-anchor-required~1] `create_workspace_group` swaps in an
  anchor spawned with `ANCHOR_KEEPALIVE_COMMAND` and closes the husk `create`
  returned, through the self-close funnel.
- [stacks.group-header.tip-not-root~1] A stack group is named after the tip —
  `<tip> (N)` — never the root.
- [stacks.snoozed-diversion.not-position-based~1] A chain whose tip is snoozed is
  diverted whole into the snoozed fold; there is no position-only answer.
- [stacks.tui-nesting.one-level-only~1] The table indents stack members exactly one
  level under the tip, keyed by index; a base cycle falls back to flat rows.
- [stacks.derive.no-gh-view-no-persist~1] (untested: design rationale) Chains are
  derived from `PR.base` every cycle — no `gh stack view`, no persisted stack.

## Pills

- [pills.pr-pill.exclusive-renderer~1] While the `pr` pill renders, `draft`,
  `state` and the four `ci_*` kinds keep `None` cmux renderers — still emitted,
  never rendered.
- [pills.wip-suppression.not-reorder~1] `wip` is suppressed while `rebase` or
  `merge` is in flight; `KIND_ORDER` is unchanged.
- [pills.pr-pill.coverage-narrower-accepted~1] (untested: design rationale) The
  `pr` pill reaches only tracked workspaces; no pill is spawned for untracked
  ones.

## Sidebar tags

- [sidebar-tag.cwd-alone.no-tag~1] A spawn with no repo determined (`--cwd` alone)
  gets no tag; there is no `discover_repo()` fallback.
- [sidebar-tag.fold-header.reads-bucket-not-member~1] A trailing fold's tag comes
  from the bucket's own declaration and substitutes for the bucket name; a
  member's merged tag never labels the fold.
- [sidebar-tag.separator.last-char~1] A tag ending in a non-alphanumeric takes a
  space instead of `SIDEBAR_TAG_SEP`, keyed on the last character; the
  `{repo}`-expanded form keeps the separator.
- [sidebar-tag.token.resolved-string-only~1] `{repo}` is expanded once at load time
  by `expand_sidebar_tags`; `tag_workspace_name` takes a resolved string and
  never sees the token.
- [sidebar-tag.naming.two-sites-only~1] (untested: design rationale) The tag is
  applied by the daemon's `workspace_name` half and `spawn.py`'s `ws_name` half —
  no third naming site.

## cmux events

- [events.cursor-file.not-cache-cell~1] The events cursor file is cmux's resume
  bookmark only — never stored inventory, never routed through `cache.py`.
- [events.doorbell.trigger-only~1] An event only kicks the fast tick; no payload
  feeds a decision, a cell, or the slow tick.
- [events.self-close.single-funnel~1] Cockpit's own closes are recorded by
  `_note_self_close` inside `cmux_close_workspace_best_effort`, keyed by UUID —
  the one funnel every close path goes through.
- [events.sidebar-x.routes-to-refuse-not-force~1] The sidebar ✕ routes to the
  refusing teardown gate, never to force, and every refusal toasts.
