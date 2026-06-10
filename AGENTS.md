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

**How to read this (you're an agent editing this repo).** Each `###` is one invariant: the rule, its enforcing `file::symbol`, and the bug it prevents — obey the **Never** / **Do not** lines, they encode paid-for regressions. This is the *invariant* half of the design; `docs/state-machine.md`'s four Mermaid diagrams are the *control-flow* half. Keep both in sync.

### Keep `docs/state-machine.md` in sync — a stale diagram is worse than none

Four diagrams (orientation map, reconcile decision tree, nudge idle-gate, cell data-flow) map the three state sources (GitHub PR, Claude session, cmux workspace; + auxiliary Linear) onto the decision functions. Any change to those functions (`match_worktrees`, `_spawn_missing_workspaces`, `nudge_if_idle`, `_track_dev_done`, `_maybe_autoclose`), the `cache.py` cell writers, tick cadence, or the spawn/teardown/nudge/devdone/color rules MUST update the matching diagram in the same PR.

### Inventory is derived every cycle, never stored

Each cycle re-reads `git worktree list` and `cmux tree`. Only PR payloads are cached (`~/.config/cockpit/cache/<repo>__pr-<N>.json`, a network round-trip). **Never** add a stored identity file — cached-vs-real drift is the bug class this avoids.

### Packaged as the `cockpit` console script — invoke by subcommand, never by file path

`[project.scripts] cockpit = cockpit.cli:main` is a thin dispatcher (`cli.py`) routing `watch / setup / statusline / starship / new / nudge` to each module's `main` (`watch`/`setup` → `cockpit.cockpit` as `--watch`/`--setup`; `setup` re-wires the statusLine without starting the daemon). Plugin `commands/*.md` + `hooks.json` call `cockpit <sub>`; generated configs use `{python} -m cockpit.cli <sub>` (resolves off-PATH in starship's render env). That `{python}` is `sys.executable` pinned at `cockpit setup` time, so **never run setup from inside a worktree venv** (`uv run cockpit setup`) — it bakes the worktree's ephemeral `.venv/bin/python`, which dies on cleanup (the "footer disappeared after update" bug); `bin/update.sh` re-runs `cockpit setup` through the stable console script after each `uv tool install` to re-pin. Wheel version single-sourced from `.claude-plugin/plugin.json` via a hatch hook. Must be installed (`uv tool install`); `preflight._warn_cockpit_not_on_path` soft-warns, never hard-fails. **Add entry points as `cockpit <sub>` in `cli.py`**, not file-path invocations.

### `cockpit watch` is a Textual TUI, and the TUI *is* the daemon (`cockpit/tui/`)

