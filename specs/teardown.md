# Teardown and diff — behavior spec

## Teardown

- [teardown.close-funnel~1] When cockpit closes a workspace, the call goes
  through `cmux_close_workspace_best_effort`. That funnel records the
  self-close. `cmux events` reads the close as cockpit's own and never as the
  user's sidebar ✕. The call never goes through a raw close-workspace call.
- [teardown.unlanded~1] Given my own branch with unlanded commits, the commit
  guard blocks via `count_unlanded`. It never consults the coworker baseline.
  Given a coworker's branch whose commits exist only locally, the guard blocks
  via `commits_only_local`. Pushing clears neither block.
- [teardown.mute-holds~1] Given a clean worktree whose PR merged, autoclose
  skips it while the PR is muted, mine or a coworker's, and logs the skip. A
  snoozed PR, or one with no pref, is torn down as before.

## cockpit diff

- [diff.render~1] Given `cockpit diff --branch` run inside a worktree,
  `render_diff` names neither a workspace nor a surface. It passes the
  worktree root as `cwd`. It never passes the invoking directory.
- [diff.pr-fallback~1] Given no PR on the branch, the default opens a local
  diff and does not exit. It names which diff it substituted. On a trunk
  branch the substitute is `--unstaged`. On any other branch it is `--branch`.
- [diff.source-flags~1] Given any of `--branch`, `--staged`, `--unstaged` or
  `--last-turn`, the flag goes over as cmux's own `--source` with no patch.
  The command never reaches `gh`.
- [diff.viewer~1] (untested: design rationale) There is no second in-overlay
  renderer. There is no `delta` dependency. Both were tried and removed.
- [diff.resolution~1] `cockpit diff` works in any git repo, registered or not.
  Resolution is `git.worktree_root` alone. Asserted structurally: `diff.py`
  names neither `load_config` nor `_resolve_target`.
- [diff.comments~1] Given a pending note, `--comments` prints it with the
  `--ack` hint. It marks nothing and opens no diff. `--ack` marks exactly the
  pending ids as delivered. It puts the viewer tab away and never opens one.
  Given nothing pending, `--ack` closes nothing.
- [diff.comments-anchor~1] A printed note carries the side that its line number
  belongs to. It carries the text that the note was written against. A dragged
  range keeps its span. A record with none of the three prints as it did
  before they existed.
- [diff.comments-neutralized~1] The output neutralizes the anchored text.
