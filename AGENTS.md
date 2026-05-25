# Privacy & Internal References

This is a public repository. Never include the following in commits, PRs, code comments, or documentation:

- Internal ticket IDs (Linear `ENG-123`, Jira `PROJ-456`, etc.)
- Internal GitHub PR/issue URLs from private repos
- Real names of teammates (use roles instead: "the reviewer", "the on-call engineer")
- Internal Slack channels, wiki URLs, or tool links
- Internal hostnames, service names, or infra identifiers
- Customer names or company-specific identifiers

When writing commit messages or PR descriptions:

- Describe *what* changed and *why*, not which ticket tracks it
- Reference public GitHub issues only (`#123` in this repo)
- If context requires an internal ticket, summarize the requirement instead of linking

Before committing, scan for cases gitleaks can't catch:

- Your team's real ticket prefixes (e.g. `ENG-123`, `LIN-456`)
- `@firstname` references that aren't GitHub handles

## Architecture notes

**Worktree + workspace inventory is derived, not stored.** Each cycle re-reads `git worktree list` and `cmux tree` rather than maintaining its own `state.json`. PR payloads *are* cached (`~/.config/cockpit/cache/<repo>__pr-<N>.json`) because they're a network round-trip; everything else is recomputed. Don't add a `state.json` for worktree/workspace identity — drift between cached identity and the real `git`/`cmux` state was the bug class this design avoids.

**PR cache writes are confined to the daemon and renderer-spawned refreshers.** The daemon (`scripts/orchestrators/cycle.py`) is the single authority for PR-derived state: it resolves every signal once per cycle, then bakes the result into the per-PR JSON (`write_pr_cache`) and the flat cells (`write_branch_pr_cache`). The renderer-spawned background refreshers (`refresh_pr_data`, `refresh_pr_checks` in `scripts/lib/cache.py`) only republish JSON → flat cells; they never re-decide state and must not import resolvers like `nudges`. Renderer field printers in `scripts/lib/starship.py` are read-only. New PR-state signals (e.g. nudge mute) must be loaded once in the daemon's `_prepare_cycle` and threaded through `write_pr_cache` / `write_branch_pr_cache` / `apply_pills`. Letting renderer paths consult source state directly produces same-render disagreement between fields (one segment reads the snapshot, another reads live state).

## Release versioning

Handled by the pre-push hook — bumps `.claude-plugin/plugin.json`'s `version` automatically. No manual action needed.

## Test layout

New modules get their own `test_<name>.py` — don't append tests for a new source file to an unrelated test module. Shell hooks under `hooks/` are the exception: they live as `tests/test_<hook>.py` with no Python source mirror.

## Test style by layer

- **Leaf modules** (`scripts/lib/*` wrapping `git`, `gh`, `cmux`, `shutil.which`, `subprocess.run`, etc.) test against the real tool on `tmp_path`. Stubbing the underlying command tests the stub, not the integration.
- **Orchestrators** (`scripts/orchestrators/*`) compose those leaves. Tests mock collaborator calls (`patch.object(teardown_mod, "remove_worktree", …)`) to assert ordering, guards, and gating without re-validating the leaves underneath.
- **CLI entry-points** (`tests/test_<script>.py` for `scripts/{close,cockpit,spawn}.py`) test the argparse layer and dispatch. Mock at the orchestrator boundary (`patch("scripts.cockpit.teardown", …)`) — the layer below is covered by orchestrator tests, so re-exercising it here adds noise.
- **End-to-end** (`tests/e2e/*`) run the full pipeline against real binaries. No mocking. These are the slowest and most fragile — reserve for genuinely cross-layer behavior (e.g. `cship` + `starship` integration).

## Sync

AGENTS.md is canonical — `CLAUDE.md` imports it, `.github/copilot-instructions.md` symlinks to it; edit only this file.
