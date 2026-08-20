# Respawn — design notes (no code here)

This is a design document only. There is no `cockpit respawn` command, no CLI
flag, no skill. It exists to write down what "respawn a session" would mean
before anyone builds it, since the obvious motivation — "I edited a skill,
make the running sessions pick it up" — turns out not to be as simple as it
sounds.

## What respawn would mean mechanically

Close the cmux workspace at a worktree and spawn a fresh one at the same
path, distinct from `cockpit close` (which also removes the worktree/branch).
The pieces already exist and would be reused, not rebuilt:

- **Close, workspace only.** `cockpit.lib.cmux.cmux_close_workspace_best_effort(ref)`
  is exactly this primitive — it's what `orchestrators/teardown.py::teardown`
  calls before (separately) deciding whether to remove the worktree. A
  respawn would call this and stop, never reaching `remove_worktree`.
- **Spawn, fresh.** `cockpit.lib.cmux.spawn_pr_workspace(pr, wt, ...)` and
  `spawn_orphan_workspace(wt, ...)` are the two existing entry points that
  create a new workspace at an existing worktree and seed its first-turn
  prompt (`build_pr_prompt` / `build_orphan_prompt`). `cycle.py::_spawn_missing_workspaces`
  already calls exactly these two functions today, for the "worktree exists,
  no workspace" case — a respawn is mechanically the same case, just reached
  by closing an existing workspace first instead of finding it already
  missing.

So the shape is: `cmux_close_workspace_best_effort(ref)` →
`spawn_pr_workspace(pr, wt, ...)` or `spawn_orphan_workspace(wt, ...)`,
depending on whether the worktree is PR-tracked. No new primitive is needed
at the git/cmux layer.

## What is irreversibly lost: the conversation

This is the central fact, and it's why respawn isn't a casual operation.
Closing a workspace and spawning a new one at the same path is **not a
reload** — it's a brand new Claude Code session with a brand new
conversation. Everything in the closed session's context — the discussion
that led to the current state of the branch, decisions made and not yet
written down anywhere, in-progress reasoning — is gone. The new session's
only knowledge of prior work is whatever is durable on disk: the git history,
the worktree's current file contents, and whatever the seeded first-turn
prompt says (a PR's `build_pr_prompt` recaps the PR/issue, not the session
that wrote it). A respawn is closer to "fire the assistant and hire a new one
who reads the file" than "reload the page."

## Would it even solve the motivating problem?

The motivating case is: a skill or slash command file got edited, and the
running session should pick up the new version. Two things are relevant, one
verified and one not:

- **Verified, from `cockpit/spawn.py::resolve_skill`:** when cockpit seeds a
  new workspace's first turn with a skill, it hands Claude Code the bare
  string `/{name}` — not the file's contents. Cockpit never reads or inlines
  `SKILL.md`/command-markdown content into the prompt it sends; it only
  references the command by name and trusts whatever picks that up to
  resolve it from disk at invocation time. So *if* a fresh session resolves
  `/{name}` by reading the file at the moment the command runs, a respawned
  session would see the edit — cockpit's own prompt-seeding code imposes no
  staleness of its own.
- **Unverified:** whether an *already-running* session would also pick up an
  edited skill/command file the next time it invokes that command, without
  needing a respawn at all. That resolution behavior belongs to Claude Code
  itself, not to anything in this repository, and nothing here confirms or
  rules it out. If an existing session already re-reads command files per
  invocation, respawning it buys nothing but the conversation-loss cost
  above — the whole feature would be solving a problem that doesn't exist.
  **This should be checked first**, before writing any respawn code: try
  editing a skill file and invoking it again in an already-open session.

## The self-workspace hazard

Respawning the workspace the daemon/TUI itself runs in would kill the
dashboard mid-operation — there is no daemon left to finish the respawn it
started. `cockpit.lib.cmux.workspace_cwds()` already excludes the caller's
own workspace by default (`include_self=False`, keyed on
`$CMUX_WORKSPACE_ID`) — see `broadcast.py` for a design that leans on this
for free. Any respawn implementation, whether it enumerates targets via
`workspace_cwds()` or resolves a single target some other way, **must**
either reuse that same self-exclusion or add an explicit equivalent check.
This is not optional — it's the difference between "closed a stale session"
and "the daemon closed itself and nothing is running to notice."

## The idle gate's role

Respawning a workspace mid-turn doesn't just interrupt a `send` the way a
missed nudge does — it discards whatever that turn was doing, along with
everything the prior conversation established (see above). So a respawn path
must gate at least as strictly as `cockpit broadcast` does today, i.e. it
should refuse the same `Running` / ambiguous-`Needs input` states that
`cockpit.lib.cmux.nudge_if_idle` already refuses, reusing that gate rather
than writing a new one (see the "Nudge idle-gate" and "`cockpit broadcast`
reuses the nudge gate" notes in `AGENTS.md`). Given how much more a respawn
discards than a skipped nudge, "idle" alone may not be enough — this is a
case where confirming with the user before acting (rather than a passive
skip-and-report like `broadcast`) is probably the right default, not an
afterthought.

## Open questions, before anyone builds this

- Does an already-running session re-read an edited skill/command file on
  its next invocation? (See "unverified" above — answering this might
  remove the motivation for respawn entirely.)
- If conversation history is genuinely lost, what would be handed to the new
  session so it doesn't start from zero? A summary written by the closing
  session before it's killed? The existing PR/orphan seed prompts
  (`build_pr_prompt` / `build_orphan_prompt`) already recap the PR/branch —
  is that recap sufficient, or does losing the discussion actually cost
  real work?
- Who initiates it — a TUI row action (like `cockpit broadcast`'s fan-out
  shape) or a targeted CLI command against one worktree (like `cockpit
  close`)? A fan-out respawn multiplies the conversation-loss cost by every
  workspace it touches, which argues for a single-target design, not a
  broadcast-shaped one.
- Does confirmation happen per-workspace, or once for a batch? A silent
  passive-skip (broadcast's model) seems wrong here given the stakes; some
  kind of explicit confirm-before-acting seems closer to right, but the
  exact UX isn't decided.
