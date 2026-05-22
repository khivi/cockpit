# TODO

## spawn.py: Linear and Slack positional detection

`detect_source()` in `scripts/spawn.py` currently handles GitHub PR URLs, bare PR numbers, and branch names. Two planned extensions:

- **Linear ID** (`PE-1234`, case-insensitive `[A-Z]{2,}-\d+`): resolve via Linear GraphQL API to get title + description, derive branch `khivi/<id-lower>-<title-slug>`, generate plan-only prompt with ticket body. Requires `LINEAR_API_KEY` env var; fall back to branch mode if unset.
- **Slack URL** (`https://<workspace>.slack.com/archives/<channel>/p<ts>`): resolve thread via Slack API, derive branch from first-message slug, generate plan-only prompt with thread text. Requires Slack MCP or `SLACK_TOKEN`; fall back to branch mode if unavailable.

Both should follow the same detect → derive-branch → plan-only-prompt pattern already used for PR mode.

## Linear title in cship pill

Cockpit no longer renders the statusline itself — `use_cship: true` delegates to the `cship` binary. Any "Linear title in the statusline" work belongs in cship's repo, not here.

The data path cockpit could still own: enrich `~/.config/cockpit/cache/{repo}__pr-{N}.json` with `linear_id` / `linear_title` so cship (or any other consumer) reads them without its own Linear API call. Deferred until cship grows a hook for that.

## Extract cycle pipeline → `orchestrators/cycle.py`

`scripts/cockpit.py` is still ~1050 LoC. ~565 of those are the per-repo
reconciliation pipeline (composing gh + cmux + git + cache + starship + teardown),
not CLI dispatch. Move:

- `RepoCycle` dataclass, 8 phase helpers (`_prepare_cycle`, `_write_pr_caches`,
  `_dedupe_workspaces`, `_refresh_tracked_pills`, `_print_tracked_summary`,
  `_handle_orphans_and_close_stale`, `_refresh_orphan`, `_spawn_missing_workspaces`),
  and `cycle_repo` itself
- `cycle_all`, `_drain_close_requests`, `_reap_workspace_orphans`
- `_maybe_autoclose` (forced-teardown on PR merge)
- supporting helpers used only by the pipeline: `maybe_nudge`, `match_worktrees`,
  `_resolve_wt`, `_orphan_snapshot`, `_ci_glyph`, `_is_post_merge_stale`,
  `_workspace_ref_for_path`, `_refresh_base_distance`

After: `scripts/cockpit.py` shrinks to ~485 LoC of pure CLI (argparse, `_build_state`,
`_once_with`, `_watch`, `_statusline_command`, `main`) that imports the orchestrator.

## Re-home non-tool-wrapper lib modules

`lib/` is documented as "single-tool wrappers + single-concern modules", but a
few residents don't fit either bucket:

- `lib/close_requests.py` — file-based queue (`$COCKPIT_HOME/state/close-requests/`)
  marshaling `TeardownRequest` payloads between the `cockpit close` CLI and the
  daemon. Marker schema already mirrors `TeardownRequest`.
- `lib/daemon.py` — pidfile + SIGUSR1 kick + sleep/wake loop. App-internal IPC,
  not a tool wrapper.

Options:

- Group under a new `state/` (or `runtime/`) package — clean taxonomy but two-file
  package is borderline over-engineered.
- Fold `close_requests` into `orchestrators/teardown.py` (tight coupling already
  exists via the shared payload).
- Leave both in `lib/` and broaden its definition formally.

Tackle alongside the cycle extraction above so the package boundaries land in
one PR rather than churning twice.

## Repo-rooted imports (drop `mypy_path` / `pythonpath`)

`pyproject.toml` currently needs `mypy_path = "scripts:tests"` and `pythonpath = ["scripts", "tests"]` because imports are package-root relative (`import lib.git`, `from cockpit_helpers import …`).

Alternative: rewrite all imports as repo-rooted (`from scripts.lib import git`, `from tests.cockpit_helpers import …`). Mypy/pytest would then resolve everything from cwd with zero extra config.

Trade-off: cleaner `pyproject.toml` vs. touching every `import lib.X` site across ~40 files + every test, and treating `scripts/`+`tests/` as packages they aren't really. Currently judged not worth the churn; revisit if more `tests/` helpers need cross-dir imports.
