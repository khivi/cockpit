---
description: "Open the current worktree's diff in cmux's viewer, or address the line notes a reviewer left in it."
argument-hint: "apply | [--branch|--staged|--unstaged|--last-turn] [--base REF]"
allowed-tools: Bash
---

If `$ARGUMENTS` is `apply`, follow **Addressing notes** below. Otherwise invoke
the Bash tool with this exact command, then report what it printed — it names
which diff you got, which matters when the PR fallback fired:

```bash
cockpit diff $ARGUMENTS
```

The split lands beside **this** session, so run it here rather than from the
cockpit dashboard. Resolution is entirely from the current directory and reads
no cockpit config, so any git repo works. It writes nothing and changes no git
state.

Reference (see `cockpit diff --help` for the full list):

- *(no arguments)* — the PR diff; falls back to `--branch` when there's no PR
  yet, and says so.
- `--branch` — the branch against its merge base.
- `--staged` / `--unstaged` — only those changes.
- `--last-turn` — only what changed since this session's last turn. This is
  cmux's own agent-turn baseline, and the fastest answer to "what did I just
  change" without reasoning about the index.
- `--base REF` — re-point `--branch` at another base.

## Addressing notes

`/cockpit-diff apply`, or the daemon sending it when notes appear. Three steps,
in order:

```bash
cockpit diff --comments   # 1. read them
                          # 2. address every one, in this worktree
cockpit diff --ack        # 3. retire them, once addressed
```

Each note prints as `file:line — remark`. They are review feedback aimed at this
session and nothing else surfaces them.

**Do the work before step 3.** `--comments` marks nothing, so notes survive a
turn that ends early; `--ack` is what says *addressed*, and it is not reversible
— an acked note is gone from the ledger. If you cannot action one, say so and
leave it unacked rather than acking to clear the list.

**They never reach GitHub.** These are local notes about *this* worktree, so act
on them here — do not reply on the PR or treat them as review threads to
resolve.

If it reports the browser is disabled, the fix is `cmux enable-browser`. If it
reports no `diff` verb, cmux is too old.
