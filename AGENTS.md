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

**Why**: The cmux + cockpit workflow keys off the one-worktree-per-branch invariant. In-place edits on the primary checkout pollute its `main` (which must always equal `origin/main`) and break PR-tracking — cockpit derives per-branch state from `git worktree list` and misattributes or drops cells for any branch not isolated in its own worktree.

**How to apply**: Before any Edit or Write, run `git branch --show-current` and `git worktree list`. If HEAD is `main`/`master`, or if the working-tree path is the primary checkout (first entry in `git worktree list`), stop and spawn a worktree via `/cockpit:new` before touching any file.

## Architecture notes

**`docs/state-machine.md` visualizes this section — keep it in sync.** Five Mermaid diagrams map the three state sources (GitHub PR, Claude session, cmux workspace — plus an auxiliary Linear read) onto the decision logic: orientation map, reconcile decision tree (slow tick), nudge idle-gate, stuck-pill timer, and cell data-flow/ownership. Any code change that alters that logic — the decision functions (`match_worktrees`, `_spawn_missing_workspaces`, `nudge_if_idle`, `_track_stale_issue`, `_track_dev_done`, `_maybe_autoclose`), the cell writers in `cache.py`, tick cadence/ownership, or the spawn/teardown/nudge/stuck/devdone/color rules — MUST update the matching diagram in the same PR. Treat a stale diagram like a stale comment: it is worse than none. The diagrams render on GitHub from the blob view.

**Worktree + workspace inventory is derived, not stored.** Each cycle re-reads `git worktree list` and `cmux tree` rather than maintaining its own `state.json`. PR payloads *are* cached (`~/.config/cockpit/cache/<repo>__pr-<N>.json`) because they're a network round-trip; everything else is recomputed. Don't add a `state.json` for worktree/workspace identity — drift between cached identity and the real `git`/`cmux` state was the bug class this design avoids.

**Only the daemon writes to the cache.** Renderer field printers in `scripts/lib/starship.py` are strictly read-only — no `gh`, no `git`, no subprocess forks, no atomic_write calls. The daemon owns every cell:

- **Slow tick** (`slow_poll_interval_seconds`, default 300s) — `scripts/orchestrators/cycle.py::cycle_all` runs the full reconcile (gh PR fetch, base-distance fetch, per-PR JSON, branch-keyed PR flat cells, base-distance/ahead cells, git-state cells, pills).
- **Fast tick** (`fast_poll_interval_seconds`, default 30s) — `scripts/cockpit.py::_fast_tick` does network-free republishing: git-state cells for every worktree (via `write_git_state_cache`) and PR flat cells from the persistent JSON snapshots (via `republish_pr_caches_from_disk`). The fast tick exists so `git checkout` and OS-level tmpdir wipes recover within ~30s instead of ~300s. Both ticks serialize on `_tick_lock` (`scripts/lib/daemon.py`) so they never collide.

When introducing a new cell, the writer goes in `scripts/lib/cache.py` and the call site goes in the daemon's slow tick (decision + snapshot) and/or the fast tick (republish from snapshot). Never let a renderer path consult source state directly — that produces same-render disagreement between fields (one segment reads the snapshot, another reads live state) and was the bug class this design eliminates.

**`sidebar_color` is a cosmetic, cmux-only per-repo field.** The daemon applies it in the slow tick via `_apply_repo_colors` (`scripts/orchestrators/cycle.py`), which calls `cmux workspace-action --action set-color` for each workspace the repo owns. Deduplication runs through `pill_state` under a `color:<ref>` key — cmux is only touched when the color changes or after a daemon restart. Validation happens at preflight (`scripts/lib/preflight.py::_validate_sidebar_colors`) and hard-fails with `sys.exit(2)` on an unknown color name, so invalid values never reach the slow tick. The same value also tints the repo's `owner/name` in the cycle log via `_repo_name_color`, which maps the name through `colors.CMUX_COLOR_ANSI` — that dict is the single source of truth for the valid set (`cmux.WORKSPACE_COLORS = frozenset(CMUX_COLOR_ANSI)`).

**The daemon creates worktrees in the background — it never blocks the tick on `git`.** Two cases in `cycle.py::_spawn_missing_workspaces` shell out to `spawn.py` (the exact path `/cockpit:new` walks) via a detached `subprocess.Popen(start_new_session=True)`, so `git fetch` + worktree add never stalls the reconcile; the new worktree surfaces as cells on a later cycle (inventory stays derived, not stored).

