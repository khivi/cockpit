---
name: spec-audit
# model: sonnet
effort: medium
description: Judge whether each claiming test actually asserts its specs/ bullet — the exists-vs-strong gap the coverage gate cannot see. TRIGGER on "audit the spec", "spec audit", "verify covers markers", or re-verifying after a ~rev bump.
allowed-tools: Bash, Read, Grep, Glob
---

# spec-audit

Answers *does this test prove its bullet*. `tests/test_invariant_coverage.py`
proves every unwaived bullet is *claimed*; it cannot see whether the claiming
test would fail if the claim were false — the same author writes the bullet and
the marker, so the two errors correlate rather than cancel. This skill is the
third party.

Not for this: finding unclaimed bullets or dead markers (the gate fails those),
untested source lines (`/coverage-audit`), or writing the spec (it is
hand-owned).

## Arguments

| You type | Scope | Shape of the run |
|---|---|---|
| `/spec-audit` | this worktree's branch vs `origin/main` | inline, usually a handful of ids |
| `/spec-audit all` | every unwaived bullet in the ledger | fan out — see below |
| `/spec-audit <PR#>` | that PR's diff | inline |
| `/spec-audit <id>~1` | one bullet, however scoped | inline |

Anything else in the argument is an instruction about *how* to run, not what to
audit (`all - use subagents`). A bare `/spec-audit` on a branch that touched no
spec or test file is a legitimate empty run — say so rather than silently
widening to `all`, which is a hundred-and-twenty-seven-bullet read nobody asked
for.

## Scope

`scope.py` resolves it and sizes the ledger in one call. Start every run here:

```bash
python3 .claude/skills/spec-audit/scope.py          # branch, vs origin/main
python3 .claude/skills/spec-audit/scope.py all      # every unwaived bullet
python3 .claude/skills/spec-audit/scope.py --pr 539  # a PR's diff
python3 .claude/skills/spec-audit/scope.py --ids-only # just the ids, to iterate
```

It prints per-file bullet / waived / auditable counts and a total, then the
in-scope ids with how many tests claim each — so a bullet claimed by three
tests is flagged as a union to judge before you open a single file. Report the
totals you audited against; they are the denominator a reader needs to know
whether "all entailed" covered nine bullets or a hundred and twenty-seven.

**Do not** count bullets with `rg -c`. A bullet's text wraps, so its id and its
`(untested: …)` waiver sit on different lines: `rg -c '\(untested:'` counts
LINES and silently reports a different number than `scope.py` for the same
ledger. That is what the script exists to stop.

A changed spec file scopes to the ids of its edited bullets; a changed test
file scopes to the ids its markers claim. Both are resolved from ADDED lines
only, never a hunk's declared range — `gh pr diff` carries three lines of
context, so a range would pull in whatever marker happens to sit beside an
edit. Skip `(untested: …)` bullets either way; nothing runnable to judge.

**The script sizes the run; it does not perform it.** It cannot judge a pair —
that is reading a bullet against every test claiming it and deciding whether
the test would fail if the claim were false, which is the one thing here that
is not mechanical. `scope.py` exists so the *denominator* stops being guessed,
not to shrink the reading.

**`all` is ~127 bullets — fan it out.** One pass cannot hold that many pairs
honestly; it degrades into skimming, which reports "all entailed" for a ledger
nobody read. Launch one subagent per spec file (group the small ones to even
out the load), give each the rubric and the read-only constraint verbatim, and
judge their findings yourself before relaying — a subagent returns a summary,
not a fact. A branch or `--pr` scope is usually small enough to do inline.

## Pair each id

`scope.py` already told you how many tests claim each id. The mapping itself is
two greps (the same pair AGENTS.md documents):

```bash
rg -n '<id>~' specs/    # the bullet
rg -n '<id>~' tests/    # every claiming marker
```

Read the whole test function under each marker — the fixtures and helpers it
leans on too, when the assertion's strength lives there. A bullet claimed by
several tests is judged against their union: the tests together may entail
what no single one does.

## The rubric

One question per pair: **would this test fail if the bullet's claim were
false?** Concretely:

- The bullet's *given/when* scenario is the one the test constructs — not a
  narrower cousin that happens to share the assertion.
- The assertion checks the claimed behavior, not just the shape around it (a
  call was made ≠ the call did what the bullet says).
- It cannot pass vacuously: the test proves its own setup took effect the way
  the restamp test's "this isn't vacuously true" asserts do.
- For a ban bullet, the needle/AST check actually catches the violation shape
  the bullet describes, not only the spelling the author imagined.

Verdicts: **entailed**, **partial** (name exactly what goes unasserted), or
**mismatch** (the test asserts something else — the marker is vouching for the
wrong bullet).

## Never weaken

Propose, never edit. Specifically banned, because "make the audit clean" is
the adversarial reading of this skill:

- **Do not** reword a bullet down to what the test happens to assert — the
  spec demands tests, never the reverse.
- **Do not** bump a `~rev` or touch a marker; both are the author's re-verify
  act, not the auditor's.
- **Do not** propose a waiver for a bullet that is merely under-asserted.

## Report

Mismatches first, then partials with the missing assertion sketched as a
snippet, entailed as a bare count. Open with `scope.py`'s denominator — *127
auditable, 5 in scope* — since "all entailed" means nothing without it. Cap
findings at five and give the remainder as a number. When every pair holds, say
"all entailed" — promoting a nit to fill the section is how the audit stops
carrying signal.
