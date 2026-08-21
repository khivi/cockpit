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

**Bare `--context` is the one exception to "invoke verbatim"** — it means
"summarize this session", which only you can do. If `--context` appears with no
value after it, before calling Bash: write a concise 5–12 line summary of the
CURRENT session (goal, decisions already made, files touched, open questions,
relevant URLs/IDs), then invoke the command with that summary as the flag's
value — `--context '<summary>'`, single-quoted, embedded quotes escaped as
`'\''`. Print nothing before the Bash call; the summary is an argument, not a
message to the user. `--context <text>` already carrying a value passes through
untouched, and the CLI errors on a bare one, so the substitution is not optional.

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
- `--context [<text>]` — inject a summary of the current session into the new
  workspace's first-turn prompt. Bare = you write the summary (above); with
  text = that text is used verbatim.
- *(bare, no args)* — registers the cwd's repo (`use_worktree: false`) and
  opens an in-place workspace, no worktree.
- `-- <text...>` — trailing text appended to the auto-generated first-turn
  prompt.
