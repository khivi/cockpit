# Working with `specs/`

How to add, change and check a behavior invariant. The *rules* live in
AGENTS.md's "Invariant coverage" section — what a bullet is, why the spec is
hand-owned, why a revision bump forces a re-verify. This file is the procedure
around them and deliberately does not repeat them.

## Three pieces, and only one of them blocks a merge

| Piece | What it answers | Blocking? |
|---|---|---|
| `specs/*.md` | what the system promises | — it is the source |
| `tests/test_invariant_coverage.py` | is every promise *claimed* by a test | **yes**, in CI |
| `/spec-audit` | does the claiming test actually *prove* it | no — advisory |

The gate is mechanical and cheap, so it runs every time. It can only see that a
marker exists. Whether the test behind that marker would fail if the bullet
were false is a judgment call, which is what the audit is for — and why a green
CI is not evidence the ledger means anything.

## Adding an invariant

Bullets flow spec → test. Write the bullet first and let CI demand the test:

1. Add `- [<id>~1] Given …, when …, then …` to the right `specs/*.md`.
2. Run `pytest tests/test_invariant_coverage.py`. It fails — nothing claims it.
3. Write the test. Decorate it `@pytest.mark.covers("<id>~1")`.
4. Re-run. Green.

Ids are `<subject>.<aspect>` — `folds.born-collapsed`, `spawn.adopt-grace`.
Group them under the `##` heading that matches the subject; the file is read
top to bottom by people, not just grepped.

Write the bullet as an **observable scenario**, not a rule. "A trailing fold is
created collapsed" can be tested; "folds should be unobtrusive" cannot.

## Finding a pair

```bash
rg -n '<id>~' specs/    # the bullet
rg -n '<id>~' tests/    # every claiming marker
```

A bullet may be claimed by several tests and is judged against their **union** —
the set together may entail what no single test does. `spawn.adopt-grace~1` is
the worked example: one test in `tests/lib/test_git.py` proves an un-stat-able
path reads as infinitely old, two in `tests/orchestrators/test_cycle.py` prove
both adopt paths refuse a young worktree. None proves the bullet alone.

## Changing or retiring one

- **Reworded, same meaning** → leave the revision alone.
- **Different claim** → bump `~<rev>`. Every test still on the old revision now
  fails, which is the point: each one gets re-read against the new claim before
  its marker moves.
- **Nothing runnable can assert it** → `(untested: <reason>)`, and bump
  `WAIVED_COUNT` in the gate in the same edit. Never a drive-by.

## Auditing

```bash
python3 .claude/skills/spec-audit/scope.py        # branch vs origin/main
python3 .claude/skills/spec-audit/scope.py all    # the whole ledger
python3 .claude/skills/spec-audit/scope.py --pr 539
```

`scope.py` sizes the run and resolves which ids are in scope; it does not judge
anything. Count bullets with it rather than `rg -c` — a bullet's text wraps, so
its id and its `(untested: …)` waiver sit on different lines and a line count
silently disagrees with itself.

Then `/spec-audit` (branch scope) or `/spec-audit all`. The whole ledger is
~127 auditable bullets, too many for one honest pass, so `all` fans out across
subagents by spec file.

Three verdicts, and each obliges something different:

- **entailed** — nothing to do.
- **partial** — the test proves part of the claim. Name the unasserted half.
- **mismatch** — the test asserts something else; the marker is vouching for
  the wrong bullet. Move the marker, do not reword the bullet to fit.

The audit **proposes and never edits**: it must not bump a revision, move a
marker, reword a bullet down to what a test happens to assert, or waive
something that is merely under-asserted. All of those are the author's acts,
because all of them are ways to make the audit clean without making the ledger
truer.

## The failure mode to expect

A full-ledger audit found **1 mismatch and 19 partials over 127 bullets**, and
the partials were overwhelmingly one shape: *the marker sits on the first test
that proves* a *half of the bullet, while a sibling test proving the other half
sits unmarked a few hundred lines away.*

That shape is invisible to the gate — the bullet is claimed, CI is green — and
it is the natural consequence of writing the marker while writing one test. So
when you add a marker, the question is not "does this test prove something
about the bullet" but:

> Which clauses does this bullet have, and is each one asserted somewhere in
> the claiming set?

A two-clause bullet usually wants two markers. If the second test already
exists, tagging it is the whole fix.
