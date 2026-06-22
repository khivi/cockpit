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

**How to apply**: Before any Edit or Write, run `git branch --show-current` and `git worktree list`. If HEAD is `main`/`master`, or if the working-tree path is the primary checkout (first entry in `git worktree list`), stop and spawn a worktree via `/cockpit:new` before touching any file. If HEAD is already a non-main branch in a sibling worktree (not the first entry), proceed — do **not** spawn another worktree.

## Architecture notes

**How to read this (you're an agent editing this repo).** Each `###` is one invariant: the rule, its enforcing `file::symbol`, and the bug it prevents — obey the **Never** / **Do not** lines, they encode paid-for regressions. This is the *invariant* half of the design; `docs/state-machine.md`'s four Mermaid diagrams are the *control-flow* half. Keep both in sync.

### Keep `docs/state-machine.md` in sync — a stale diagram is worse than none

Four diagrams (orientation map, reconcile decision tree, nudge idle-gate, cell data-flow) map the three state sources (GitHub PR, Claude session, cmux workspace; + auxiliary Linear) onto the decision functions. Any change to those functions (`match_worktrees`, `_spawn_missing_workspaces`, `nudge_if_idle`, `_track_dev_done`, `_maybe_autoclose`), the `cache.py` cell writers, tick cadence, or the spawn/teardown/nudge/devdone/color rules MUST update the matching diagram in the same PR.

### Inventory is derived every cycle, never stored

Each cycle re-reads `git worktree list` and `cmux tree`. Only PR payloads are cached (`~/.config/cockpit/cache/<repo>__pr-<N>.json`, a network round-trip). **Never** add a stored identity file — cached-vs-real drift is the bug class this avoids.

### Packaged as the `cockpit` console script — invoke by subcommand, never by file path

`[project.scripts] cockpit = cockpit.cli:main` is a thin dispatcher (`cli.py`) routing `watch / setup / statusline / starship / new / nudge / update` to each module's `main` (`watch`/`setup` → `cockpit.cockpit` as `--watch`/`--setup`; `setup` re-wires the statusLine without starting the daemon). Plugin `commands/*.md` + `hooks.json` call `cockpit <sub>`; generated configs use `{python} -m cockpit.cli <sub>` (resolves off-PATH in starship's render env). That `{python}` is `sys.executable` pinned at `cockpit setup` time, so **never run setup from inside a worktree venv** (`uv run cockpit setup`) — it bakes the worktree's ephemeral `.venv/bin/python`, which dies on cleanup (the "footer disappeared after update" bug); `cockpit update` (and the bootstrap `bin/update.sh`) re-runs `cockpit setup` through the stable console script after each `uv tool install` to re-pin. Wheel version single-sourced from `.claude-plugin/plugin.json` via a hatch hook. Must be installed (`uv tool install`); `preflight._warn_cockpit_not_on_path` soft-warns, never hard-fails. **Add entry points as `cockpit <sub>` in `cli.py`**, not file-path invocations.

### `cockpit watch` is a Textual TUI, and the TUI *is* the daemon (`cockpit/tui/`)

