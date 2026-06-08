"""Per-repo reconciliation pipeline.

Composes gh + cmux + git + cache + starship + teardown wrappers into the
per-cycle sequence driven by `cockpit/cockpit.py`. The CLI entry points
(`--watch`) live in `cockpit.py`; everything between "read
config" and "next cycle" lives here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO

import cockpit.lib.daemon_signal as daemon_signal
from cockpit.lib import version
from cockpit.lib.cache import (
    clear_branch_pr_cache,
    find_pr_payload,
    load_pr_payloads_by_branch,
    muted_payload,
    prune_superseded_pr_caches,
    write_base_ahead,
    write_base_distance,
    write_branch_pr_cache,
    write_git_state_cache,
    write_pr_cache,
)
from cockpit.lib.cmux import (
    ORANGE,
    ORPHAN_ICON,
    ORPHAN_KEY,
    CmuxUnavailable,
    apply_devdone_pill,
    apply_pills,
    apply_stale_pill,
    apply_wip_pill,
    clear_pr_pills,
    close_gone_cwd_workspaces,
    cmux,
    cmux_close_workspace_best_effort,
    find_cockpit_workspaces,
    nudge_if_idle,
    rename_workspace_if_needed,
    set_workspace_color,
    spawn_orphan_workspace,
    spawn_pr_workspace,
    spawn_workspace,
    status_pills,
    workspace_is_idle,
    workspace_names,
    workspace_state,
)
from cockpit.lib.colors import (
    CMUX_COLOR_ANSI,
    Colorizer,
    blue,
    bold,
    cyan,
    dim,
    green,
    yellow,
)
from cockpit.lib.config import (
    COCKPIT_HOME,
    ensure_state_dirs,
    linear_dev_done_state,
)
from cockpit.lib.gh import (
    PR,
    OpenPRHead,
    fetch_merged_branches,
    list_open_pr_heads,
    list_relevant_prs,
    repo_nwo,
)
from cockpit.lib.git import (
    Worktree,
    ahead_of_base,
    behind_of_base,
    branch_commits_ahead,
    delete_local_branch,
    ff_default_branch_worktrees,
    has_remote_branch,
    has_unique_commits,
    is_ancestor,
    list_local_branches,
    log_ff_advances,
    origin_head_branch,
    prune_worktrees,
    worktrees,
    worktrees_basic,
)
from cockpit.lib.issue_color import issue_color
from cockpit.lib.linear import fetch_ticket_state, parse_linear_footers
from cockpit.lib.log_format import verb
from cockpit.lib.nudges import NudgePref
from cockpit.lib.nudges import load_pref as _load_nudge_pref
from cockpit.lib.pills import ci_glyph
from cockpit.lib.prompts import claude_command, shell_quote
from cockpit.lib.tool import is_cmux
from cockpit.orchestrators.teardown import TeardownRequest, teardown

MAIN_BRANCHES = {"master", "main"}

ACTIONABLE_ISSUES = {"ci", "comments", "conflicts"}

# Cutoff for the *deep* merged-branches fetch that feeds the branch-ref reaper.
# Effectively unbounded (≈100 years) so a branch whose worktree was removed long
# ago is still recognized as merged — the reaper has no 14-day autoclose window
# to lean on. The `fetch_merged_branches` page cap (1 000 PRs) still bounds the
# query; the oldest merges beyond that simply reap on a later tick.
_DEEP_MERGED_CUTOFF_DAYS = 36500

_NUDGE_DESC = {
    "comments": lambda pr: (
        f"{pr.unaddressed} unresolved review thread(s) — reply or push fixes"
    ),
    "ci": lambda pr: (
        f"CI is failing ({pr.ci}) — run `gh pr checks {pr.number}` and address it"
    ),
    "conflicts": lambda _pr: "merge conflicts vs base — rebase and force-push",
}


def _cache_only(cfg: dict) -> bool:
    """Skip pill / cmux-only verbs this cycle? True whenever the resolved
    workspace backend isn't cmux (limux can't do pills; 'none' = headless).
    """
    return not is_cmux()


def maybe_nudge(
    ref: str,
    message: str,
    dry: bool,
    tag: str,
    *,
    pr_number: int | None = None,
    category: str | None = None,
) -> bool:
    """Nudge `ref` if idle; return True iff the nudge actually fired."""
    if nudge_if_idle(
        ref,
        message,
        dry=dry,
        tag=tag,
        pr_number=pr_number,
        category=category,
    ):
        snippet = message if len(message) <= 60 else message[:57] + "..."
        print(
            f"  {verb('nudged', color=yellow)} {tag} → {ref}  {dim(snippet)}",
            flush=True,
        )
        return True
    return False


def _linear_state_ttl_seconds(cfg: dict) -> float:
    """Backstop staleness for the cached Linear delivery block. A ticket can move
    into the dev-done state without its PR's `Linear:` footer changing, so the
    cache is refetched when the footer id-set changes OR when it ages past this.
    Defaults to three slow cycles; override with `linear_state_ttl_seconds`.
    """
    explicit = cfg.get("linear_state_ttl_seconds")
    if explicit is not None:
        return float(explicit)
    return 3 * float(cfg.get("slow_poll_interval_seconds", 300))


def _resolve_linear_block(ctx: RepoCycle, pr: PR) -> dict | None:
    """Resolve (and cache) the Linear-delivery block for `pr` — the tickets it
    delivers (via the `Linear:` PR-body footer) and their workflow states.

    Returns `{"tickets": [{"id", "state"}], "fetched_at": ts}`, or None when the
    repo isn't Linear-configured (so `write_pr_cache` leaves the field untouched).

    Refetches ticket states from Linear only when the footer's id-set differs
    from the prior snapshot OR the prior snapshot has aged past the TTL backstop;
    otherwise it carries the prior states forward. So a re-link refreshes
    immediately and an independent state transition is caught within the TTL,
    without a per-tick Linear call for every PR. Caller gates `ctx.dry`.
    """
    if not ctx.repo_entry.get("linear_keys"):
        return None
    ids = parse_linear_footers(pr.body)
    prior = find_pr_payload(pr.branch, ctx.name)
    prior_block: dict | None = (prior or {}).get("linear") if prior else None
    now = time.time()
    if prior_block:
        prior_ids = [t.get("id") for t in prior_block.get("tickets", [])]
        fresh = now - float(
            prior_block.get("fetched_at", 0)
        ) < _linear_state_ttl_seconds(ctx.cfg)
        if prior_ids == ids and fresh:
            return prior_block
    tickets = [{"id": tid, "state": fetch_ticket_state(tid)} for tid in ids]
    return {"tickets": tickets, "fetched_at": now}


def _track_dev_done(ctx: RepoCycle, ref: str, block: dict | None) -> None:
    """Toggle the `devdone=` pill from the resolved Linear-delivery `block`
    (`{"tickets": [{"id", "state"}], ...}` or None — stashed in
    `ctx.linear_blocks` by `_resolve_linear_block`; no network here).

    The pill is raised — green — only when the PR delivers at least one ticket
    AND *every* delivered ticket is in the `linear_dev_done_state` workflow state
    (default "Dev Done"); the whole PR's scope is dev-complete. Shows the id when
    a single ticket, the `done/total` count when several. Cleared otherwise, so a
    ticket slipping back out of dev-done drops the pill. No-op in dry runs.
    """
    if ctx.dry:
        return
    if not ctx.repo_entry.get("linear_keys"):
        return
    tickets = (block or {}).get("tickets") or []
    if not tickets:
        apply_devdone_pill(ref, None)
        return
    target = linear_dev_done_state(ctx.cfg).casefold()
    done = [t for t in tickets if (t.get("state") or "").casefold() == target]
    if len(done) != len(tickets):
        apply_devdone_pill(ref, None)
        return
    label = tickets[0]["id"] if len(tickets) == 1 else f"{len(done)}/{len(tickets)}"
    apply_devdone_pill(ref, label)


def match_worktrees(
    prs: list[PR], wts: list[Worktree], self_user: str
) -> tuple[list[tuple[PR, Worktree]], list[PR]]:
    pr_by_branch = {pr.branch: pr for pr in prs}
    wt_by_branch = {w.branch: w for w in wts}
    matched: list[tuple[PR, Worktree]] = []
    skipped_self: list[PR] = []
    for pr in prs:
        if pr.author != self_user:
            continue
        wt = wt_by_branch.get(pr.branch)
        if wt is None:
            skipped_self.append(pr)
        else:
            matched.append((pr, wt))
    for wt in wts:
        pr_opt = pr_by_branch.get(wt.branch)
        if pr_opt is None or pr_opt.author == self_user:
            continue
        matched.append((pr_opt, wt))
    return matched, skipped_self


def _resolve_wt(
    ref: str,
    ws_name: str,
    cwds: dict[str, Path],
    wt_by_path: dict[Path, Worktree],
    wt_by_name: dict[str, Worktree],
) -> Worktree | None:
    """Resolve a workspace ref to its Worktree via cwd → path lookup, then name."""
    cwd = cwds.get(ref)
    if cwd is not None and (wt := wt_by_path.get(cwd.resolve())) is not None:
        return wt
    return wt_by_name.get(ws_name)


def _orphan_snapshot(
    wt: Worktree, behind_base: int
) -> tuple[frozenset[tuple[str, str]], str]:
    """Pill-state snapshot + display tag for an orphan worktree."""
    stale_tag = f" stale ↻{behind_base}" if behind_base > 0 else ""
    tag = f"orphan{' wip' if wt.dirty else ''}{stale_tag}"
    snap = frozenset(
        [
            ("orphan", ORPHAN_ICON),
            ("wip", str(wt.dirty_count) if wt.dirty else ""),
            ("stale", str(behind_base) if behind_base > 0 else ""),
        ]
    )
    return snap, tag


def _is_post_merge_stale(wt: Worktree, merged_branches: dict[str, str]) -> bool:
    """True if `wt`'s branch matches a merged PR whose head is still contained
    in HEAD — i.e. the merged work lives here and nothing has diverged onto a
    fresh lineage.

    Gated by reachability (`is_ancestor`), not commit count, so it stays True
    when the worktree pulled main on top of a squash-merge (the merge head
    remains an ancestor) yet flips False when the branch name was reused for new
    work after the old PR merged (the new HEAD no longer descends from the merge
    head). See `is_ancestor` for the full case table.
    """
    merged_head = merged_branches.get(wt.branch)
    if merged_head is None:
        return False
    return is_ancestor(wt.path, merged_head)


def _is_reused_branch_merge(wt: Worktree | None, pr: PR) -> bool:
    """True when `pr` is merged/closed but the worktree's HEAD has advanced past
    the PR's recorded head — the branch was reused for new local work, so the
    cached merged snapshot no longer describes this worktree.

    The display inverse of `_is_post_merge_stale`: that gate (keyed on the
    `merged_branches` map) decides autoclose; this one is keyed on the PR's own
    `head_oid`, so it also covers CLOSED-not-merged PRs, which never enter
    `merged_branches`. When the head SHA is unknown locally `is_ancestor`
    returns False — the same cold-repo false-negative autoclose accepts, here
    blanking a card that should briefly show the merged PR until the next fetch.

    Returns False when the head is unknown (`pr.head_oid` absent, e.g. an old
    cached PR pre-dating the field) so a real PR is never hidden.
    """
    if wt is None or not pr.head_oid:
        return False
    if str(pr.state).upper() not in ("MERGED", "CLOSED"):
        return False
    return not is_ancestor(wt.path, pr.head_oid)


def _is_orphan_main_sibling(wt: Worktree) -> bool:
    """True if `wt` is a non-trunk worktree fast-forwarded onto main with no
    local work left.

    After a feature PR squash-merges and the user pulls main, the original
    branch name is gone — `merged_branches` can't identify the worktree. The
    safe signal is "clean working tree AND no commits unique to HEAD vs
    `origin/<default>`". Caller must have already established
    `not wt.is_primary` and `wt.branch in MAIN_BRANCHES`.
    """
    if wt.dirty_count > 0:
        return False
    # unpushed == -1 means git failed; treat as "unknown, don't sweep".
    return wt.unpushed == 0


def _teardown_worktree(
    wt: Worktree,
    cwds: dict[str, Path],
    repo_path: Path,
    repo_name: str,
    *,
    dry: bool,
    delete_branch: bool = False,
) -> None:
    # Path match is the primary route; the fallback closes by workspace *name*,
    # which is `wt.label` (the branch-derived name), not the dir basename.
    ref = _workspace_ref_for_path(wt.path, cwds) or wt.label
    teardown(
        TeardownRequest(
            ref=ref,
            name=wt.short,
            worktree_path=wt.path,
            branch=wt.branch,
            repo_path=repo_path,
            repo_name=repo_name,
            forced=True,
            delete_branch=delete_branch,
        ),
        dry=dry,
    )


def _workspace_ref_for_path(wt_path: Path, cwds: dict[str, Path]) -> str | None:
    """Find the cmux workspace ref whose cwd matches `wt_path`.

    The workspace name can diverge from the worktree dir name (e.g. a
    ticket-named workspace rooted in a feature worktree), so name-based
    closing misses or hits the wrong workspace. Resolve by path instead.
    """
    target = wt_path.resolve()
    for ref, cwd in cwds.items():
        if cwd.resolve() == target:
            return ref
    return None


def _maybe_autoclose(
    repo_path: Path,
    repo_name: str,
    wts: list[Worktree],
    merged_branches: dict[str, str],
    cwds: dict[str, Path],
    *,
    prs: list[PR] | None = None,
    dry: bool,
) -> None:
    """Remove worktrees + workspaces for merged branches that are clean.

    Removes any merged branch (mine or coworker's) when the worktree is clean.
    Coworker worktrees are safe to clean since they can be re-created from the
    merged PR if needed.

    Authoritative merge signal: `gh pr list --state merged` (via
    `merged_branches`), which maps each branch to the `headRefOid` it pointed at
    when merged. A branch being *present* in that map is not enough — a branch
    name reused for new work after its old PR merged (delete-and-recreate, or a
    reset onto a different lineage) is still listed, and tearing it down nukes a
    worktree the user just created. Gate on `_is_post_merge_stale` instead: tear
    down only worktrees whose HEAD still descends from the recorded merge head;
    a reused branch on a fresh lineage does not, so it survives.

    That reachability gate cannot prove "branch work is in main" for squash- or
    rebase-merges (the resulting SHAs differ) — but it does not need to. It asks
    the answerable question, "is the merge head still in this worktree's
    history", which stays True after a squash-merge + `git pull` main (the merge
    head remains an ancestor) and only goes False when the branch diverged onto
    a fresh lineage. So the squash-then-pull-main worktree still cleans up.

    Smart-skip on PR signals the author likely still wants to revisit before
    cleanup: draft, CI not passing, or unaddressed review threads.

    Teardown delegates to `orchestrators.teardown.teardown` (forced=True since we've
    already validated merge-state-clean above).

    A merged PR is the *only* trigger that reaches teardown here — a worktree
    with no merged PR (research/planning, an open PR, a coworker branch with no
    PR) is never touched. Stale-but-merged worktrees are the sole auto-reap
    case; everything else lives until the user closes it (TUI `c`).
    """
    pr_by_branch = {pr.branch: pr for pr in (prs or [])}
    for wt in wts:
        if wt.is_primary:
            continue
        if wt.branch in MAIN_BRANCHES:
            if _is_orphan_main_sibling(wt):
                _teardown_worktree(wt, cwds, repo_path, repo_name, dry=dry)
            elif wt.dirty_count > 0 and not dry:
                # Held back by uncommitted work — new changes started on `main`
                # inside a merged worktree. No other refresh path covers main-
                # branch siblings (both pill loops skip MAIN_BRANCHES), so surface
                # a WIP pill here to explain why the workspace is being kept.
                ref = _workspace_ref_for_path(wt.path, cwds)
                if ref is not None:
                    apply_wip_pill(ref, wt.dirty_count)
            continue
        if not _is_post_merge_stale(wt, merged_branches):
            continue
        # Merged: the Linear dev-done pill raised while the PR was open is now
        # moot. The PR has left the tracked open-PR set, so `_track_dev_done`
        # won't run again to clear it on a kept workspace (live, the ticket has
        # usually moved to "Done" anyway, but don't depend on that) — clear it
        # here so a skipped teardown doesn't strand the pill.
        merged_ref = _workspace_ref_for_path(wt.path, cwds)
        if merged_ref is not None and not dry:
            apply_devdone_pill(merged_ref, None)
        if wt.dirty_count > 0:
            print(
                f"  {verb('autoclose')} {dim(f'skipped (uncommitted) {wt.short}')} "
                f"{dim(f'({wt.dirty_count} dirty)')}",
                flush=True,
            )
            continue
        pr = pr_by_branch.get(wt.branch)
        if pr is not None:
            reasons: list[str] = []
            if pr.is_draft:
                reasons.append("draft")
            if pr.ci not in ("passed", "none", "unknown", ""):
                reasons.append(f"ci={pr.ci}")
            if pr.unaddressed > 0:
                reasons.append(f"{pr.unaddressed} unaddressed")
            if reasons:
                joined = ", ".join(reasons)
                print(
                    f"  {verb('autoclose')} {dim(f'skipped ({joined}) {wt.short}')}",
                    flush=True,
                )
                continue
        # Delete the local branch ref too, but only when HEAD sits at the merged
        # head with nothing on top. `_is_post_merge_stale` permits teardown when
        # the merge head is *any* ancestor of HEAD — that includes a branch the
        # user committed new (clean, unpushed) work onto after the merge. The
        # worktree removal alone leaves that work recoverable via the branch ref;
        # `git branch -D` would not, so keep the ref in that case.
        merged_head = merged_branches.get(wt.branch)
        delete_branch = merged_head is not None and not has_unique_commits(
            wt.path, merged_head
        )
        _teardown_worktree(
            wt, cwds, repo_path, repo_name, dry=dry, delete_branch=delete_branch
        )


def _branch_reap_reason(ctx: RepoCycle, branch: str, default: str | None) -> str | None:
    """Why `branch` is safe to delete, or None to keep it.

    Two safe cases, checked merged-first so a squash-merged branch whose remote
    was already deleted is still recognized (it would fail the "contained in
    default" test the no-remote path uses):

      - merged PR (all-time, via `merged_branches_deep`) with no commits on top
        of the recorded merge head. Mirrors the `has_unique_commits(wt.path,
        merged_head)` guard `_maybe_autoclose` applies before `git branch -D`: a
        branch reset/recreated onto a fresh lineage (commits not reachable from
        the merge head) reads > 0 and is kept.
      - no remote tracking ref AND no commits unique vs `origin/<default>` — the
        branch was never pushed and is fully contained in the default branch, so
        nothing is lost. A never-pushed branch WITH unique commits is work the
        user may not have backed up anywhere; it is kept (the "block" decision).

    Returns None on any git failure: `branch_commits_ahead` yields -1 (≠ 0) for
    an unknown merge-head SHA or bad ref, so an unverifiable branch is kept.
    """
    merged_head = ctx.merged_branches_deep.get(branch)
    if merged_head is not None:
        if branch_commits_ahead(ctx.repo_path, merged_head, branch) == 0:
            return "merged PR"
        return None
    if (
        not has_remote_branch(ctx.repo_path, branch)
        and default is not None
        and branch_commits_ahead(ctx.repo_path, f"origin/{default}", branch) == 0
    ):
        return "no remote, contained in default"
    return None


def _reap_branch_refs(ctx: RepoCycle) -> None:
    """Delete stale local branch refs that have no worktree and are provably safe.

    Closes the gap `_maybe_autoclose` leaves: it only iterates *existing*
    worktrees, so a branch whose worktree was already removed (manual `rm`, a
    prior teardown, an OS tmpdir wipe) keeps its dangling local ref forever. This
    pass enumerates every local branch (mine or coworker's — branch identity, not
    prefix, decides safety) and deletes the ones `_branch_reap_reason` clears.

    Always kept: `MAIN_BRANCHES` / the repo's default branch, any branch with a
    live worktree, any branch with an open PR, and anything whose safety can't be
    verified. Worktree branches are read from `ctx.wts`, the start-of-cycle
    snapshot — a branch whose worktree `_maybe_autoclose` just removed still
    appears here, so it is conservatively skipped this tick and reaped on the
    next (when the snapshot no longer lists it). No double-delete, no error.

    Runs every slow tick — worktree teardown is unconditional cockpit behavior,
    the same as `_maybe_autoclose`.
    """
    default = origin_head_branch(ctx.repo_path)
    wt_branches = {wt.branch for wt in ctx.wts}
    open_pr_branches = {pr.branch for pr in ctx.prs if pr.state == "OPEN"}
    for branch in list_local_branches(ctx.repo_path):
        if branch in MAIN_BRANCHES or branch == default:
            continue
        if branch in wt_branches or branch in open_pr_branches:
            continue
        reason = _branch_reap_reason(ctx, branch, default)
        if reason is None:
            continue
        action = "[dry] reap-branch" if ctx.dry else "reap-branch"
        print(f"  {verb(action)} {bold(branch)}  {dim(reason)}", flush=True)
        if ctx.dry:
            continue
        ok, err = delete_local_branch(ctx.repo_path, branch)
        if not ok:
            print(
                f"  warn: git branch -D {branch} failed: {err}",
                file=sys.stderr,
                flush=True,
            )


def _refresh_base_distance(
    repo_path: Path, wts: list[Worktree], default: str | None
) -> dict[str, int]:
    """Fetch `origin/<default>` once per repo, then compute and cache both
    rebase-staleness (`HEAD..origin/<default>`) and ahead-of-base
    (`origin/<default>..HEAD`) for each feature worktree.

    Returns a `{branch: behind_count}` map for the caller to consume (e.g.
    orphan pill staleness). On any failure (no origin/HEAD, fetch error)
    all feature worktrees get an empty cache so stale readings don't
    survive.

    `git fetch` is run with `--quiet` from the main repo path; refs are
    shared across worktrees, so fetching once per repo is sufficient.
    """
    feature = [w for w in wts if not w.is_primary]
    distances: dict[str, int] = {}
    if not feature:
        return distances

    def _invalidate() -> dict[str, int]:
        for wt in feature:
            write_base_distance(wt.branch, -1)
            write_base_ahead(wt.branch, -1)
        return distances

    if not default:
        return _invalidate()
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_path), "fetch", "--quiet", "origin", default],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(
            f"  {yellow('skip')} base-distance refresh for {repo_path.name}: {e}",
            file=sys.stderr,
            flush=True,
        )
        return _invalidate()
    if res.returncode != 0:
        print(
            f"  {yellow('skip')} base-distance refresh for {repo_path.name}: "
            f"fetch origin {default} exited {res.returncode}: "
            f"{res.stderr.strip()}",
            file=sys.stderr,
            flush=True,
        )
        return _invalidate()
    for wt in feature:
        n = behind_of_base(wt.path, default)
        distances[wt.branch] = n
        write_base_distance(wt.branch, n)
        write_base_ahead(wt.branch, ahead_of_base(wt.path, default))
    return distances


@dataclass
class RepoCycle:
    """Per-repo, per-cycle context bundle. Mutable dicts (pill_state /
    pr_cache) are passed by reference and persist across cycles.
    """

    cfg: dict
    repo_path: Path
    owner: str
    name: str
    self_user: str
    wts: list[Worktree]
    prs: list[PR]
    tracked: dict[str, tuple[PR, Worktree]]
    names: dict[str, str]
    cwds: dict[str, Path]
    merged_branches: dict[str, str]
    merged_branches_deep: dict[str, str]
    pill_state: dict
    dry: bool
    headless: bool
    default_branch: str | None = None
    prefs: dict[int, NudgePref] = field(default_factory=dict)
    base_distance: dict[str, int] = field(default_factory=dict)
    pr_payloads: dict[str, dict] = field(default_factory=dict)
    review_candidates: list[OpenPRHead] = field(default_factory=list)
    # The repo's config.json entry — carries `linear_keys` for the devdone gate.
    # Defaulted so existing RepoCycle(...) call sites and test stubs need no change.
    repo_entry: dict = field(default_factory=dict)
    # branch → resolved Linear-delivery block (or None) for this cycle, stashed by
    # _write_pr_caches. Read by _track_dev_done — NOT via ctx.pr_payloads, whose
    # pre-write snapshot wouldn't carry the freshly-resolved block (it'd lag a
    # cycle and miss the pill the cycle a footer first appears).
    linear_blocks: dict[str, dict | None] = field(default_factory=dict)


def _repo_name_color(repo_entry: dict) -> Colorizer:
    """Colorizer for this repo's name in the cycle log — its `sidebar_color`
    (echoing the cmux sidebar tint) when set, else plain `bold`. The value is
    preflight-validated, so an unset/missing key is the only fallback case.
    """
    return CMUX_COLOR_ANSI.get(repo_entry.get("sidebar_color") or "", bold)


def _prepare_cycle(
    repo_entry: dict,
    self_user: str,
    *,
    cfg: dict,
    pr_cache: dict,
    pill_state: dict,
    dry: bool,
) -> RepoCycle | None:
    """Validate the repo, fetch wts/state/merged in parallel, fetch relevant PRs,
    print the cycle header. Returns None if the repo should be skipped this cycle.
    """
    repo_path = Path(os.path.expanduser(repo_entry["path"]))
    if not repo_path.is_dir():
        print(
            f"  {yellow('skip')} {repo_entry.get('name', repo_path.name)}: "
            f"path does not exist ({repo_path})",
            flush=True,
        )
        return None
    try:
        owner, name = repo_nwo(repo_path)
    except RuntimeError as e:
        print(f"  {yellow('skip')} {repo_path}: {e}", flush=True)
        return None

    # Drop admin entries for worktree dirs deleted out-of-band before we read
    # the list, so teardown/autoclose never act on a path that no longer exists.
    prune_worktrees(repo_path)

    headless = _cache_only(cfg)
    with ThreadPoolExecutor(max_workers=4) as ex:
        wts_fut = ex.submit(worktrees, repo_path, repo_entry.get("branch_prefix", ""))
        state_fut = None if headless else ex.submit(workspace_state)
        merged_fut = ex.submit(
            fetch_merged_branches,
            owner,
            name,
            cutoff_days=int(cfg.get("autoclose_age_days", 14)),
        )
        # Unbounded merged map for the branch-ref reaper (`_reap_branch_refs`),
        # which sees branches whose worktrees were removed long before the
        # 14-day autoclose window. Fetched in parallel so it adds no latency.
        merged_deep_fut = ex.submit(
            fetch_merged_branches, owner, name, cutoff_days=_DEEP_MERGED_CUTOFF_DAYS
        )
        wts = wts_fut.result()
        try:
            names, cwds = ({}, {}) if state_fut is None else state_fut.result()
        except CmuxUnavailable as e:
            print(
                f"  {yellow('skip')} {owner}/{name}: cmux unavailable: {e}",
                flush=True,
            )
            return None
        merged_branches = merged_fut.result()
        merged_branches_deep = merged_deep_fut.result()

    # Pass every local feature branch (mine + coworker). The per-branch leg
    # in list_relevant_prs fetches any-state PRs so the cache refreshes after
    # OPEN→MERGED / OPEN→CLOSED — `is:open author:self` alone misses those.
    branches = sorted({w.branch for w in wts if w.branch not in MAIN_BRANCHES})
    try:
        prs = list_relevant_prs(owner, name, self_user, branches, cache=pr_cache)
    except RuntimeError as e:
        print(
            f"  {yellow('skip')} {owner}/{name}: list_relevant_prs failed: {e}",
            flush=True,
        )
        return None

    # When `review_prs` is set, also pull every other-authored open PR so the
    # spawn phase can create review worktrees for ones we don't track yet. The
    # daemon's normal query is `author:self` + per-worktree aliases, so without
    # this the daemon never sees a coworker's PR until a local worktree exists.
    review_candidates: list[OpenPRHead] = []
    if repo_entry.get("review_prs") and not headless:
        try:
            review_candidates = list_open_pr_heads(owner, name)
        except RuntimeError as e:
            print(
                f"  {yellow('warn')} {owner}/{name}: review_prs open-PR fetch "
                f"failed: {e}",
                file=sys.stderr,
                flush=True,
            )

    tracked = find_cockpit_workspaces(prs, wts, names=names, cwds=cwds)
    # Resolve nudge prefs once per cycle — the single point of mute-state I/O.
    # Everything downstream (write_pr_cache, write_branch_pr_cache, apply_pills,
    # status_pills) reads from this dict. See AGENTS.md "PR cache writers".
    prefs = {pr.number: _load_nudge_pref(pr.number) for pr in prs}
    mine = sum(1 for pr in prs if pr.author == self_user)
    coworker_relevant = len(prs) - mine
    feature_wts = [w for w in wts if w.branch not in MAIN_BRANCHES]
    wip_count = sum(1 for w in feature_wts if w.dirty)
    ts = datetime.now().isoformat(timespec="seconds")
    print(
        f"{green(f'[{ts}]')} {_repo_name_color(repo_entry)(f'{owner}/{name}')}  "
        f"mine: {mine}  coworker-with-wt: {coworker_relevant}  "
        f"worktrees: {len(feature_wts)}  tracked: {len(tracked)}  wip: {wip_count}",
        flush=True,
    )
    return RepoCycle(
        cfg=cfg,
        repo_entry=repo_entry,
        repo_path=repo_path,
        owner=owner,
        name=name,
        self_user=self_user,
        wts=wts,
        prs=prs,
        tracked=tracked,
        names=names,
        cwds=cwds,
        merged_branches=merged_branches,
        merged_branches_deep=merged_branches_deep,
        pill_state=pill_state,
        dry=dry,
        headless=headless,
        default_branch=origin_head_branch(repo_path),
        prefs=prefs,
        review_candidates=review_candidates,
    )


def _write_pr_caches(ctx: RepoCycle) -> None:
    """Refresh base-distance cache + PR caches for the cship statusline.

    Mirroring PR fields into the cship cache lets `starship.toml [custom.*]`
    modules render fresh on the first session render without each field
    having to spawn its own `gh pr view` from cold.
    """
    # Build the branch→payload map once for the cycle's downstream consumers
    # (_refresh_tracked_pills reads `reusedBranch` from it), replacing a per-PR
    # find_pr_payload scan. The loader applies the same rank dedup, so this
    # pre-write snapshot matches what a post-write per-call lookup would return.
    ctx.pr_payloads = load_pr_payloads_by_branch(ctx.name)
    if ctx.dry:
        return
    ctx.base_distance = _refresh_base_distance(
        ctx.repo_path, ctx.wts, ctx.default_branch
    )
    for wt in ctx.wts:
        write_git_state_cache(wt.path)
    wt_by_branch = {wt.branch: wt for wt in ctx.wts}
    open_branches = {p.branch for p in ctx.prs if p.state == "OPEN"}
    for pr in ctx.prs:
        pref = ctx.prefs.get(pr.number)
        wt_opt = wt_by_branch.get(pr.branch)
        # Resolve the Linear-delivery block BEFORE write_pr_cache (it reads the
        # prior snapshot to decide refetch-vs-carry-forward, so it must run
        # against the old file). None for non-Linear repos → field untouched.
        linear = _resolve_linear_block(ctx, pr)
        ctx.linear_blocks[pr.branch] = linear
        reused = _is_reused_branch_merge(wt_opt, pr)
        write_pr_cache(ctx.name, pr, wt_opt, pref, linear=linear, reused_branch=reused)
        if reused:
            # Branch reused for new local work after this PR merged/closed —
            # show no PR. Only clear the branch-keyed cells when no live open PR
            # shares the branch; otherwise that PR's own iteration writes them
            # (and `_pr_payload_rank` resolves the card to the open PR).
            if pr.branch not in open_branches:
                clear_branch_pr_cache(pr.branch)
            continue
        write_branch_pr_cache(
            pr.branch,
            state=pr.state,
            is_draft=pr.is_draft,
            review_decision=pr.review_decision,
            number=pr.number,
            title=pr.title,
            ci_glyph=ci_glyph(pr.ci),
            muted=muted_payload(pref),
            comments=pr.unaddressed,
        )
    # After the live snapshots are on disk, drop any superseded snapshot
    # sharing a branch (reused branch: old merged PR alongside the live one)
    # so branch-keyed flat cells resolve deterministically.
    prune_superseded_pr_caches(ctx.name)
    # Reload after the writes so downstream consumers see this tick's freshly
    # computed `reusedBranch` flags and the post-prune winners (the pre-write
    # snapshot at the top still serves the dry-run early return above).
    ctx.pr_payloads = load_pr_payloads_by_branch(ctx.name)


def _ref_pid(ref: str) -> int:
    """PID embedded in a cmux `workspace:<pid>` ref (the sort key for dedup)."""
    return int(ref.split(":")[1])


def _dedupe_workspaces(ctx: RepoCycle) -> set[str]:
    """Close duplicate cmux workspaces (same name, or same feature-worktree
    path), keeping the lowest-PID per group. Returns the surviving refs.
    """

    def _close_extras(refs_sorted: list[str], reason: str) -> None:
        keep_name = ctx.names.get(refs_sorted[0], refs_sorted[0])
        for extra in refs_sorted[1:]:
            extra_name = ctx.names.get(extra, extra)
            print(
                f"  {verb('duplicate')} {extra_name} → {extra}  "
                f"({reason.format(keep=keep_name, first=refs_sorted[0])})",
                flush=True,
            )
            if not ctx.dry:
                cmux_close_workspace_best_effort(extra)

    by_name: dict[str, list[str]] = {}
    for ref, ws_name in ctx.names.items():
        by_name.setdefault(ws_name, []).append(ref)
    keep_refs: set[str] = set()
    for refs in by_name.values():
        refs_sorted = sorted(refs, key=_ref_pid)
        keep_refs.add(refs_sorted[0])
        _close_extras(refs_sorted, "keeping {first}")

    feature_wt_paths = {wt.path.resolve() for wt in ctx.wts if not wt.is_primary}
    by_wt_path: dict[Path, list[str]] = {}
    for ref in keep_refs:
        cwd = ctx.cwds.get(ref)
        if cwd is None:
            continue
        resolved = cwd.resolve()
        if resolved in feature_wt_paths:
            by_wt_path.setdefault(resolved, []).append(ref)
    for refs in by_wt_path.values():
        if len(refs) <= 1:
            continue
        refs_sorted = sorted(refs, key=_ref_pid)
        for extra in refs_sorted[1:]:
            keep_refs.discard(extra)
        _close_extras(refs_sorted, "same worktree as {keep}")
    return keep_refs


def _refresh_tracked_pills(
    ctx: RepoCycle, keep_refs: set[str]
) -> tuple[bool, list, list]:
    """Refresh PR-pill state for tracked workspaces, nudge actionable issues.

    Returns (printed_refresh, mine_items, others_items). Items are reused by
    the post-loop summary printer.
    """
    tracked_kept = [
        (ref, pr, wt) for ref, (pr, wt) in ctx.tracked.items() if ref in keep_refs
    ]
    mine_items = sorted(
        (t for t in tracked_kept if t[1].author == ctx.self_user),
        key=lambda t: -t[1].number,
    )
    others_items = sorted(
        (t for t in tracked_kept if t[1].author != ctx.self_user),
        key=lambda t: -t[1].number,
    )

    printed_refresh = False
    for group_label, group in (("mine", mine_items), ("coworkers", others_items)):
        group_header_printed = False
        for ref, pr, wt in group:
            # `label` is the workspace's *current* cmux name; `wt.label` is the
            # branch-derived name we re-assert it to.
            label = ctx.names.get(ref, ref)
            if rename_workspace_if_needed(ref, wt.label, label, dry=ctx.dry):
                if not group_header_printed:
                    print(f"  {dim(group_label)}", flush=True)
                    group_header_printed = True
                print(
                    f"    {verb('renamed')} {cyan(label)} → {cyan(wt.label)}",
                    flush=True,
                )
                printed_refresh = True
                label = wt.label  # corrected name for this cycle's log lines
            pref = ctx.prefs.get(pr.number)
            pr_payload = ctx.pr_payloads.get(pr.branch)
            if pr_payload and pr_payload.get("reusedBranch"):
                # Branch reused for new local work after its PR merged/closed —
                # clear the stale merged pills so the card shows no PR, and skip
                # nudging (a merged PR is never actionable). The persisted flag
                # is the daemon's single reused-branch decision (see
                # `_write_pr_caches` / `write_pr_cache`).
                blank: frozenset = frozenset()
                changed = ctx.pill_state.get(ref) != blank
                if changed and not ctx.dry:
                    clear_pr_pills(ref)
                if changed:
                    if not group_header_printed:
                        print(f"  {dim(group_label)}", flush=True)
                        group_header_printed = True
                    print(
                        f"    {verb('suppressed')} {blue(f'#{pr.number}')} → "
                        f"{cyan(label)}  {dim('(branch reused — merged PR hidden)')}",
                        flush=True,
                    )
                    printed_refresh = True
                if changed and not ctx.dry:
                    ctx.pill_state[ref] = blank
                continue
            desired = frozenset(status_pills(pr, wt, ctx.self_user, pref))
            changed = ctx.pill_state.get(ref) != desired
            if changed and not ctx.dry:
                apply_pills(ref, pr, wt, ctx.self_user, pref)
            if changed:
                if not group_header_printed:
                    print(f"  {dim(group_label)}", flush=True)
                    group_header_printed = True
                op = " rebasing" if wt.rebasing else (" merging" if wt.merging else "")
                tag = pr.display_issue + op
                print(
                    f"    {verb('refreshed')} {blue(f'#{pr.number}')} → {cyan(label)}  "
                    f"[{issue_color(pr.display_issue)(tag)}]",
                    flush=True,
                )
                printed_refresh = True
            if changed and not ctx.dry:
                ctx.pill_state[ref] = desired
            # A merged/closed PR is never actionable: its CI, comments, and
            # conflicts can no longer be resolved, so nudging an idle session to
            # "fix CI" on it loops forever (the nudge never stops because the
            # state never changes). A merged PR can still be tracked here when
            # _maybe_autoclose kept its worktree (e.g. merged with red CI) — the
            # per-branch query refreshes any-state PRs into the cache. Gate on
            # OPEN so the footer pill still shows the merged-with-red-CI state
            # for inspection, but no nudge fires.
            actionable = pr.display_issue in ACTIONABLE_ISSUES and pr.state == "OPEN"
            if actionable:
                maybe_nudge(
                    ref,
                    f"PR #{pr.number}: {_NUDGE_DESC[pr.display_issue](pr)}.",
                    ctx.dry,
                    label,
                    pr_number=pr.number,
                    category=pr.display_issue,
                )
            _track_dev_done(ctx, ref, ctx.linear_blocks.get(pr.branch))
    return printed_refresh, mine_items, others_items


def _print_tracked_summary(
    ctx: RepoCycle, mine_items: list, others_items: list
) -> None:
    for group_label, group in (("mine", mine_items), ("coworkers", others_items)):
        labels = sorted(ctx.names.get(ref, ref) for ref, _, _ in group)
        if labels:
            print(
                f"  {verb('tracked')} {dim(group_label)}: "
                f"{', '.join(cyan(lbl) for lbl in labels)}",
                flush=True,
            )


def _handle_orphans_and_close_stale(ctx: RepoCycle, keep_refs: set[str]) -> None:
    """Apply orphan pills to every surviving workspace whose worktree branch has
    no open PR. Worktrees are never closed here — a merged PR is the only reaper
    (`_maybe_autoclose`), so a research/planning worktree survives until the user
    closes it (TUI `c`). Mine-prefixed branches also get the "open a PR or close"
    nudge; coworker branches (someone else's PR I'm reviewing locally) get the
    pills only — nudging a coworker branch to open a PR makes no sense.
    """
    wt_by_name = {wt.label: wt for wt in ctx.wts}
    wt_by_path = {wt.path.resolve(): wt for wt in ctx.wts}
    pr_branches = {pr.branch for pr in ctx.prs}
    my_prefix = f"{ctx.self_user}/"
    for ref in keep_refs:
        ws_name = ctx.names.get(ref, "")
        wt_opt = _resolve_wt(ref, ws_name, ctx.cwds, wt_by_path, wt_by_name)
        if (
            wt_opt is None
            or wt_opt.branch in pr_branches
            or wt_opt.branch in MAIN_BRANCHES
        ):
            continue
        wt = wt_opt
        _refresh_orphan(ctx, ref, wt, ws_name, nudge=wt.branch.startswith(my_prefix))


def _refresh_orphan(
    ctx: RepoCycle, ref: str, wt: Worktree, ws_name: str, *, nudge: bool = True
) -> None:
    """Apply orphan/wip/stale pills; nudge to push-or-close when `nudge` is set."""
    if _is_post_merge_stale(wt, ctx.merged_branches):
        print(
            f"  {verb('orphan')} {dim(f'{ws_name} ({wt.branch}) merged — autoclose may handle')}",
            flush=True,
        )
        return
    if rename_workspace_if_needed(ref, wt.label, ws_name, dry=ctx.dry):
        print(
            f"  {verb('renamed')} {cyan(ws_name)} → {cyan(wt.label)}",
            flush=True,
        )
        ws_name = wt.label
    behind_base = ctx.base_distance.get(wt.branch, 0)
    if not ctx.dry:
        cmux(
            "set-status",
            ORPHAN_KEY,
            ORPHAN_ICON,
            "--workspace",
            ref,
            "--color",
            ORANGE,
            check=False,
        )
        apply_wip_pill(ref, wt.dirty_count)
        apply_stale_pill(ref, behind_base)
    orphan_snap, tag = _orphan_snapshot(wt, behind_base)
    changed = ctx.pill_state.get(ref) != orphan_snap
    if changed:
        print(
            f"  {verb('refreshed')} {cyan(ws_name)} → {ref}  [{yellow(tag)}]",
            flush=True,
        )
    if changed and not ctx.dry:
        ctx.pill_state[ref] = orphan_snap
    if nudge:
        maybe_nudge(
            ref,
            f"Worktree {wt.short} on {wt.branch} still has no open PR. "
            f"Push commits and open a PR, or close the worktree if abandoned.",
            ctx.dry,
            ws_name,
        )


_SPAWN_LOG = COCKPIT_HOME / "spawn.log"
# Suppress a re-spawn of the same branch for two slow ticks (default 300s each)
# so a manual SIGUSR1 kick can't double-launch while a `git fetch` +
# worktree add is still in flight. Expires so a failed creation is retried.
_SPAWN_INFLIGHT_TTL_SECONDS = 600


def _bg_spawn_pr(
    ctx: RepoCycle, repo_name: str | None, number: int, branch: str, *, review: bool
) -> None:
    """Fire `cockpit new --pr <n> [--repo <name>] [--review]` detached so the
    slow tick never blocks on `git fetch` + worktree add.

    Invoked via module dispatch (`python -m cockpit.cli new …`), NOT `spawn.py`
    by path: a path invocation puts the package dir on `sys.path[0]`, where
    `cockpit.py` shadows the `cockpit` package and the child dies on
    `ModuleNotFoundError: 'cockpit' is not a package` before doing anything.

    The child reuses the exact path `/cockpit:new` walks (create_worktree +
    spawn_pr_workspace), then the new worktree surfaces as cells on a later
    cycle — inventory is derived, not stored (see AGENTS.md). `--repo` is passed
    when the config entry has a name; otherwise the child's cwd-based discovery
    resolves the repo from `ctx.repo_path`. An in-flight guard keyed by branch
    in `pill_state` keeps back-to-back ticks from double-spawning; stderr/stdout
    land in `spawn.log` so detached failures are not silent.
    """
    key = f"spawn:{ctx.owner}/{ctx.name}:{branch}"
    last = ctx.pill_state.get(key)
    now = time.monotonic()
    if isinstance(last, float) and (now - last) < _SPAWN_INFLIGHT_TTL_SECONDS:
        return
    label = "bg-review" if review else "bg-spawn"
    if ctx.dry:
        print(f"  [dry] {label} #{number} branch={branch}", flush=True)
        return
    cmd = [sys.executable, "-m", "cockpit.cli", "new", "--pr", str(number)]
    if repo_name:
        cmd += ["--repo", repo_name]
    if review:
        cmd.append("--review")
    logfile: IO[bytes] | None = None
    try:
        logfile = open(_SPAWN_LOG, "ab")  # noqa: SIM115 — handle is passed to a detached Popen and must outlive this scope
    except OSError:
        logfile = None
    sink: IO[bytes] | int = logfile if logfile is not None else subprocess.DEVNULL
    try:
        subprocess.Popen(
            cmd,
            cwd=str(ctx.repo_path),
            stdout=sink,
            stderr=sink,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(
            f"  {yellow('warn')} {label} #{number}: failed to launch spawn.py: {e}",
            file=sys.stderr,
            flush=True,
        )
        return
    finally:
        if logfile is not None:
            logfile.close()
    ctx.pill_state[key] = now
    print(
        f"  {verb(label)} {bold(branch)}  #{number}"
        + (f"  {dim('(review)')}" if review else ""),
        flush=True,
    )


def _spawn_missing_workspaces(ctx: RepoCycle, repo_entry: dict) -> None:
    """Spawn/create the workspaces and worktrees a cycle is missing:

    1. PR-matched worktrees that lack a cmux workspace → spawn one.
    2. My open PRs with no local worktree → create worktree + workspace in the
       background (replaces the old "create one with /cockpit:new" warning).
    3. `review_prs`: every other-authored open PR without a worktree → create a
       review worktree (`spawn.py --review`) in the background. Uncapped.
    4. My-prefix orphan worktrees not yet covered by any workspace → spawn one.
    """
    repo_name = repo_entry.get("name")
    matched, skipped_self = match_worktrees(ctx.prs, ctx.wts, ctx.self_user)
    for pr in skipped_self:
        _bg_spawn_pr(ctx, repo_name, pr.number, pr.branch, review=False)
    tracked_pr_numbers = {pr.number for pr, _ in ctx.tracked.values()}
    for pr, wt in matched:
        if pr.number not in tracked_pr_numbers:
            spawn_pr_workspace(
                pr,
                wt,
                self_user=ctx.self_user,
                pref=ctx.prefs.get(pr.number),
                dry=ctx.dry,
            )
    if ctx.review_candidates:
        existing_branches = {w.branch for w in ctx.wts}
        for cand in ctx.review_candidates:
            if cand.author == ctx.self_user:
                continue  # mine — handled by skipped_self above
            if cand.branch in existing_branches:
                continue  # already have a worktree — tracked via the matched path
            _bg_spawn_pr(ctx, repo_name, cand.number, cand.branch, review=True)
    pr_branches = {pr.branch for pr in ctx.prs}
    my_prefix = f"{ctx.self_user}/"
    covered_paths = {p.resolve() for p in ctx.cwds.values()}
    # Live workspace names → the existing cwd(s) using each. A same-named
    # workspace rooted at a DIFFERENT, still-existing path is a cross-repo clash
    # (two repos each with a `foo` branch): spawning here would create a
    # duplicate-named workspace that churns every cycle, since cmux allows
    # duplicate names and the path-keyed dedup above never covers this path.
    # Dead-cwd workspaces are excluded — `close_gone_cwd_workspaces` reaps them,
    # so they must not suppress a legitimate spawn.
    name_to_paths: dict[str, set[Path]] = {}
    for ref, ws_name in ctx.names.items():
        cwd = ctx.cwds.get(ref)
        if cwd is None:
            continue
        resolved = cwd.resolve()
        if resolved.exists():
            name_to_paths.setdefault(ws_name, set()).add(resolved)
    for wt in ctx.wts:
        if not wt.branch.startswith(my_prefix) or wt.branch in pr_branches:
            continue
        if wt.path.resolve() in covered_paths:
            continue
        if _is_post_merge_stale(wt, ctx.merged_branches):
            print(
                f"  {verb('skip')} {dim(f'orphan-spawn {wt.short} — branch {wt.branch} has merged PR')}",
                flush=True,
            )
            continue
        clash = name_to_paths.get(wt.label, set()) - {wt.path.resolve()}
        if clash:
            other = sorted(str(p) for p in clash)[0]
            print(
                f"  {verb('skip')} {dim(f'orphan-spawn {wt.label} — workspace name already used by {other}')}",
                flush=True,
            )
            continue
        spawn_orphan_workspace(wt, dry=ctx.dry)


def _resolve_skill_prompt(name: str, repo_path: Path) -> str | None:
    """Return the slash-command prompt for a skill, or None if not found.

    Lookup order mirrors `spawn.resolve_skill` (global always wins):
      1. ~/.claude/skills/<name>/skill.md
      2. <repo_path>/.claude/skills/<name>/skill.md

    `repo_path` is the managed repo running the skill — its `.claude/skills/`,
    NOT cockpit's own plugin tree (skills are configured per managed repo and
    run in that repo's worktree).
    """
    rel = Path(".claude") / "skills" / name / "skill.md"
    if (Path.home() / rel).exists():
        return f"/{name}"
    if (repo_path / rel).exists():
        return f"/{name}"
    return None


def _run_repo_skills(repo_entry: dict, *, dry: bool) -> None:
    """Run fast_skills (blocking, non-interactive) and slow_skills (workspace spawn)
    configured on the repo entry.

    fast_skills: `claude -p /<name>` in the repo's main worktree — completes inline.
    slow_skills: cmux new-workspace with `claude /<name>` — idempotent by workspace name.
    """
    repo_path = Path(repo_entry["path"]).expanduser().resolve()

    for skill in repo_entry.get("fast_skills") or []:
        prompt = _resolve_skill_prompt(skill, repo_path)
        if prompt is None:
            print(
                f"  {yellow('skip')} fast_skill {skill!r}: skill.md not found",
                flush=True,
            )
            continue
        if dry:
            print(f"  dry: claude -p {shell_quote(prompt)} in {repo_path}", flush=True)
            continue
        subprocess.run(
            f"claude -p {shell_quote(prompt)}",
            shell=True,
            cwd=repo_path,
        )

    slow_skills = repo_entry.get("slow_skills") or []
    if slow_skills:
        try:
            existing = set(workspace_names().values())
        except CmuxUnavailable:
            return
    for skill in slow_skills:
        prompt = _resolve_skill_prompt(skill, repo_path)
        if prompt is None:
            print(
                f"  {yellow('skip')} slow_skill {skill!r}: skill.md not found",
                flush=True,
            )
            continue
        ws_name = f"skill-{skill}"
        if ws_name in existing:
            continue
        if dry:
            print(
                f"  dry: spawn workspace {ws_name!r} with {claude_command(prompt)!r}",
                flush=True,
            )
            continue
        spawn_workspace(ws_name, repo_path, claude_command(prompt))


def _repo_owned_refs(ctx: RepoCycle, keep_refs: set[str]) -> list[str]:
    """Surviving workspace refs whose cwd sits inside this repo (its main
    worktree or any feature worktree). Scopes the global workspace list down
    to the repo being cycled — `ctx.cwds` spans every repo's workspaces.
    """
    roots = {ctx.repo_path.resolve()} | {wt.path.resolve() for wt in ctx.wts}
    owned: list[str] = []
    for ref in keep_refs:
        cwd = ctx.cwds.get(ref)
        if cwd is None:
            continue
        resolved = cwd.resolve()
        if any(parent in roots for parent in (resolved, *resolved.parents)):
            owned.append(ref)
    return owned


def _apply_repo_colors(ctx: RepoCycle, repo_entry: dict, keep_refs: set[str]) -> None:
    """Tint this repo's workspace sidebar entries with its `sidebar_color`.

    Optional per-repo `sidebar_color` (a cmux `WORKSPACE_COLORS` name); unset
    → no-op, so repos that don't set it keep cmux's default. Deduped via
    `pill_state` under a `color:<ref>` key so cmux is only touched when a
    workspace's color actually changes, and re-applied once after a daemon
    restart (when `pill_state` is empty). cmux-only — the wrapper no-ops on
    limux. A manual `clear-color` won't be re-tinted until the next restart.

    `sidebar_color` is validated at preflight (`_validate_sidebar_colors`), so
    a value reaching here is already a known cmux color.
    """
    color = repo_entry.get("sidebar_color")
    if not color or ctx.dry:
        return
    for ref in _repo_owned_refs(ctx, keep_refs):
        if ctx.pill_state.get(f"color:{ref}") == color:
            continue
        set_workspace_color(ref, color)
        ctx.pill_state[f"color:{ref}"] = color


def cycle_repo(
    repo_entry: dict,
    self_user: str,
    *,
    dry: bool,
    pr_cache: dict,
    pill_state: dict,
    cfg: dict,
) -> None:
    ctx = _prepare_cycle(
        repo_entry,
        self_user,
        cfg=cfg,
        pr_cache=pr_cache,
        pill_state=pill_state,
        dry=dry,
    )
    if ctx is None:
        return
    _write_pr_caches(ctx)
    if ctx.headless:
        return
    keep_refs = _dedupe_workspaces(ctx)
    printed_refresh, mine_items, others_items = _refresh_tracked_pills(ctx, keep_refs)
    if ctx.tracked and not printed_refresh:
        _print_tracked_summary(ctx, mine_items, others_items)
    _handle_orphans_and_close_stale(ctx, keep_refs)
    _apply_repo_colors(ctx, repo_entry, keep_refs)
    _spawn_missing_workspaces(ctx, repo_entry)
    _maybe_autoclose(
        ctx.repo_path,
        ctx.name,
        ctx.wts,
        ctx.merged_branches,
        ctx.cwds,
        prs=ctx.prs,
        dry=dry,
    )
    _reap_branch_refs(ctx)
    log_ff_advances(
        ff_default_branch_worktrees(
            ctx.repo_path, ctx.wts, default=ctx.default_branch, dry=dry
        ),
        dry=dry,
    )
    _run_repo_skills(repo_entry, dry=dry)


def _drain_close_requests(dry: bool) -> None:
    """Process pending close markers (enqueued by the TUI's `c`/`C` actions and
    autoclose) through the shared teardown.

    Refused markers (blockers reappeared between probe and drain) are dropped
    with a log line — the user re-presses `C` in the TUI to force-retry.
    """
    daemon_signal.prune_stale()
    for path, req in daemon_signal.iter_pending():
        ok, blockers = teardown(req, dry=dry)
        if ok:
            if not dry:
                daemon_signal.pop(path)
            continue
        label = req.name or req.ref
        print(
            f"  {verb('refused', color=yellow)} {label}: " + "; ".join(blockers),
            file=sys.stderr,
            flush=True,
        )
        if not dry:
            daemon_signal.pop(path)


def _reap_workspace_orphans(repos: list[dict], self_user: str, *, dry: bool) -> None:
    """Close cockpit-owned workspaces whose worktree no longer exists.

    Ownership is derived from cwd: a workspace is cockpit's iff its cwd
    resolves under a registered repo's path or one of its live worktrees.
    Workspaces outside every registered repo are ignored entirely.

    Within owned workspaces, a stranded one (no matching live worktree by
    cwd or name) is enqueued for tear-down — but only when Claude is idle.
    If Claude is mid-turn the reap is deferred to the next cycle so we
    don't yank the session out from under an active turn. Only mine-prefix
    branches are reaped; coworker-spawned workspaces are left to the user.
    """
    all_wts: list[Worktree] = []
    repo_lookup: dict[Path, tuple[str, Path]] = {}
    registered_roots: dict[Path, tuple[str, Path]] = {}
    for entry in repos:
        repo_path = Path(os.path.expanduser(entry["path"]))
        if not repo_path.is_dir():
            continue
        repo_name = entry.get("name") or repo_path.name
        registered_roots[repo_path.resolve()] = (repo_name, repo_path)
        try:
            # Identity only (path/branch) — skip the dirty/unpushed stat forks.
            for wt in worktrees_basic(repo_path, entry.get("branch_prefix", "")):
                all_wts.append(wt)
                repo_lookup[wt.path.resolve()] = (repo_name, repo_path)
        except RuntimeError:
            continue

    wt_by_path = {wt.path.resolve(): wt for wt in all_wts}
    wt_by_name = {wt.label: wt for wt in all_wts}

    names, cwds = workspace_state()
    my_prefix = f"{self_user}/"

    def _owning_repo(cwd: Path | None) -> tuple[str, Path] | None:
        if cwd is None:
            return None
        for parent in [cwd, *cwd.parents]:
            resolved = parent.resolve()
            hit = repo_lookup.get(resolved) or registered_roots.get(resolved)
            if hit:
                return hit
        return None

    for ref, ws_name in names.items():
        cwd = cwds.get(ref)
        wt_opt = _resolve_wt(ref, ws_name, cwds, wt_by_path, wt_by_name)
        if wt_opt is not None:
            continue
        owner = _owning_repo(cwd)
        if owner is None:
            continue
        repo_name, repo_path = owner
        label = ws_name or ref
        if not workspace_is_idle(ref):
            print(
                f"  {verb('defer')} {dim(f'reap workspace {label} ({ref}) — not idle (Claude mid-turn)')}",
                flush=True,
            )
            continue
        last_known_branch = ws_name if ws_name.startswith(my_prefix) else None
        req = TeardownRequest(
            ref=ref,
            name=ws_name,
            worktree_path=None,
            branch=last_known_branch,
            repo_path=repo_path,
            repo_name=repo_name,
            forced=True,
        )
        action = "[dry] reap" if dry else "reap"
        print(
            f"  {verb(action)} {dim(f'orphan workspace {label} ({ref}) — no matching worktree (cwd={cwd})')}",
            flush=True,
        )
        if not dry:
            daemon_signal.enqueue(req)


# Re-query the install repo for a newer version at most hourly — the running
# version can't change mid-daemon-run, so checking every slow tick (300s) just
# spends a `gh api` call. The per-version log guard below caps noise further.
_UPDATE_CHECK_TTL_SECONDS = 3600


def _check_plugin_update(cfg: dict, pill_state: dict) -> None:
    """Log once when a newer cockpit is published on the install repo's default
    branch. Gated on `check_update` (default true), throttled to one `gh` query
    per `_UPDATE_CHECK_TTL_SECONDS` and one log line per newer version — both
    keyed in `pill_state` like the spawn in-flight guard. Daemon-wide (not
    per-repo), so it runs before the repo loop. Any fetch failure logs nothing
    (see lib.version).
    """
    if not cfg.get("check_update", True):
        return
    now = time.monotonic()
    last = pill_state.get("update-check:ts")
    if isinstance(last, float) and (now - last) < _UPDATE_CHECK_TTL_SECONDS:
        return
    pill_state["update-check:ts"] = now
    running = version.running_version()
    latest = version.latest_version()
    if not latest or not version.is_newer(latest, running):
        return
    if pill_state.get("update-check:warned") == latest:
        return
    pill_state["update-check:warned"] = latest
    ts = datetime.now().isoformat(timespec="seconds")
    print(
        f"[{ts}] {yellow('cockpit:')} update available\n"
        f"  {running} -> {latest} (run /plugin update cockpit)",
        flush=True,
    )


def cycle_all(
    cfg: dict,
    self_user: str,
    *,
    dry: bool,
    pr_cache: dict,
    pill_state: dict,
) -> None:
    ensure_state_dirs()
    _check_plugin_update(cfg, pill_state)
    repos = cfg.get("repos", [])
    if not repos:
        print(
            f"  {yellow('no managed repos')} — register one via /cockpit:new in a git repo",
            flush=True,
        )
        return
    if not _cache_only(cfg):
        _drain_close_requests(dry=dry)
    if not _cache_only(cfg):
        try:
            close_gone_cwd_workspaces(dry=dry)
        except CmuxUnavailable as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] {yellow('skip')} close_gone_cwd_workspaces: cmux unavailable: {e}",
                file=sys.stderr,
                flush=True,
            )
    for repo_entry in repos:
        try:
            cycle_repo(
                repo_entry,
                self_user,
                dry=dry,
                pr_cache=pr_cache,
                pill_state=pill_state,
                cfg=cfg,
            )
        except (RuntimeError, subprocess.SubprocessError, OSError) as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] cycle error for {repo_entry.get('name')}: {e}\n"
                f"{traceback.format_exc()}",
                file=sys.stderr,
                flush=True,
            )
    if not _cache_only(cfg):
        try:
            _reap_workspace_orphans(repos, self_user, dry=dry)
        except CmuxUnavailable as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] {yellow('skip')} _reap_workspace_orphans: cmux unavailable: {e}",
                file=sys.stderr,
                flush=True,
            )
