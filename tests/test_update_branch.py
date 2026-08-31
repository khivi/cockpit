"""Auto-update of stale PR branches (`cycle._update_stale_branches`).

Three layers, per the repo's test-style rule: the `PR` gate is a pure property
so it's tested directly; `git.resync_to_origin` runs against real git on
`tmp_path`; the orchestrator mocks the leaves to assert gating and ordering.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cockpit.lib.config import update_branch_method, update_stale_branches
from cockpit.lib.gh import PR, _pr_from_node
from cockpit.lib.git import Worktree, head_oid, resync_to_origin
from cockpit.orchestrators import cycle


def _pr(
    *,
    number: int = 1,
    branch: str = "khivi/feat",
    state: str = "OPEN",
    mine: bool = True,
    merge_state: str = "BEHIND",
    can_update: bool = True,
    review_decision: str = "APPROVED",
    dismisses: bool = False,
    node_id: str = "PR_node",
    head: str = "abc123",
) -> PR:
    return PR(
        number=number,
        title="t",
        branch=branch,
        url="",
        author="khivi",
        is_draft=False,
        review_decision=review_decision,
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state=state,
        mine=mine,
        node_id=node_id,
        head_oid=head,
        base="main",
        merge_state=merge_state,
        can_update_branch=can_update,
        dismisses_stale_reviews=dismisses,
    )


# ── PR.stale_vs_base / update_branch_skip_reason ────────────────────────────


def test_stale_vs_base_is_behind_only():
    """`BEHIND` is the only state meaning "the base moved AND the repo requires
    up-to-date branches". `BLOCKED`/`CLEAN`/`UNKNOWN` are not staleness."""
    assert _pr(merge_state="BEHIND").stale_vs_base
    for other in ("CLEAN", "BLOCKED", "UNKNOWN", "DIRTY", ""):
        assert not _pr(merge_state=other).stale_vs_base


def test_a_clean_behind_approved_pr_is_updatable():
    assert _pr().update_branch_skip_reason() == ""


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"state": "MERGED"}, "not open"),
        ({"mine": False}, "not my PR"),
        ({"merge_state": "CLEAN"}, "not behind base"),
        ({"can_update": False}, "github says the branch can't be updated"),
        ({"node_id": ""}, "no node id / head oid"),
        ({"head": ""}, "no node id / head oid"),
    ],
)
def test_skip_reasons(kwargs, expected):
    assert _pr(**kwargs).update_branch_skip_reason() == expected


def test_approved_pr_is_skipped_when_the_base_dismisses_stale_reviews():
    """The load-bearing gate. Updating an approved PR under
    `dismissesStaleReviews` discards the approval, turning a mergeable PR into
    one awaiting re-review — strictly worse than leaving it stale."""
    pr = _pr(review_decision="APPROVED", dismisses=True)
    assert "dismisses stale reviews" in pr.update_branch_skip_reason()


def test_the_dismissal_verdict_can_be_injected_for_rulesets():
    """`dismisses_stale_reviews` reads only classic branch protection; the caller
    resolves rulesets too and passes the combined verdict."""
    pr = _pr(review_decision="APPROVED", dismisses=False)
    assert pr.update_branch_skip_reason() == ""
    assert "dismisses stale reviews" in pr.update_branch_skip_reason(dismisses=True)
    # An explicit False can't be overridden by the classic field being True.
    strict = _pr(review_decision="APPROVED", dismisses=True)
    assert strict.update_branch_skip_reason(dismisses=False) == ""


def test_a_snoozed_unapproved_pr_still_updates_under_that_rule():
    """The dismiss gate keys on APPROVED, not on the rule alone — there is no
    approval to lose on a PR that hasn't got one."""
    pr = _pr(review_decision="REVIEW_REQUIRED", dismisses=True)
    assert pr.update_branch_skip_reason() == ""


def test_coworker_pr_is_never_updated_even_when_snoozed():
    """The snoozed fold holds coworkers' PRs too; their branches aren't ours."""
    assert _pr(mine=False).update_branch_skip_reason() == "not my PR"


# ── _pr_from_node wiring ────────────────────────────────────────────────────


