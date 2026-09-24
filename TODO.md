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

- **An AGENTS.md rule with no bullet behind it** — nothing checks that a documented
  invariant is a *tested* one. The gate is three tests, all bullet ↔ marker; `/spec-audit`
  is bullet ↔ test; neither reads AGENTS.md. A **Never** can sit there fully documented,
  fully believed and entirely untested, and nothing anywhere goes red. The ledger was grown
  from AGENTS.md by hand, so today's coverage is whatever that one pass happened to catch,
  and nothing ratchets a *newly added* rule into having a bullet.
  - **Not a textual alignment check**, which is the obvious build and the wrong one.
    AGENTS.md states the scar and its **Never**; the bullet states the observable behavior
    the scar protects. They are different altitudes and deliberately never quote each other,
    so rewording either breaks nothing — that independence is a feature, and a check that
    forced them to agree would collapse one into the other. `test_comment_references.py`
    already covers the only thing that *should* be shared: that a backticked name in either
    still resolves.
  - **The checkable direction is coverage, not agreement**: count the `**Never**` /
    `**Do not**` lines per AGENTS.md section against the bullets in the matching `specs/`
    file, and flag sections sitting at zero.
  - **Advisory, never a gate.** The section→file mapping is not 1:1, and some rules are
    genuinely unassertable — which is what `(untested: …)` exists for. Gate it and the
    cheapest way green is a waiver per flagged rule, manufacturing exactly the false
    assurance the ledger is for. A report can be read and argued with; a block gets
    satisfied.
  - If built, it is a sibling script under the `spec-audit` skill (`scope.py`'s neighbour),
    not a mode of the audit itself — it answers a different question and needs none of the
    bullet↔test pairing.

- **Twelve findings from the first full-ledger audit, unfixed** — 19 findings over 127
  auditable bullets; the 7 whose missing half was already proven by an untagged sibling are
  closed (see `docs/specs.md`), leaving the ones that need a real new assertion. Each is a
  bullet whose claiming test proves less than the bullet says, so the gate is green and the
  claim is not held up.
  - **1 mismatch** — `ask.line~1`: the bullet's given is "pending diff comments", but the
    test builds a bare `AskScreen()` and never goes through `action_ask_row`. Vacuous
    against the regression it guards: re-couple comments into `AskScreen` and a bare one
    still opens empty. Needs an app-level test pressing `a` with comments seeded.
  - **The widest** — `cache.session-cells~1`: the AST ban scans `cockpit.py` +
    `orchestrators/*.py`, but AGENTS.md says the TUI *is* the daemon, so `cockpit/tui/`
    — the largest part of it — is unscanned. No violation today; the hole is latent.
  - **The rest** — `stacks.header~1`, `suite.isolation~1` (nothing proves `real_backend`
    actually opts a test out of the no-live-backend guard), `spawn.coworker-pr~1`,
    `cache.no-worktree-no-cells~1`, `table.links~1`, `globalkeys.sync~1`,
    `stdout.queue-writer~1`, `orphan.no-nudge~1`, `nudge-cli.split-chain~1`,
    `teardown.unlanded~1`.
  - **Do not** close any of these by rewording its bullet down to what the test asserts,
    or by waiving it — under-asserted is not unassertable.

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