- **No headless mode:** non-TTY `watch` exits 2. The app owns the pidfile (`daemon.claim_pidfile`/`release_pidfile`). `lib/daemon.py::run_watcher` survives only as the tested signal/pidfile primitive.
- **Ticks:** slow + fast run in `@work(thread=True)` workers; bodies (`cockpit.cockpit._once_with`/`_fast_tick`) are lock-free, serialized by the app's `_tick_lock` acquired *inside* the worker (blocked → `waiting`, holds → `running`). Startup is slow-first — `_start_fast` only after the first slow completes, so the first republish isn't a no-op vs empty caches.
- **Per-repo table republish:** `cycle_all` writes each repo's cells before fetching the next, so the slow tick hands it an `on_repo_done` hook (`_run_slow` → `_publish_inventory`, the same git+cells read its `finally` runs) fired after each repo — a finished repo surfaces in the table while later repos still round-trip `gh`, instead of all repos landing at once on tick end. The hook is a pure read (`_gather_inventory` + `call_from_thread(_render_table)`); a failing callback is logged and never aborts the remaining repos. **Never** let `on_repo_done` write a cell — only the daemon writes; this is a renderer refresh.
- **Signals:** `loop.add_signal_handler` only (SIGUSR1 → slow kick; SIGTERM/SIGHUP → clean exit). **Never** `signal.signal` — it raises off the main thread.
- **Table is read-only** (`worktree_table.py`, keyed by worktree path; repo shown by tinting the name with `sidebar_color`, not a column). Reads only daemon-written flat cells (`cache.py::_write_pr_flat_cells`, republished fast): `pr-muted` → 🔇, `pr-nudge` → 🔔 (mute wins), `pr-author` → `@login`, `pr-comments` → 💬 count. The `pr-nudge` cell is `PR.nudge_issue` — the same actionable-issue value the slow tick's nudge decision reads, so the bell can't disagree with whether a nudge would fire; it clears automatically when the issue resolves (derived, never stored).
- **Row actions** (`f w p l c C m N n`) live on the app, and — except `n` and `w` (which spawn a workspace, *not* a cache cell) — never touch cmux/the cache:
  - `f` focus → `cmux focus`; `p`/`l` open the PR / Linear URL in a browser.
  - `w` open-workspace → ensure the row's worktree has a workspace, spawning one (reusing the daemon's `spawn_pr_workspace`/`spawn_orphan_workspace`, PR-payload-reconstructed via `_pr_from_payload`) when missing, then focus it. The cwd-keyed `find_cockpit_workspaces` adopts the new workspace next tick (no double-spawn) and `_dedupe_workspaces` reaps a race dupe. **Unlike `f` (focus-only, cmux-only), `w` works on limux** — limux can spawn but has no select verb, so there it creates the workspace and the user switches via limux's own UI; slow-kicks after spawn.
  - `c` close → `probe_blockers` gate (refuses dirty/unpushed/open-PR) → enqueue `TeardownRequest` → slow kick, so teardown flows through the daemon's `_drain_close_requests`.
  - `C` force-close → overrides the *soft* open-PR block (`forced=True`) but still refuses the *hard* `worktree_state_blockers` (uncommitted/unpushed), so force never discards local work. (Closing is TUI-only — no `cockpit close` CLI; daemon autoclose/orphan-reap enqueue through `orchestrators.teardown`.)
  - `m` mute → writes a `NudgePref` (session state, not a cell) + slow kick to republish `pr-muted` + 🔇.
  - `N` nudge → `cmux.nudge_if_idle` with no pr/category (overrides mute + throttle) but still honours the idle gate — passive skip-with-toast when not at rest, never a forced `send`.
  - `n` new → modal (`NewWorkspaceScreen`), then `cockpit new <source>` detached via module dispatch (`python -m cockpit.cli new`, **not** `spawn.py` by path — that breaks imports); auto-detects bare/`#N`/URL/Linear-id/Slack-URL and routes to the modal's chosen repo (its `cwd`), then slow-kicks so the worktree surfaces later. `N` is off `n` so New gets the bare key.
- **Global keys:** `s` sync, `r` repo-config, `o` output, `q` quit, `u` self-update.
- **`u` self-update:** `_check_update` rides the slow tick (`_run_slow` finally, `exclusive` to coalesce manual kicks) + once at startup — so a fresh release surfaces within ~one slow interval, not up to an hour. It compares `version.running_version()` vs `latest_version()` → header "⬆" indicator; `u` exits with `RESTART_EXIT_CODE` (42), which `bin/cockpit.sh` catches to run `bin/update.sh` + relaunch. Intrinsic restart (the update `uv tool install --force`s the running package). `bin/update.sh --check`: exit 10 = available, 0 = current.
  - **The supervisor is reached automatically — never rely on the user launching `bin/cockpit.sh`.** The wheel bundles the manifests but **not** `bin/`, so the uv-installed `cockpit watch` (the documented launch command) has no supervisor next to it; exiting 42 unsupervised would just kill the session with no update (the bug this fixes). So `cli.py`'s watch path calls `cockpit.lib.supervisor.reexec_through_supervisor` first: it finds the **newest cached** `bin/cockpit.sh` (`{claude}/plugins/cache/<marketplace>/<plugin>/<ver>/bin/`, honouring `CLAUDE_CONFIG_DIR`, ordered by `version.parse_version` — the same comparator as the update check) and `os.execvpe`s into it with `COCKPIT_SUPERVISED=1` in the child env only (never a live `os.environ` mutation) — the shell loop, not Python, owns the update+relaunch. `bin/cockpit.sh` also `export`s it, and `is_supervised()` requires the exact value `"1"` (`=0` means NOT supervised). The re-exec **declines** (watch runs inline, `supervised=False`, `u` warns instead of exiting into the void) when: already supervised; first arg is cockpit.sh's reserved `update` verb (forwarding it would silently exec `update.sh` — argparse must reject it instead); non-TTY; **argv[0] isn't the PATH-installed `cockpit`** (a dev's `uv run cockpit watch` from a worktree must not be exec-swapped for the installed wheel); or no cached script. `bin/update.sh`'s cache redirect probes the same `CLAUDE_CONFIG_DIR` root and refuses to **downgrade** (skips the reinstall when the uv-installed version is newer than the newest cached dir). **Never** make `u` exit 42 without confirming `self._supervised`.
