# Contributing

## Quick start

```bash
git clone https://github.com/khivi/cockpit.git
cd cockpit
./setup.sh            # needs pre-commit: brew install pre-commit
uv run cockpit watch  # run the daemon/TUI from a dev checkout
```

`setup.sh` wires the pre-commit hooks for both the commit and push stages.

## Tests, types, lint

```bash
pytest                # test suite (also runs on pre-push)
mypy cockpit/          # type-check
pre-commit run ruff ruff-format --files <changed paths>   # lint/format
```

**Never** lint/format with `uvx ruff` or a global `ruff` install. `uvx` pulls
the latest ruff, whose rules drift from the version pinned in
`.pre-commit-config.yaml` — running it (especially tree-wide) rewrites lines
in files you never touched, producing unrelated churn the pinned hook then
fights on commit. The pinned pre-commit hook is the formatter and what CI
enforces; scope it to your changed paths only.

Test layout and per-layer test style (leaf modules vs orchestrators vs CLI
vs TUI vs e2e) are documented in [`AGENTS.md`](./AGENTS.md#test-layout) —
new source files get their own `test_<name>.py`.

## Worktree discipline

One dedicated git worktree per branch. Never commit directly to `main` in
the primary checkout, and never edit a feature branch in place without a
sibling worktree.

Why: cockpit derives per-branch state from `git worktree list`. An in-place
edit on the primary checkout pollutes the branch that must always track
`origin/main`, and a feature branch without its own worktree breaks
PR-tracking. See [`AGENTS.md`](./AGENTS.md#worktree-discipline) for the full
rule.

## PR title

We squash-merge, so the PR title becomes the commit subject on `main`. Title
must follow [Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): summary`, where `type` is one of `feat|fix|docs|style|refactor|
perf|test|build|ci|chore|revert`. Enforced by the required `lint-pr-title`
check. See [`AGENTS.md`](./AGENTS.md#commit--pr-title-convention).

## Privacy — this is a public repo

Never include, in commits, PRs, code comments, or docs:

- Internal ticket IDs (`ENG-123`, `PROJ-456`, etc.)
- Internal GitHub PR/issue URLs from private repos
- Real names of teammates (use roles: "the reviewer", "the on-call engineer")
- Internal Slack channels, wiki URLs, or tool links
- Internal hostnames, service names, or infra identifiers
- Customer names or company-specific identifiers

Full rules, including what to scan for before committing:
[`AGENTS.md`](./AGENTS.md#privacy--internal-references).

## Architecture

Invariants and the reasoning behind them live in
[`AGENTS.md`](./AGENTS.md#architecture-notes); control-flow diagrams live in
[`docs/state-machine.md`](./docs/state-machine.md). Read both before
touching daemon/TUI/cache code — they encode fixes for real regressions.