def _node(**over) -> dict:
    node = {
        "id": "PR_kwabc",
        "number": 7,
        "title": "t",
        "body": "",
        "url": "u",
        "isDraft": False,
        "headRefName": "khivi/feat",
        "baseRefName": "main",
        "headRefOid": "deadbeef",
        "mergeable": "MERGEABLE",
        "reviewDecision": "APPROVED",
        "updatedAt": "",
        "state": "OPEN",
        "mergeStateStatus": "BEHIND",
        "viewerCanUpdateBranch": True,
        "author": {"login": "khivi", "__typename": "User"},
        "baseRef": {"branchProtectionRule": {"dismissesStaleReviews": True}},
        "reviewThreads": {"nodes": []},
        "reviews": {"nodes": []},
        "commits": {
            "nodes": [{"commit": {"checkSuites": {"nodes": []}, "status": None}}]
        },
    }
    node.update(over)
    return node


def test_pr_from_node_carries_the_update_fields():
    pr = _pr_from_node(_node(), self_user="khivi")
    assert pr is not None
    assert pr.node_id == "PR_kwabc"
    assert pr.merge_state == "BEHIND"
    assert pr.can_update_branch is True
    assert pr.dismisses_stale_reviews is True


def test_dismisses_stale_reviews_survives_a_null_check_suite():
    """`bpr` is read outside the CI branch — a null `checkSuites` (resolver
    error → ci="unknown") must not leave the protection rule unread."""
    pr = _pr_from_node(
        _node(commits={"nodes": [{"commit": {"checkSuites": None, "status": None}}]}),
        self_user="khivi",
    )
    assert pr is not None
    assert pr.ci == "unknown"
    assert pr.dismisses_stale_reviews is True


# ── branch_dismisses_stale_reviews ──────────────────────────────────────────


def _rules_run(payload: str, *, rc: int = 0):
    from types import SimpleNamespace

    return lambda *_a, **_k: SimpleNamespace(returncode=rc, stdout=payload, stderr="")


@pytest.mark.parametrize(
    "payload,expected",
    [
        (
            '[{"type":"pull_request","parameters":{"dismiss_stale_reviews_on_push":true}}]',
            True,
        ),
        (
            '[{"type":"pull_request","parameters":{"dismiss_stale_reviews_on_push":false}}]',
            False,
        ),
        ("[]", False),
        # A non-pull_request rule carrying the key must not count.
        (
            '[{"type":"deletion","parameters":{"dismiss_stale_reviews_on_push":true}}]',
            False,
        ),
        # Real shape: two rulesets both applying to the branch, one dismissing.
        (
            '[{"type":"pull_request","parameters":{"dismiss_stale_reviews_on_push":false}},'
            '{"type":"pull_request","parameters":{"dismiss_stale_reviews_on_push":true}}]',
            True,
        ),
    ],
)
def test_ruleset_dismissal_parsing(payload, expected):
    from cockpit.lib.gh import branch_dismisses_stale_reviews

    with patch("subprocess.run", _rules_run(payload)):
        assert branch_dismisses_stale_reviews("o/n", "main") is expected


@pytest.mark.parametrize("payload,rc", [("", 1), ("not json", 0), ('{"a":1}', 0)])
def test_ruleset_dismissal_is_none_when_unreadable(payload, rc):
    """None, not False — the caller fails closed on it."""
    from cockpit.lib.gh import branch_dismisses_stale_reviews

    with patch("subprocess.run", _rules_run(payload, rc=rc)):
        assert branch_dismisses_stale_reviews("o/n", "main") is None


def test_ruleset_dismissal_needs_a_repo_and_branch():
    from cockpit.lib.gh import branch_dismisses_stale_reviews

    assert branch_dismisses_stale_reviews("", "main") is None
    assert branch_dismisses_stale_reviews("o/n", "") is None


# ── git.resync_to_origin (real git) ─────────────────────────────────────────


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo_pair(tmp_path):
    """A bare origin plus a clone on `feat`, so origin/feat can be rewritten."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )
    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", str(origin), str(work)], check=True, capture_output=True
    )
    _run(work, "config", "user.email", "t@t")
    _run(work, "config", "user.name", "t")
    (work / "a.txt").write_text("1")
    _run(work, "add", "a.txt")
    _run(work, "commit", "-m", "base")
    _run(work, "push", "origin", "main")
    _run(work, "checkout", "-b", "feat")
    (work / "b.txt").write_text("2")
    _run(work, "add", "b.txt")
    _run(work, "commit", "-m", "feat")
    _run(work, "push", "origin", "feat")
    return origin, work


def _rewrite_origin_feat(origin: Path, tmp_path: Path) -> None:
    """Stand in for GitHub's REBASE: force-push a different `feat` to origin."""
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(origin), str(other)], check=True, capture_output=True
    )
    _run(other, "config", "user.email", "t@t")
    _run(other, "config", "user.name", "t")
    _run(other, "checkout", "-b", "feat", "origin/main")
    (other / "c.txt").write_text("3")
    _run(other, "add", "c.txt")
    _run(other, "commit", "-m", "rebased feat")
    _run(other, "push", "--force", "origin", "feat")