- **stdout:** all tick prints go through one process-wide `_QueueWriter` — **never** per-tick `redirect_stdout` (the threads race).

### Only the daemon writes the cache; renderers read

`lib/starship.py` field printers are strictly read-only (no gh/git/subprocess/`atomic_write`).

- **Slow tick** (`slow_poll_interval_seconds`, 300s) — `cycle.py::cycle_all`: full reconcile (gh fetch, base-distance, per-PR JSON, PR flat cells, git-state cells, pills).
- **Fast tick** (`fast_poll_interval_seconds`, 30s) — `cockpit.py::_fast_tick`: network-free republish of git-state (`write_git_state_cache`) + PR flat cells from disk (`republish_pr_caches_from_disk`), so `git checkout` / tmpdir wipes recover in ~30s.

New cell → writer in `cache.py`, call site in the slow tick (decide + snapshot) and/or fast tick (republish). **Never** let a renderer read source state directly — that same-render disagreement is the bug class this avoids.

### `sidebar_color` — cosmetic, cmux-only, per-repo

Applied slow-tick via `_apply_repo_colors` → `cmux … set-color`, deduped in `pill_state` under `color:<ref>` (cmux touched only on change/restart). Validated at preflight (`_validate_sidebar_colors`, `sys.exit(2)` on unknown). Valid set = `colors.CMUX_COLOR_ANSI` (= `cmux.WORKSPACE_COLORS`).

### Workspace names track the branch (`wt.label`), re-asserted on both ticks

`wt.label` (`git.py::branch_label`) derives from the *branch*, not the dir basename (`wt.short`): strip `branch_prefix`, drop a leading base segment (`master/`), slugify, drop a leading ticket/PR token (`pe-4608-`) — but never to `""`. cockpit resolves workspaces by cwd→path, never by name; `cmux.rename_workspace_if_needed` re-asserts the label idempotently (slow tick `_refresh_tracked_pills`/`_refresh_orphan`; fast tick `reconcile_workspace_names`), recovering drift in ~30s. Cosmetic, never a `send`. **Consequence:** to relabel, rename the *branch* — a manual workspace rename is reverted next tick. **Exception:** the primary checkout (`wt.is_primary`, a main branch ∈ `MAIN_BRANCHES`) keeps its custom name (`reconcile_workspace_names` guards on it), so a `master` workspace named e.g. `morning` isn't force-renamed.

### The daemon creates worktrees in the background — never blocking the tick on `git`

`cycle.py::_spawn_missing_workspaces` shells out via module dispatch (`python -m cockpit.cli new`) in a detached `Popen(start_new_session=True)`:

- **My PR, no worktree** → `cockpit new --pr <n> --repo <name>` (plan-only prompt). Always on.
- **`review_prs` (per-repo, default false)** → every coworker open PR without a worktree → `cockpit new --pr <n> --review`. Discovery: `gh.list_open_pr_heads` (uncapped `is:pr is:open`; the normal query is `author:self`-only). Validated `_validate_review_prs`.

