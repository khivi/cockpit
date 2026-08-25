# Contributing

## Quick start

```bash
git clone https://github.com/khivi/cockpit.git && cd cockpit
./setup.sh            # wires pre-commit (commit + push stages); needs `brew install pre-commit`
./dev.sh              # run the TUI from this checkout, against a throwaway sandbox
```

**Run your build with `./dev.sh`, not `uv run cockpit watch`.** A bare
`uv run cockpit watch` shares `~/.config/cockpit` and `$TMPDIR` with your
installed daemon: it fights for the pidfile, drains the real close-request queue
(tearing down real worktrees), repaints your live statusline, and — since
autoclose goes through git rather than the workspace backend — can `git branch -D`
merged branches. `./dev.sh` isolates `COCKPIT_HOME` + `TMPDIR` and runs with
`tool: none` + `--dry`, so it decides and prints without acting. It re-seeds from
a copy of your real PR cache each run, so the table shows real rows.

`./dev.sh --empty` for a repo-less table; `./dev.sh -- <subcommand>` for anything
other than `watch`. Everything cmux-facing (folds, focus, `a`, `d`) is inert
under `tool: none` — those need a real cmux. And never run `cockpit setup` from
a worktree; `dev.sh` refuses it, because it bakes the worktree's ephemeral
`.venv/bin/python` into your `~/.claude` and `starship.toml`.

## Checks

```bash
pytest -n auto        # whole suite; also runs on pre-push
pytest tests/test_spawn.py::test_route_by_ticket   # one test — skip -n, worker boot is pure tax
mypy cockpit/
pre-commit run ruff ruff-format --files <changed paths>
```

Most of the suite's wall clock is idle time waiting on real `git`/`gh`/`cmux`
subprocesses, so `-n auto` parallelises it near-linearly (115s → 16s on 18 cores).

**Don't** lint/format with `uvx ruff` or a global `ruff` — it pulls a newer version than the pinned hook and rewrites unrelated lines into churn. The pinned pre-commit hook is the formatter CI enforces; scope it to your changed paths.

Test layout and per-layer style (leaf vs orchestrator vs CLI vs TUI vs e2e; new files get their own `test_<name>.py`): [`AGENTS.md`](./AGENTS.md#test-layout).

## Rules (full text in AGENTS.md)

- **Worktrees** — one dedicated worktree per branch; never edit `main` or a feature branch in the primary checkout. Cockpit derives per-branch state from `git worktree list`, so in-place edits break PR-tracking. [details](./AGENTS.md#worktree-discipline)
- **PR title** — squash-merged, so it becomes the commit subject. [Conventional Commits](https://www.conventionalcommits.org/) (`type(scope): summary`), enforced by the required `lint-pr-title` check. [details](./AGENTS.md#commit--pr-title-convention)
- **Privacy** — public repo: keep internal ticket IDs, private URLs, teammate names, and infra identifiers out of commits/PRs/code/docs. [scan list](./AGENTS.md#privacy--internal-references)

## Architecture

Invariants + reasoning: [`AGENTS.md`](./AGENTS.md#architecture-notes). Control-flow diagrams: [`docs/state-machine.md`](./docs/state-machine.md). Read both before touching daemon/TUI/cache code — they encode fixes for real regressions.
