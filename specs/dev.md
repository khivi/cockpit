# Docs, dev, release and the suite — behavior spec

## Docs

- [docs.never-lines~1] (untested: process rule) The **Never** / **Do not**
  lines in AGENTS.md encode paid-for regressions. Contributors obey them as
  invariants.
- [docs.guide-url~1] `FEATURE_GUIDE_URL` reads from `main`. The release notes
  open the unpinned releases index. Any tag-pinned spelling fails.
- [docs.backticks~1] Every backticked snake_case symbol and file path in a
  comment, docstring, AGENTS.md or specs/ resolves somewhere in the tree. A
  name that is deliberately gone goes unbackticked.
- [skills.no-gesture-skill~1] (untested: design rationale) Cockpit ships no
  agent skill for a gesture the daemon already sends a command for.
- [skills-dir.footprint~1] (untested: process rule) Repo-local
  `.claude/skills/` is dev tooling outside the wheel. It is not the subject of
  the `~/.claude` footprint rule.
- [skills-dir.placement~1] (untested: process rule) A repo-local skill holds
  only a judgment nothing else can enforce. It never holds a rule a hook, a
  test, or AGENTS.md would hold better.

## Capabilities

- [capabilities.degrade~1] Given a cmux missing a required verb, preflight
  prints a warning. The warning names the verb and the tier it disables.
  Preflight does not raise.
- [capabilities.tiers~1] The required set names no tier cockpit never built.
  The two removed ids stay pinned out. The set carries the workspace-groups
  capability. `workspace-group` stays off the verb axis.
- [capabilities.no-verb-counts~1] (untested: process rule) Verb counts live
  in the audit and its e2e mirror. They never appear in prose.

## dev.sh and --dry

- [dev-script.git-refusal~1] Given `dev.sh -- new` or `-- close`, it exits 2
  and refuses to run. It creates no sandbox.
- [dry.surfaces~1] Given a dry fast tick, no live cmux call is made. The
  rename and recolour passes stay home. The local disk republish still runs.
- [dry.threading~1] `_build_state(dry)` reaches `cycle_all`'s `dry` kwarg for
  both values.
- [lint.pinned-ruff~1] (untested: process rule) Lint and format run through
  the pinned pre-commit hook. They never run through a floating ruff.

## Release

- [release.tag-yml~1] `tag.yml`'s checkout never sets
  `persist-credentials: false`. `zizmor.yml` keeps `tag.yml` on the
  `artipacked` ignore list.
- [release.please-guard~1] (untested: process rule) The release-please skip
  guard keys off the commit subject. The title pattern and the guard change
  together.
- [release.publish-yml~1] (untested: process rule) The fix for an
  `invalid-publisher` failure goes in PyPI's pending-publisher claims. It
  never edits `publish.yml`.

## The suite's own rules

- [suite.isolation~1] Exec'ing the real cmux raises "blocked". A hand-rebuilt
  write into the real `$COCKPIT_HOME` raises too. The guard sits below every
  fixture. `real_backend` is the only opt-out.
- [suite.runtime-dir~1] (untested: process rule) Test isolation sets the
  runtime-dir env var, not just module attributes.
- [tests.helpers~1] (untested: process rule) An extracted helper takes its
  input rather than fetching it.
- [tests.verify-outcome~1] (untested: process rule) A gate that stands in for
  a third party's readiness verifies the outcome. It never trusts the proxy
  signal.
- [e2e.followup-delivery~1] (untested: process rule) The live-delivery e2e
  buys vocabulary and effect. It never buys the readiness race. A green run
  there is not proof that the race is handled.
- [coverage.claimed-not-tested~1] (untested: process rule) A claimed bullet
  proves a guard exists. It never proves that the guard is strong.
