# Contributing

## Quick start

```bash
git clone https://github.com/khivi/cockpit.git && cd cockpit
./setup.sh            # wires pre-commit (commit + push stages); needs `brew install pre-commit`
uv run cockpit watch  # run the daemon/TUI from a dev checkout
```

## Checks

```bash
pytest                # also runs on pre-push
mypy cockpit/
pre-commit run ruff ruff-format --files <changed paths>
```

**Don't** lint/format with `uvx ruff` or a global `ruff` — it pulls a newer version than the pinned hook and rewrites unrelated lines into churn. The pinned pre-commit hook is the formatter CI enforces; scope it to your changed paths.

Test layout and per-layer style (leaf vs orchestrator vs CLI vs TUI vs e2e; new files get their own `test_<name>.py`): [`AGENTS.md`](./AGENTS.md#test-layout).

## Rules (full text in AGENTS.md)

- **Worktrees** — one dedicated worktree per branch; never edit `main` or a feature branch in the primary checkout. Cockpit derives per-branch state from `git worktree list`, so in-place edits break PR-tracking. [details](./AGENTS.md#worktree-discipline)
- **PR title** — squash-merged, so it becomes the commit subject. [Conventional Commits](https://www.conventionalcommits.org/) (`type(scope): summary`), enforced by the required `lint-pr-title` check. [details](./AGENTS.md#commit--pr-title-convention)
- **Privacy** — public repo: keep internal ticket IDs, private URLs, teammate names, and infra identifiers out of commits/PRs/code/docs. [scan list](./AGENTS.md#privacy--internal-references)

## Architecture

Invariants + reasoning: [`AGENTS.md`](./AGENTS.md#architecture-notes). Control-flow diagrams: [`docs/state-machine.md`](./docs/state-machine.md). Read both before touching daemon/TUI/cache code — they encode fixes for real regressions.
