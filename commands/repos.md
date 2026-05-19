---
description: "List configured cockpit repos with their paths and defaults."
argument-hint: ""
model: haiku
allowed-tools: Bash
---

# /cockpit:repos

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase or skip. After Bash returns, paste its stdout verbatim.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/repos.py
```

## Output

```text
NAME       PATH                       BRANCH_PREFIX   DEFAULT_BASE
myrepo     ~/code/myrepo              khivi/          main
otherrepo  ~/code/otherrepo                           master
```

Reads `~/.config/cockpit/config.json`. Referenced by `/cockpit:new`'s error when `--repo <name>` doesn't match any configured repo.
