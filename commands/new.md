---
description: "Create a git worktree + workspace for a new branch, existing PR, or Slack thread."
argument-hint: "<branch|PR|url|slack-url> | --pr N | --branch X | --cwd P | --skill S [--repo R] [--name X] [--context] [-- <text...>]"
model: haiku
allowed-tools: Bash
---

# /cockpit:new

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase, summarize, or skip. Do not respond with any text before the Bash result is in. After Bash returns, paste its stdout verbatim — do not interpret or assume the spawn succeeded based on intent.

```bash
cockpit new $ARGUMENTS
```

**`--context` handling — the one exception to "invoke verbatim".** If `--context` is among the arguments, before calling Bash you must:

1. Write a concise summary (≈5–12 lines) of the CURRENT session: the goal, key decisions already made, files touched, open questions, and any relevant URLs/IDs. This is what the spawned workspace should inherit so it doesn't start cold.
2. Invoke spawn.py with `--context` **removed** and `--context-text '<your summary>'` added in its place. Single-quote the value and escape embedded single quotes as `'\''`. `spawn.py` does not understand a bare `--context` flag, so you must perform this substitution — passing `--context` through verbatim will error.

You still print nothing before the Bash call — the summary goes into the command's `--context-text` argument, not into a message to the user. `--context-text` is injected into the new (or attached) workspace's first-turn prompt under a "Caller session context" heading.

If the Bash result does not include a line matching `workspace <name> spawned at <path>` (or `attached existing workspace <name>`), treat the spawn as failed and surface the error to the user. Do not say "the workspace should be setting up" without proof.

After you report the spawn result, STOP — end your turn. The task runs in the **spawned workspace**, not here: `spawn.py` seeds a first-turn prompt (plan-only for a PR / Linear / Actions / `--context` / `-- <text>` source; none for a blank `<name> --repo` spawn, which starts ready for the user) into the new workspace's Claude, which executes it autonomously. Your entire job in this session is to spawn and report. Do NOT carry out the task in the caller session — no `gh`, no `git diff`/`git show`, no PR assessment, no file reads on the target repo, no planning, no edits. Even if the user's request reads like "do X", invoking `/cockpit:new` delegates X to the new workspace; performing X here is the bug this rule prevents. Focus into the workspace from the `cockpit watch` TUI (`f` on its row) if you want to watch it work.

## Arguments (reference only — do not act on these)

- Positional `<branch|PR|url>` — auto-detected (GitHub PR URL, GitHub Actions run/job URL, Slack thread permalink, `#123` PR ref, Linear key, or branch name). Mutex with `--branch`/`--pr`/`--name`/`--skill`. Actions URLs always spawn a fresh `ci-…` investigation worktree (`ci-<job-name>` for a job URL, else `ci-<workflow>-<title>`, falling back to `ci-<workflow>-<sha>`/`<run-id>`) — never attach to the run's head branch — that would collide with the main repo checkout when CI failed on master); the prompt fetches `--log-failed` first and surfaces the original head branch.
- Slack thread permalinks (`https://<workspace>.slack.com/archives/<CH>/p<TS>` or the `app.slack.com/client/...` deep link) spawn a worktree on a deterministic codename branch (`<prefix><adj>-<noun>`, e.g. `khivi/cosmic-otter`, derived from the thread's stable identity so re-spawning the same URL is idempotent). With `use_slack: true` the first-turn prompt instructs Claude to read the thread via the Slack MCP and rename the branch + workspace to append a topic slug (`cosmic-otter` → `cosmic-otter-fix-oauth`); the thread URL is seeded as context either way.
- `--branch <name>` / `--pr <num>` — explicit input; strictly mutex with each other, with the positional source, `--name`, and `--skill` (pick exactly one source)
- `--name <short>` — workspace short name; alone, also seeds a new branch name
- *(no arguments)* — bare spawn registers the **current git repo** in `~/.config/cockpit/config.json` (marked `in_place: true`, so the daemon shows its row but never auto-spawns worktrees for it) and opens an in-place workspace on the current branch — **no worktree**. The "cd into a dir and go" path for repos you don't want worktrees for (master-only or off-GitHub work). Off-GitHub and master-only repos register fine (empty branch prefix, base falls back to git `symbolic-ref` / `main`). Errors if cwd is not a git repo (use `--cwd <path>` for an arbitrary non-repo dir). An already-registered repo is reused as-is — a bare spawn in a normal managed repo does NOT flip it to `in_place`.
- `--cwd <path>` — arbitrary dir, no repo or worktree
- `--skill <name>` — run a global (`~/.claude/skills/`) or repo (`<repo>/.claude/skills/`) skill; cwd defaults to `$HOME` (global) or the repo path (repo skill)
- `--repo <name>` — universal override targeting a configured repo by name. With `--skill`, sets workspace cwd to that repo's path even when the global skill wins resolution
- `--context` — capture the current session's context. The skill summarizes the live session and forwards it as `--context-text` (see the **`--context` handling** section above). Combine with any source.
- `-- <text...>` — trailing text after `--` is appended to the auto-generated first-turn prompt (plan-only / skill / Linear MCP). Useful for layering extra context onto the seeded prompt. Supplying `-- <text>` on an otherwise-blank spawn also flips it into plan-only (the text is the task to plan).

Plan-only is seeded only when there's something to study first — a PR, a Linear ticket, `--context`, or `-- <text>`. A blank spawn (`/cockpit:new <name> --repo <repo>` with none of those) starts ready to work on with no seeded plan prompt; any configured `prompt_prefix` (e.g. a session-setup skill) still runs.

`spawn.py` is idempotent — an existing worktree + workspace for the same branch attaches instead of erroring. When attaching to an **existing** workspace, the seeded prompt (PR-action / plan / `-- <text>` / `--context-text`) is delivered into the already-running Claude via the active workspace backend's `send` + Enter (`cmux send` / `limux send`) — so re-spawning with new instructions actually reaches the session instead of being silently dropped. Errors with exit 1 if `--repo` names a repo not in `~/.config/cockpit/config.json` (the error lists the configured repos inline).

## Examples

```text
/cockpit:new fix-login                               # branch (local, remote, or new)
/cockpit:new https://github.com/org/repo/pull/12345  # PR by URL
/cockpit:new https://github.com/org/repo/actions/runs/123/job/456  # Actions failure by URL
/cockpit:new https://acme.slack.com/archives/C0123/p1700000000123  # Slack thread → codename branch
/cockpit:new --pr 12345 --repo myrepo                # PR by number in a named repo
/cockpit:new                                         # bare: register cwd's repo (in_place) + in-place workspace, no worktree
/cockpit:new --cwd ~/scratch/spike                   # arbitrary dir, no repo
/cockpit:new --skill <skill-name>                    # global skill, cwd = $HOME
/cockpit:new --skill <skill-name> --repo myrepo      # skill + cwd = myrepo
```
