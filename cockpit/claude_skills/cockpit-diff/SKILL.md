---
name: cockpit-diff
description: "Read the current worktree's diff in cmux's viewer, and address the line notes a reviewer left in it. TRIGGER on `/cockpit-diff apply`, when asked to show/open/review the diff of the current branch or PR, or to check for or act on pending review comments on this work. DO NOT TRIGGER to read a diff as text — use `git diff` for that."
allowed-tools: Bash
---

# cockpit-diff

Opens the current worktree's diff in a cmux browser split beside this session, and reads
back the line-anchored notes a reviewer leaves in it.

## `apply` — address the notes left on this work

`/cockpit-diff apply`, or the daemon sending it when notes appear. Three steps, in order:

```bash
cockpit diff --comments   # 1. read them
                          # 2. address every one, in this worktree
cockpit diff --ack        # 3. retire them, once addressed
```

Each note prints as `file:line — remark`. They are review feedback aimed at this session
and nothing else surfaces them.

**Do the work before step 3.** `--comments` marks nothing, so notes survive a turn that
ends early; `--ack` is what says *addressed*, and it is not reversible — an acked note is
gone from the ledger. If you cannot action one, say so and leave it unacked rather than
acking to clear the list.

**They never reach GitHub.** These are local notes about *this* worktree, so act on them
here — do not reply on the PR or treat them as review threads to resolve.

**Check for them before continuing substantial work on a reviewed branch**, even unasked:
a note contradicting what you are about to build is cheapest to find first.

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
  than from the cockpit dashboard. There is no diff key in the dashboard.
- `--last-turn` is cmux's own agent-turn baseline and is the fastest way to answer "what
  did I just change" without reasoning about the index.
- If it reports the browser is disabled, the fix is `cmux enable-browser`. If it reports
  no `diff` verb, cmux is too old.
- It writes nothing and changes no git state — safe to run at any point.