- **No headless mode:** non-TTY `watch` exits 2. The app owns the pidfile (`daemon.claim_pidfile`/`release_pidfile`). `lib/daemon.py::run_watcher` survives only as the tested signal/pidfile primitive.
- **Ticks:** slow + fast run in `@work(thread=True)` workers; bodies (`cockpit.cockpit._once_with`/`_fast_tick`) are lock-free, serialized by the app's `_tick_lock` acquired *inside* the worker (blocked → `waiting`, holds → `running`). Startup is slow-first — `_start_fast` only after the first slow completes, so the first republish isn't a no-op vs empty caches.
- **Per-repo table republish:** `cycle_all` writes each repo's cells before fetching the next, so the slow tick hands it an `on_repo_done` hook (`_run_slow` → `_publish_inventory`, the same git+cells read its `finally` runs) fired after each repo — a finished repo surfaces in the table while later repos still round-trip `gh`, instead of all repos landing at once on tick end. The hook is a pure read (`_gather_inventory` + `call_from_thread(_render_table)`); a failing callback is logged and never aborts the remaining repos. **Never** let `on_repo_done` write a cell — only the daemon writes; this is a renderer refresh.
- **Signals:** `loop.add_signal_handler` only (SIGUSR1 → slow kick; SIGTERM/SIGHUP → clean exit). **Never** `signal.signal` — it raises off the main thread.
- **Table is read-only** (`worktree_table.py`, keyed by worktree path; repo shown by tinting the name with `sidebar_color`, not a column). Reads only daemon-written flat cells (`cache.py::_write_pr_flat_cells`, republished fast): `pr-muted` → 🔇, `pr-nudge` → 🔔 (mute wins), `pr-author` → `@login`, `pr-comments` → 💬 count. The `pr-nudge` cell is `PR.nudge_issue` — the same actionable-issue value the slow tick's nudge decision reads, so the bell can't disagree with whether a nudge would fire; it clears automatically when the issue resolves (derived, never stored).
- **Row actions** (`f w p t c C m N n`) live on the app, and — except `n` and `w` (which spawn a workspace, *not* a cache cell) — never touch cmux/the cache. The **footer help text** is gated on two axes: (1) the resolved **backend** (`FooterBar.BACKEND_ACTIONS`, fed `resolve_tool()` at compose): `f`/Focus + `N`/Nudge are cmux-only verbs so their hints hide on limux/none; `w`/Open is limux's only reach-a-workspace path so its hint hides on cmux (redundant with `f`); and (2) the **highlighted row's capabilities** (`FooterBar.ACTION_REQUIRES`, fed `WorktreeTable.current_capabilities()` via `app._refresh_footer_caps` on every row-highlight and table refresh): `p`/Open-PR + `m`/Mute hide on a row with no PR, `t`/Ticket hides on a row with no delivered ticket, and `m` reads **Unmute** when the row's PR is muted. Row caps are `{pr, ticket, muted}` tokens read from the same daemon-written cells the cells render from (`row_capabilities`: `pr-num`, `pr-muted`, the cached delivery block) — never a network call. Caps `None` (empty table) shows the full legend, not an empty one. The `t` key is further gated globally by `show_tickets` (some repo has a provider). The actions stay bound and self-guard (pressing a hidden key still warns) — only the advertised hint follows backend + row state:
  - `f` focus → `cmux focus`; `p` opens the PR URL. `t`/Open-ticket is **provider-neutral** — it routes through the row's `TicketProvider.ticket_url` (`tickets.provider_for`): GitHub builds the issue URL deterministically from the delivered ref + the PR's repo nwo (parsed from the cached PR URL, no network); Linear reads the exact `Linear: [ID](url)` footer link out of the PR body via `gh.pr_body` (its canonical URL can't be hand-constructed). The delivery block is cached under the (historically named) `linear` key for **both** providers.
  - `w` open-workspace → ensure the row's worktree has a workspace, spawning one (reusing the daemon's `spawn_pr_workspace`/`spawn_orphan_workspace`, PR-payload-reconstructed via `_pr_from_payload`) when missing, then focus it. The cwd-keyed `find_cockpit_workspaces` adopts the new workspace next tick (no double-spawn) and `_dedupe_workspaces` reaps a race dupe. **Unlike `f` (focus-only, cmux-only), `w` works on limux** — limux can spawn but has no select verb, so there it creates the workspace and the user switches via limux's own UI; slow-kicks after spawn.
  - `c` close → `probe_blockers` gate (refuses dirty/unpushed/open-PR) → enqueue `TeardownRequest` → slow kick, so teardown flows through the daemon's `_drain_close_requests`.
  - `C` force-close → overrides the *soft* open-PR block (`forced=True`) but still refuses the *hard* `worktree_state_blockers` (uncommitted/unpushed), so force never discards local work. The TUI's `c`/`C` and the `cockpit close` CLI (`cockpit/close.py`, the `/cockpit:close` command — defaults to the cwd's worktree, `--force` overrides the soft open-PR gate) are the two human entry points; both resolve the same `(state, blockers)` and `enqueue` the same `TeardownRequest`, and the daemon's autoclose/orphan-reap enqueue alongside them — all draining through the one `orchestrators.teardown` path. **Closing never runs teardown inline** (no daemon → the marker stays durably queued); only the daemon writes the cache.
  - `m` mute → writes a `NudgePref` (session state, not a cell) + slow kick to republish `pr-muted` + 🔇.
  - `N` nudge → `cmux.nudge_if_idle` with no pr/category (overrides mute + throttle) but still honours the idle gate — passive skip-with-toast when not at rest, never a forced `send`.
  - `n` new → modal (`NewWorkspaceScreen`), then `cockpit new <source>` detached via module dispatch (`python -m cockpit.cli new`, **not** `spawn.py` by path — that breaks imports); auto-detects bare/`#N`/URL/Linear-id/Slack-URL and routes to the modal's chosen repo (its `cwd`), then slow-kicks so the worktree surfaces later. `N` is off `n` so New gets the bare key.
  - **Row-action kicks are repo-scoped:** every state-changing row key (`w`/`c`/`C`/`m`/`n`) kicks `_kick_slow(<row's repo path>)` → `cycle_all(only_repo=…)`, which reconciles *only* that row's repo and skips the repo-spanning sweeps (`close_gone_cwd_workspaces`, `_reap_workspace_orphans`) + the plugin-update check — so the line refreshes without round-tripping `gh` for every other repo. The close queue is still drained (a `c`/`C` teardown lands there and the keypress is waiting on it). `s` sync, SIGUSR1, the periodic interval, and startup stay **full-cycle** (`only_repo=None`). An unknown `only_repo` path reconciles nothing (no fall-through to all-repos).
