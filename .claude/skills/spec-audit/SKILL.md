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

## Scope

Default: only pairs the current branch touched.

```bash
git diff origin/main --name-only -- specs/ tests/
```

A changed spec file scopes to the ids of its edited bullets; a changed test
file scopes to the ids its markers claim. On `all`, audit every unwaived
bullet. Skip `(untested: …)` bullets either way — nothing runnable to judge.

## Pair each id

The mapping is two greps (the same pair AGENTS.md documents):

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
snippet, entailed as a bare count. Cap findings at five and give the remainder
as a number. When every pair holds, say "all entailed" — promoting a nit to
fill the section is how the audit stops carrying signal.
