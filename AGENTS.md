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

## Worktree discipline

Always use a dedicated git worktree for any code change. Never commit directly to `main`/`master` in the primary checkout, and never make in-place edits on a feature branch without a dedicated sibling worktree.

**Why**: The cmux + cockpit workflow keys off the one-worktree-per-branch invariant. In-place edits on the primary checkout pollute its `master` (which must always equal `origin/master`) and break PR-tracking — cockpit derives per-branch state from `git worktree list` and misattributes or drops cells for any branch not isolated in its own worktree.

**How to apply**: Before any Edit or Write, run `git branch --show-current` and `git worktree list`. If HEAD is `main`/`master`, or if the working-tree path is the primary checkout (first entry in `git worktree list`), stop and spawn a worktree via `/cockpit:new` before touching any file.

## Architecture notes

**Worktree + workspace inventory is derived, not stored.** Each cycle re-reads `git worktree list` and `cmux tree` rather than maintaining its own `state.json`. PR payloads *are* cached (`~/.config/cockpit/cache/<repo>__pr-<N>.json`) because they're a network round-trip; everything else is recomputed. Don't add a `state.json` for worktree/workspace identity — drift between cached identity and the real `git`/`cmux` state was the bug class this design avoids.

**Only the daemon writes to the cache.** Renderer field printers in `scripts/lib/starship.py` are strictly read-only — no `gh`, no `git`, no subprocess forks, no atomic_write calls. The daemon owns every cell:

- **Slow tick** (`slow_poll_interval_seconds`, default 300s) — `scripts/orchestrators/cycle.py::cycle_all` runs the full reconcile (gh PR fetch, base-distance fetch, per-PR JSON, branch-keyed PR flat cells, base-distance/ahead cells, git-state cells, pills).
- **Fast tick** (`fast_poll_interval_seconds`, default 30s) — `scripts/cockpit.py::_fast_tick` does network-free republishing: git-state cells for every worktree (via `write_git_state_cache`) and PR flat cells from the persistent JSON snapshots (via `republish_pr_caches_from_disk`). The fast tick exists so `git checkout` and OS-level tmpdir wipes recover within ~30s instead of ~300s. Both ticks serialize on `_tick_lock` (`scripts/lib/daemon.py`) so they never collide.

When introducing a new cell, the writer goes in `scripts/lib/cache.py` and the call site goes in the daemon's slow tick (decision + snapshot) and/or the fast tick (republish from snapshot). Never let a renderer path consult source state directly — that produces same-render disagreement between fields (one segment reads the snapshot, another reads live state) and was the bug class this design eliminates.

**The single exception is session-scoped cells.** `lib.claude.stash_from_stdin` writes `context-<sid>`, `rate-limit-5h-<sid>`, `model-<sid>`, `permission-mode-<sid>`, `transcript-path-<sid>`, and `cost-<sid>` from Claude Code's statusLine stdin in the statusline path (not the daemon). These cannot route through the daemon because the data only exists in the real-time statusLine stream. They are session-scoped and never read by the daemon, so the rule "renderer reads, daemon writes" is preserved for everything the daemon owns. Do not extend this exception to any new cell.

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
