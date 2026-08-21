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
  `stash_from_stdin` writes `context[-$sid]` (`"<pct> <limit>"`) on every render.
  - Use `context`, not tokens and not `cost`. Cumulative tokens are near-redundant
    with the `rate-limit-5h` gauge; `cost` is `total_cost_usd`, which the app dropped
    on the grounds that Claude Code writes no cost on a Max plan — verify it's
    non-zero here before trusting that column.
  - The work is **keying, not reading**: caches are keyed by Claude Code `session_id`,
    a table row is a worktree path. `cwd_cache(stem, cwd)` (`lib/cache.py:322`) is the
    existing primitive, and the statusLine blob carries the cwd. One extra
    `atomic_write` in `stash_from_stdin`, one column in `column_labels`
    (`tui/widgets/worktree_table.py:199`), one cache read per row per cycle — no
    subprocess, so none of the per-row cost that killed the blocked-state token.
  - **Two things to settle first.** (1) Staleness: the cache only updates when that
    session's statusline renders, so an idle or exited agent leaves a frozen value —
    needs an mtime TTL, or the column lies, which is worse than no column. (2) It
    hard-depends on the **optional** cship statusline; without it `statusline.py`
    never runs and the caches never exist, so the column is blank for anyone who
    declined it at `cockpit setup`. Gate on cache presence, or accept it as
    statusline-users-only.

- **Hover explanations** (`khivi/tooltip`) — **answer the open question before
  building.** The app puts a tooltip on every badge and glyph; the equivalent here is
  the adaptive footer hint bar, which may already do the job better in a
  keyboard-driven TUI where there's no pointer resting anywhere. If the answer is
  "the footer is enough," close the branch and record that — it's a real answer.
  Only the *problem* the app hit is worth carrying over regardless: a CSS `::after`
  tip inside a scrolling container gets clipped on both axes, which is why
  `src/lib/tooltip.ts` is a single measured `position: fixed` node instead.

## Done

- **Ticket title in PR cache** — the delivery block (`payload["ticket"]`, renamed
  from `linear`) now carries a provider-neutral `title` per ticket
  (`provider.fetch_titles`, Linear/Jira/Trello/GitHub), so cship (or any
  consumer) reads the ticket name from `~/.config/cockpit/cache/{repo}__pr-{N}.json`
  without its own API call. Rendering the title in the statusline is cship's job.