Both go through `_bg_spawn_pr`, which guards in-flight launches in `pill_state` (`spawn:<owner>/<name>:<branch>`, TTL `_SPAWN_INFLIGHT_TTL_SECONDS`) against a double-launch; logs to `$COCKPIT_HOME/spawn.log`. Review worktrees then track via the normal `match_worktrees` coworker path; orphan auto-spawn stays `{self_user}/`-gated.

### Slack thread source — codename branch, MCP-delegated fetch, no `claude mcp list` probe

`spawn.detect_source` classifies a Slack permalink (`slack.SLACK_URL_RE`) as `slack` mode — a user-initiated source only (never daemon auto-spawned), so it has no cache cell / pill / `docs/state-machine.md` node, same shape as `actions`. A Slack URL carries no human name, so spawn synthesizes a deterministic codename branch (`codename.codename(slack.slack_seed(url))` → `<prefix><adj>-<noun>`). The seed is the thread's **stable identity** (channel id + message ts via `slack_seed`), NOT the raw URL — so query params (`?thread_ts=…&cid=…`) and the `archives` vs `app.slack.com/client` shape don't change the branch, keeping re-spawns idempotent. Cockpit never calls the Slack API: `_slack_prompt` delegates the thread read to the in-session Slack MCP (mirrors `_linear_prompt`), and under `use_slack: true` instructs Claude to append a topic slug to the codename (`cosmic-otter` → `cosmic-otter-fix-oauth`) and rename the workspace. **Never** add a `slack_mcp_available()` / `claude mcp list` pre-flight gate: that probe is unreliable for claude.ai-managed connectors (false-negatives even when live), so a positive-detection gate would silently disable the feature — the prompt's own retry-then-STOP logic handles a genuinely absent connector. `use_slack` (bool, `_validate_use_slack`) gates only the fetch/rename detail; the URL is seeded as context regardless.

### The one cache exception: session-scoped cells (written outside the daemon)

`lib.claude.stash_from_stdin` writes `context-<sid>`, `rate-limit-5h-<sid>`, `model-<sid>`, `permission-mode-<sid>`, `transcript-path-<sid>`, `cost-<sid>` from the statusLine stdin (the only place that data exists). Never read by the daemon. **Do not** extend this exception to a new cell.

### Nudge idle-gate: trust the `idle=` pill, NOT cmux's native `Needs input`

`nudge_if_idle` (`lib/cmux.py`) must tell "parked at prompt (safe to `send`)" from "awaiting a y/n permission (unsafe)". cmux native `claude_code=` has `Running`, `Idle`, and the **ambiguous `Needs input`** (fires for both an idle session aged past Notification *and* a pending permission). The gate: block on native `Running`; safe iff the `idle=` pill is present OR native is the unambiguous `Idle`; self-heal a dropped Stop-hook write by re-asserting `idle=` under native `Idle`. The pill (`hooks/cmux-idle-pill.sh`) uses a verify+retry loop — its silent loss under cmux contention (`Broken pipe`) was the original bug. **Never** simplify the gate to trust `Needs input`.

### `devdone=` pill — Linear is the one auxiliary (read-only) state source

