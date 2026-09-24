# TODO

## Open

- **Per-row context headroom** (branch `khivi/stale` on origin, no local worktree,
  nothing committed) — show, per table row, how close that agent is to needing a
  `/compact`.
  The Tauri rewrite (`khivi/cockpit-app`) grew a per-session token badge for this;
  don't copy its approach. It sums transcript JSONL because it has no statusLine
  hook into the session — we already cache the pre-computed numbers.
  `lib/claude.py`'s `stash_from_stdin` writes `context-<sid>` (`"<pct> <limit>"`)
  on every render.
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

- **Detecting an unbumped `~rev`** — a spec bullet's revision is hand-declared, so a bullet
  whose *meaning* changes without a bump leaves every claiming test silently vouching for
  the old claim, and `tests/test_invariant_coverage.py` cannot see it: the id still
  resolves, the revision still matches, the suite stays green. No instance found yet — the
  one marker defect so far was an id rewrite rotating five markers inside one file, which
  the two-directional gate caught. Reopen when a real one turns up.
  - **Not by hashing the id.** `covers("cache.flat-cells~a3f2")` fires on every cosmetic
    edit — bullets are hard-wrapped, so a reflow rewrites text that claims exactly what it
    claimed before — and a trigger that mostly fires for nothing gets its markers updated
    without the tests being re-read, which manufactures false assurance instead of leaving
    an honest gap. It also forces a re-stamp tool, and one command that re-stamps twelve
    markers asserts twelve re-verifications nobody performed: the move the `spec-audit`
    skill exists to refuse.
  - **If built**: a lockfile mapping id → hash of the normalized bullet text, checked by an
    advisory job reporting "text changed, revision didn't". Markers stay readable, a bullet
    edit stays a one-file diff, and it fails as a report rather than a merge block — so a
    blind update costs a stale line, not a false verification.

## Closed unbuilt

- **A generated `SPEC.md`** — rejected. The `covers()` markers now claim hand-owned
  `specs/*.md` bullets instead of quoting AGENTS.md, so the traceability matrix exists,
  but generating its prose from the markers would summarize the tests' own claims and
  review nothing. The spec stays hand-written; only the bootstrap was generated.

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
