# Teardown and diff — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Teardown

- [teardown.close-funnel~1] When cockpit closes a workspace, the call goes
  through `cmux_close_workspace_best_effort` — the funnel that records the
  self-close so `cmux events` reads it as cockpit's own, not the user's
  sidebar ✕ — never a raw close-workspace call.
- [teardown.unlanded~1] Given my own branch with unlanded commits, the commit
  guard blocks via `count_unlanded` and never consults the coworker baseline;
  given a coworker's branch whose commits exist only locally, it blocks via
  `commits_only_local`. Pushing clears neither.

## cockpit diff

- [diff.render~1] Given `cockpit diff --branch` run inside a worktree,
  `render_diff` names neither a workspace nor a surface, and passes the
  worktree root — not the invoking directory — as `cwd`.
- [diff.source-flags~1] Given any of `--branch`, `--staged`, `--unstaged`,
  `--last-turn`, the flag goes over as cmux's own `--source` with no patch,
  and `gh` is never reached.
- [diff.viewer~1] (untested: design rationale) There is no second in-overlay
  renderer and no `delta` dependency — both tried and removed.
- [diff.resolution~1] `diff.py` names neither `load_config` nor
  `_resolve_target`; resolution is `git.worktree_root` alone, so any git
  repo works whether or not it is registered.
- [diff.comments~1] Given a pending note, `--comments` prints it with the
  `--ack` hint, marks nothing, and opens no diff; `--ack` marks exactly the
  pending ids as delivered and puts the viewer tab away rather than opening
  one — while with nothing pending it closes nothing, since an open diff
  with no notes is one somebody is still reading.