Gated on `linear_keys` + a `Linear: [PE-1234](url)` PR-body footer. Delivery is **footer-only** (`lib.linear.parse_linear_footers`) — never the branch-slug regex, which catches non-delivered predecessor/follow-up tickets (same signal as `morning-align`'s `linear_delivery.py`). The `{tickets, fetched_at}` block is cached in the PR JSON; `cycle.py::_prefetch_linear_blocks` decides refetch-vs-carry-forward per PR (`_decide_linear_refetch`: footer-id change or past `linear_state_ttl_seconds`) reading the *prior* snapshot via `ctx.pr_payloads`, then resolves the union of due tickets across *all* a repo's PRs in one **batched** `fetch_ticket_states` (one GraphQL query per team, not one per ticket). It runs once before the write loop so each `write_pr_cache` still overwrites against the old file. `_track_dev_done` raises the pill (`apply_devdone_pill`) only when *every* delivered ticket is in `linear_dev_done_state` ("Dev Done"). Passive, never a `send`, absent from `ACTIONABLE_KEYS`. **Do not** drop back to a per-PR fetch fan-out; keep the union batched. A per-team query failure isolates to its own team's ids (they stay None) — never blanks unrelated tickets.

### `linear_done_on_merge` — the daemon's only sanctioned Linear *write*

Opt-in (default false, per-repo). Slow-tick `cycle.py::_transition_merged_tickets` moves a merged PR's delivered tickets to `linear_merge_done_state` ("Done", distinct from devdone's `linear_dev_done_state`) via `update_ticket_state` → `issueUpdate` — the codebase's single GraphQL write. Fires on `_is_post_merge_stale` independently of teardown (ships tickets even when the worktree is held back). Per ticket (footer-only): skip unless assigned to the API-key `viewer`; skip if already at target or `type: canceled` (note "Dev Done"/"Done" are both `completed`, so name-equality decides); else resolve the target UUID (`fetch_team_states`) and mutate. A per-run `merged-done:…` marker in `pill_state` prevents re-query. The near-immutable `viewer` id and per-team state maps are cached across ticks in `pill_state` (`_cached_viewer_id`/`_cached_team_states`, TTL `linear_identity_ttl_seconds` = 12 slow cycles; the viewer key is a non-secret fingerprint of `LINEAR_API_KEY`, never the raw key); the `viewer` fetch is **lazy** — deferred to the first eligible ticket, so a flag-on repo with nothing merged makes zero Linear calls. A falsy/failed identity fetch is never cached (retries next tick). Gated `linear_keys` + `LINEAR_API_KEY` + not-dry. **Precedent for any future daemon Linear write:** opt-in, viewer-gated, idempotent, logged.

## Dev setup and common commands

```bash
# One-time after cloning — wires pre-commit hooks for commit + push stages:
./setup.sh

# Run the test suite (also runs on pre-push via pre-commit):
pytest

# Type-check:
mypy cockpit/

# Lint + format (both also enforced by pre-commit):
ruff check --fix cockpit/ tests/
ruff format cockpit/ tests/

# Run the full pre-push gate locally (version-bump + pytest):
pre-commit run --hook-stage pre-push --all-files
```

## Release versioning

Handled by the pre-push hook — bumps `.claude-plugin/plugin.json`'s `version` automatically. No manual action needed.

## Test layout

New modules get their own `test_<name>.py` — don't append tests for a new source file to an unrelated test module. Shell hooks under `hooks/` are the exception: they live as `tests/test_<hook>.py` with no Python source mirror.

## Test style by layer

- **Leaf modules** (`cockpit/lib/*` wrapping `git`, `gh`, `cmux`, `shutil.which`, `subprocess.run`, etc.) test against the real tool on `tmp_path`. Stubbing the underlying command tests the stub, not the integration.
- **Orchestrators** (`cockpit/orchestrators/*`) compose those leaves. Tests mock collaborator calls (`patch.object(teardown_mod, "remove_worktree", …)`) to assert ordering, guards, and gating without re-validating the leaves underneath.
- **CLI entry-points** (`tests/test_<script>.py` for `cockpit/{cockpit,spawn}.py`, plus `tests/test_cli.py` for the dispatcher) test the argparse / routing layer. Mock at the orchestrator boundary (`patch("cockpit.cockpit.teardown", …)`) or, for the dispatcher, at each module's `main` — the layer below is covered by its own tests, so re-exercising it here adds noise.
- **TUI** (`tests/tui/*`) drive the Textual app headlessly via `App.run_test()`/Pilot (no TTY) with the tick functions and `load_config` injected; card markup is a pure function tested off seeded cache cells. Test the TUI's own scheduling / in-flight gating / log capture, not the reconcile cycle underneath.
- **End-to-end** (`tests/e2e/*`) run the full pipeline against real binaries. No mocking. These are the slowest and most fragile — reserve for genuinely cross-layer behavior (e.g. `cship` + `starship` integration).

## Sync

AGENTS.md is canonical — `CLAUDE.md` imports it, `.github/copilot-instructions.md` symlinks to it; edit only this file.
