# Cache — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

- [inventory.identity.never-stored~1] (untested: design rationale) Inventory is
  re-derived every cycle from `git worktree list` and the workspace list; only
  network round-trips are cached, and no stored identity file exists.
- [cache.key.flat-cells-by-worktree-path~1] Every flat cell is keyed by worktree
  path (`cwd_cache`) or session id, never by branch; a PR with no local worktree
  writes no cells.
- [cache.renderer.never-reads-source-state~1] Renderers read cells the daemon
  wrote; no field printer reaches git, gh, or a subprocess.
- [cache.session-cells.daemon-never-writes~1] Session-scoped cells are written only
  by `stash_from_stdin`; the daemon reads `cost-<sid>` and writes none of them.
- [cache.strip-control.single-seam-in-read-text~1] Externally authored cell text is
  neutralized by `strip_control` inside `read_text`, the one seam every flat-cell
  renderer shares; control characters are replaced one-for-one, never dropped.
- [wt-cost.gate.data-not-plan~1] The `$` column is gated on cost data existing,
  never on a config field or plan detection; blank is not zero.
- [wt-cost.slug.one-way-only~1] `_claude_project_slug` is lossy — distinct paths
  can share a slug — so it is only ever walked forwards, worktree path →
  directory.
- [config.atomic-write.pid-scoped-suffix~1] The atomic-write temp file carries
  `os.getpid()`, so concurrent writers cannot land one process's content under
  another's name.
- [config.atomic-write.no-reinline~1] `_atomic_write_text` is the one writer in
  `config.py`; no call site re-inlines the write.
