# Cache — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

- [inventory.derived~1] (untested: design rationale) Inventory is re-derived
  every cycle from `git worktree list` and the workspace list; only network
  round-trips are cached, and no stored identity file exists.

## Flat cells

- [cache.flat-cells~2] Given two worktrees in different repos on the same
  branch label, when the daemon writes their PR and base-distance cells, each
  worktree reads back its own values — a key that collapses to the branch
  merges unrelated repos' rows.
- [cache.no-worktree-no-cells~1] Given a PR snapshot stamped with no local
  worktree, the flat republish writes no cell for it — no row, no session,
  nowhere to key one — while the JSON snapshot is still written and served.
- [cache.strip-control~1] Given a `pr-title` cell carrying its own OSC 8
  escape sequence, `read_text` returns it with no ESC byte and one replacement
  character per control byte — same codepoint count, visibly tampered.
- [cache.session-cells~1] No write of the six session-cell stems (`context`,
  `rate-limit-5h`, `model`, `permission-mode`, `transcript-path`, `cost`)
  appears anywhere in the daemon — `stash_from_stdin` is the sole writer, and
  the daemon's one read of them is `cost-<sid>`.
- [cache.renderer-readonly~2] `lib/starship.py` references no `subprocess`,
  no `atomic_write`, imports nothing from `gh`, and touches nothing in
  `git.py` beyond the `GitStatusCounts` data type.

## Worktree cost

- [wt-cost.gate~2] `cost_reporting_available` stays False while every session
  reports zero and flips True on one real number; no config field or plan
  detection is consulted, and a costless worktree renders blank, not `$0`.
- [wt-cost.slug~1] `/opt/dev/repo.wt` and `/opt/dev/repo-wt` resolve to one
  slug, so the map has no inverse and is only ever walked forwards, worktree
  path → directory.

## Atomic writes

- [config.tmp-suffix~1] Given two processes atomically writing the same
  config path, each temp file's name carries its own `os.getpid()`, so the
  loser cannot land its whole content under the winner's name.
- [config.write-funnel~1] `os.replace` is called in exactly one function
  under cockpit/ — `_atomic_write_text`; sibling state dirs that repeat the
  pid-suffix pattern go through `Path.replace` instead.
