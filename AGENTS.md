# Privacy & Internal References

This is a public repository. **Never** include any of these in commits, PRs, code comments, or documentation:

- Internal ticket IDs (Linear `ENG-123`, Jira `PROJ-456`)
- Internal GitHub PR/issue URLs from private repos
- Real names of teammates (use roles: "the reviewer", "the on-call engineer")
- Internal Slack channels, wiki URLs, tool links, hostnames, service names, infra identifiers
- Customer names or company-specific identifiers

In commit messages and PR descriptions, say what changed and why, not which ticket tracks it. Reference public GitHub issues only. If the context needs an internal ticket, summarize the requirement. Before you commit, scan for what gitleaks cannot catch: your team's ticket prefixes, and `@firstname` references that are not GitHub handles.

## Worktree discipline

Use a dedicated git worktree for every code change. **Never** commit directly to `main`/`master` in the primary checkout. **Never** edit in place on a feature branch without a sibling worktree. cockpit derives per-branch state from `git worktree list`, so an unisolated branch is misattributed or dropped.

Before any Edit or Write, run `git branch --show-current` and `git worktree list`. If HEAD is `main`/`master`, or the tree is the primary checkout (first entry), spawn a worktree with `cockpit new` first. If HEAD is already a non-main branch in a sibling worktree, proceed. Do **not** spawn another.

## Architecture notes

Each `###` is one invariant: the rule and its enforcing `file::symbol`. Obey the **Never** / **Do not** lines. `docs/state-machine.md` holds four Mermaid diagrams, the control-flow half of this file. Keep both in sync.

### Shared rules

- Match a workspace to a repo by cwd against that repo's own `worktrees()`. **Never** use a path-prefix test, because a worktree usually lives in a sibling directory.
- An extracted helper takes its input and does not fetch it. A helper that fetches its own input silently un-mocks every caller's test stubs.
- `None` means "couldn't ask". `[]` means "answered with nothing". **Never** collapse them, or a network blip reads as an empty result.
- Fold and snooze membership comes from the render's own record. **Never** re-derive it, because it partitions per chain, not per row.
- Updates are brew's job. **Do not** add a version check, a `u` key, a re-exec, or `cockpit update`.
- A new `~/.claude` install target needs an inverse in `teardown_claude_integration`, or `cockpit teardown` leaves it behind.

### Keep `docs/state-machine.md` in sync — a stale diagram is worse than none

Any change to `match_worktrees`, `_spawn_missing_workspaces`, `nudge_if_idle`, `_track_dev_done`, `_maybe_autoclose`, the `cache.py` cell writers, tick cadence, or the spawn/teardown/nudge/devdone/color rules MUST update the matching diagram in the same PR.

### Docs have four altitudes — put a fact at exactly one of them

`FEATURES.md` (user), `README.md` (visitor), `docs/config.md` (operator), and `AGENTS.md` + `docs/state-machine.md` + `specs/` (you: rulebook, control flow, behavior ledger). **A change to user-visible behaviour updates `FEATURES.md` in the same PR.** Nothing fails when it is skipped.

- **Don't restate across altitudes.** Duplicated prose drifts silently.
- `FEATURE_GUIDE_URL` points at `main`. The version bump lands before `tag.yml` pushes the tag, so a pinned URL 404s during the release-PR window. **Do not** pin it to a tag.
- A user-facing doc names capabilities, never `file::symbol`, so it survives renames.

### Inventory is derived every cycle, never stored

Each cycle re-reads `git worktree list` and cmux's workspace list. Only PR payloads are cached (`~/.config/cockpit/cache/<repo>__pr-<N>.json`), because each is a network round-trip. **Never** add a stored identity file.

### Packaged as the `cockpit` console script — invoke by subcommand, never by file path

- **Dispatch:** `cli.py` routes `watch / setup / teardown / statusline / starship / idle-pill / new / close / diff / nudge` and `--version`. **Add entry points as `cockpit <sub>` in `cli.py`**, not as file-path invocations.
- **`teardown`** (`config.teardown_claude_integration`) inverts the `~/.claude` writes of `setup`. Run it *before* `brew uninstall`.
- **Distribution:** a brew formula whose single source of truth is the tap repo `khivi/homebrew-cockpit`. **Do not** vendor it here. There is no plugin, marketplace, or self-update path.
- **Version:** static in `pyproject.toml`, read via `importlib.metadata`. `preflight._warn_cockpit_not_on_path` only warns.
- **Claude footprint:** `cockpit setup` idempotently writes the statusLine command, idle-pill hooks, command templates under `~/.claude/commands/`, and cmux's `sidebar.showPullRequests` (see the `pr` pill section). Each has an inverse in `teardown_claude_integration`.
- **`_COCKPIT_HOOKS` is exactly two hooks:** `Stop` → `idle-pill stop` and `UserPromptSubmit` → `idle-pill prompt`. Only those have a reader; **do not** add one without a reader. The drop pass in `install_claude_hooks` sweeps every event in the file, since a retired hook sits under an event the template no longer names. An event left with no groups is deleted. `_COCKPIT_HOOK_CMD_RE` still matches `statusline` to clean older installs.
- **`{python}` pin:** starship configs use `{python} -m cockpit.cli <sub>`, pinned to `sys.executable` at setup. **Never run setup from inside a worktree venv**, because it bakes in an ephemeral `.venv/bin/python` that dies on cleanup.
- **Pin self-heals:** `cockpit watch` re-pins at startup via `config.repin_interpreter_if_stale`, rewriting only the interpreter prefix.
- **idle-pill hook:** `cockpit/hooks/cmux-idle-pill.sh` ships in the wheel and is `bash`-exec'd, so it needs no exec bit.

**Slash commands are user commands, not a plugin.** `cockpit/claude_commands/*.md` wrap `cockpit new/close/broadcast/nudge/diff $ARGUMENTS`. A template documents the CLI and **never** reimplements it. `parse_args` **errors** on a bare `--context`, because an unexpanded flag means the substitution failed. **Do not** teach the CLI to synthesize its own context. Templates install as flat, hyphenated files (`cockpit-new.md` → `/cockpit-new`), since colon-namespacing is plugin-only. hatchling ships only VCS-tracked files, so `git add` a new template.

**Every gesture is a command; cockpit ships no agent skill.** The daemon delivers review notes by sending the literal `DIFF_COMMENTS_NUDGE` string, which a typed command serves. **Do not** add a skill for a gesture the daemon already sends a command for.

### `cockpit watch` is a Textual TUI, and the TUI *is* the daemon (`cockpit/tui/`)

- **No headless mode.** A non-TTY `watch` exits 2. The app owns the pidfile.
- `daemon._reclaim` (via `daemon.reassert_pidfile`) must re-create the state directory. **Do not** use `ensure_state_dirs()`, which also seeds a `config.json`.
- Slow and fast ticks run in `@work(thread=True)` workers, serialized by `_tick_lock` acquired inside the worker.
- **Never** let the slow tick's `on_repo_done` hook write a cell. Only the daemon writes.
- Use `loop.add_signal_handler`. **Never** use `signal.signal`, which raises off the main thread.
- Tick prints go through one `_QueueWriter`. **Never** use per-tick `redirect_stdout`.

#### Table

- The table is read-only (`worktree_table.py`), keyed by worktree path, grouped under header rows (`HEADER_KEY_PREFIX`).
- The status glyph sits in a fixed-width slot (`_STATUS_SLOT`), because 🔇/🔔 differ in ink width. **Do not** drop that padding.
- An ellipsized cell **must** keep its full text on its tooltip (`row_tooltips`).
- `pr-nudge` gives 🔔 and is `PR.nudge_issue`, so bell and nudge cannot disagree. Mute (🔇) wins. `pr-snoozed` gives fold membership and no glyph.

#### Links

Every cell naming something on the web is an OSC 8 hyperlink (`worktree_table.py::_cell_links` / `_apply_links`). `CI` links to `<pr-url>/checks`.

- The ticket URL **must** be cached. The daemon writes it every cycle into the `ticket` block's `url` (`cycle._stamp_ticket_urls`), because three providers expose it only in the PR body's footer. **Do not** resolve a missing link in `worktree_table.py`.
- The stamp always writes, including `None`, and runs on carried blocks, because the ticket id decides carry-versus-rebuild.
- `t` reads the cached string first (`app._open_ticket_url`), so key and click agree.
- **Never** link a blank cell.
- **No underline and no click handler.** A `DataTable` handler would collide with row select or Focus. `p` and `t` stay the keyboard route.
- The tooltip names the destination. `test_links_survive_all_the_way_into_terminal_output` pins that Textual still emits the escape.

#### Row actions

- Except `n`, `f`, `m` and `z`, row keys never touch cmux or the cache.
- Footer help is gated by `ACTION_REQUIRES` fed by `current_capabilities()`. Actions stay bound and self-guard.
- `f` ensures a workspace (spawn if missing), then focuses. `use_worktree: false` repos host several sessions at one cwd, so `f` resolves by repo name (`_workspace_ref_by_name`), then cwd, then spawn.
- `c` runs `probe_blockers`, enqueues a `TeardownRequest`, slow-kicks. The commit guard splits by ownership (`worktree_state_blockers`). Own branch: `git.count_unlanded` (patch-id against `origin/<default>`, reachable from no remote ref except `origin/<branch>`). Coworker's branch: `git.commits_only_local`. **Do not** collapse the two. **Do not** re-baseline `count_unlanded` on `origin/<branch>`.
- `c` on a primary checkout: `teardown` always skips `git worktree remove`. On the default branch it closes the workspace only and the guard relaxes. On a feature branch it deletes the branch and the guard does not relax.
- `C` overrides the soft open-PR block but still refuses `worktree_state_blockers`. Closing never runs teardown inline. With no daemon the marker stays queued.
- `m` and `z` repaint on the keypress (`app._repaint_pref` calls `cache.restamp_pref`), the one row-action cache write. It writes cells and snapshot, because `republish_pr_caches_from_disk` reverts cells alone. **Do not** extend it to a cell the daemon derives.
- `n` launches `cockpit new <source>` detached via `python -m cockpit.cli new`. **Do not** run `spawn.py` by path.
- **Do not** add a row key that pipes into `cmux diff`.

