---
description: "Create a git worktree + workspace for a new branch, existing PR, or Slack thread."
argument-hint: "<branch|PR|url> | --pr N | --branch X | --cwd P | --skill S [--repo R] [--name X] [--context] [-- <text...>]"
allowed-tools: Bash
---

Invoke the Bash tool with this exact command, then paste its stdout verbatim
(don't paraphrase, and don't claim success without a
`workspace <name> spawned at <path>` / `attached existing workspace <name>`
line in the output):

```bash
cockpit new $ARGUMENTS
```

**Bare `--context` is the one exception to "invoke verbatim"** — it means "hand
over what the new workspace needs", which only you can do. If `--context`
appears with no value after it, write that hand-over before calling Bash and
pass it as the flag's value — `--context '<text>'`, single-quoted, embedded
quotes escaped as `'\''`. Print nothing before the Bash call; it is an
argument, not a message to the user. The CLI errors on a bare `--context`, so
the substitution is not optional.

**Select, don't summarize.** The new workspace is starting one task, not
resuming this conversation. Carry only what it cannot re-derive from the repo
it is about to open: the goal in a sentence, decisions already made and why,
approaches already ruled out, and exact identifiers — PR numbers, ticket keys,
URLs, file paths, error strings. Leave out what it will read for itself (file
contents, diffs, command output), how this session arrived at the goal, and
anything about unrelated topics worked on here. 5–12 lines, and one honest
line beats padding when there is little to hand over.

**`--context '<text>'` scopes the hand-over, it does not replace it.** The text
is what the caller knows the new workspace needs: keep it verbatim, then add
only the session material bearing on it, under the rule above. If nothing here
bears on it, the text passes through alone.

`cockpit new` is idempotent — re-running against an existing branch/PR
attaches to its worktree + workspace instead of erroring. The seeded prompt
runs in the **new workspace**, not this session; after reporting the result,
stop.

Reference (see `cockpit new --help` for the full list):

- `<branch|PR|url>` — auto-detected: GitHub PR URL/`#N`, GitHub issue URL,
  GitHub Actions run URL, Slack thread permalink, Trello card URL, a Linear or
  Jira ticket ID *or* issue URL, or a branch name.
- `--branch <name>` / `--pr <num>` — explicit source (mutex with the
  positional and each other).
- `--repo <name>` — target a configured repo by name.
- `--name <short>` (with `--repo` or `--cwd`) — new branch/workspace short
  name.
- `--cwd <path>` — arbitrary dir, no repo, no branch.
- `--skill <name>` — spawn a workspace running a global or repo skill.
- `--context [<text>]` — inject what the new workspace needs from the current
  session into its first-turn prompt. Bare = you select it (above); with text =
  that text is kept and scopes what else you select.
- *(bare, no args)* — registers the cwd's repo (`use_worktree: false`) and
  opens an in-place workspace, no worktree.
- `-- <text...>` — trailing text appended to the auto-generated first-turn
  prompt.
