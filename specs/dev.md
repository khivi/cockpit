# Docs, dev, release and the suite — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## Docs

- [docs.never-lines~1] (untested: process rule) The **Never** / **Do not** lines
  in AGENTS.md encode paid-for regressions and are obeyed as invariants.
- [docs.guide-url~1] `FEATURE_GUIDE_URL` and `RELEASE_NOTES_URL` point at `main`
  and the releases index, never at a tag pinned to the running version.
- [docs.backticks~1] Every backticked symbol and path in comments, docstrings,
  AGENTS.md and specs/ resolves in the tree.
- [skills.no-gesture-skill~1] (untested: design rationale) Cockpit ships no
  agent skill for a gesture the daemon already sends a command for.
- [skills-dir.footprint~1] (untested: process rule) Repo-local `.claude/skills/`
  is dev tooling outside the wheel; it is not the `~/.claude` footprint rule's
  subject.
- [skills-dir.placement~1] (untested: process rule) A repo-local skill holds
  only a judgment nothing else can enforce — never a rule a hook, a test, or
  AGENTS.md would hold better.

## Capabilities

- [capabilities.degrade~1] The capability probe warns and degrades; it never
  exits the daemon.
- [capabilities.tiers~1] Every required verb and capability names a tier cockpit
  actually has, and `workspace-group` stays off the verb axis.
- [capabilities.no-verb-counts~1] (untested: process rule) Verb counts live in
  the audit and its e2e mirror, never in prose.

## dev.sh and --dry

- [dev-script.git-refusal~1] `dev.sh` refuses the subcommands that mutate
  outside the sandbox through git, alongside `setup`.
- [dry.surfaces~1] `--dry` gates the cycle, the fast tick's live-cmux writes,
  and the outward TUI row keys.
- [dry.threading~1] `dry` threads one path from the CLI into the cycle; no call
  site hardcodes it and no second suppression path exists.
- [lint.pinned-ruff~1] (untested: process rule) Lint and format run through the
  pinned pre-commit hook, never a floating ruff.

## Release

- [release.tag-yml~1] `tag.yml`'s checkout keeps its credentials; it is exempt
  from `artipacked`.
- [release.please-guard~1] (untested: process rule) The release-please skip
  guard keys off the commit subject, so the title pattern and the guard change
  together.
- [release.publish-yml~1] (untested: process rule) An `invalid-publisher`
  failure is fixed in PyPI's pending-publisher claims, never by editing
  `publish.yml`.

## The suite's own rules

- [suite.isolation~1] The suite cannot reach the live machine: Popen, tool
  resolution and the real config writer are all blocked, loudly, with
  `real_backend` the only opt-out.
- [suite.runtime-dir~1] (untested: process rule) Test isolation sets the
  runtime-dir env var, not just module attributes, because fixtures reload
  `lib.config`.
- [tests.helpers~1] (untested: process rule) An extracted helper takes its input
  rather than fetching it, so its callers' stubs keep holding.
- [tests.verify-outcome~1] (untested: process rule) A gate standing in for a
  third party's readiness verifies the outcome rather than trusting the proxy
  signal.
- [e2e.followup-delivery~1] (untested: process rule) The live-delivery e2e buys
  vocabulary and effect, never the readiness race; green there is not proof the
  race is handled.
- [coverage.claimed-not-tested~1] (untested: process rule) A claimed bullet
  proves a guard exists, never that the guard is strong.
