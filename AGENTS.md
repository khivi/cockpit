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

Before committing, scan for:

- Your team's real ticket prefixes (e.g. `\b(ENG|LIN|PROJ)-\d+\b`). The generic `[A-Z]{2,}-\d+` is too noisy — it matches `ISO-3166`, `HTTP-200`, `RFC-5246`, `UTF-8`, etc.
- `linear.app`, `atlassian.net`, internal company domains
- `@firstname` references that aren't GitHub handles

## Release versioning

Before opening a PR, bump `.claude-plugin/plugin.json`'s `version` field (semver patch for fixes, minor for features). Stage and commit the bump with the rest of the change — do not ship a PR that leaves the version untouched.

## Test layout

Tests mirror sources one-to-one: `scripts/<path>/<name>.py` is exercised by `tests/<path>/test_<name>.py`. New modules get their own `test_<name>.py`; do not append tests for a new source file to an unrelated test module. Shell hooks under `hooks/` are the only exception — they live as `tests/test_<hook>.py` without a Python source mirror.

## Test style by layer

- **Leaf modules** (`scripts/lib/*` wrapping `git`, `gh`, `cmux`, `shutil.which`, `subprocess.run`, etc.) test against the real tool on `tmp_path`. Stubbing the underlying command tests the stub, not the integration.
- **Orchestrators** (`scripts/orchestrators/*`) compose those leaves. Tests mock collaborator calls (`patch.object(teardown_mod, "remove_worktree", …)`) to assert ordering, guards, and gating without re-validating the leaves underneath.
- **CLI entry-points** (`tests/test_<script>.py` for `scripts/{close,cockpit,spawn}.py`) test the argparse layer and dispatch. Mock at the orchestrator boundary (`patch("scripts.cockpit.teardown", …)`) — the layer below is covered by orchestrator tests, so re-exercising it here adds noise.
- **Shell hooks** (`tests/test_<hook>.py` for `hooks/*.sh`) drive the real script via `subprocess.run` against a `tmp_path` setup. No Python source mirror exists.
- **End-to-end** (`tests/e2e/*`) run the full pipeline against real binaries. No mocking. These are the slowest and most fragile — reserve for genuinely cross-layer behavior (e.g. `cship` + `starship` integration).

Shared test helpers live at `tests/` root:

- `tests/fixtures.py` — real-state builders (`make_bin_on_path`, `make_shim_on_path`, `make_git_repo`, `setup_cockpit_config`)
- `tests/asserts.py` — assertion helpers (`expected_starship`)

## Python dev env (uv)

`pyproject.toml` declares dev dependencies under `[dependency-groups].dev`. Each worktree gets its own `.venv/` via `uv sync` — the venvs are independent, but installs are cheap because uv hardlinks from its global content-addressed cache (`~/.cache/uv/`).

Workflow in a fresh worktree:

```sh
uv sync       # creates .venv with pinned dev deps; cheap if cache is warm
uv run pytest # tests; equivalent to .venv/bin/pytest
uv run mypy scripts/ tests/
```

`uv.lock` is gitignored on purpose — version pins in `pyproject.toml` are exact (`==`), so the lockfile adds no extra reproducibility for this tools-only env.

Pre-commit maintains its own per-hook venvs in `~/.cache/pre-commit/`. CI runs them with `PRE_COMMIT_USE_UV=1`, which routes pre-commit's installs through `uv pip install` so the package files hardlink from `~/.cache/uv/` (cached by `astral-sh/setup-uv` keyed on `pyproject.toml`).

For local dev, [direnv](https://direnv.net/) is recommended-but-optional: the repo ships a `.envrc` that exports `PRE_COMMIT_USE_UV=1`, runs `uv sync`, and puts `.venv/bin` on PATH. Run `direnv allow` once per worktree. Without direnv, `uv sync && export PRE_COMMIT_USE_UV=1` does the same by hand.

## Enforcement

The `gitleaks` pre-commit hook (`.gitleaks.toml`) blocks the regex-catchable cases at commit time: hardcoded home paths, Slack IDs, bare UUIDs, plus gitleaks' default credential ruleset. This document covers the judgment calls the regex can't reliably catch.

## Sync

This file is the canonical source. `CLAUDE.md` imports it via `@AGENTS.md`, `.github/copilot-instructions.md` is a symlink to it, and `CONTRIBUTING.md` references it. Edit `AGENTS.md`; the rest stay in sync automatically.