def test_resync_moves_a_clean_unmodified_worktree(repo_pair, tmp_path):
    origin, work = repo_pair
    prior = head_oid(work)
    _rewrite_origin_feat(origin, tmp_path)

    assert resync_to_origin(work, "feat", expected_head=prior) is True
    assert head_oid(work) != prior
    assert (work / "c.txt").exists()


def test_resync_refuses_a_dirty_worktree(repo_pair, tmp_path):
    """A dirty tree keeps its state — the update is cosmetic beside losing work."""
    origin, work = repo_pair
    prior = head_oid(work)
    _rewrite_origin_feat(origin, tmp_path)
    (work / "scratch.txt").write_text("uncommitted")

    assert resync_to_origin(work, "feat", expected_head=prior) is False
    assert head_oid(work) == prior
    assert (work / "scratch.txt").read_text() == "uncommitted"


def test_resync_refuses_when_head_moved_past_the_expected_sha(repo_pair, tmp_path):
    """The compare-and-swap: a local commit made after the cycle's fetch means
    the worktree holds work origin never had, so a hard reset would discard it."""
    origin, work = repo_pair
    prior = head_oid(work)
    _rewrite_origin_feat(origin, tmp_path)
    (work / "local.txt").write_text("local work")
    _run(work, "add", "local.txt")
    _run(work, "commit", "-m", "local only")
    moved = head_oid(work)

    assert resync_to_origin(work, "feat", expected_head=prior) is False
    assert head_oid(work) == moved
    assert (work / "local.txt").exists()


def test_resync_needs_a_branch_and_an_expected_head(repo_pair):
    _origin, work = repo_pair
    assert resync_to_origin(work, "", expected_head="abc") is False
    assert resync_to_origin(work, "feat", expected_head="") is False


# ── config readers ──────────────────────────────────────────────────────────


def test_update_stale_branches_defaults_off_and_resolves_repo_over_global():
    assert update_stale_branches({}, {}) is False
    assert update_stale_branches({"update_stale_branches": True}, {}) is True
    # Repo wins outright, in both directions.
    assert (
        update_stale_branches(
            {"update_stale_branches": True}, {"update_stale_branches": False}
        )
        is False
    )
    assert (
        update_stale_branches(
            {"update_stale_branches": False}, {"update_stale_branches": True}
        )
        is True
    )


def test_org_defaults_reach_both_fields_with_no_org_aware_reader():
    """Org support is free: `apply_org_defaults` merges the block into each repo
    entry at load, so the plain repo → global → default chain picks it up. Per
    the `orgs` invariant there is deliberately no org-aware reader to add."""
    from cockpit.lib.config import apply_org_defaults

    cfg = {
        "orgs": {
            "acme": {"update_stale_branches": True, "update_branch_method": "merge"}
        },
        "repos": [
            {"name": "a", "path": "/tmp/a", "org": "acme"},
            # A repo overriding one field still inherits the other.
            {
                "name": "b",
                "path": "/tmp/b",
                "org": "acme",
                "update_stale_branches": False,
            },
            {"name": "c", "path": "/tmp/c"},
        ],
    }
    apply_org_defaults(cfg)
    a, b, c = cfg["repos"]
    assert (update_stale_branches(cfg, a), update_branch_method(cfg, a)) == (
        True,
        "MERGE",
    )
    assert (update_stale_branches(cfg, b), update_branch_method(cfg, b)) == (
        False,
        "MERGE",
    )
    assert (update_stale_branches(cfg, c), update_branch_method(cfg, c)) == (
        False,
        "REBASE",
    )


def test_update_branch_method_defaults_to_rebase_and_rejects_junk():
    assert update_branch_method({}, {}) == "REBASE"
    assert update_branch_method({"update_branch_method": "merge"}, {}) == "MERGE"
    assert update_branch_method({}, {"update_branch_method": "MeRgE"}) == "MERGE"
    assert update_branch_method({"update_branch_method": "squash"}, {}) == "REBASE"


# ── _update_stale_branches orchestration ────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_ruleset_dismissal():
    """Default the ruleset lookup to "doesn't dismiss" so orchestration tests
    don't shell out to `gh`. Without it every approved candidate fails closed on
    the unreachable endpoint — correct behaviour, but it masks what these assert.
    Tests that care patch over this.
    """
    with patch.object(cycle, "branch_dismisses_stale_reviews", return_value=False):
        yield


