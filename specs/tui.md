# TUI — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Table

- [table.links~1] A `link` span comes out of a rendered DataTable line as a
  real OSC 8 escape — pinned against the actual render, since Textual makes
  no public promise about emitting it — and the hover tooltip names the
  destination.
- [table.status-slot~1] A belled row, a muted row and a quiet row all start
  their label at the same cell column — the status glyph sits in a
  fixed-width slot a glyphless row pays in blanks, measured in cells, not
  bytes.
- [table.ticket-link~1] `worktree_table.py` never calls the live ticket-URL
  resolver — the ticket cell's link reads the `url` the daemon cached, and a
  renderer resolving its own is the banned thing.

## Header bar

- [header.countdowns~1] Given the cursor row's repo name growing much longer,
  the countdown segment's painted x-position does not move — `#header-repo`
  owns the bar's one flexible slot.
- [header.brand~1] `brand_text` links the version to exactly the URL it is
  handed, and carries no link at all when the URL is empty — the bar cannot
  drift off the destination the palette's "What's new" opens.
- [header.guide-entry~1] The feature-guide action opens exactly
  `FEATURE_GUIDE_URL`, an `https://` URL — never a local file path.
- [header.tick-glyphs~1] The tooltip spells out both tick glyphs by word,
  fed from the same constants the bar renders, and the two glyphs measure
  the same cell width so the pair cannot misalign against itself.
- [header.repo-segment~1] (untested: design rationale) The cursor row's repo
  is named by the header segment, never by a Repo column or
  `DataTable.fixed_rows`.
- [header.upgrade-toast~1] (untested: design rationale) The upgrade toast
  compares cockpit against its own last run and never becomes a version
  check, a `u` key, or a re-exec.
- [header.menu~1] (untested: design rationale) The palette's visible entry
  point is the header's menu segment; the key is not printed beside it and
  the segment never moves into the footer.

## Palette, global keys, footer

- [palette.order~1] `discover` yields `COMMANDS` in tuple order, and that
  order runs in-app overlays before `$EDITOR` before the browser pair — an
  entry appended to the end fails.
- [palette.discover~1] Every entry appears on an empty palette via
  `discover` — implementing `search` alone left the palette showing none of
  cockpit's entries exactly when it opens.
- [globalkeys.sync~1] The palette offers Output — its only in-app route —
  and does not offer Sync, which has the `s` key instead; one surface per
  action.
- [footer.tooltips~1] Hovering any footer segment — key or label — sets the
  bar's tooltip to that action's explanation, read off the segment's own
  `@click` meta; one widget, never a widget per key.
- [hidden.h-key~1] (untested: design rationale) `h` is the one park key:
  expand/collapse the hidden section, un-park a revealed repo, or park the
  cursor row's repo — never a second key for reveal.

## App plumbing

- [tui.on-repo-done~1] Given the slow tick's per-repo hook firing, no cache
  write occurs — the republish repaints the table and writes nothing.
- [tui.signals~1] `signal.signal(` appears nowhere in `tui/app.py` —
  handlers install through `loop.add_signal_handler`, since the direct call
  raises off the main thread.
- [tui.diff-key~1] `render_diff` appears nowhere under `cockpit/tui/` — a
  second caller would reopen the stale-surface bug the `d` key was removed
  for.
- [tui.nudge-key~1] (untested: design rationale) There is no manual nudge key
  with a canned message; a manual send is `a`'s typed line.
- [stdout.queue-writer~1] `redirect_stdout(` appears nowhere under cockpit/ —
  one process-wide `_QueueWriter` captures tick prints, and a per-tick
  redirect would let the two tick threads race on the global stream.
- [ask.line~1] Given pending diff comments, the ask box still opens empty —
  `a` sends exactly what you type, and the notes are read in the workspace
  by `cockpit diff --comments` instead.

## Bands and row-action kicks

- [bands.snoozed~1] Given a snoozed row whose nudge cell is set, the row
  paints no glyph at all — no 🔔 — and the hover text says snoozed; dropping
  the glyph did not drop the suppression.
- [rowaction.z-folds~1] Given a repo-scoped cycle, no `ReviewFolds` is built
  and the cross-repo reconcile is never reached — a scoped bucket would
  dissolve every other org's fold.
- [rowaction.z-tick~1] `_reconcile_review_groups` is unreachable from the
  fast tick's call graph, while `restore_trailing_folds` — the narrower,
  create-only replay — is reachable.

## Tickets screen

- [tickets-screen.widths~1] Given a fold opened by a rebuild that never
  yields to idle, a revealed Title is not clipped to the column label's
  width, and a routing marker never widens the Ticket column — widths are
  explicit and every cell is ellipsized to the cap its column is sized from.