- **My PR with no worktree** → background `spawn.py --pr <n> --repo <name>` (creates worktree + workspace with the plan-only prompt). Replaces the old "create one with /cockpit:new" warning; always on.
- **`review_prs: true|false` (per-repo, default false)** → for *every* other-authored open PR without a local worktree, background `spawn.py --pr <n> --review` (worktree + workspace whose first turn runs `/review`). Discovery uses `gh.list_open_pr_heads` (a paginated, uncapped `is:pr is:open` search — the daemon's normal query is `author:self` + per-worktree aliases, so coworker PRs are otherwise invisible). Uncapped by design: a busy repo can spawn many review worktrees at once; every spawn is logged. Validated as a bool at preflight (`_validate_review_prs`).

Both paths run through `_bg_spawn_pr`, which keys an in-flight guard in `pill_state` (`spawn:<owner>/<name>:<branch>`, a `time.monotonic()` stamp expiring after `_SPAWN_INFLIGHT_TTL_SECONDS`) so a `/cockpit:sync` kick can't double-launch while a creation is mid-flight, and a failed creation is retried on a later tick. Detached stdout/stderr land in `$COCKPIT_HOME/spawn.log`. Once a review worktree exists it is tracked via the normal `match_worktrees` coworker path and torn down on merge/close like any other; orphan auto-spawn stays `{self_user}/`-prefix gated, so review worktrees are never orphan-spawned.

**The single exception is session-scoped cells.** `lib.claude.stash_from_stdin` writes `context-<sid>`, `rate-limit-5h-<sid>`, `model-<sid>`, `permission-mode-<sid>`, `transcript-path-<sid>`, and `cost-<sid>` from Claude Code's statusLine stdin in the statusline path (not the daemon). These cannot route through the daemon because the data only exists in the real-time statusLine stream. They are session-scoped and never read by the daemon, so the rule "renderer reads, daemon writes" is preserved for everything the daemon owns. Do not extend this exception to any new cell.

**The nudge idle-gate trusts the persistent `idle=` pill, NOT cmux's native `claude_code=Needs input`.** `nudge_if_idle` (`scripts/lib/cmux.py`) must distinguish "parked at the main prompt (safe to `send` a nudge)" from "awaiting a y/n permission decision (unsafe — the nudge text would land in the confirmation)". cmux's native `claude_code=` has three values, but only two are usable: `Running` (active) and `Idle` (Stop fired, parked). The third, `Needs input`, is **ambiguous** — it fires both for an idle-at-prompt session aged past Claude's ~60s Notification *and* for a pending permission request mid-turn — so it is never a safe at-rest signal on its own (verified against the cmux event stream). The gate therefore: blocks on native `Running` (also catches a dropped `idle=` clear left on a now-running session); treats the workspace as safe iff the `idle=` pill is present OR native is the unambiguous `Idle`; and self-heals a dropped Stop-hook write by re-asserting `idle=` when native `Idle` holds but the pill is missing. The `idle=` pill itself (`hooks/cmux-idle-pill.sh`, Stop branch) is set with a verify+retry loop — its silent loss under cmux-daemon contention (`Broken pipe`) was the original bug, leaving genuinely-parked workspaces un-nudgeable forever. Do not "simplify" the gate to trust `Needs input`; that reintroduces the permission-prompt hazard.

**The `stuck=` pill is the stale-running escape hatch — a passive visual, never a `send`.** When an actionable PR issue (`ci`/`comments`/`conflicts`) persists past `nudge_stale_seconds` (default `3 × slow_poll_interval_seconds`) without the workspace ever becoming nudgeable — agent wedged mid-turn, or every `idle=` self-heal failed — `cycle.py::_track_stale_issue` raises `stuck=` via `cmux.apply_stuck_pill`. It is deliberately out-of-session (a sidebar pill, not an injected prompt) so it can't type into a permission prompt, and idempotent (no anti-spam state needed). Per-category timing lives in `NudgePref.first_seen_at` (one JSON file per PR); a successful nudge, a resolved issue, or a user mute all reset it. The pill is managed directly in the slow tick, NOT via `apply_pills`, so it is intentionally absent from `cmux.ACTIONABLE_KEYS`.

**The `devdone=` pill marks a PR whose Linear ticket(s) reached the dev-done column — Linear is the one auxiliary state source.** Gated on the repo being Linear-configured (`linear_keys`) AND the PR's body carrying a `Linear: [PE-1234](url)` footer. Delivery is resolved **footer-only** (`lib.linear.parse_linear_footers`) — never the branch-slug regex, which catches predecessor / follow-up tickets the PR doesn't deliver. This is the same strict signal the `morning-align` skill's `linear_delivery.py` uses; `extract_ticket(branch)` stays the footer's id pill but is NOT a delivery signal. The resolved `{tickets:[{id,state}], fetched_at}` block is **cached in the PR JSON** (`write_pr_cache`) like any other network round-trip: `cycle.py::_resolve_linear_block` refetches each ticket's workflow state from the Linear GraphQL API (`lib.linear.fetch_ticket_state`, `LINEAR_API_KEY` env) only when the footer id-set changes OR the block ages past `linear_state_ttl_seconds` (default `3 × slow_poll_interval_seconds`) — the TTL backstop catches a state move that doesn't touch the footer. `cycle.py::_track_dev_done` then reads that cached block (no network) and raises the green pill via `cmux.apply_devdone_pill` only when *every* delivered ticket is in `linear_dev_done_state` (default "Dev Done"). Like `stuck=` it is a passive slow-tick visual, never a `send`, and absent from `cmux.ACTIONABLE_KEYS`. Missing `LINEAR_API_KEY` degrades silently to no pill (preflight warns once). Do NOT add a per-cycle Linear issues query / branch-match signal without revisiting the cache-and-refresh design here.

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
