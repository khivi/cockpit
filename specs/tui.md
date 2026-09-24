# TUI — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Table

- [table.links~1] Every cell naming a web destination is an OSC 8 hyperlink, the
  escape survives all the way into Textual's terminal output, and the hover
  tooltip names the destination.
- [table.status-slot~1] The status glyph sits in a fixed-width slot that a
  glyphless row pays in blanks, so glyph ink-width differences cannot misalign
  the column.
- [table.ticket-link~1] The ticket cell's link reads the cached per-ticket `url`
  the daemon stamped; the renderer never resolves a URL itself.

## Header bar

- [header.countdowns~1] `#header-repo` owns the bar's one growing slot, so the
  tick countdowns to its right do not move when the cursor changes repo.
- [header.brand~1] The brand segment takes its release-notes URL as an argument
  (the `version_url` reactive), so the linked version cannot drift off the
  destination the palette's "What's new" opens.
- [header.guide-entry~1] The feature-guide entry opens `FEATURE_GUIDE_URL` in
  the browser, never a local file path.
- [header.tick-glyphs~1] Every glyph the bar shows is spelled out by word in the
  tooltip, fed from the same constants.
- [header.repo-segment~1] (untested: design rationale) The cursor row's repo is
  named by the header segment, never by a Repo column or `DataTable.fixed_rows`.
- [header.upgrade-toast~1] (untested: design rationale) The upgrade toast
  compares cockpit against its own last run and never becomes a version check, a
  `u` key, or a re-exec.
- [header.menu~1] (untested: design rationale) The palette's visible entry point
  is the header's menu segment; the key is not printed beside it and the segment
  never moves into the footer.

## Palette, global keys, footer

- [palette.order~1] `discover` yields `COMMANDS` in tuple order, so the tuple is
  the menu; an entry slotted out of order fails.
- [palette.discover~1] Every palette entry is reachable while the search box is
  empty — `discover` yields it, not only `search`.
- [globalkeys.sync~1] Sync is the `s` key; the output overlay is a palette entry
  only, with no key of its own.
- [footer.tooltips~1] Footer key hints explain themselves on hover via
  `TOOLTIPS`, matched off the segment's own `@click` meta — one widget, not a
  widget per key.
- [hidden.h-key~1] (untested: design rationale) `h` is the one park key:
  expand/collapse the hidden section, un-park a revealed repo, or park the
  cursor row's repo — never a second key for reveal.

## App plumbing

- [tui.on-repo-done~1] The slow tick's per-repo `on_repo_done` hook republishes
  the table and writes no cell.
- [tui.signals~1] Signal handlers install via `loop.add_signal_handler` only;
  `signal.signal` never appears in the TUI.
- [tui.diff-key~1] No row key pipes into `cmux diff`; `cockpit diff` is the only
  diff entry point.
- [tui.nudge-key~1] (untested: design rationale) There is no manual nudge key
  with a canned message; a manual send is `a`'s typed line.
- [stdout.queue-writer~1] All tick prints go through the one process-wide
  `_QueueWriter`; nothing redirects stdout per tick.
- [ask.line~1] `a` carries the typed line and nothing else; pending diff
  comments are never appended to it.

## Bands and row-action kicks

- [bands.snoozed~1] A snoozed row shows no glyph yet still suppresses the 🔔; 🔇
  wins for a row muted and snoozed.
- [rowaction.z-folds~1] `z` kicks a full cycle: `ReviewFolds` is built only when
  `only_repo is None`, never under a repo-scoped kick.
- [rowaction.z-tick~1] The fold pass runs on the slow tick; the fast tick's
  network-free inputs never decide fold membership.

## Tickets screen

- [tickets-screen.widths~1] The inbox columns take explicit widths, each cell
  ellipsized to the cap the column is sized from; `DataTable` auto-sizing is
  never used.
