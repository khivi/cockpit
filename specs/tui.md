# TUI — behavior spec

## Table

- [table.links~1] A `link` span in a rendered DataTable line comes out as a
  real OSC 8 escape. The test pins this against the actual render. The hover
  tooltip names the destination.
- [table.status-slot~1] A belled row, a muted row and a quiet row all start
  their label at the same cell column. The status glyph sits in a fixed-width
  slot. A glyphless row pays in blanks. The slot is measured in cells, not
  bytes.
- [table.ticket-link~1] The ticket cell's link is the `url` the daemon cached.
  A renderer never resolves its own. Asserted structurally: `worktree_table.py`
  never calls the live ticket-URL resolver.

## Header bar

- [header.countdowns~1] Given the cursor row's repo name growing much longer,
  the countdown segment's painted x-position does not move. `#header-repo`
  owns the bar's one flexible slot.
- [header.brand~1] `brand_text` links the version to exactly the URL it is
  handed. It carries no link at all when the URL is empty. The bar never
  drifts off the destination the palette's "What's new" opens.
- [header.guide-entry~1] The feature-guide action opens exactly
  `FEATURE_GUIDE_URL`. That URL is an `https://` URL. The action never opens a
  local file path.
- [header.tick-glyphs~1] The tooltip spells out both tick glyphs by word. It
  reads them from the same constants the bar renders. The two glyphs measure
  the same cell width. The pair never misaligns against itself.
- [header.repo-segment~1] (untested: design rationale) The header segment
  names the cursor row's repo. A Repo column never does. `DataTable.fixed_rows`
  never does.
- [header.upgrade-toast~1] (untested: design rationale) The upgrade toast
  compares cockpit against its own last run. It never becomes a version check,
  a `u` key, or a re-exec.
- [header.menu~1] (untested: design rationale) The header's menu segment is
  the palette's visible entry point. The header does not print the key beside
  it. The segment never moves into the footer.

## Palette, global keys, footer

- [palette.order~1] `discover` yields `COMMANDS` in tuple order. That order
  runs in-app overlays, then `$EDITOR`, then the browser pair. An entry
  appended to the end fails.
- [palette.discover~1] Every entry appears on an empty palette via
  `discover`. A provider that implements `search` alone shows none of
  cockpit's entries.
- [globalkeys.sync~1] The palette offers Output as its only in-app route. The
  palette does not offer Sync. Sync has the `s` key. Each action has one
  surface.
- [footer.tooltips~1] Hovering any footer segment, key or label, sets the
  bar's tooltip to that action's explanation. The tooltip reads the segment's
  own `@click` meta. The footer is one widget, never a widget per key.
- [hidden.h-key~1] (untested: design rationale) `h` is the one park key. It
  expands or collapses the hidden section, un-parks a revealed repo, or parks
  the cursor row's repo. A second key for reveal never exists.

## App plumbing

- [tui.on-repo-done~1] Given the slow tick's per-repo hook firing, no cache
  write occurs. The republish repaints the table and writes nothing.
- [tui.signals~1] Signal handlers install through `loop.add_signal_handler`.
  Installing one off the main thread cannot raise. Asserted structurally:
  `signal.signal(` appears nowhere in `tui/app.py`.
- [tui.diff-key~1] No row key opens a diff. The `d` key does not exist.
  Asserted structurally: `render_diff` appears nowhere under `cockpit/tui/`.
- [tui.nudge-key~1] (untested: design rationale) There is no manual nudge key
  with a canned message. A manual send is `a`'s typed line.
- [stdout.queue-writer~1] One process-wide `_QueueWriter` captures every tick
  print. Asserted structurally: `redirect_stdout(` appears nowhere under
  cockpit/.
- [ask.line~1] Given pending diff comments, the ask box still opens empty. `a`
  sends exactly what you type. `cockpit diff --comments` reads the notes in
  the workspace instead.

## Bands and row-action kicks

- [bands.snoozed~1] Given a snoozed row whose nudge cell is set, the row
  paints no glyph at all, and no 🔔. The hover text says snoozed. Dropping the
  glyph does not drop the suppression.
- [rowaction.z-folds~1] Given a repo-scoped cycle, no `ReviewFolds` is built.
  The cross-repo reconcile is never reached.
- [rowaction.z-tick~1] The fast tick never rebuilds a trailing fold from its
  network-free inputs. It only replays a standing record. Asserted
  structurally: `_reconcile_review_groups` is unreachable from the fast tick's
  call graph. The create-only `restore_trailing_folds` is reachable.

## Tickets screen

- [tickets-screen.widths~1] Given a fold opened by a rebuild that never
  yields to idle, a revealed Title is not clipped to the column label's width.
  A routing marker never widens the Ticket column. Widths are explicit. Every
  cell is ellipsized to the cap its column is sized from.
