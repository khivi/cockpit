# Docs, dev, release and the suite — behavior spec

## Docs

- [docs.never-lines~1] (untested: process rule) The **Never** / **Do not**
  lines in AGENTS.md encode paid-for regressions and are obeyed as
  invariants.
- [docs.guide-url~1] `FEATURE_GUIDE_URL` reads from `main` and the release
  notes open the unpinned releases index — any tag-pinned spelling fails,
  since the tag lands after the version bump and the pinned URL 404s for the
  whole release-PR window.
- [docs.backticks~1] Every backticked snake_case symbol and file path in a
  comment, docstring, AGENTS.md or specs/ resolves somewhere in the tree — a
  name that is deliberately gone goes unbackticked.
- [skills.no-gesture-skill~1] (untested: design rationale) Cockpit ships no
  agent skill for a gesture the daemon already sends a command for.
- [skills-dir.footprint~1] (untested: process rule) Repo-local
  `.claude/skills/` is dev tooling outside the wheel; it is not the
  `~/.claude` footprint rule's subject.
- [skills-dir.placement~1] (untested: process rule) A repo-local skill holds
  only a judgment nothing else can enforce — never a rule a hook, a test, or
  AGENTS.md would hold better.

## Capabilities

- [capabilities.degrade~1] Given a cmux missing a required verb, preflight
  prints a warning naming the verb and the tier it disables — and does not
  raise.
- [capabilities.tiers~1] The required set names no tier cockpit never built —
  the two removed ids stay pinned out — carries the workspace-groups
  capability, and `workspace-group` stays off the verb axis, which cannot
  see it.
- [capabilities.no-verb-counts~1] (untested: process rule) Verb counts live
  in the audit and its e2e mirror, never in prose.

## dev.sh and --dry

- [dev-script.git-refusal~1] Given `dev.sh -- new` or `-- close`, it exits 2
  refusing to run and creates no sandbox — neither `tool: none` nor `--dry`
  gates git, and the snapshot config points at the real repos.
- [dry.surfaces~1] Given a dry fast tick, no live cmux call is made — the
  rename and recolour passes stay home — while the local disk republish
  deliberately still runs.
- [dry.threading~1] `_build_state(dry)` reaches `cycle_all`'s `dry` kwarg
  for both values — a flag that landed in state but never reached the cycle
  would leave autoclose removing real worktrees.
- [lint.pinned-ruff~1] (untested: process rule) Lint and format run through
  the pinned pre-commit hook, never a floating ruff.

## Release

- [release.tag-yml~1] `tag.yml`'s checkout never sets
  `persist-credentials: false`, and `zizmor.yml` keeps `tag.yml` on the
  `artipacked` ignore list — losing either arrives at the same silently-dead
  release.
- [release.please-guard~1] (untested: process rule) The release-please skip
  guard keys off the commit subject, so the title pattern and the guard
  change together.
- [release.publish-yml~1] (untested: process rule) An `invalid-publisher`
  failure is fixed in PyPI's pending-publisher claims, never by editing
  `publish.yml`.

## The suite's own rules

- [suite.isolation~1] Exec'ing the real cmux raises "blocked", and a
  hand-rebuilt write into the real `$COCKPIT_HOME` raises too — the guard
  sits below every fixture, with `real_backend` the only opt-out.
- [suite.runtime-dir~1] (untested: process rule) Test isolation sets the
  runtime-dir env var, not just module attributes, because fixtures reload
  `lib.config`.
- [tests.helpers~1] (untested: process rule) An extracted helper takes its
  input rather than fetching it, so its callers' stubs keep holding.
- [tests.verify-outcome~1] (untested: process rule) A gate standing in for a
  third party's readiness verifies the outcome rather than trusting the
  proxy signal.
- [e2e.followup-delivery~1] (untested: process rule) The live-delivery e2e
  buys vocabulary and effect, never the readiness race; green there is not
  proof the race is handled.
- [coverage.claimed-not-tested~1] (untested: process rule) A claimed bullet
  proves a guard exists, never that the guard is strong.