def _ctx(tmp_path, prs, *, cfg=None, prefs=None, tracked=None, dry=False):
    return cycle.RepoCycle(
        cfg=cfg if cfg is not None else {"update_stale_branches": True},
        repo_path=tmp_path,
        owner="o",
        name="n",
        self_user="khivi",
        wts=[],
        prs=prs,
        tracked=tracked or {},
        names={},
        cwds={},
        merged_branches={},
        merged_branches_deep={},
        pill_state={},
        dry=dry,
        headless=False,
        prefs=prefs or {},
    )


def test_opt_in_is_required(tmp_path):
    ctx = _ctx(tmp_path, [_pr()], cfg={})
    with patch.object(cycle, "update_pull_request_branch") as upd:
        cycle._update_stale_branches(ctx)
    upd.assert_not_called()


def test_dry_never_writes(tmp_path):
    ctx = _ctx(tmp_path, [_pr()], dry=True)
    with patch.object(cycle, "update_pull_request_branch") as upd:
        cycle._update_stale_branches(ctx)
    upd.assert_not_called()


def test_an_approved_behind_pr_is_updated_with_a_compare_and_swap(tmp_path):
    ctx = _ctx(tmp_path, [_pr(head="abc123")])
    with patch.object(
        cycle, "update_pull_request_branch", return_value=(True, "new")
    ) as upd:
        cycle._update_stale_branches(ctx)
    upd.assert_called_once()
    args, kwargs = upd.call_args
    assert args[0] == "PR_node"
    assert args[1] == "abc123"  # expected_head_oid — the CAS guard
    assert kwargs["method"] == "REBASE"


def test_a_snoozed_pr_is_updated_even_though_it_is_not_approved(tmp_path):
    from cockpit.lib.nudges import NudgePref

    pr = _pr(number=5, review_decision="REVIEW_REQUIRED")
    ctx = _ctx(tmp_path, [pr], prefs={5: NudgePref(snoozed=True)})
    with patch.object(
        cycle, "update_pull_request_branch", return_value=(True, "new")
    ) as upd:
        cycle._update_stale_branches(ctx)
    upd.assert_called_once()


def test_an_unapproved_unsnoozed_pr_is_left_alone(tmp_path):
    """The quiescent-state scoping: a PR under active work may have a session
    mid-turn on it, and rewriting the head underneath one is the failure this
    scoping exists to avoid."""
    pr = _pr(review_decision="REVIEW_REQUIRED")
    ctx = _ctx(tmp_path, [pr])
    with patch.object(cycle, "update_pull_request_branch") as upd:
        cycle._update_stale_branches(ctx)
    upd.assert_not_called()


def test_the_marker_stops_a_repeat_but_a_new_head_re_evaluates(tmp_path):
    ctx = _ctx(tmp_path, [_pr(head="head1")])
    with patch.object(
        cycle, "update_pull_request_branch", return_value=(True, "new")
    ) as upd:
        cycle._update_stale_branches(ctx)
        cycle._update_stale_branches(ctx)
        assert upd.call_count == 1

        ctx.prs = [_pr(head="head2")]
        cycle._update_stale_branches(ctx)
        assert upd.call_count == 2


def test_a_failure_clears_the_marker_so_the_next_tick_retries(tmp_path):
    ctx = _ctx(tmp_path, [_pr()])
    with patch.object(
        cycle, "update_pull_request_branch", return_value=(False, "stale oid")
    ) as upd:
        cycle._update_stale_branches(ctx)
        cycle._update_stale_branches(ctx)
    assert upd.call_count == 2
    assert not any(k.startswith("update-branch:") for k in ctx.pill_state)


def test_a_ruleset_dismissal_blocks_an_approved_update(tmp_path):
    """The hole this closes: a rulesets-only repo reports
    `branchProtectionRule: null`, so the classic field reads False."""
    ctx = _ctx(tmp_path, [_pr(review_decision="APPROVED", dismisses=False)])
    with (
        patch.object(cycle, "branch_dismisses_stale_reviews", return_value=True),
        patch.object(cycle, "update_pull_request_branch") as upd,
    ):
        cycle._update_stale_branches(ctx)
    upd.assert_not_called()


def test_an_unreadable_ruleset_fails_closed_for_approved_prs(tmp_path):
    """ "Couldn't ask" must not read as "safe" — being wrong here discards an
    approval. This is the one place cockpit fails closed."""
    ctx = _ctx(tmp_path, [_pr(review_decision="APPROVED")])
    with (
        patch.object(cycle, "branch_dismisses_stale_reviews", return_value=None),
        patch.object(cycle, "update_pull_request_branch") as upd,
    ):
        cycle._update_stale_branches(ctx)
    upd.assert_not_called()