#### `z` snooze

- `z` writes `NudgePref.snoozed` plus three wake snapshots from the cached payload, resolved via `_cache_repo_name`, because the config `name` matches no file.
- Keep mute and snooze as separate fields, **never** one tri-state, or the CLI's mute would self-clear. `z` clears any mute it lands on.
- `cycle.py::_resolve_prefs` wakes a snooze on review activity (`wake_on`, from `total_from_others` not `unaddressed`), new work (`nudge_issue` differs from `wake_nudge` and is non-empty), or a push to a PR I review (`head_oid` differs from `wake_head`, `not PR.mine` only). An empty snapshot wakes nothing.
- **Do not** fold `head_oid` into `wake_signature`, which my own PRs share. **Do not** give the snooze a time-based `until`.
- A stacked chain snoozes and wakes as one unit.
  - The pressed row's direction applies to every member (`app._apply_snooze`).
  - Membership comes from `worktree_table.chain_paths`, read on the main thread in `action_snooze_row` (render's own record, see Shared rules).
  - The wake half is `cycle.py::_wake_chains`. It fires on a wake, never on the chain existing. `cockpit nudge snooze <N>` stays per-PR.
  - **Do not** give the snoozed row a glyph to fix a half-snoozed chain.
- A bare `cockpit nudge snooze` warns (`nudge_cli._warn_split_chain`) and the write stays per-PR. The tip comes from `stacks.py::chain_tip`. **Do not** re-derive a chain in `nudge_cli.py`. The warning fails silent on an absent cache.
- Both CLI commands write unconditionally, because `restamp_pref` plus `kick_running` converges a diverged pref and surface. Re-snoozing re-arms the snapshots from the cached payload, except with no payload: blanking `wake_on` to `"0|"` would wake it next tick.

#### `a` ask and `A` ask-snoozed

- `a` is the one manual send. `AskScreen` routes through `cmux.nudge_if_idle` with no `pref_key`, so it overrides mute and snooze but honours every idle guard. It writes no cell or pill.
- **Do not** add a manual nudge key with a canned message.
- The modal is an `Input`, **never** a `TextArea`, or a multi-line box submits several truncated prompts.
- `a` carries the typed line and nothing else. **Do not** re-attach diff comments.
- On a repo header `a` addresses the whole repo. The fan-out matches by cwd (see Shared rules) and excludes the daemon's own.
- A partial send keeps the draft and the refs that missed, and the retry targets only those. A refusal keeps the text and names its cause from `skips`. A merely busy session queues the line (see the queued-ask rule).
- The async state hint (`rest_skip_reason`) **never** blocks the submit. `nudge_if_idle` is the sole authority.
- `A` is `a`'s fan-out aimed at one snoozed fold. Membership comes from `snoozed_paths` (see Shared rules). **Do not** pass a `pref_key`: snoozing silences the automatic nudge, never a typed line.

#### Kicks

- Row-action kicks are repo-scoped (`_kick_slow(<row's repo>)`) except `z`. `s`, SIGUSR1, the interval and startup stay full-cycle.
- `z` kicks full-cycle, because `cycle_all` builds `ReviewFolds` only when `only_repo is None`. **Do not** build `folds` under `only_repo`: a bucket with no ref from the scoped repo is dissolved. **Do not** move the pass to the fast tick, which would read absent payloads as "no reviews left".

#### Global keys, palette and header

- Global keys: `q`, `s` (in `GLOBAL_ORDER`, not `ROW_ACTIONS`) and the repo-scoped `h`. **Do not** give `action_show_output` a key back. **Do not** list sync in `COMMANDS` as well as binding it. Updates are brew's job (see Shared rules).
- "Feature guide" opens `open_url(FEATURE_GUIDE_URL)`. **Never** point it at a local path, since the wheel may not ship the file. "What's new" is `action_open_release_notes` to `RELEASE_NOTES_URL`. **Do not** pin either URL to a tag.
- `app._announce_upgrade` compares `version.upgraded_version()` against cockpit's own last run, with no network. **Do not** widen it into a version check. A first-ever run and an unresolvable `running_version()` stamp nothing.
- `ConfigCommands` implements `discover` as well as `search`, because the palette is empty until `discover()` fills it. **Do not** add an entry without a `discover` hit.
- `COMMANDS` order is the menu: in-app overlays, then `$EDITOR`, then the browser pair. **Do not** append to the end.
- `#header-repo` names the cursor row's repo, fed by `app._refresh_footer_caps`. The colour comes from a `_repo_color` map filled by `update_inventory`. **Never** call `load_config()` there. **Do not** add a Repo column or use `DataTable.fixed_rows`.
- `#header-repo` owns the one `1fr` slot, so segments right of it stay right-anchored. `test_the_countdowns_do_not_move_when_the_cursor_changes_repo` pins it. **Do not** add a second growing segment.
- `#header-brand` shows the running version, dim and linked. `brand_text` takes the URL as an argument, set from `RELEASE_NOTES_URL` onto the `version_url` reactive. **Do not** import the URL into the widget. **Do not** move the version into the menu.
- The trailing `≡ Menu` (`#header-menu`) is the palette's one visible entry point and is unconditional, because `ctrl+p` cannot come from `BINDINGS`. **Do not** move it into `FooterBar`. **Do not** print the key beside it. Override `link-color` and `link-style` in CSS, not `color`. Use a single-cell glyph
- The countdowns use a glyph in the bar and a word in the tooltip (`SLOW_GLYPH` / `FAST_GLYPH`). **Do not** put a glyph in the bar that the tooltip does not spell out.
- Footer keys explain themselves on hover through `TOOLTIPS`. **Do not** rebuild the footer as a widget per key.

#### `h` parks a repo

`h` is the one repo-scoped key and the only user state that stops the daemon polling.

- One key, three meanings by cursor row: expand or collapse the hidden section, un-park a revealed repo, park the cursor row's repo. **Do not** add a second key for reveal.
- `on_click` on the hidden row **must** resolve the row from `event.style.meta`, never the cursor, because Textual runs it before the cursor moves.
- Key by resolved path in `$COCKPIT_HOME/hidden-repos.json` (`lib/hidden.py`), never the config `name`. It fails open.
- `cycle_all` filters a parked repo out, except when `only_repo` is set.
- Parking closes the repo's cmux workspaces (`_park_workspaces`), workspace-only, matched by cwd (see Shared rules). It spares the daemon's own and any not `workspace_is_idle`.
- Un-parking closes and respawns nothing, and parking never costs a fetch.
- A spawn into a parked repo un-parks it in `spawn.py::_unhide_spawn_target`, one gate for every mode, after the spawn, keyed off `main_worktree_path(wt)`. **Do not** move it into a per-mode branch. **Do not** key it on the worktree path.
- `n`'s repo picker keeps parked repos, sunk and dimmed. `_spawn_new` un-parks before launching, redundant with the gate so the row repaints at once. **Do not** filter parked repos out. **Do not** delete either half as duplication.
- Parked repos collapse into one trailing session-only row (`HIDDEN_ROW_KEY`). `h` shows in the footer only on a repo-reading row (`HEADER_CAP`). The binding stays live everywhere.

### Only the daemon writes the cache; renderers read

`lib/starship.py` field printers are strictly read-only.

- Slow tick (300s), `cycle.py::cycle_all`: full reconcile (gh fetch, base-distance, per-PR JSON, PR flat cells, git-state cells, pills).
- Fast tick (30s), `cockpit.py::_fast_tick`: pidfile re-assert, then a network-free republish of git-state, cost and PR cells, plus name and colour reconcile, trailing-fold restore and the `idle=` pill re-assert. The last three write into live cmux and are `dry`-gated. It ends with the diff-comment hand-over and the seed-queue drain, each `dry`-gated through `nudge_if_idle`'s own `dry=`.

A new cell needs a writer in `cache.py` and a call site in a tick. **Never** let a renderer read source state directly.

Cell text is externally authored (PR titles, ticket ids, footer URLs). `cache.py::strip_control` neutralizes it inside `read_text`, because Rich's `Text` strips BEL but not ESC, so a title could inject its own OSC 8 link.

- Keep it in `read_text`, the one seam every flat-cell renderer shares. **Do not** re-implement it per renderer or move it to the writers, which would leave older cells raw.
- Strip whitespace first, then replace control characters one-for-one, never drop them. `_ellipsize` and `_STATUS_SLOT` count codepoints.
- Payload-derived text never passes a flat cell and needs its own call: `_ticket_ids`, the ticket half of `_cell_links`, and anchor text in `diff_comments.py`.

Key every flat cell by worktree path (`cache.py::cwd_cache`) or session id. **Never** key by branch: same-named worktrees in different repos would merge.

- Use the worktree, not repo+branch. Only the path is held by all three renderers (TUI, starship, `restamp_pref`).
- The path travels in the PR payload (`write_pr_cache`'s `cwd`), because `republish_pr_caches_from_disk` has only the JSON. Dedup per worktree.
- A PR with no local worktree writes no cells, but its JSON snapshot is still written.
- `find_pr_payload_for_cwd` prefers the snapshot stamped with this worktree, and falls back to a branch match only for older payloads.

### `cmux events` is a doorbell — it wakes a tick, it is never state

`lib/events.py::watch_workspace_events` treats an event as a trigger only. It kicks the fast tick, which re-derives everything. Gate on `has_capability("events.v1")` plus `is_cmux()`. **Do not** feed an event into a decision, a cell, or the slow tick.

- Subscribe to `workspace.created` and `workspace.closed` only. `sidebar.metadata.*` and `workspace.renamed` make the daemon ring its own doorbell.
- Debounce in the app. An event during a fast tick sets `_events_pending`, and `_run_fast`'s `finally` kicks once more.
- The cursor file is cmux's resume bookmark. **Do not** grow it into inventory or route it through `cache.py`.
- `on_unmount` `killpg`s the child, because killing the leader leaves a grandchild holding the stdout pipe. Give up after `_MAX_FAST_EXITS` quick exits.

The one payload read is the sidebar X. `_closed_workspace` passes `workspace_id` and `cwd` to an optional `on_closed` callback. Nothing caches the payload.

- A raising `on_closed` handler is logged, not fatal.
- Filter cockpit's own closes, or parking a repo tears down every worktree in it. `_note_self_close` records the UUID before the close and lives in `cmux_close_workspace_best_effort`. **Do not** re-implement it per call site. Key by UUID, not cwd.
- Route the X to the refusing gate. **Do not** map it onto `C`. Refusals toast loudly. `quiet` suppresses only the missing-worktree toast.

### `sidebar_color` — cosmetic, cmux-only, per-repo

Applied slow-tick via `_apply_repo_colors` and fast-tick via `_tint_repo_workspaces`, deduped in `pill_state` under `color:<ref>`. Preflight validates it (`_validate_sidebar_colors`, `sys.exit(2)`). Valid set: `colors.CMUX_COLOR_ANSI`.

### The `pr` pill replaces cmux's native sidebar PR row — which cockpit cannot set, and must not trust

cmux resolves a branch to a PR by name alone, so it shows the first of several PRs and a draft as `open`. `cockpit setup` turns that row off (`sidebar.showPullRequests: false`). Cockpit renders its own from `ctx.prs`.

- The `pr` pill is the one pill that names the PR. `draft`, `state` and the four `ci_*` kinds are still emitted, but their `_CMUX_RENDERERS` entries are `None`. **Do not** re-enable any without turning the `pr` pill off.
- CI rides the pill as a trailing glyph, and a non-passing build takes the colour. Keep `"ci"` in `ACTIONABLE_KEYS` although nothing writes it: it sweeps stale pills.
- `PR_KEY` is in `_PR_PILL_CLEAR_KEYS` but not `ACTIONABLE_KEYS`, so `clear_pr_pills` clears what `apply_pills` wrote.
- Put the emoji in the value, not `set-status --icon`. The renderer contract is a `(key, value, color)` 3-tuple.
- Emit it for every state including OPEN, so no pill means no tracked PR. **Do not** spawn pills for untracked workspaces.
- `lib/cmux_config.py` makes cockpit's one write into another tool's config. It edits `~/.config/cmux/cmux.json` as text, because the file is JSONC and a `json` round-trip strips its comments, and no `cmux config set` key covers this setting. A trailing `MARKER` comment owns the line, so `teardown` undoes only its own edit with no stored state.
- A file that already reads `false` is the user's. It gets no marker, so teardown never turns their choice back on.
- Skip any layout the line edit cannot prove by re-parsing, and print a by-hand instruction. Never guess.
- The write is setup-only and gated on `is_cmux()`. **Do not** move it to `watch` startup, which would override a user who turned the row back on.

A cmux card shows three rows before "Show more" and no setting changes that. Cockpit drops `wip` while `rebase` or `merge` is in flight, because it restates that pill. **Do not** fix a buried pill by reordering `KIND_ORDER`.

### `orgs` is a load-time defaults layer — nothing below `load_config` knows orgs exist

`config.py::apply_org_defaults` merges an org block into each member inside `load_config()`, so the reader chain gains an org rung with no call-site changes. **Do not** add an org-aware reader, an `org_*` field, or a `repo_org(...)` helper.

- One level deep, repo wins. A scalar the repo sets beats the org's, and a block unions per field. **Do not** make it recursive or a whole-block override. Rebuild each block into a fresh dict, never alias it.
- **Do not** persist it or add a writer that serializes `load_config()`'s dict.
- Validate effective values: `_validate_orgs` first (an undefined org hard-fails), then the merge.
- Orgs affect TUI ordering only. No org header row, no cross-repo workspace-group, no park-the-whole-org key.

### Stacked PRs: one cmux sidebar group + one indented TUI row — derived from `PR.base`, never stored

`lib/stacks.py::find_stacks` derives chains from `PR.base`, matching on `PR.branch`. **Do not** shell out to `gh stack view` or persist a stack. A fork yields one chain, because a workspace is in one group.

`cycle.py::_reconcile_sidebar_groups` renders each chain of two or more PRs with local workspaces as a collapsible group (`square.stack`). The header is `<tip> (N)`, the tip and **not** the root, with the repo's `sidebar_tag`. Members follow, tip first. It is cmux-only, best-effort and cosmetic: it never spawns, closes, nudges or writes a cell.

- Reconcile against cmux's live `workspace-group list`, not a `pill_state` mirror. Match by member ref, never name. Touch only groups overlapping this repo's refs.
- The header is cmux's spawned anchor, never a member. Spawn it with `--cwd $HOME`, outside every registered repo, or `_reap_workspace_orphans` reaps it. Close it on dissolve, because `ungroup` leaves it as a loose row. Also sweep groups where `icon == square.stack` and `members <= {anchor}`.
- The anchor must own a live shell (`_durable_anchor`), because `create` spawns it with no command and it dies silently. `create_workspace_group` swaps in an anchor spawned with `ANCHOR_KEEPALIVE_COMMAND`, then closes the husk through `cmux_close_workspace_best_effort`. **Never** use a raw close, which routes into teardown as a user X. **Do not** revert to the anchor `create` returns.
- Minimum fold size is one, so `create_workspace_group` refuses only an empty ref list. Callers set their own floor.
- A stack whose tip is snoozed joins the `snoozed` fold whole and stays out of `desired`, because groups do not nest. Key on the tip. Gate the divert on `folds is not None`, or a repo-scoped kick strands members. **Do not** use a position-based answer.

The TUI shows the stack as indentation from the flat cell `pr-base`. `stacks.py::stack_order` returns `(index, depth)` and `_stack_rows` sorts each chain under its tip. Nesting is one level. **Do not** restore the per-level cascade. Key by index, not branch, so a base cycle falls back to flat rows.

### Reviews and snoozed PRs sink to the bottom — TUI row bands + two trailing cmux folds

Both surfaces answer "is this my turn?" from flat cells, never a stored marker: row order in the table, two collapsed groups at the bottom of the sidebar (per org).

The snoozed band collapses behind a per-repo `▸ N snoozed` row, so a snoozed row has no glyph.

- `z` opens it, as one key with three meanings like `h`. **Do not** hang this off `h` or a new key. Enter and a single click toggle it too.
- The fold row carries `SNOOZED_CAP`, not `HEADER_CAP`, which would hide `z`. `_skip` suppresses every row key except `SNOOZED_ROW_ACTIONS`. Both drop their `ACTION_REQUIRES` entry via `req = None`, not an early return, which would skip `BACKEND_ACTIONS`.
- The cursor-skip loop must stop on it. It keys off its own sentinel, not `HEADER_KEY_PREFIX`.
- A snooze moves the cursor onto the fold that swallowed the row. Ask the table, since a snooze below the tip folds nothing.
- `_split_snoozed` partitions by chain, so `z` writes the whole chain's prefs. Membership is the render's own record (see Shared rules).
- A snoozed row keeps the 🔔 suppression: `_status_glyph` returns blanks, but `pr-nudge` is never blanked. **Do not** drop the suppression with the glyph. 🔇 wins over snoozed.

The TUI renders three bands per repo (`_row_band`): 0 my queue, 1 a coworker's PR I review, 2 snoozed. Snooze outranks review. The sort is stable. A chain bands by its tip. Mute is not a band, since 🔇 means "stop nudging me about a PR I'm working on".

Coworker reviews fold into one trailing `<org> reviews (N)` group, the one fold that spans repos. `not PR.mine` workspaces get the `eyeglasses` icon and are re-parked at the bottom every cycle (`--to-index 9999`, since `workspace-group list` reports no index). The key is the repo's `org`, else its `name`. A bucket with a `sidebar_tag` is named by the glyph (`_fold_tag`).

- It has its own pass, because one repo cannot see sibling reviews. `_reconcile_sidebar_groups` only collects into a `ReviewFolds` accumulator, and `_reconcile_review_groups` drains it once after every repo.
- Ownership splits by icon. The per-repo pass filters review-iconed groups out.
- Stacks win overlap. A lone review folds too. The drop-departed-members loop guards on `folds.owned`, not the bucket, because a hand-added workspace is the user's. **Do not** persist a "this is a review" marker.
- An incomplete cycle suspends the dissolve (`ReviewFolds.partial`), the one irreversible step, because a repo that never reported leaves its bucket absent, which looks like "no reviews". Re-park, rename and re-member still run. **Do not** key this on the bucket being empty instead of the cycle being complete.

Snoozed PRs fold into a second trailing `<org> snoozed (N)` group below reviews, through the same accumulator and pass walking `_TRAILING_FOLDS`. Membership is `NudgePref.snoozed`, mine included.

- Precedence on overlap: stacks, then snoozed, then reviews.
- Order comes from pass order, not a rank field. **Do not** reorder that tuple expecting names to sort it.
- Match the two families separately, or one claims the other's fold. The per-repo pass filters both icons out of `all_groups`, or it dissolves a snooze-iconed group every cycle.

Both piles are born collapsed, at create time only, since cmux creates groups expanded. Only `_reconcile_review_groups` passes `collapsed=True`. A stack stays open. **Do not** re-assert collapse per cycle, which would slam shut a fold the user opened, and **do not** read `is_collapsed` back.

`cycle.restore_trailing_folds` rebuilds a fold lost mid-interval on the fast tick. It replays the slow pass's `(name, refs)` record from `pill_state` and is not a second authority.

- It can only create: no dissolve, rename, member change or re-park. That is what permits 30s.
- Read `read_workspace_groups`, never `list_workspace_groups`, which flattens a failed read to `[]` (see Shared rules). **Do not** re-merge them.
- A live group of the same icon sharing any member means the fold exists. Match by overlap, not name.
- Filter refs against live workspaces. If all are gone, skip and leave the record.
- Keep the record in `pill_state`, never on disk. With no records, make no cmux call.
- Retiring a record rides `folds.partial`.

Whatever destroys the folds is still unidentified. **Do not** treat it as solved.

### Workspace names track repo + branch (`wt.workspace_name`), re-asserted on both ticks

`wt.label` (`git.py::branch_label`) derives from the branch, not the dir basename, and is never `""`. The cmux name is `label` under an optional `<tag>` prefix. Every path that spawns, renames or matches by name uses `workspace_name`. `rename_workspace_if_needed` re-asserts idempotently. Naming is cosmetic and never a `send`. To relabel, rename the branch. Any main-branch worktree (`wt.is_primary` or `wt.branch in MAIN_BRANCHES`) keeps its custom name. The branch half matters in a bare repo, where no sibling is `is_primary`.

`sidebar_tag` is opt-in per repo and applied by one function, `git.py::tag_workspace_name`.

- Expand `{repo}` once at load time (`config.py::expand_sidebar_tags`), right after the org merge, so an org can declare a tag and all five `repo_entry.get("sidebar_tag", "")` readers see the result. **Do not** teach `tag_workspace_name` the token. The value is the repo's `name`, else path basename, as in `broadcast._repo_label`. Never persist it.
- Apply the tag in both naming halves, or a fresh workspace is renamed a tick later: `Worktree.sidebar_tag` for the daemon, `spawn.py`'s `ws_name` for `cockpit new`. **Do not** add a third naming site.
- `spawn.py` resolves the tag after every routing hop, since routing rewrites `args.repo`. With no determined repo (`--cwd` alone), apply no tag. **Do not** fall back to `discover_repo()`.
- Pass the tag only at call sites that read `workspace_name`: the fast tick's rename pass, `cycle_all`, the orphan reap's `wt_by_name`, and the TUI's `_resolve_worktree`.
- The primary checkout takes no tag. A tag would double its repo name and break `_workspace_ref_by_name`.
- A stack header takes the tag through `cycle.py::_stack_group_name` and `tag_workspace_name`. A trailing fold spans repos, so `cycle.py::_fold_tag` reads the bucket's own declaration (the `orgs` block, or the repo entry for an org-less pile) and substitutes it for the bucket name. It reaches the cross-repo pass in `ReviewFolds.tags`. **Do not** feed a member's merged tag into a fold header: `{repo}` is already one repo there. In a fold `{repo}` expands to nothing, and empty falls back to the bucket name. Keep the state word, or the two folds and a stack header look alike.
- A tag ending in a non-alphanumeric takes a space, not `SIDEBAR_TAG_SEP`, because after a glyph the `·` renders as a stray dot. Test the last character, not for an emoji.

### The daemon creates worktrees in the background — never blocking the tick on `git`

`cycle.py::_spawn_missing_workspaces` shells out via module dispatch in a detached `Popen(start_new_session=True)`:

- My PR with no worktree runs `cockpit new --pr <n> --repo <name>`. Always on.
- `review_prs` (per-repo, default false) spawns every coworker open PR without a worktree. Dependabot PRs are excluded unless `"dependabot": true`. External (non-collaborator) PRs are excluded unless `"review_external": true`, because a fork PR's body and diff are untrusted and a Bash-capable agent on them risks prompt injection. The gates are independent.

The seeded first turn is the configurable `skills.review`, run dry: report findings, ask before posting. `skills.plan` and `skills.actions` are sibling seams, each followed by the shared `plan_tail.txt` gate.

All four `skills` fields default to unset and fall back to prose cockpit ships, because a command default (even built-in `/review`) lives outside the wheel and can contradict `review.txt`'s dry-run rule. The fallback is `review_prose.txt`, rendered into `review.txt`'s `{lead}` slot by `prompts.py::review_lead`, the one resolver for `spawn._review_prompt` and `build_pr_prompt`'s coworker branch. **Do not** re-add a default naming any command. **Do not** copy the dry-run tail into `review_prose.txt`.

`_bg_spawn_pr` guards in-flight launches in `pill_state` and logs to `$COCKPIT_HOME/spawn.log`.

`_too_young_to_adopt` refuses a worktree younger than `_SPAWN_ADOPT_GRACE_SECONDS` (120s), because `cockpit new` creates worktree and workspace in two steps and a poll between them spawns a second Claude.

- Guard both attach paths. The `--pr` form races too.
- Use age, not a lock or spawn-side registration.
- Fail open: an unstattable path reads as old. **Do not** invert that. Not configurable.

A coworker's PR is review-mode, never author-mode. `PR.mine` defaults to true. `nudge_issue` requires `mine`, which also quiets the 🔔, while pills, cells and the Issue column still show the issue. `build_pr_prompt` branches on `mine`, so a coworker's PR gets `review.txt`, not `pr_authority.txt`'s force-push grant. **Do not** add a nudge or authority grant keyed on the worktree's existence alone.

`use_worktree: false` repos opt out of all of the above. It defaults to true. `_spawn_missing_workspaces` early-returns for them. The row shows only while a workspace is open on it, and `n` starts one. Read it as `not repo.get("use_worktree", True)`: a bare `.get()` treats an unconfigured repo as opted out. **Do not** let these repos reach any auto-spawn.

### The live PR list carries at most one PR per head branch

Cockpit joins PR to worktree, workspace, row and cache by head branch. With two PRs on one head, `{pr.branch: pr}` is last-wins while `match_worktrees` emits both, so a workspace is spawned and `_dedupe_workspaces` closes it every cycle. `gh.list_relevant_prs` collapses through `gh._one_pr_per_branch`, and `cache.prune_superseded_pr_caches` is the disk half. **Do not** harden one reader instead. Fix it at the producer.

The per-branch alias returns the newest PR in any state, so `gh._pr_rank` prefers OPEN before `updated_at` and number, matching `cache._pr_payload_rank`. **Do not** rank by number alone.

### Trunk-headed PRs get a synthesized branch — `main`/`master` heads never become the worktree branch

`gh.py::pr_worktree_branch(number, head, base)` is the single normalizer: a head in `MAIN_BRANCHES` becomes `pr-<N>-<base-slug>`, else the head verbatim. Apply it at the four join points, which must agree: `_pr_from_node`, `resolve_pr_branch` (fetches `refs/pull/N/head`, never touching local `main`), `list_open_pr_heads`, and `_relevant_pr_query`/`_collect_nodes` (which rejoin by embedded number). `branch_label` strips the token. **Do not** thread `headRefName` straight into a worktree branch.

### Slack thread source — codename branch, MCP-delegated fetch, no `claude mcp list` probe

A Slack permalink classifies as `slack` mode, user-initiated only. Spawn synthesizes a codename branch seeded on the thread's identity (channel id + message ts), not the URL, so re-spawns are idempotent. `_slack_prompt` delegates the read to the in-session MCP. **Never** add a `claude mcp list` pre-flight gate: it reads managed connectors as absent while they handshake and would silently disable the feature. The prompt's retry-then-STOP logic handles an absent connector, in `prompts/linear.txt` as in `prompts/jira.txt`. **Do not** reintroduce a gating pre-flight for any provider. The one sanctioned reader is `lib/mcp.py`, which decides nothing.

### Prompt prose lives in packaged `cockpit/prompts/*.txt`, not Python string lists

Prompts render via `templates.render(name, **slots)`. Templates hold static prose and `{slots}` only. The Python builder owns control flow, picks the template and builds conditional blocks as slots. A missing slot raises `KeyError`. **Do not** re-inline prose into Python lists or add conditionals to a `.txt`. hatchling ships only VCS-tracked files, so `git add` new templates.

`plan_tail.txt` and the `plan_only.txt` fallback tell the session to write `plan.md` in the worktree root. The gate's no-code rule must carve that file out, or the two lines contradict. **Do not** teach it to commit the file. Spawned repos lack our `.gitignore`, so the template's never-stage line is the only guard. **Do not** promote it to a cache cell, config field, or anything the daemon reads, because a session might not have written it.

### The one cache exception: session-scoped cells (written outside the daemon)

`lib.claude.stash_from_stdin` writes `context-<sid>`, `rate-limit-5h-<sid>`, `model-<sid>`, `permission-mode-<sid>`, `transcript-path-<sid>` and `cost-<sid>` from the statusLine stdin. The daemon never reads them, except `cost-<sid>` to derive `wt-cost`. **Do not** extend this to a new cell. The daemon must never write one.

### `wt-cost` — session cost folded onto a worktree, the daemon's bridge across the two keyings

The `$` column totals what every session at a worktree has spent. Sessions are keyed by id and rows by path, so `cache.py::write_worktree_cost_cache` joins them on the fast tick into `wt-cost-<cwd>`. Renderers read only that cell.

- Join on Claude Code's project-directory slug, forwards only. It is lossy, so apply `_claude_project_slug` only from path to directory. **Do not** recover a path from a slug.
- Name the stem `wt-cost`, not `cost`, or `cost_reporting_available`'s glob latches on cockpit's own value.
- Gate the column on data, never on plan. **Do not** add a config field or detect the plan: the blob carries no tier, and some plans report `0`.
- Blank is not zero. The cell is `""` for a costless worktree, since absent means "never reported" as often as free.

### Nudge idle-gate: trust the `idle=` pill, NOT cmux's native `Needs input`

`nudge_if_idle` (`lib/cmux.py`) must tell "parked at prompt (safe to `send`)" from "awaiting a y/n permission (unsafe)". Native `claude_code=` has `Running`, `Idle` and the ambiguous `Needs input`, which fires for both.

- Block on native `Running`. Send only if the `idle=` pill is present or native is `Idle`. Re-assert a dropped pill under native `Idle`, with a verify+retry loop. **Never** simplify the gate to trust `Needs input`.
- `reassert_idle_pills` also runs on the fast tick, because native state vanishes on a cmux restart while the pill persists. It only writes pills, is `dry`-gated, and trusts `Running`/`Needs input` no more than the gate. **Do not** widen it into a second at-rest authority.
- A ref with no native state is covered by `_screen_signals_idle`, from two sources. Turn running? Ask the transcript (`lib/transcript.py::turn_in_flight`, only a definite `False` passes, union over every session at the cwd, cwd threaded in by the caller). **Do not** use the screen for that: `-- INSERT --` tracks composer focus, not turn state. Choice pending? Ask the screen: a marker (`Enter to select`, `Esc to cancel`, `to navigate`) refuses, `-- INSERT --` must be present, and the prompt line must be empty. It feeds only the self-heal, is cmux-only, and fails closed. **Do not** let the transcript reach the send gate: a running turn and a pending permission both look like one unanswered `tool_use`.
- The hook's liveness guard compares against `cmux workspace list --json`, which carries UUIDs. `cmux list-workspaces` prints only refs and names, so matching against it exits 0 for every session. **Do not** stub that listing with a `workspace:N` id.
- `_idle_skip_reason(status_lines)` is the one verdict, in the order `nudge_if_idle` applies. `rest_skip_reason(ref)` wraps it for display callers. **Do not** re-derive it at a call site.
- `cmux.one_line` collapses every message to one line inside `nudge_if_idle`, before the `dry` print, because `cmux send` turns newlines into Enter and submits a truncated prompt. **Do not** re-implement it per call site. This is why the `a` modal is an `Input`, never a `TextArea`.
- `cmux.deliver_followup` delivers a spawn's seeded body by keystroke and confirms it on screen before Enter, because `claude_code=` registers before Claude Code takes input. Retry until `_screen_shows` finds the body's leading characters. A body that never appears is reported, never submitted: giving up presses no Enter. Verification fails open when the screen is unreadable or the needle was already present. `_screen_shows` returns `None`, not `False`, when it cannot see. `_FOLLOWUP_SEND_ATTEMPTS` is 2, because a re-send whose predecessor landed stacks a second copy. **Do not** raise it for a slow spawn.
- The echo covers only the first `_FOLLOWUP_ECHO_PREFIX_CHARS`, so `_warn_if_body_garbled` re-checks the submitted body via `transcript.submitted_body`. It warns and does not `_queue_retry`, because a garbled body still reached the session. No matching record is never corruption. **Do not** widen the needle to the whole body. What drops bytes is still unidentified, so this makes the loss visible and does not prevent it.
- With the `/cockpit-seed` template installed, `deliver_followup` writes the body under `$COCKPIT_RUNTIME_DIR/seed-bodies/` (`lib/seed_bodies.py`) and types only `/cockpit-seed <id>`. **Do not** add a second token path at a call site. An absent template (a stat of the file) or unwritable store falls back to typing the body. A read never deletes. `write` prunes on `RETAIN_SECONDS`, past `seed_queue.STALE_SECONDS`. Validate the id before it names a file. The template carries no lead line.

### `cockpit diff` is the ONLY diff entry point — a CLI, because the daemon cannot be one

`cmux diff` defaults to the caller's own workspace and surface, which a daemon cannot supply, so the entry point is the `cockpit/diff.py` CLI. **Do not** add a TUI row key that pipes into `cmux diff`.

- `cmux.py::render_diff` has one caller. It names neither `--workspace` nor `--surface` and passes the environment untouched. **Do not** add a `workspace=`/`keep_surface=` pair.
- It takes exactly one of `patch=` (stdin, the only way to show a PR) or `source=` (cmux's `unstaged`/`staged`/`branch`/`last-turn`). **Do not** reimplement a git source, add an in-overlay renderer or add a `delta` dependency.
- Pass `--layout unified`, `--cwd` and the subprocess `cwd`. `"diff"` is in `_CMUX_ONLY_VERBS`. Comments stay local to cmux.
- `_move_diff_to_caller_pane` re-homes the viewer as a tab in the caller's pane. It resolves the caller, never a target, with one `tree` call. Every failure is silent and degrades to the split.
- `close_diff_viewers` recognises a viewer by the `cmux-diff-viewer://` scheme, never by title, and closes by UUID, only on `--ack`.
- Default to the PR diff. With no PR, fall back to a local diff and name the substitution. On a `MAIN_BRANCHES` branch use `--unstaged`, not `--branch`, which against `origin/HEAD` shows only unpushed commits. Key it on the branch, not a dirty tree.
- Use bare `gh pr diff`, so no daemon is needed. The cache supplies only the title's PR number (`find_pr_payload_for_cwd`). A source flag reaches no network.
- Read no cockpit config. Resolve with `git.worktree_root`. **Do not** route through `close.py::_resolve_target`.
- `--comments` prints `lib/diff_comments.py`'s pending notes and marks nothing. `--ack` calls `mark_delivered` and closes the tab. **Do not** merge them: acking on print loses a note when a turn dies. Offer both the worktree and `main_worktree_path` as roots. Nothing pending closes nothing.
- It writes nothing durable.

### Two destructive primitives, and only ONE of them removes a worktree

- `cmux.py::cmux_close_workspace_best_effort(ref)` closes a session and touches nothing on disk. Direct callers: dedup, parking (`h`), the anchor husk swap, dissolving a trailing fold.
- `teardown.py::teardown(TeardownRequest)` closes the workspace, removes the worktree, deletes the branch and drops the PR cache. It owns the self-close ledger.
- `TeardownRequest.worktree_path` is the whole difference. Autoclose and `c`/`C`/`cockpit close` pass it. `_reap_workspace_orphans` passes `None` and gets a workspace-only close plus a delete of a `<login>/` branch ref. One path removes a worktree, guarded once: a dirty tree or unlanded commits refuse both `c` and `C`.
- A new destructive trigger builds a `TeardownRequest`. It does not open a third path.
- **Never** call `cmux("close-workspace", …)` raw. The `workspace.closed` event looks like the user's sidebar X and routes into teardown.
- The stale-branch-ref reaper is the one destructive action outside both. **Do not** copy it as a precedent.

### The daemon makes exactly THREE automatic sends, and only one of them is a new message

The set is closed: the PR nudge (`cycle.py`, slow tick, `PR.nudge_issue`, silenced by `m`/`z` via `pref_key`), the diff-comment hand-over, and the seed retry (`cockpit.py::_drain_seed_queue`). Everything else is typed by the user (`a`, `A`, `cockpit broadcast`) and passes no `pref_key`. A fourth must derive from an actionable defect the session can fix. Judge it by whether it originates with cockpit or finishes something the user started.

- Queued ask (`lib/ask_queue.py`, `cockpit.py::_drain_ask_queue`): when `a`/`A` meets a session refused only as busy (`cmux.rest_pending`, never `parked`), queue the line and re-offer it through `nudge_if_idle` on the fast tick. Use its own directory, not the seed queue's, since both key on ref. Drain after the seed drain. The marker pins the cwd at enqueue and is dropped if the ref moved. A queued ref leaves the retry set (`_ask_misses`). The toast keeps the gate's reason. `STALE_SECONDS` is 600 and is correctness. A failed enqueue keeps the draft.
- Diff-comment hand-over (`cockpit.py::_nudge_diff_comments`): sends `DIFF_COMMENTS_NUDGE` through `nudge_if_idle` to the session in a worktree with pending notes. **Do not** give it a second send path or generalise it into a fan-out. Pass no `pref_key`, since a note the user wrote is not noise. Dedup on the comment-id set, not the worktree. Record only on an accepted send. Run after `reassert_idle_pills`. Gate `dry` through the gate's own `dry=`.
- Seed retry (`lib/seed_queue.py`): one durable JSON marker per workspace under `$COCKPIT_RUNTIME_DIR/seed-requests/`. Queue only where the body did not reach the composer, never on a failed Enter, which would type a second copy. `cmux.py::_queue_retry` is the one writer and always returns False. Key by ref, so a respawn supersedes. `STALE_SECONDS` is 300 and is correctness. **Do not** lengthen it. Drop a marker whose ref is not live. Retire only on an accepted send. Isolate `seed_queue.STATE_DIR` in `tests/conftest.py::_isolate_runtime_dir`, since it binds `COCKPIT_RUNTIME_DIR` at import.
- The 📝 column is a cue, never an input. Nothing reads the cell back.

### `cockpit broadcast` reuses the nudge gate — no second send path, no cache cell

`cockpit/broadcast.py` is a one-shot gesture: no cell, pill or `pill_state`, and skipped refs are printed, never queued. **Do not** give it its own send path, idle check or cache cell. Extend `nudge_if_idle`.

- `--repo` and `--worktree` filter the one loop. **Never** make either a second scope.
- `--repo` matches workspace cwds against the repo's own `worktrees()` via `_repo_paths` (cwd match, see Shared rules). Name the repo by `_repo_label`, casefolded. **Do not** accept the path basename, which is `.bare` for every bare clone. An unknown name exits 2 and lists the repos.
- The unscoped path reads no config, because broadcast reaches unmanaged workspaces.
- `--worktree` is an exact cwd match, not a prefix and not a single ref, since a `use_worktree: false` repo hosts several sessions at one cwd. A non-directory exits 2. It reads no config and excludes `--repo`.

### Nudge prefs are keyed per repo — a PR number alone is not an identity

`NudgePref` persists one JSON file per PR at `$COCKPIT_HOME/cache/nudges/<repo>__<number>.json`, keyed by the git nwo name, as the PR cache files are. Every entry point threads it, including `_resolve_row_pref` (via `_cache_repo_name`, not the config `name`) and `cockpit nudge`'s `_resolve_pr`, which exits 2 rather than fall back to a bare number.

- Keyed by number alone, two repos' PR #10 share a file: a mute silences both, and each cycle wakes the other's snooze. **Do not** add a call site that invents a key without a repo. **Do not** default `repo_name` to `""`.
- `load_pref` falls back to a legacy `<number>.json` and never unlinks it, since several repos may read it.
- A worktree with no PR gets no pref and no nudge. `_refresh_orphan` applies pills only. **Do not** answer "an abandoned worktree should nag" with a send. Use a derived cell.

### Backend capability gate — probed once at startup, warns and degrades, never dies

`lib/capabilities.py` checks the backend is new enough on two axes: `REQUIRED_VERBS` (each mapped to the tier it disables) and `REQUIRED_CAPABILITIES`. Verbs are parsed from `cmux --help`. A parse miss warns.

- A missing `capabilities` verb is the too-old signal. Report it as its own warning, not as "no capabilities". An empty verb set warns about nothing.
- Warn, never die. **Do not** promote any of these to `sys.exit(2)`.
- Cache in `capabilities.probe`, never in `resolve_tool`, so tests can vary PATH and config.
- Probe in the daemon only, since `cockpit setup` may install the backend. Skip when the backend is not cmux.
- `has_capability(id)` gates features built on the baseline. Pair it with `is_cmux()`.
- Every entry names a tier cockpit has. **Do not** add one for a feature that does not exist.
- `workspace.groups.v1` is required because `workspace-group` is absent from `cmux --help`'s `Commands:` list. **Do not** add `workspace-group` to `REQUIRED_VERBS`. **Do not** narrow its capability. **Do not** write verb counts into prose. The map is `docs/cmux-surface-audit.md`, and its live half is `tests/e2e/test_cmux_surface.py`.

### `$COCKPIT_HOME` may be inside a file-sync folder — write pid-scoped, warn on conflicts

- `config.py::_atomic_write_text` puts `os.getpid()` in its temp name. A fixed `<name>.tmp` lets the losing process's content land under the winner's name. **Do not** go back to a fixed suffix. **Do not** re-inline the write at a `config.py` call site. A sibling module owning its own state dir may repeat the pattern, as `seed_queue.enqueue` does. The guard is the literal `os.replace`.
- `preflight._warn_sync_conflicts` only surfaces a conflicted copy, because the process cannot resolve it. Match only `conflicted copy` and `.sync-conflict-`. Other spellings look like ordinary filenames, and a false alarm trains the user to ignore a real warning.

### Machine-local runtime state lives in `$COCKPIT_RUNTIME_DIR`, never `$COCKPIT_HOME`

`config.COCKPIT_RUNTIME_DIR` owns `PID_FILE`, `daemon_signal.STATE_DIR` and `seed_queue.STATE_DIR`. It does not follow `COCKPIT_HOME`. These files key on local cmux refs. Under a synced home, machines would fight over one pidfile, drain each other's close queue and type each other's prompts.

- **Not** `$TMPDIR`: the pidfile is an IPC rendezvous, and `TMPDIR` resolves per launch context.
- **Not** a hostname stamp: `gethostname()` drifts on macOS.
- Name the legacy files, never read or delete them. Reading honours a close from an unknown machine, and another machine may still use them.
- Tests set the env var, not only module attributes, because fixtures reload `lib.config`. Fixtures isolating only `COCKPIT_HOME` do not cover the runtime dir.
- The two `COCKPIT_HOME`-inspecting `preflight` warnings need a hermetic home in tests.

### Config surface has three faces — keep them in sync

`cockpit/lib/config.py` is the authoritative reader. Two mirrors drift silently: `cockpit/config.example.json` (documentation only, never installed) and `docs/config.md`. Any change to a config field MUST update all three in the same PR. Provider ticket fields also flow through the provider's `CONFIG_FIELDS`.

### `tickets` config — the one provider selector

`tickets` is an object: `{provider, close_on_merge, dev_done, merge_done}` plus per-provider extras. A bare string is shorthand for `{provider: …}`. Fields and defaults live in `docs/config.md`.

- One field per concept across providers. **Do not** re-split `dev_done`/`merge_done`/`token_env` per provider. `project` and `board` stay distinct. Trello keeps `key_env` and `token_env`. GitHub rejects `merge_done`.
- Superseded spellings (`use_linear`, flat `linear_*`) hard-fail in `preflight._check_legacy` (`_LEGACY_TICKET_FIELDS`), naming the replacement. `tickets_field_errors` can't tell a rename from a typo. **Do not** re-add an alias arg or a "guess the provider from a sibling field" fallback.
- Resolve per field: repo block, global block, default. Provider selection uses `_tickets_block`, where the repo's whole block wins.
- `tickets::provider_for(cfg, repo_entry)` returns the `TicketProvider`, the single source of truth for `dev_done_value`, `parse_footers`, `fetch_states`, `fetch_titles` and `narrow_repos`. **Do not** add `provider == "github" ? …` ternaries. Add a `TicketProvider` field.
- `spawn.py` picks the prompt from a `(mode, provider)` table (`_TICKET_PROMPTS`). Add a provider by adding its pair. It reads `repo_tickets`, **never** the global `tickets()`. It runs **after** every routing hop, or it resolves the cwd's repo, not the target's. The ticket-key routing gate is per-repo for the same reason. With no matching pair, it falls to `_plan_only_prompt(..., source=…)`. **Do not** drop the `source` slot.
- Credentials are env-only. Config holds the variable name, never the value. Warnings, logs and errors name the variable, never the value.
- `cockpit config tickets` exits 2 for a cwd outside every configured repo.
- `start_label` is the one spawn-time tracker write (GitHub, opt-in, best-effort).
- `linear_team_keys` is provider-neutral, since Jira declares `keys` too. Pair it with the resolved provider.
- To add a setting, put it in the provider's `CONFIG_FIELDS` and add a reader in `config.py`. **Do not** use a hardcoded preflight list or a top-level flat key. **Do not** flatten the per-provider schemas into one dict, since names repeat across providers.

**Ticket→repo routing has two stages: a free match, then a paid tiebreak.** `find_repos_by_ticket_key` over `tickets.keys` is offline. `TicketProvider.narrow_repos` (on `tickets.project`, one fetch) runs **only when the free match returned more than one**. It never narrows to zero. It groups candidates by resolved credential, because a team key is workspace-scoped and another workspace answers about a different issue with the same identifier. **Do not** collapse this to one key read off `candidates[0]`.

`spawn.py` must not branch on a provider name. `_route_by_ticket` is the shared tail.

- Linear: free-match `keys`, tiebreak on `project`.
- Jira: free-match the same `keys` field and reader. It stays `_no_narrow`. **Do not** give it a `project` field or duplicate `find_repos_by_ticket_key`.
- The shape gate stays `LINEAR_RE_CI`. Widening it reclassifies branches like `feature2-1` as tickets.
- Trello: no free match. The `tickets.board` opt-in is the discriminator (a list is allowed). With none declared, the spawn makes zero network calls.
- GitHub needs neither stage.

**Trello alone has a third stage, `tickets.label` (`tickets::_narrow_by_label`), for two repos on one board.** Do not route on the card's list, because the list is `state`. A label is orthogonal to it.

- It runs only on a board tie where some repo claims a label.
- A repo with no label is the board's default and takes every unclaimed card. A claimed label wins over the default.
- It never narrows to zero. An inconclusive fetch or an unmatched card leaves the board match, and the caller refuses.
- `fetch_card_labels` returns `None` for "couldn't ask" and `[]` for "no labels". **Do not** collapse them, or an API blip hands the card to the default repo.
- It is its own `GET`. **Do not** add it to `fetch_card_board`, which must cost one fetch.
- Routing only. It never reaches the inbox scope, where `board` stays the only scope.

**A ticket URL is a first-class source for every provider.** Linear and Jira URLs extract the identifier and take the same mode as the bare id. **Do not** pass them verbatim the way `slack`/`trello` mode does, or `git worktree add -b <URL>` fails.

**Per-org credentials come from the env-name indirection**, a `_tickets_field` call, so an org block covers its members.

- Identity caches key on the resolved secret, not the variable name (`_secret_fingerprint`), or org B reads org A's viewer id.
- Spawned sessions get no ticket credentials. `_bg_spawn_pr` passes the env minus `config.credential_env_names(cfg)`, because a `review_prs` session runs over an untrusted diff. Strip by resolved name, never a prefix guess, and touch nothing else (`PATH`, `COCKPIT_HOME`, `CMUX_*` pass through).
- The first-turn prompt states the repo's tracker identity (`spawn.py::_tickets_block`), since a session cannot derive it. It names no credential variable.
- An unset credential warns at startup for every provider through `TicketProvider.credential_envs`. The provider names its own variables. **Do not** add a provider-name ternary. Resolution is the repo's. Keep it in step with `credential_env_names`, which a test pins.

### `devdone=` pill — the ticket provider is the one auxiliary (read-only) state source

The pill needs `repo_tickets(...) != "none"` and a PR-body delivery footer. Use footers only (`provider.parse_footers`), never a branch slug or bare mention. The `{provider, tickets, fetched_at}` block is cached in the PR JSON under `ticket`.

`_prefetch_linear_blocks` decides refetch or carry-forward per PR (footer-id change or TTL). It resolves the union of due ids across all a repo's PRs through `provider.fetch_states`. **Do not** fall back to a per-PR fetch fan-out. It runs before the write loop. A per-source failure isolates to its own ids. `title` never feeds a decision. `_track_dev_done` raises the pill only when every delivered ticket equals `provider.dev_done_value(...)`, casefolded. The pill never sends.

### done-on-merge — the daemon's only sanctioned tracker *writes*, dispatched per provider

Opt-in via `tickets.close_on_merge`. `_transition_merged_tickets` dispatches on `provider.name`, fired on `_is_post_merge_stale` independent of teardown. Each writer is guarded by a per-run marker in `pill_state`, viewer-gated, idempotent and logged.

- Linear: `issueUpdate` to `merge_done`. Skip unless assigned to the API-key `viewer`, or if already at target or canceled (both are `completed`, so name equality decides). Cache the viewer id and team state maps. Fetch the viewer lazily.
- GitHub: `gh issue close` on each delivered issue still open and assigned to the auth login.
- Jira: REST transitions API, since Jira moves by transition.
- Trello: move the card to the list named `merge_done` (no default). Skip unless I'm a member.

Never cache a failed identity fetch. A failed write clears the marker to retry. Any future daemon tracker write must be opt-in, viewer-gated, idempotent and logged.

### The ticket inbox — the one surface NOT derived from `git worktree list`

`T` opens `TicketsScreen`: tickets assigned to me, in an active state, with no worktree. `cycle.py::_collect_ticket_inbox` collects per repo, `orchestrators/ticket_inbox.py::publish` drains once, and `cockpit/tui/widgets/tickets_screen.py` renders.

- Shape it like `ReviewFolds`. The per-repo pass only records and the cross-repo pass fetches. Build it only when `only_repo is None`.
- The fetch groups by resolved credential and the payload keys by org, because asking the wrong workspace answers about a different issue with the same identifier. Group on `(provider, credential, bucket)`. **Do not** collapse the two axes. The group key is the env-var NAME (`TicketProvider.credential_envs`), never the secret.
- `fetch_my_open` returns `None` for "couldn't ask" and `[]` for "answered with nothing" (see Shared rules). The distinction reaches `TicketInbox.partial`. An incomplete cycle suspends every bucket and a failed group suspends the buckets it feeds. A suspended bucket keeps its old payload. **Do not** re-key this on the bucket being empty.
- The collector never branches on a provider name. `inbox_scopes` is a `TicketProvider` field.
- An empty scope means "ask about everything" except for Trello, where it means "ask about nothing" (`tickets._trello_my_open`), because a Trello account spans every board its owner joined. `tickets.board` is required here and takes a list (`config.py::trello_boards`). Undeclared returns `[]`, not None.
- The inbox is a payload, never a flat cell (`cache.py::write_ticket_inbox`), because an unstarted ticket has no worktree path or session id. It has no TTL.
- Stamp `in_flight` on BOTH ticks and always write it, including `False` (the `_stamp_ticket_urls` rule). `publish` stamps on the slow tick and `cache.py::stamp_inbox_in_flight` on the fast tick, both through `ticket_inbox.py::active_ids`, which takes its inputs and fetches nothing (see Shared rules).
- `ticket_inbox.py::_drop_done` drops tickets whose `state` matches `TicketProvider.done_values`, casefolded. Those are the user's own `dev_done` and `merge_done`, so it takes **no config field of its own**. A bucket it empties is still written, because `[]` is an answer.
- `tickets.inbox_states`, when set, IS the whole filter and skips `_drop_done`. Half-replacing would re-hide a state the user listed. `config.ticket_inbox_states` resolves per repo and a fetch group takes the union. Linear filters server-side and case-exact. **Do not** add client-side casefolding for Linear. Jira and Trello filter client-side, casefolded. GitHub ignores it, and `preflight._validate_inbox_states` warns.
- `trello.py::_board_and_list_names` resolves names, because `/members/me/cards` returns ids and ignores `board=true` / `list=true`. A failed names call returns **None**, not partial cards. Archived boards drop their cards. An unknown board id is not archived. A Trello row shows `#<idShort>` as `handle`, and `id` stays the key for every join, dedup and `in_flight` match.
- The screen reads payloads only: no fetch, no git, no `load_config` per keypress, no cell written. Pass tracker text through `strip_control` (`cache.py`), because it bypasses `read_text`.
- The app computes routing candidates (`app._ticket_routes`) and passes them as `routes`. **Do not** derive them in the screen, because `find_repos_by_ticket_key` reads `load_config()` per call. **Do not** call `narrow_repos` for a marker, because it is the paid stage.
- Give the columns explicit widths. **Do not** go back to `add_columns`. An auto column resizes on idle while the cell render cache ignores the width, so rows cache at a stale width. Ellipsize each cell to the column cap plus 1, since `_ellipsize` leaves a string one over the limit alone.
- Each org is a fold and `enter` on a header toggles it through `_rebuild()`. Every org starts folded except a lone one. Folds are session-only.
- `tickets_screen.py::ticket_source` returns the ticket's URL, falling back to its id, and `app._start_ticket` shells out to `cockpit new`. Pass the URL, because `owner/repo#N` does not classify.
- `enter` posts `TicketsScreen.Start` and the overlay stays up. Only `escape` dismisses. **Do not** give the screen a dismiss value. It is a `ModalScreen[None]` pushed with no callback.
- A started row shows `STARTED_STATE` and a second `enter` on it is a no-op, because `cockpit new` runs detached and a double-tap cuts a `-2` path. Key `_started` by spawn source, not row key. The mark is optimistic, so every declining path (the `--dry` gate, an unroutable ticket, a cancelled picker) releases it through `app._release_ticket` → `TicketsScreen.release`. **Do not** make the mark a payload field.
- `_start_ticket` passes `--repo` explicitly (`_with_repo`, shell-quoted) and refuses when routing cannot resolve one, because its cwd is the daemon's own. Route through `spawn.route_ticket_repos`, which shares `ticket_repo_candidates` with `cockpit new`, from a `@work(thread=True)` worker. A URL with its own nwo is still checked against the config. **Do not** re-add a per-provider waiver. **Do not** default to the cursor row's repo.
- `_route_ticket` branches on the surviving set from `route_ticket_repos`. One name spawns, none refuses through `_refuse_ticket`, several ask through `_pick_ticket_repo` → `RepoPickScreen`. Never re-derive the set. Treat a raised route as no candidates. The picker pre-selects nothing, because a seeded `Select` posts `Changed` on mount. Compare against `Select.NULL`. **Do not** use `Select.BLANK`, which is a plain `False` in Textual 8.x.
- The Ticket column shows `?` (ambiguous) or `!` (nothing claims it) from stage one only, so opening the modal reaches no network. A ticket stage one cannot answer for is absent from `routes`, not `[]`. The marker takes its width from the handle's budget (`_MARK_SLOT`) and **must never widen a column**. **Do not** add a Repo column.
- The inbox is passive: no daemon-derived cell, no send, no config field. `T` is not `--dry` gated. `_start_ticket` is.

**`c` in the inbox diagnoses one bucket — `lib/ticket_check.py`, the only ticket surface that may ask a tracker a question the daemon never asks.**

- Ask through the `TicketProvider` and never branch on a provider name. `whoami` proves the credential works. `verify_scopes` reports which declared `tickets.keys` / `tickets.board` the tracker knows.
- `verify_scopes` asks about the declared scopes, never for a listing, because a paginated listing can push a scope onto page two and report it missing.
- A failed connection suppresses the scope verdict. `whoami` runs first and `verify_scopes` only on a pass.
- `None` and `[]` stay distinct into the report. `unknown_scopes is None` renders as "not checked", never "all fine".
- Report a credential by env var NAME, as `bool(os.environ.get(name))`. A value never enters the report.
- `lib/mcp.py` probes the declared MCP server through `claude mcp list`. It is cockpit's one probe, allowed because it gates nothing. It returns **None** for "couldn't read it", never `{}`. `_mcp_verdict` reports "not listed" as a probable miss, never a verdict. Run it in the repo's cwd, dedupe per cwd across a bucket, and skip it for a repo declaring no server. **Do not** let it reach a tick, a cell, or any gate.
- The check is read-only and not `--dry` gated. It writes no cell, pill, `pill_state` or tracker state.
- `check_bucket` has one `on_repo` hook, read by `CheckProgressScreen`, for progress and cancel. Push the overlay on the UI thread before the worker starts. Cancel by raising out of the hook. **Do not** use `worker.cancel()`, which stops only a queued worker. A cancelled run pushes **no report**.
- Check per repo, not per credential, so a repo whose `tickets` block overrides the org's is reported. `c` on an empty inbox checks every bucket.

### `gh api` ignores the cwd — the host is stated per call, never process-wide

`gh pr view` and `gh repo view` infer the host from the cwd's remote. `gh api` and `--repo` do not: they answer on the default host with HTTP 200 and an empty result, so a tenant renders zero rows and `list_relevant_prs` reports success.

`git.py::origin_host` reads the host from the remote with no network. `gh.py::gh_env` states it as a per-child `GH_HOST`. **Never** set `GH_HOST` on the daemon process, since it would redirect every github.com repo. **Never** thread `cwd=` into an `api` call.

- `""` means "state no host". `origin_host` returns `""` for github.com and an unparsable remote, `gh_env("")` returns None, and None is byte-identical to the host-unaware call. A wrong host is worse than the default.
- `_graphql`'s `host` is a required parameter, so each new call site must choose. The two internal phase helpers default it.
- The login is per host (`gh_self_user(host)`, cached; `self_user_for_host` is the tolerant wrapper). `PR.mine` gates the nudge, the review-vs-author prompt and the force-push grant, so a github.com login on a tenant repo makes every PR a coworker's. `_prepare_cycle` resolves the host before `self_user`. The reaper resolves it per owning repo (`_my_prefix`). Both fall back to the process-wide login, which acts less.
- `github_issues.py` derives the host at `_gh_json`, its one I/O seam. **Do not** thread it through the fetchers or `TicketProvider` signatures.
- `preflight._warn_unauthenticated_hosts` is the only detector, since the failure looks like success. It uses `gh auth token --hostname`, never reads the token, and only warns.
- There is no `host` config field. Add one only for an origin the remote can't name (an `insteadOf` rewrite, a mirror on a third host). Known gap: `fetch_run_info`'s `-R <nwo>` is host-blind.

### A null `reviewDecision` is not "no approval" — `gh.py::_review_decision` falls back to the reviews

GitHub returns `reviewDecision: null` on an approved PR when a ruleset declares the required reviewers (the same GraphQL blind spot as `dismissesStaleReviews`). Defaulting null to `REVIEW_REQUIRED` hides the `approved` pill that `decide_pills` keys on it.

- A reported decision always wins. The fallback runs only on null.
- Only APPROVED, CHANGES_REQUESTED and DISMISSED are verdicts, per reviewer, latest wins. COMMENTED and PENDING keep the prior verdict. CHANGES_REQUESTED beats APPROVED across reviewers.
- Exclude the PR author's reviews and every Bot review, matching `_unaddressed`.
- Derive it at the one construction site, so `primary_issue`, the `approved` pill, `update_branch_skip_reason` and `wake_signature` agree. **Do not** re-derive approval at a renderer or gate.

### `update_stale_branches` — the daemon updates a PR head *server-side*, never by rebasing the worktree

Opt-in, slow-tick `_update_stale_branches`.

- Trigger on `mergeStateStatus == "BEHIND"`. **Never** use the local `behind_of_base` count or `base-distance`, which fire on every repo without the protection rule.
- Update with the `updatePullRequestBranch` mutation. **Do not** replace it with a local rebase and force-push: a conflicted rebase strands a `rebase-merge` state that wedges teardown.
- `expected_head_oid` is mandatory. It is the compare-and-swap, like `--force-with-lease`. **Do not** drop it.
- Scope it to approved or snoozed PRs, the quiescent states. **Do not** widen it to every stale PR.
- Under stale-review dismissal, a new commit discards the approval. `update_branch_skip_reason` therefore refuses `APPROVED and <dismisses>`. **Do not** remove this gate.
- The dismissal verdict has two sources. `dismissesStaleReviews` covers classic branch protection only, and a ruleset-only repo reports `branchProtectionRule: null`. `gh.branch_dismisses_stale_reviews` reads the merged effective rules, and the two are ORed. **Do not** gate on the GraphQL field alone.
- The ruleset lookup fails closed, the one place cockpit does. `None` means "dismisses", because a wrong answer silently discards an approval. Only the approved half pays for it. **Do not** invert it for symmetry.
- Key the marker by head oid. A failed mutation pops it. A skip keeps it.
- REBASE rewrites the head, so `git.resync_to_origin` reconciles the worktree. Its guard is also a compare-and-swap: refuse unless the tree is clean **and** HEAD equals the pre-update sha. **Do not** weaken `expected_head` to a dirty-check, which would reset away unpushed commits.

## Dev setup and common commands

```bash

# One-time after cloning — wires pre-commit hooks for commit + push stages:
./setup.sh

# Run THIS worktree's build against a throwaway sandbox (never `uv run cockpit

# watch`, which shares state with the installed daemon — see below):
./dev.sh

# Run the test suite serially — right for a single test or a small selection:
pytest tests/test_spawn.py::test_linear_key_routes_to_matching_repo_without_repo_flag

# Run the WHOLE suite — always pass -n auto (near-linear parallel speedup).
# `addopts` omits it because worker boot is pure tax on the single-test line above:
pytest -n auto

# Coverage report — `--cov` is not in `addopts` for the same reason.
# `coverage.yml` runs it nightly against a floor:
pytest -n auto --cov --cov-report=term-missing

# Type-check:
mypy cockpit/

# Lint + format — ALWAYS via the pinned pre-commit hook, scoped to your files:
pre-commit run ruff ruff-format --files <changed paths>

# Audit the workflows for security issues after touching .github/:
pre-commit run zizmor --all-files
```

**Never lint/format with `uvx ruff` (or a globally-installed `ruff`).** `uvx` pulls the latest ruff, whose rules drift from the pinned version and rewrite files you never touched. The pinned hook is what CI enforces.

### `./dev.sh` — five isolation axes, and none of them is optional

`uv run cockpit watch` from a worktree is **not** a dev run: it shares all state with the installed daemon. `dev.sh` seeds a `.cockpit-dev/` sandbox. Each axis blocks a different path to real damage:

- **`COCKPIT_HOME`** → `config.json` and the PR cache.
- **`COCKPIT_RUNTIME_DIR`** → `cockpit.pid` and `close-requests/`. It does not follow `COCKPIT_HOME`. If shared, every `cockpit close` in every worktree routes into the dev build and runs the real `teardown`.
- **`TMPDIR`** → the flat cells live under `tempfile.gettempdir()`, not `COCKPIT_HOME`. Isolating only the home leaves the fast tick repainting the user's live footer.
- **`tool: none`** → every cmux write becomes a no-op through the existing gates. Use that validated config value, **not** a dev-only code branch.
- **`--dry`** → `tool: none` does not cover `_maybe_autoclose`, which removes worktrees and runs `git branch -D` through git. `--dry` also gates tracker writes. It covers the reconcile cycle (`ctx.dry`), the fast tick (`state["dry"]`), and the TUI row keys that reach outside (`n`/`f`/`h`/`a`, via `_blocked_by_dry`). It does not gate `c`/`C` (they only enqueue) or `m`/`z` (they touch only `COCKPIT_HOME`). **Do not** narrow the gate back to the cycle.

`dev.sh` forces `--dry` onto every `watch` invocation. **Do not** re-hardcode `dry=False` in `cockpit.py`, and **do not** add a second dev-only suppression path. `--dry` also suppresses cache writes, so snapshot mode copies the real PR JSONs in.

`dev.sh` refuses `cockpit setup` (exit 2), because setup bakes a `.venv/bin/python` that dies on cleanup. It also refuses `new` and `close`, because `tool: none` and `--dry` do not gate git and its config points at the real repos. **Do not** add a subcommand that mutates through git without a refusal beside it.

### `.claude/skills/` is repo-local dev tooling — never an install target

A skill here loads only for people working in this repo. The wheel ships `packages = ["cockpit"]` and `cockpit setup` installs from `cockpit/claude_commands/`, so nothing here is installed and nothing owes a teardown inverse. **Do not** confuse it with cockpit's `~/.claude` footprint.

- A skill wraps a command instead of reimplementing it, like `cockpit-dev` over `./dev.sh`.
- The `description` loads into every session. Keep it under 250 bytes and put the procedure in the body.
- `allowed-tools` is required, with least privilege.
- A skill fits only a judgment nothing else can enforce. A `PreToolUse` hook cannot block an edit to `COVERAGE_FLOOR` in `.github/workflows/coverage.yml`, so `coverage-audit` covers it. **Do not** put a rule here that a hook, a test, or this file would hold better.

### `.github/workflows/tag.yml`'s checkout must keep its credentials

`tag.yml` is exempt from zizmor's `artipacked` rule. Its checkout credentials authenticate the `git push origin "v$v"` that fires `release.yml` and `publish.yml`. Adding `persist-credentials: false` breaks every release silently, and zizmor accepts the change. **Do not** "fix" it.

## Release versioning

Release mechanics live in `docs/releasing.md`. Read it before touching any release workflow or `release-please-config.json`.

- **Never** hand-edit `CHANGELOG.md`. release-please owns it.
- **Never** remove `skip-github-release`, `skip-labeling` or the `chore(main): release` guard from `release-please.yml`. Each removal breaks releases silently.
- **Never** remove `create-branch: false` from `release.yml`. The tap's `main` is protected, so the formula update would never land.
- **Never** edit `publish.yml` to fix a PyPI `invalid-publisher` failure. The fix is on the PyPI side.

## Commit / PR-title convention

We squash-merge, so the PR title becomes the commit subject on `main`. Use [Conventional Commits](https://www.conventionalcommits.org/): `feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert`. `pr-title.yml` enforces it as the required `lint-pr-title` check. Local WIP messages are unconstrained.

Branch protection plus a repository ruleset guard `main`, so a plain `gh pr merge` fails even when green. The ruleset requires approving and code-owner review. A solo author cannot approve, so merge with `gh pr merge <N> --squash --admin`. Check the ruleset with `gh api repos/khivi/cockpit/rulesets`.

## Test layout

New modules get their own `test_<name>.py`. Do not append tests for a new source file to an unrelated module. Shell hooks under `cockpit/hooks/` live as `tests/test_<hook>.py` with no Python source mirror.

## The suite cannot reach the live machine — `tests/conftest.py`, and it is not per-test

The backend has no path to redirect: `cmux create` and `cmux send` act on the live sidebar. `_no_live_backend` + `_isolate_cockpit_home` block it, and `tests/test_suite_isolation.py` pins the property.

- **An extracted helper takes its input; it does not fetch it** (see Shared rules). Patching a name in one module never rebinds another module's copy, so a helper that fetches its own input goes live under tests. `refs_at`, `skip_summary` and `git.repo_worktree_paths` are pure for this reason. **Do not** make one call `workspace_cwds()` or `worktrees()`.
- Three layers block the backend: `subprocess.Popen`, `shutil.which` for cmux/limux, and `config._atomic_write_text` (raises inside the real `$COCKPIT_HOME`).
- The guard fails loud, never inert. `cmux(..., check=False)` returns `""` on a missing binary, which hides the leak.
- The guard keys on where the executable resolves, not its name. A test may build its own fake `cmux` under `tmp_path` (as `tests/lib/test_events.py` does).
- `@pytest.mark.real_backend` is the only opt-out, used by the `tests/e2e/` files. **Append** it (`pytestmark = [pytestmark, ...]`), because a second `pytestmark =` replaces the first.

## Test style by layer

- **Leaf modules** (`cockpit/lib/*` wrapping `git`, `gh`, `cmux`, `subprocess.run`) test against the real tool on `tmp_path`. Stubbing the command tests the stub.
- **Orchestrators** mock collaborator calls to assert ordering and gating. **CLI entry-points** mock at the orchestrator boundary.
- **TUI** (`tests/tui/*`) drive the app headlessly via `App.run_test()`/Pilot with the ticks and `load_config` injected.
- **End-to-end** (`tests/e2e/*`) run real binaries. Reserve them for cross-layer behaviour.
- **A gate that stands in for a third party's readiness must verify the outcome, not trust the proxy.** `cmux` tests use stubs, and a stub only re-asserts the belief it was written from. **Do not** add a call site that infers a third party's readiness from a signal it emits about itself and then acts irreversibly. Confirm the effect, and report when it cannot be confirmed.
- `tests/e2e/test_followup_delivery.py` mutates live state, so it needs `COCKPIT_E2E_LIVE_DELIVERY=1` on top of `real_backend` and cannot ride `pytest -n auto`. **Do not** promote it to a regression test for the readiness race, and **do not** read a green run as proof the race is handled.
- Repo-wide invariant tests (`tests/e2e/test_cmux_surface.py`, `tests/test_comment_references.py`) assert facts about the tree. The latter checks that every `backticked` symbol and path resolves, in code comments and in this file. Leave a retired name unbackticked. **Do not** park a stale reference in their allowlists.

## Invariant coverage — `specs/` is the ledger, and every bullet must be claimed

`specs/*.md` is the human-owned behavior spec: `- [<id>~<rev>] <what the system does>`, one bullet per invariant. This file holds the rule and its **Never**; the bullet holds the behavior. A test claims a bullet with `@pytest.mark.covers("<id>~<rev>")`. `rg 'covers\(' tests/` maps test to id.

`tests/test_invariant_coverage.py` fails on a marker naming an unknown id and on an unwaived bullet no test claims. Editing the spec is how you demand a test. Bullets flow spec to test, never the other way.

- Bump `~<rev>` only when a bullet's claim changes. That fails every test still claiming the old revision until you re-check it and bump its marker.
- `(untested: <reason>)` waives a bullet nothing runnable can assert. Waivers count against `WAIVED_COUNT`, so adding one is a deliberate edit there. A test claiming a waived bullet fails.
- A claim proves a guard exists, not that it is strong. The `spec-audit` skill is the advisory check. It proposes, never edits, and never gates a merge.
- **Do not** generate the spec from the markers. That would review nothing.

`docs/specs.md` covers the procedure.

## Sync

AGENTS.md is canonical. `CLAUDE.md` imports it and `.github/copilot-instructions.md` symlinks to it. Edit only this file.
