---
name: coverage-audit
# model: sonnet
effort: medium
description: Audit the nightly coverage.yml run for real test gaps, separating deliberate I/O shims from genuine ones. TRIGGER on "audit coverage", "coverage gaps", "what isn't tested".
allowed-tools: Bash, Read, Grep, Glob
---

# coverage-audit

Answers *what should I test next*, from the nightly run. It does not answer *did
coverage drop* — that is `COVERAGE_FLOOR` (which fails the run) and Codecov's
per-commit delta.

Not for this: running the suite locally (`pytest -n auto --cov
--cov-report=term-missing`), reading a single percentage (the run's job summary
has it), or writing tests for a change you just made.

## Read last night's run

Never dispatch a fresh one. The nightly already ran, and a re-run costs ~90s for
numbers that only move when `main` does.

```bash
gh run list --workflow=coverage.yml --branch main --status success -L1 \
  --json databaseId,headSha,createdAt
```

Then the per-file table with missed line numbers:

```bash
gh run view <run-id> --log 2>/dev/null \
  | sed -n 's/^coverage\tRun suite with coverage\t[^ ]* //p' \
  | awk '/^Name .*Stmts/,/^Required test coverage/'
```

Use the run log, not the run's job summary (a markdown table with percentages
but **no line numbers**) and not Codecov's raw upload payload (the same data
wrapped in an envelope that needs stripping before it parses).

## Check staleness before reading anything

Compare the run's `headSha` against `origin/main`. If `main` has moved, the
report describes an older tree: its line numbers point into files that have
since shifted, so a "gap" may be code that no longer exists.

```bash
git rev-parse origin/main
```

Say so up front when it is stale. **Do not** silently audit a stale report, and
do not fix it by dispatching a run — offer that as the user's call.

## Classify every miss before proposing a single test

`coverage.yml` reserves headroom for thin I/O shims the suite does not cover on
purpose. Proposing tests for those is the failure mode this skill exists to
prevent — they look like the worst files in the report because they are small
and almost entirely uncovered.

| Deliberate | Evidence |
|---|---|
| A `main()` reading stdin | `statusline.py` — 8 of 13 statements, the whole body |
| Pidfile acquisition | `lib/daemon.py:23-32` |
| An interactive `input()` | named in `coverage.yml`'s `COVERAGE_FLOOR` comment |

That list is not closed. Read `coverage.yml`'s `COVERAGE_FLOOR` comment each
time — it is the authority, and it is a workflow comment nobody opens during an
audit, which is why it gets re-litigated.

A miss is a **real gap** when the uncovered lines carry a decision: a branch, a
guard, an error path, a fallback. Rank those by what breaks if they are wrong,
not by percentage — `lib/gh.py` at 81% with 84 missed statements of PR-join
logic outranks a 38% file that is one stdin read.

## Never weaken the check

Three moves are banned, and an agent told "improve coverage" reaches for all
three. The PreToolUse hook blocks skip/xfail literals under `tests/`; nothing
blocks the first two, so they are this skill's responsibility.

- **Do not** lower `COVERAGE_FLOOR` in `.github/workflows/coverage.yml`. Raising
  it when measured has moved up is sanctioned by the file itself; lowering it
  because a run failed is the config-edit form of `--no-verify`.
- **Do not** add `# pragma: no cover`. The tree has zero of them — exclusions go
  in `pyproject.toml`'s `[tool.coverage.report] exclude_also` as a pattern, and
  never in the same change as whatever tripped the floor.
- **Do not** skip or xfail a test to move the number.

Propose tests. If a line is genuinely unreachable, propose the `exclude_also`
pattern and say why, but leave the call to the user.

## Report

Real gaps ranked, deliberate misses as a bare count. Cap the list at five and
give the remainder as a number. When nothing survives classification, say "no
real gaps" — promoting a shim to fill the section is how this stops carrying
signal.
