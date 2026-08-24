# TODO

## From the cockpit-app audit (2026-08-21)

`khivi/cockpit-app` is the Tauri rewrite (see its `FUTURE.md`). Its feature set was
audited against this repo on 2026-08-21 and split three ways: 11 items are app-only
and structural (it owns its own terminal, so theming, fonts, clipboard, scrollback
and splits don't transfer); a longer list runs the *other* way — things this repo has
that the app still needs, tracked in the app's own `FUTURE.md`, not here. What
follows is the only bucket that lands in this repo.

Both items are already cut as worktrees off `.bare`. Neither is committed to yet —
the second one may well be closed unbuilt.

- **Per-row context headroom** (`khivi/stale`) — show, per table row, how close that
  agent is to needing a `/compact`. The app grew a per-session token badge for this;
  don't copy its approach. It sums transcript JSONL because it has no statusLine hook
  into the session — we already cache the pre-computed numbers. `lib/claude.py`'s
  `stash_from_stdin` writes `context-<sid>` (`"<pct> <limit>"`) on every render.
  - Use `context`, not tokens. Cumulative tokens are near-redundant with the
    `rate-limit-5h` gauge.
  - The work is **keying, not reading**: caches are keyed by Claude Code `session_id`,
    a table row is a worktree path. `cache.cwd_cache(stem, cwd)` is the existing
    primitive, and the statusLine blob carries the cwd. One extra `atomic_write` in
    `stash_from_stdin`, one column in `worktree_table.column_labels`, one cache read
    per row per cycle — no subprocess, so none of the per-row cost that killed the
    blocked-state token.
  - **The `$` cost column already walked this path** — same session→worktree keying
    (`cache.write_worktree_cost_cache` folds `cost-<sid>` into `wt-cost-<cwd>` on the
    fast tick), and it settled the "what if the statusline isn't installed" question:
    gate the column on the data (`cache.cost_reporting_available`), never on config,
    and render blank rather than zero, because *absent* and *none* aren't the same
    claim. Copy that shape.
  - **One thing still to settle.** Staleness: the cache only updates when that
    session's statusline renders, so an idle or exited agent leaves a frozen value.
    Needs an mtime TTL, or the column lies — which is worse than no column. Cost
    dodged this (a stale total is still the total spent); a stale *headroom* reading
    is wrong.

- **Hover explanations** (`khivi/tooltip`) — **answer the open question before
  building.** The app puts a tooltip on every badge and glyph; the equivalent here is
  the adaptive footer hint bar, which may already do the job better in a
  keyboard-driven TUI where there's no pointer resting anywhere. If the answer is
  "the footer is enough," close the branch and record that — it's a real answer.
  Only the *problem* the app hit is worth carrying over regardless: a CSS `::after`
  tip inside a scrolling container gets clipped on both axes, which is why
  `src/lib/tooltip.ts` is a single measured `position: fixed` node instead.

## Closed unbuilt

- **Respawn a session** — close a worktree's workspace and spawn a fresh one at the
  same path, so a session picks up an edited skill file. Not building it. The
  primitives all exist (`cmux_close_workspace_best_effort` →
  `spawn_pr_workspace` / `spawn_orphan_workspace`, the pair
  `_spawn_missing_workspaces` already calls), so the code was never the cost — the
  cost is that a respawn discards the entire conversation, every decision not yet
  written to disk, in exchange for a session that knows only what's on disk plus its
  seed prompt.

  **It rests on one assumption**: that an already-running session re-reads an edited
  skill/command file on its next invocation. Assume it does, and the motivating case
  is already handled — there is nothing left to build. Cockpit adds no staleness of
  its own either way; `spawn.py::resolve_skill` seeds the bare string `/{name}`,
  never the file's contents. Reopen this only if that assumption turns out false.

  If it ever is built: reuse `nudge_if_idle`'s gate rather than writing a second one,
  confirm explicitly instead of passive-skipping (a respawn discards far more than a
  missed nudge does), and honour `workspace_cwds(include_self=False)` or the daemon
  respawns itself and nothing survives to finish the job. (Was `docs/respawn.md`,
  deleted 2026-08-24.)

## Done

- **Ticket title in PR cache** — the delivery block (`payload["ticket"]`, renamed
  from `linear`) now carries a provider-neutral `title` per ticket
  (`provider.fetch_titles`, Linear/Jira/Trello/GitHub), so cship (or any
  consumer) reads the ticket name from `~/.config/cockpit/cache/{repo}__pr-{N}.json`
  without its own API call. Rendering the title in the statusline is cship's job.
