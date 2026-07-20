---
description: "Queue a cockpit worktree + workspace teardown for the daemon (defaults to the current workspace)."
argument-hint: "[branch|slug|path] [--force] [--dry-run]"
allowed-tools: Bash
---

Invoke the Bash tool with this exact command, then paste its stdout verbatim:

```bash
cockpit close $ARGUMENTS
```

Resolves the target from the positional query if given, else from the
current directory's worktree root. Writes a durable close-request marker and
SIGUSR1-kicks the running `cockpit watch` daemon; if no daemon is running the
request stays queued until one starts.

Reference (see `cockpit close --help` for the full list):

- `[branch|slug|path]` — worktree to close, by branch name, sidebar label,
  dir basename, or path. Defaults to the worktree at the current directory.
- `--force` — override the soft open-PR refusal (and close a teammate's
  pushed-but-unmerged PR worktree once their commits are pushed). Never
  overrides uncommitted changes or commits that exist only locally.
- `--dry-run` — report the resolved target and any blockers without
  enqueuing.
