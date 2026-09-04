---
name: cockpit-diff
description: "Read the current worktree's diff in cmux's viewer, and collect the line notes a reviewer left in it. TRIGGER when asked to show, open or review the diff of the current branch or PR, or to check for pending review comments on this work. DO NOT TRIGGER to read a diff as text — use `git diff` for that."
allowed-tools: Bash
---

# cockpit-diff

Opens the current worktree's diff in a cmux browser split beside this session, and reads
back the line-anchored notes a reviewer leaves in it.

**Check for pending notes before continuing substantial work on a reviewed branch.** They
are review feedback aimed at this session and nothing else surfaces them:

```bash
cockpit diff --comments
```

Each prints as `file:line — remark`. They never reach GitHub — they are local review notes
about *this* worktree, so act on them here rather than replying on the PR. Reading them
marks them delivered, so they will not appear again; act on what you read in the same turn
or restate it somewhere durable.

## Opening a diff

```bash
cockpit diff              # the PR diff; falls back to --branch when there's no PR yet
cockpit diff --branch     # branch against its merge base
cockpit diff --staged     # staged changes only
cockpit diff --unstaged   # unstaged changes only
cockpit diff --last-turn  # only what changed since this session's last turn
cockpit diff --base REF   # re-point --branch at another base
```

Resolution is entirely from the current directory and needs no cockpit config, so any git
repo works. Report what the command printed — it names which diff you got, which matters
when the PR fallback fired.

## Notes

- The split lands beside **this** session, which is why the command is run here rather
  than from the cockpit dashboard.
- `--last-turn` is cmux's own agent-turn baseline and is the fastest way to answer "what
  did I just change" without reasoning about the index.
- If it reports the browser is disabled, the fix is `cmux enable-browser`. If it reports
  no `diff` verb, cmux is too old.
- It writes nothing and changes no git state — safe to run at any point.
