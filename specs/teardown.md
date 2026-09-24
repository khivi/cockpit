# Teardown and diff — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Teardown

- [teardown.close.never-raw-cmux~1] No call site closes a workspace raw; every
  close goes through `cmux_close_workspace_best_effort` or `teardown`.
- [teardown.unlanded.ownership-split-not-collapsed~1] The commit guard splits on
  ownership — my branch uses `count_unlanded` against the default branch, a
  coworker's uses `commits_only_local` — and pushing clears neither.

## cockpit diff

- [diff.render.no-second-caller-params~1] `render_diff` has exactly one caller and
  names neither a workspace nor a surface.
- [diff.source-flags.no-reimplemented-git~1] A source flag goes over as cmux's own
  `--source` and reaches no network; cockpit reimplements no git source.
- [diff.viewer.no-second-renderer~1] (untested: design rationale) There is no
  second in-overlay renderer and no `delta` dependency — both tried and removed.
- [diff.resolution.no-configured-repo-required~1] `cockpit diff` resolves by
  `git.worktree_root` alone; any git repo works unregistered.
- [diff.comments.split-not-merged~1] `--comments` prints and marks nothing;
  `--ack` retires. The two never merge into one call.
