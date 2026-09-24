# GitHub — behavior spec

Every bullet is one invariant a test claims by id (`@pytest.mark.covers("<id~rev>")`);
`(untested: …)` waives a bullet no test can assert. AGENTS.md holds the mechanism
and the rationale behind each rule.

## PR identity

- [pr-list.one-per-branch~1] The live PR list carries at most one PR per head
  branch (`_one_pr_per_branch`); `prune_superseded_pr_caches` is the on-disk
  half.
- [gh.pr-rank~1] The per-branch alias prefers OPEN before `updated_at` and
  number, matching the payload-side rank.
- [gh.trunk-head~1] A trunk-headed PR's worktree branch comes from
  `pr_worktree_branch` at every join point; `headRefName` never threads straight
  into a worktree branch.
- [gh.review-decision~1] (untested: design rationale) The null-`reviewDecision`
  fallback is derived once at PR construction; no renderer or gate re-derives an
  approval.

## update_stale_branches

- [update-stale.trigger~1] The trigger is `mergeStateStatus == "BEHIND"`, never
  the local base-distance count.
- [update-stale.mutation~1] The update is the `updatePullRequestBranch` mutation
  — no local rebase, no force-push.
- [update-stale.cas~1] `expected_head_oid` rides every mutation as the compare-
  and-swap.
- [update-stale.scope~1] Only the two quiescent states — approved or snoozed —
  are updated.
- [update-stale.dismissal-gate~1] `update_branch_skip_reason` refuses an
  APPROVED PR whose protection dismisses stale reviews.
- [update-stale.ruleset-read~1] `branch_dismisses_stale_reviews` reads the
  merged effective rules, so ruleset-only repos — where the GraphQL field is
  null — still report dismissal.
- [update-stale.two-sources~1] The dismissal verdict ORs classic branch
  protection with the effective rules; the GraphQL field alone never opens the
  gate.
- [update-stale.fail-closed~1] A failed ruleset lookup reads as dismisses — fail
  closed, on the approved half only.
- [update-stale.resync~1] `resync_to_origin` refuses unless the tree is clean
  and HEAD still equals the pre-update sha, never a dirty-check alone.
