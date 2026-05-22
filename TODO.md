# TODO

## spawn.py: Linear and Slack positional detection

`detect_source()` in `scripts/spawn.py` currently handles GitHub PR URLs, bare PR numbers, and branch names. Two planned extensions:

- **Linear ID** (`PE-1234`, case-insensitive `[A-Z]{2,}-\d+`): resolve via Linear GraphQL API to get title + description, derive branch `khivi/<id-lower>-<title-slug>`, generate plan-only prompt with ticket body. Requires `LINEAR_API_KEY` env var; fall back to branch mode if unset.
- **Slack URL** (`https://<workspace>.slack.com/archives/<channel>/p<ts>`): resolve thread via Slack API, derive branch from first-message slug, generate plan-only prompt with thread text. Requires Slack MCP or `SLACK_TOKEN`; fall back to branch mode if unavailable.

Both should follow the same detect → derive-branch → plan-only-prompt pattern already used for PR mode.

## Linear title in cship pill

Cockpit no longer renders the statusline itself — `use_cship: true` delegates to the `cship` binary. Any "Linear title in the statusline" work belongs in cship's repo, not here.

The data path cockpit could still own: enrich `~/.config/cockpit/cache/{repo}__pr-{N}.json` with `linear_id` / `linear_title` so cship (or any other consumer) reads them without its own Linear API call. Deferred until cship grows a hook for that.

## Repo-rooted imports (drop `mypy_path` / `pythonpath`)

`pyproject.toml` currently needs `mypy_path = "scripts:tests"` and `pythonpath = ["scripts", "tests"]` because imports are package-root relative (`import lib.git`, `from cockpit_helpers import …`).

Alternative: rewrite all imports as repo-rooted (`from scripts.lib import git`, `from tests.cockpit_helpers import …`). Mypy/pytest would then resolve everything from cwd with zero extra config.

Trade-off: cleaner `pyproject.toml` vs. touching every `import lib.X` site across ~40 files + every test, and treating `scripts/`+`tests/` as packages they aren't really. Currently judged not worth the churn; revisit if more `tests/` helpers need cross-dir imports.