def test_a_snoozed_pr_never_pays_the_ruleset_lookup(tmp_path):
    """No approval to lose, so it neither consults the endpoint nor fails closed
    when that endpoint is unavailable."""
    from cockpit.lib.nudges import NudgePref

    pr = _pr(number=5, review_decision="REVIEW_REQUIRED")
    ctx = _ctx(tmp_path, [pr], prefs={5: NudgePref(snoozed=True)})
    with (
        patch.object(cycle, "branch_dismisses_stale_reviews") as rules,
        patch.object(
            cycle, "update_pull_request_branch", return_value=(True, "new")
        ) as upd,
    ):
        cycle._update_stale_branches(ctx)
    rules.assert_not_called()
    upd.assert_called_once()


def test_the_ruleset_lookup_is_cached_per_base_branch(tmp_path):
    ctx = _ctx(
        tmp_path,
        [
            _pr(number=1, branch="a", head="h1"),
            _pr(number=2, branch="b", head="h2"),
            _pr(number=3, branch="c", head="h3"),
        ],
    )
    with (
        patch.object(
            cycle, "branch_dismisses_stale_reviews", return_value=False
        ) as rules,
        patch.object(cycle, "update_pull_request_branch", return_value=(True, "n")),
    ):
        cycle._update_stale_branches(ctx)
    assert rules.call_count == 1  # three PRs, one base


def test_a_rebase_resyncs_the_local_worktree(tmp_path):
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat")
    pr = _pr(head="prior")
    ctx = _ctx(tmp_path, [pr], tracked={"khivi/feat": (pr, wt)})
    with (
        patch.object(cycle, "update_pull_request_branch", return_value=(True, "new")),
        patch.object(cycle, "resync_to_origin", return_value=True) as sync,
    ):
        cycle._update_stale_branches(ctx)
    sync.assert_called_once_with(wt.path, "khivi/feat", expected_head="prior")


def test_merge_method_never_resyncs(tmp_path):
    """MERGE adds a commit, so the worktree fast-forwards on its own — a hard
    reset would be gratuitous."""
    wt = Worktree(path=tmp_path / "wt", branch="khivi/feat")
    pr = _pr()
    ctx = _ctx(
        tmp_path,
        [pr],
        cfg={"update_stale_branches": True, "update_branch_method": "merge"},
        tracked={"khivi/feat": (pr, wt)},
    )
    with (
        patch.object(cycle, "update_pull_request_branch", return_value=(True, "new")),
        patch.object(cycle, "resync_to_origin") as sync,
    ):
        cycle._update_stale_branches(ctx)
    sync.assert_not_called()


def test_a_pr_with_no_worktree_updates_and_skips_the_resync(tmp_path):
    ctx = _ctx(tmp_path, [_pr()], tracked={})
    with (
        patch.object(
            cycle, "update_pull_request_branch", return_value=(True, "new")
        ) as upd,
        patch.object(cycle, "resync_to_origin") as sync,
    ):
        cycle._update_stale_branches(ctx)
    upd.assert_called_once()
    sync.assert_not_called()


def test_cycle_repo_runs_the_update_before_the_lifecycle_reconcile(tmp_path):
    """Ordering matters: `_reconcile_worktree_lifecycle` reads the worktree's
    commit state, so the resync must already have landed."""
    order: list[str] = []
    ctx = _ctx(tmp_path, [])
    with (
        patch.object(cycle, "_prepare_cycle", return_value=ctx),
        patch.object(cycle, "_write_pr_caches"),
        patch.object(cycle, "has_workspace_backend", return_value=False),
        patch.object(cycle, "log_ff_advances"),
        patch.object(cycle, "ff_default_branch_worktrees", return_value=[]),
        patch.object(
            cycle,
            "_update_stale_branches",
            side_effect=lambda *_a, **_k: order.append("update"),
        ),
        patch.object(
            cycle,
            "_transition_merged_tickets",
            side_effect=lambda *_a, **_k: order.append("tickets"),
        ),
        patch.object(
            cycle,
            "_reconcile_worktree_lifecycle",
            side_effect=lambda *_a, **_k: order.append("lifecycle"),
        ),
    ):
        cycle.cycle_repo({}, "khivi", dry=False, pr_cache={}, pill_state={}, cfg={})
    assert order == ["update", "tickets", "lifecycle"]
