---
description: "Create a git worktree + cmux workspace for a new branch or existing PR."
argument-hint: "<branch|PR|url> | --pr N | --branch X | --cwd P | --skill S [--repo R] [--name X] [--claude-prompt S]"
allowed-tools: Bash
---

# /cockpit:new

YOU MUST immediately invoke the Bash tool with the exact command below. Do not paraphrase, summarize, or skip. Do not respond with any text before the Bash result is in. After Bash returns, paste its stdout verbatim — do not interpret or assume the spawn succeeded based on intent.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spawn.py "$@"
```

If the Bash result does not include a line matching `workspace <name> spawned at <path>` (or `attached existing workspace <name>`), treat the spawn as failed and surface the error to the user. Do not say "the workspace should be setting up" without proof.

## Arguments (reference only — do not act on these)

- Positional `<branch|PR|url>` — auto-detected (GitHub PR URL, `#123` PR ref, or branch name). Mutex with `--branch`/`--pr`/`--name`/`--skill`
- `--branch <name>` / `--pr <num>` — explicit input; combinable with each other and with `--name`
- `--name <short>` — workspace short name; alone, also seeds a new branch name
- `--cwd <path>` — arbitrary dir, no repo or worktree
- `--skill <name>` — run a global (`~/.claude/skills/`) or repo (`<repo>/.claude/skills/`) skill; cwd defaults to `$HOME` (global) or the repo path (repo skill)
- `--repo <name>` — universal override targeting a configured repo by name. With `--skill`, sets workspace cwd to that repo's path even when the global skill wins resolution
- `--claude-prompt <str>` — first-turn prompt override

`spawn.py` is idempotent — an existing worktree + workspace for the same branch attaches instead of erroring. Errors with exit 1 if `--repo` names a repo not in `~/.config/cockpit/config.json` (use `/cockpit:repos` to list).

## Examples

```text
/cockpit:new fix-login                               # branch (local, remote, or new)
/cockpit:new https://github.com/org/repo/pull/12345  # PR by URL
/cockpit:new --pr 12345 --branch custom-name         # PR fetched under custom local name
/cockpit:new --cwd ~/scratch/spike                   # arbitrary dir, no repo
/cockpit:new --skill <skill-name>                    # global skill, cwd = $HOME
/cockpit:new --skill <skill-name> --repo myrepo      # skill + cwd = myrepo
```