- **Global keys:** `s` sync, `o` output, `q` quit, `u` self-update. (No `r` repo-config key — the per-row repo-config view was removed; the command palette's "Show config: all repos" / "Edit config" entries remain.)
- **`u` self-update:** `_check_update` rides the slow tick (`_run_slow` finally, `exclusive` to coalesce manual kicks) + once at startup — so a fresh release surfaces within ~one slow interval, not up to an hour. It compares `version.running_version()` vs `latest_version()` → header "⬆" indicator; `u` exits watch with `RESTART_EXIT_CODE` (42), which **`cli.py`'s watch branch** catches (`_self_update_and_reexec`): after the TUI tears down it runs the in-wheel Python updater (`cockpit.lib.updater.run_update`) then `os.execvp`s a fresh `cockpit watch` on the new version. No shell supervisor — the update logic ships in the wheel (`updater.py`), so there's nothing to cache-hunt. The reinstall (`uv tool install --force --no-cache` of the newest plugin-cache version dir, with a downgrade guard) lands on disk *before* the exec loads it; an install failure declines the re-exec (returns the failure, leaves the user a shell). `_running_as_installed_cockpit()` gates the re-exec — a dev's `uv run cockpit watch` from a worktree must not be auto-swapped for the released wheel; it degrades to a stderr hint. `cockpit update --check`: exit 10 = available, 0 = current (the former `bin/update.sh --check`). `bin/update.sh` is now **first-install bootstrap only** (uv + first `uv tool install`, then `exec cockpit update --skip-install`); `bin/cockpit.sh` is gone (devs run `uv run cockpit watch` directly).
- **stdout:** all tick prints go through one process-wide `_QueueWriter` — **never** per-tick `redirect_stdout` (the threads race).

### Only the daemon writes the cache; renderers read

`lib/starship.py` field printers are strictly read-only (no gh/git/subprocess/`atomic_write`).

- **Slow tick** (`slow_poll_interval_seconds`, 300s) — `cycle.py::cycle_all`: full reconcile (gh fetch, base-distance, per-PR JSON, PR flat cells, git-state cells, pills).
- **Fast tick** (`fast_poll_interval_seconds`, 30s) — `cockpit.py::_fast_tick`: network-free republish of git-state (`write_git_state_cache`) + PR flat cells from disk (`republish_pr_caches_from_disk`), so `git checkout` / tmpdir wipes recover in ~30s.

New cell → writer in `cache.py`, call site in the slow tick (decide + snapshot) and/or fast tick (republish). **Never** let a renderer read source state directly — that same-render disagreement is the bug class this avoids.

### `sidebar_color` — cosmetic, cmux-only, per-repo

Applied slow-tick via `_apply_repo_colors` → `cmux … set-color`, deduped in `pill_state` under `color:<ref>` (cmux touched only on change/restart). Validated at preflight (`_validate_sidebar_colors`, `sys.exit(2)` on unknown). Valid set = `colors.CMUX_COLOR_ANSI` (= `cmux.WORKSPACE_COLORS`).

### Workspace names track the branch (`wt.label`), re-asserted on both ticks

`wt.label` (`git.py::branch_label`) derives from the *branch*, not the dir basename (`wt.short`): strip `branch_prefix`, drop a leading base segment (`master/`), slugify, drop a leading ticket/PR token (`pe-4608-`) — but never to `""`. cockpit resolves workspaces by cwd→path, never by name; `cmux.rename_workspace_if_needed` re-asserts the label idempotently (slow tick `_refresh_tracked_pills`/`_refresh_orphan`; fast tick `reconcile_workspace_names`), recovering drift in ~30s. Cosmetic, never a `send`. **Consequence:** to relabel, rename the *branch* — a manual workspace rename is reverted next tick. **Exception:** any **main-branch** worktree — `wt.is_primary` **or** `wt.branch in MAIN_BRANCHES` (`cockpit.lib.constants`) — keeps its custom name (`reconcile_workspace_names` guards on both), so a `master` workspace named e.g. `morning` isn't force-renamed. The branch half of the guard matters in a **bare repo**, where no sibling worktree is ever `is_primary`: a feature worktree temporarily parked on `main` would otherwise be renamed to `main`, colliding with another workspace and breaking switching.

### The daemon creates worktrees in the background — never blocking the tick on `git`

`cycle.py::_spawn_missing_workspaces` shells out via module dispatch (`python -m cockpit.cli new`) in a detached `Popen(start_new_session=True)`:

- **My PR, no worktree** → `cockpit new --pr <n> --repo <name>` (plan-only prompt). Always on.
- **`review_prs` (per-repo, default false)** → every coworker open PR without a worktree → `cockpit new --pr <n> --review --review-command <cmd>`. Discovery: `gh.list_open_pr_heads` (uncapped `is:pr is:open`; the normal query is `author:self`-only). Validated `_validate_review_prs`. The seeded first turn is the **configurable** `review_command` (`config.py::review_command`, single-sourced from `REVIEW_COMMAND_DEFAULT`, per-repo → global → default, validated `_validate_review_command` as a leading-`/` string). The default is cockpit's own **`/cockpit:review` plugin command** (`commands/review.md`) — it ships with the plugin, so it resolves in every spawned review workspace for every cockpit user and every watched repo (a personal global skill like `/pr-review` only resolves for its owner; a project skill only when the watched repo is that repo — the plugin command is the only home portable across all three). `/cockpit:review` is **convention-aware**: it reads the *target repo's* `AGENTS.md`/`CLAUDE.md` and grades the diff against the rules that repo documents, so it isn't cockpit-specific. Override per-repo (or globally) with the built-in `"/review"` or a personal `"/pr-review"`. The auto-review is **dry-run**: both the `/cockpit:review` body and `spawn._review_prompt`'s closing line tell it to report findings and **ask before posting comments or submitting an approve / request-changes verdict** — a human authorizes those, the daemon never auto-posts. **Keep the spawn-layer default in sync with `REVIEW_COMMAND_DEFAULT`** (`spawn.py` imports it for both the `--review-command` argparse default and `_review_prompt`'s param) — don't reintroduce a literal `"/review"`.

Both go through `_bg_spawn_pr`, which guards in-flight launches in `pill_state` (`spawn:<owner>/<name>:<branch>`, TTL `_SPAWN_INFLIGHT_TTL_SECONDS`) against a double-launch; logs to `$COCKPIT_HOME/spawn.log`. Review worktrees then track via the normal `match_worktrees` coworker path; orphan auto-spawn stays `{self_user}/`-gated.

**`in_place` repos opt out of all of the above.** A repo registered by bare `cockpit new` (no source/`--cwd`/`--repo`) carries `"in_place": true` and `_spawn_missing_workspaces` **early-returns** for it — the user works in-place on the main worktree (often `master`, possibly off-GitHub) and never wants cockpit creating PR/review/orphan worktrees. The row still renders (it's derived from `git worktree list` + the cell writers, independent of spawning). Registration is `registry.py::register_cwd(in_place=True)`: it appends the cwd's main repo to `config.json` (empty branch prefix when off-GitHub via `gh_self_user`'s try/except; `default_base` from `gh` then git `symbolic-ref` then `"main"`), skips the interactive branch-prefix prompt (irrelevant with no worktree branches), and is idempotent — an already-registered repo is returned untouched, so a bare spawn in a normal managed repo does **not** flip it to in-place. Validated `_validate_in_place` (bool). The bare path in `spawn.py::main` then flows through the existing `--cwd` branch (workspace at the repo root, no branch, no worktree). **Do not** let `in_place` repos reach any auto-spawn — the early return is the single gate.

### Slack thread source — codename branch, MCP-delegated fetch, no `claude mcp list` probe

`spawn.detect_source` classifies a Slack permalink (`slack.SLACK_URL_RE`) as `slack` mode — a user-initiated source only (never daemon auto-spawned), so it has no cache cell / pill / `docs/state-machine.md` node, same shape as `actions`. A Slack URL carries no human name, so spawn synthesizes a deterministic codename branch (`codename.codename(slack.slack_seed(url))` → `<prefix><adj>-<noun>`). The seed is the thread's **stable identity** (channel id + message ts via `slack_seed`), NOT the raw URL — so query params (`?thread_ts=…&cid=…`) and the `archives` vs `app.slack.com/client` shape don't change the branch, keeping re-spawns idempotent. Cockpit never calls the Slack API: `_slack_prompt` delegates the thread read to the in-session Slack MCP (mirrors `_linear_prompt`), and under `use_slack: true` instructs Claude to append a topic slug to the codename (`cosmic-otter` → `cosmic-otter-fix-oauth`) and rename the workspace. **Never** add a `slack_mcp_available()` / `claude mcp list` pre-flight gate: that probe is unreliable for claude.ai-managed connectors (false-negatives even when live), so a positive-detection gate would silently disable the feature — the prompt's own retry-then-STOP logic handles a genuinely absent connector. `use_slack` (bool, `_validate_use_slack`) gates only the fetch/rename detail; the URL is seeded as context regardless.

### First-turn prompt prose lives in packaged `cockpit/prompts/*.txt`, not Python string lists

The spawn first-turn prompts (`spawn._linear_prompt` / `_github_issue_prompt` / `_slack_prompt` / `_plan_only_prompt` / `_review_prompt` / `_actions_prompt`) render packaged templates via `cockpit.lib.templates.render(name, **slots)` (`str.format`, no escaping needed — rendered bodies have no literal braces). The split is strict: **templates carry only static prose + `{slots}`; the Python builders own all control flow and value computation** — they compute the interpolated values, choose which template/sub-block applies (Slack's two modes are `slack_fetch.txt` vs `slack_context.txt`; conditional blocks like the plan-only PR context and the Actions related-PR line are built in Python and injected as a `{source_block}` / `{related_pr_block}` / `{context}` slot), and pass the shared `plan_tail` (`plan_tail.txt`) into templates that end with `{plan_tail}`. A missing slot raises `KeyError` loudly — never a silent stray placeholder. **Do not** re-inline prompt prose into Python lists, and **do not** add conditionals to a `.txt` (no templating engine — pick the template in the builder). The `/cockpit:review` command name still leads `_review_prompt` (it is delivered as a slash command, expanded in-session — see `commands/review.md`); only its surrounding context is templated. **Packaging gotcha:** hatchling ships only **VCS-tracked** files, so a new `cockpit/prompts/*.txt` must be `git add`ed or it silently won't land in the wheel (`importlib.resources` then `FileNotFoundError`s on the installed daemon); `tests/test_templates.py` asserts every template resolves.

### The one cache exception: session-scoped cells (written outside the daemon)

`lib.claude.stash_from_stdin` writes `context-<sid>`, `rate-limit-5h-<sid>`, `model-<sid>`, `permission-mode-<sid>`, `transcript-path-<sid>`, `cost-<sid>` from the statusLine stdin (the only place that data exists). Never read by the daemon. **Do not** extend this exception to a new cell.

### Nudge idle-gate: trust the `idle=` pill, NOT cmux's native `Needs input`

`nudge_if_idle` (`lib/cmux.py`) must tell "parked at prompt (safe to `send`)" from "awaiting a y/n permission (unsafe)". cmux native `claude_code=` has `Running`, `Idle`, and the **ambiguous `Needs input`** (fires for both an idle session aged past Notification *and* a pending permission). The gate: block on native `Running`; safe iff the `idle=` pill is present OR native is the unambiguous `Idle`; self-heal a dropped Stop-hook write by re-asserting `idle=` under native `Idle`. The pill (`hooks/cmux-idle-pill.sh`) uses a verify+retry loop — its silent loss under cmux contention (`Broken pipe`) was the original bug. **Never** simplify the gate to trust `Needs input`.

### `tickets` config — the one provider selector (replaced the `use_linear` bool)

`tickets` is an **object** with self-describing fields — `{provider: none|linear|github, close_on_merge: bool}` plus, for Linear, `keys: [<team-prefix>]` / `dev_done_state` / `merge_done_state`, and for GitHub, `dev_done_label: <label>` (default `"ready for review"`). The bare string `"tickets": "github"` is shorthand for `{provider: github}` (defaults elsewhere). The field *names* carry the meaning — `dev_done_label` is a GitHub issue label, `dev_done_state` is a Linear workflow-state name — so a reader (and cockpit) never has to infer it from the provider. Fields resolve **per-field** repo-block → global-block → default (`config.py::_tickets_field`), so a global `tickets.close_on_merge` (or any field) applies to a repo whose own block omits it — a repo block doesn't have to repeat shared settings. GitHub also has `start_label` (opt-in): when `spawn.py` creates a worktree on a `gh-issue` source, it marks the issue "work started" via `gh issue edit --add-label` (`github_issues.add_label`, `config.github_start_label`) — the one *spawn-time* tracker write, best-effort (a failed label never blocks the spawn), distinct from the slow-tick `close_on_merge` write. Resolved per-repo over global by `config.py::_active_tickets` (the repo entry's whole `tickets` block wins outright); `tickets()` is the global provider, `repo_tickets(cfg, repo_entry)` the per-repo one. The provider selects: the spawn fetch/rename prompt, the `devdone=` pill, the done-on-merge writer, and the TUI ticket columns all gate on `repo_tickets(...)`. `use_linear` is **gone** — preflight (`_validate_tickets`) hard-fails on a leftover key. Back-compat: a repo with the legacy flat `linear_keys` but no `tickets` resolves to `"linear"`, and the legacy flat `linear_keys` / `linear_dev_done_state` / `linear_done_on_merge` / `linear_merge_done_state` keys are still honored as fallbacks (the `tickets` block's `keys` / `dev_done_state` / `close_on_merge` / `merge_done_state` take precedence), so existing Linear configs keep working untouched. `linear_team_keys(cfg, repo_entry)` is the reader for the team prefixes (`tickets.keys` → legacy `linear_keys`). `cockpit.lib.tickets::provider_for(cfg, repo_entry)` → a `TicketProvider` (or None) is the **single source of truth** mapping the provider onto its strategy (`dev_done_value(cfg, repo_entry)`, `parse_footers`, `fetch_states`); `cycle.py` calls the strategy instead of sprinkling `provider == "github" ? … : …` ternaries. **Do not** re-introduce those ternaries — add a `TicketProvider` field. Settings readers (`ticket_close_on_merge`, `github_dev_done_label`, `linear_*_state`, `linear_team_keys`) source via `_tickets_field`; **do not** add a new top-level `github_*` flat key — extend the `tickets` object. The accepted-field *schema* is owned by each provider — `linear.py` / `github_issues.py` export `CONFIG_FIELDS` (`(name, kind)` pairs), `tickets.py` composes them with the common fields and validates via `tickets_field_errors` (which also rejects a field that belongs to the *other* provider), and `preflight._validate_tickets` just delegates. **Add a new ticket setting** by adding it to the provider's `CONFIG_FIELDS` + a reader in `config.py` — not by editing a hardcoded list in preflight.

### `devdone=` pill — the ticket provider is the one auxiliary (read-only) state source

Gated on `repo_tickets(...) != "none"` + a PR-body delivery footer. Delivery is **footer-only** (`provider.parse_footers`): Linear → `Linear: [PE-1234](url)` (`lib.linear.parse_linear_footers`); GitHub → a closing keyword `Closes #123` / `Fixes owner/repo#45` (`lib.github_issues.parse_github_issue_refs`) — never a branch-slug or a bare mention, which catch non-delivered tickets (same strict signal as `morning-align`'s `linear_delivery.py`). The `{tickets, fetched_at}` block is cached in the PR JSON under the (historically named) `linear` key; `cycle.py::_prefetch_linear_blocks` decides refetch-vs-carry-forward per PR (`_decide_linear_refetch`: footer-id change or past `linear_state_ttl_seconds`) reading the *prior* snapshot via `ctx.pr_payloads`, then resolves the union of due ids across *all* a repo's PRs via `provider.fetch_states` (Linear: one **batched** GraphQL query per team; GitHub: one `gh issue view` per issue — an issue carrying the dev-done label `github_dev_done_label` (default `"ready for review"`, via `tickets.dev_done_label`) maps to that label so the comparison stays provider-neutral). It runs once before the write loop so each `write_pr_cache` still overwrites against the old file. `_track_dev_done` raises the pill (`apply_devdone_pill`) only when *every* delivered ticket equals `provider.dev_done_value(cfg, repo_entry)` (Linear's `dev_done`/"Dev Done" state / GitHub's primary accepted label, casefolded). Passive, never a `send`, absent from `ACTIONABLE_KEYS`. **Do not** drop back to a per-PR fetch fan-out; keep the Linear union batched. A per-source query failure isolates to its own ids (they stay None) — never blanks unrelated tickets.

### done-on-merge — the daemon's only sanctioned tracker *writes*, dispatched per provider

Opt-in (default false, per-repo) via the shared `tickets.close_on_merge` (`config.py::ticket_close_on_merge`, legacy `linear_done_on_merge` flat key honored as fallback). Slow-tick `cycle.py::_transition_merged_tickets` dispatches on `provider.name` to one writer, both fired on `_is_post_merge_stale` independently of teardown (ship even when the worktree is held back), both guarded by a per-run `merged-done:…` marker in `pill_state`, both viewer-gated (never touch a teammate's ticket), both idempotent + logged:

- **Linear** (`_transition_merged_linear`, gated `ticket_close_on_merge` + `linear_team_keys` + `LINEAR_API_KEY`): moves the ticket to `linear_merge_done_state` (`tickets.merge_done_state`, default "Done", distinct from `dev_done_state`) via `update_ticket_state` → `issueUpdate`. Per ticket: skip unless assigned to the API-key `viewer`; skip if already at target or `type: canceled` (note "Dev Done"/"Done" are both `completed`, so name-equality decides); else resolve the target UUID (`fetch_team_states`) and mutate. The `viewer` id + per-team state maps are cached across ticks (`_cached_viewer_id`/`_cached_team_states`, TTL `linear_identity_ttl_seconds`; viewer key is a non-secret fingerprint of `LINEAR_API_KEY`); the `viewer` fetch is **lazy** (first eligible ticket only).
- **GitHub** (`_transition_merged_github`, gated `ticket_close_on_merge`): runs `gh issue close` on each delivered issue still open and assigned to the `gh` auth login (`github_viewer_login`, cached via `_cached_github_viewer`). GitHub auto-closes same-repo closing-keyword refs on merge, so this mainly catches cross-repo refs and label-only links.

A falsy/failed identity fetch is never cached (retries next tick); a failed write clears the marker to retry. **Precedent for any future daemon tracker write:** opt-in, viewer-gated, idempotent, logged.

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
