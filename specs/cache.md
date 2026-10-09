# Cache — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

- [inventory.derived~1] (untested: design rationale) The daemon re-derives
  inventory every cycle from `git worktree list` and the workspace list. It
  caches only network round-trips. No stored identity file exists.

## Flat cells

- [cache.flat-cells~2] Given two worktrees in different repos on the same
  branch label, the daemon writes their PR and base-distance cells. Each
  worktree reads back its own values. A key never collapses to the branch.
- [cache.no-worktree-no-cells~1] Given a PR snapshot stamped with no local
  worktree, the flat republish writes no cell for it. The republish still
  writes and serves the JSON snapshot.
- [cache.stale-stamp~1] Given a PR snapshot stamped with a worktree that now
  holds another branch, the flat republish writes none of its
  cells. It clears that worktree's PR cells when no snapshot for the branch
  it now holds is stamped there. `find_pr_payload_for_cwd` does not serve it.
- [cache.stamp-fallback~1] Given no snapshot stamped with a worktree for its
  branch, `find_pr_payload_for_cwd` serves an unstamped snapshot on that
  branch. It never serves a snapshot stamped with another worktree.
- [cache.strip-control~1] Given a `pr-title` cell that carries its own OSC 8
  escape sequence, `read_text` returns it with no ESC byte. It returns one
  replacement character per control byte. The codepoint count stays the same.
  The tampering stays visible.
- [cache.session-cells~1] No write of the six session-cell stems (`context`,
  `rate-limit-5h`, `model`, `permission-mode`, `transcript-path`, `cost`)
  appears anywhere in the daemon. `stash_from_stdin` is the sole writer. The
  daemon's one read of them is `cost-<sid>`.
- [cache.renderer-readonly~2] A field printer reads cells and nothing else. It
  reads no source state and writes no cache. Asserted structurally:
  `lib/starship.py` references no `subprocess` and no `atomic_write`. It
  imports nothing from `gh`. It touches nothing in `git.py` beyond the
  `GitStatusCounts` data type.

## Worktree cost

- [wt-cost.gate~2] `cost_reporting_available` stays False while every session
  reports zero. It flips True on one real number. It consults no config field
  and no plan detection. A costless worktree renders blank, not `$0`.
- [wt-cost.slug~1] `/opt/dev/repo.wt` and `/opt/dev/repo-wt` resolve to one
  slug. The map has no inverse. The code walks it only forwards, from worktree
  path to directory.

## Atomic writes

- [config.tmp-suffix~1] Given two processes that atomically write the same
  config path, each temp file's name carries its own `os.getpid()`. The loser
  never lands its whole content under the winner's name.
- [config.write-funnel~1] Every config write lands through the one pid-scoped
  writer. No call site reintroduces a fixed temp suffix. Asserted
  structurally: `os.replace` is called in exactly one function under
  cockpit/, `_atomic_write_text`. Sibling state dirs that repeat the pid-suffix
  pattern go through `Path.replace` instead.
