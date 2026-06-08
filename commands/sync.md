---
description: "Force the cockpit to run one reconciliation cycle now."
argument-hint: ""
model: haiku
allowed-tools: Bash
---

# /cockpit:sync

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase or skip. After Bash returns, paste its stdout verbatim.

```bash
cockpit sync
```

## Behaviour

Kicks the running daemon to run a cycle now (refresh the PR cache, update cmux pills).

- If `~/.config/cockpit/cockpit.pid` exists and the process is alive → `kill -USR1 <pid>` (cheap, no double-poll). Returns 0.
- Otherwise → prints "no daemon running — start one with `cockpit watch`" and exits 1. There is no inline fallback; start the daemon (`cockpit watch` or `bin/cockpit.sh`).
