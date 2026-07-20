---
description: "Create a git worktree + workspace for a new branch, existing PR, or Slack thread."
argument-hint: "<branch|PR|url> | --pr N | --branch X | --cwd P | --skill S [--repo R] [--name X] [-- <text...>]"
allowed-tools: Bash
---

Invoke the Bash tool with this exact command, then paste its stdout verbatim
(don't paraphrase, and don't claim success without a
`workspace <name> spawned at <path>` / `attached existing workspace <name>`
line in the output):

```bash
cockpit new $ARGUMENTS
```

`cockpit new` is idempotent — re-running against an existing branch/PR
attaches to its worktree + workspace instead of erroring. The seeded prompt
runs in the **new workspace**, not this session; after reporting the result,
stop.

Reference (see `cockpit new --help` for the full list):

- `<branch|PR|url>` — auto-detected: GitHub PR URL/`#N`, GitHub Actions run
  URL, Slack thread permalink, Linear ID, or branch name.
- `--branch <name>` / `--pr <num>` — explicit source (mutex with the
  positional and each other).
- `--repo <name>` — target a configured repo by name.
- `--name <short>` (with `--repo` or `--cwd`) — new branch/workspace short
  name.
- `--cwd <path>` — arbitrary dir, no repo, no branch.
- `--skill <name>` — spawn a workspace running a global or repo skill.
- `--context-text <text>` — inject a summary of the current session into the
  new workspace's first-turn prompt.
- *(bare, no args)* — registers the cwd's repo (`use_worktree: false`) and
  opens an in-place workspace, no worktree.
- `-- <text...>` — trailing text appended to the auto-generated first-turn
  prompt.
