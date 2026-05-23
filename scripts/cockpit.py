#!/usr/bin/env python3
"""cockpit: the reconcile-loop daemon for PRs ↔ cmux workspaces.

Per cycle, for every repo registered in $COCKPIT_HOME/config.json:
  1. fetch relevant PRs (mine + coworker-PRs with local worktrees)
  2. refresh status pills on existing tracked workspaces
  3. spawn workspaces for PRs that have a worktree but no workspace
  4. close duplicate workspaces (same name, or same worktree under different name)
  5. close workspaces whose branch's PR is no longer open
  6. mark orphan worktrees (mine, no PR) with an orphan pill
  7. write a PR cache snapshot under $COCKPIT_HOME/cache
  8. autoclean merged worktrees + workspaces (clean + no unpushed only)

Modes:
  --watch [SECS]  long-running daemon; SIGUSR1 kicks an immediate cycle
  --once          run exactly one cycle and exit

Sibling entry points (each script does one job):
  scripts/footer.py   statusLine shim — pipes Claude Code's stdin to cship
  scripts/list.py     `/cockpit:list` table
  scripts/sync.py     USR1-kick the daemon, else fall back to `cockpit.py --once`
  scripts/spawn.py    `/cockpit:new` — create worktree + workspace

Failure policy: each cycle MUST exit 0 even on GitHub API errors. Errors go to
stderr (visible in the --watch terminal); the next cycle retries.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.cmux import (  # noqa: E402
    BLUE,
    LOOP_ICON,
    LOOP_KEY,
    ORANGE,
    ORPHAN_ICON,
    ORPHAN_KEY,
    _resolve_tool,
    apply_pills,
    apply_stale_pill,
    apply_wip_pill,
    close_gone_cwd_workspaces,
    cmux,
    cmux_close_workspace_best_effort,
    find_cockpit_workspaces,
    nudge_if_idle,
    spawn_orphan_workspace,
    spawn_pr_workspace,
    status_pills,
    workspace_is_idle,
    workspace_state,
)
from scripts.lib.colors import (  # noqa: E402
    bold,
    blue,
    cyan,
    dim,
    green,
    red,
    yellow,
)
from scripts.lib.issue_color import issue_color  # noqa: E402
from scripts.lib.log_format import verb  # noqa: E402
from scripts.lib.config import (  # noqa: E402
    ensure_state_dirs,
    load_config,
    install_cship_default_config,
    install_cship_statusline_if_configured,
    install_starship_default_config,
)
from scripts.lib.daemon import run_watcher  # noqa: E402
import scripts.lib.daemon_signal as daemon_signal  # noqa: E402
from scripts.lib.cache import (  # noqa: E402
    write_base_ahead,
    write_base_distance,
    write_branch_pr_cache,
    write_pr_cache,
)
from scripts.lib.gh import (  # noqa: E402
    PR,
    fetch_merged_branches,
    gh_self_user,
    list_relevant_prs,
    repo_nwo,
)
from scripts.lib.git import (  # noqa: E402
    Worktree,
    ahead_of_base,
    behind_of_base,
    count_commits_since,
    ff_default_branch_worktrees,
    log_ff_advances,
    origin_head_branch,
    worktrees,
)
from scripts.orchestrators.teardown import TeardownRequest, teardown  # noqa: E402

# ── constants ───────────────────────────────────────────────────────────────
MAIN_BRANCHES = {"master", "main"}

NUDGE_INTERVAL_SECS = 300
ACTIONABLE_ISSUES = {"ci", "comments", "conflicts"}

_NUDGE_DESC = {
    "comments": lambda pr: (
        f"{pr.unaddressed} unresolved review thread(s) — reply or push fixes"
    ),
    "ci": lambda pr: (
        f"CI is failing ({pr.ci}) — run `gh pr checks {pr.number}` and address it"
    ),
    "conflicts": lambda _pr: "merge conflicts vs base — rebase and force-push",
}

DEFAULT_POLL_SECS = 300
MIN_POLL_SECS = 5


def _cache_only(cfg: dict) -> bool:
    """Skip pill / cmux-only verbs this cycle? True whenever the resolved
    workspace backend isn't cmux (limux can't do pills; 'none' = headless).
    """
    return _resolve_tool() != "cmux"


# ── helpers ─────────────────────────────────────────────────────────────────
def maybe_nudge(
    ref: str,
    message: str,
    nudge_state: dict,
    dry: bool,
    tag: str,
    *,
    pr_number: int | None = None,
    category: str | None = None,
) -> None:
    if nudge_if_idle(
        ref,
        message,
        nudge_state=nudge_state,
        interval_secs=NUDGE_INTERVAL_SECS,
        dry=dry,
        tag=tag,
        pr_number=pr_number,
        category=category,
    ):
        print(f"  {verb('nudged', color=yellow)} {tag} → {ref}", flush=True)


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


def _ci_glyph(ci: str) -> str:
    """One-char glyph for a PR's CI state. Empty when state is unknown."""
    if ci.startswith("failed"):
        return "✗"
    return {"passed": "✓", "pending": "•"}.get(ci, "")


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
    """True if `wt`'s branch matches a merged PR and HEAD has not advanced past it."""
    merged_head = merged_branches.get(wt.branch)
    if merged_head is None:
        return False
    return count_commits_since(wt.path, merged_head) == 0


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
    cfg: dict,
    repo_path: Path,
    repo_name: str,
    wts: list[Worktree],
    merged_branches: dict[str, str],
    cwds: dict[str, Path],
    *,
    dry: bool,
) -> None:
    """Remove worktrees + workspaces for merged branches that are clean.

    Removes any merged branch (mine or coworker's) when the worktree is clean
    and has not advanced past the head SHA recorded when its PR was merged.
    Coworker worktrees are safe to clean since they can be re-created from the
    merged PR if needed.

    The post-merge check uses `count_commits_since(wt, merged_head)` rather
    than `wt.unpushed` because `git cherry` (which powers `wt.unpushed`) cannot
    recognize GitHub squash-merges — see `_count_unpushed` docstring.

    Teardown delegates to `orchestrators.teardown.teardown` (forced=True since we've
    already validated merge-state-clean above).
    """
    if not cfg.get("auto_cleanup_on_merge", True):
        return
    for wt in wts:
        if wt.branch in MAIN_BRANCHES:
            continue
        merged_head = merged_branches.get(wt.branch)
        if merged_head is None:
            continue
        if wt.dirty_count > 0:
            print(
                f"  {verb('autoclose')} {dim(f'skipped (uncommitted) {wt.short}')} "
                f"{dim(f'({wt.dirty_count} dirty)')}",
                flush=True,
            )
            continue
        ahead = count_commits_since(wt.path, merged_head)
        if ahead < 0:
            print(
                f"  {verb('autoclose')} {dim(f'skipped (merge-head check failed) {wt.short}')}",
                flush=True,
            )
            continue
        if ahead > 0:
            print(
                f"  {verb('autoclose')} {dim(f'skipped ({ahead} commits after merge) {wt.short}')}",
                flush=True,
            )
            continue
        ref = _workspace_ref_for_path(wt.path, cwds) or wt.short
        teardown(
            TeardownRequest(
                ref=ref,
                name=wt.short,
                worktree_path=wt.path,
                branch=wt.branch,
                repo_path=repo_path,
                repo_name=repo_name,
                forced=True,
            ),
            dry=dry,
        )


def _refresh_base_distance(repo_path: Path, wts: list[Worktree]) -> dict[str, int]:
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
    feature = [w for w in wts if w.branch not in MAIN_BRANCHES]
    distances: dict[str, int] = {}

    def _invalidate() -> dict[str, int]:
        for wt in feature:
            write_base_distance(wt.branch, -1, 0)
            write_base_ahead(wt.branch, -1, 0)
        return distances

    default = origin_head_branch(repo_path)
    if not default:
        return _invalidate()
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "fetch", "--quiet", "origin", default],
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return _invalidate()
    now = int(time.time())
    for wt in feature:
        n = behind_of_base(wt.path, default)
        distances[wt.branch] = n
        write_base_distance(wt.branch, n, now)
        write_base_ahead(wt.branch, ahead_of_base(wt.path, default), now)
    return distances


@dataclass
class RepoCycle:
    """Per-repo, per-cycle context bundle. Mutable dicts (pill_state /
    nudge_state / pr_cache) are passed by reference and persist across cycles.
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
    pill_state: dict
    nudge_state: dict
    keep_stale: bool
    no_spawn: bool
    dry: bool
    verbose: bool
    headless: bool
    base_distance: dict[str, int] = field(default_factory=dict)


def _prepare_cycle(
    repo_entry: dict,
    self_user: str,
    *,
    cfg: dict,
    pr_cache: dict,
    pill_state: dict,
    nudge_state: dict,
    keep_stale: bool,
    no_spawn: bool,
    dry: bool,
    verbose: bool,
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

    headless = _cache_only(cfg)
    with ThreadPoolExecutor(max_workers=3) as ex:
        wts_fut = ex.submit(worktrees, repo_path)
        state_fut = None if headless else ex.submit(workspace_state)
        merged_fut = ex.submit(fetch_merged_branches, repo_path)
        wts = wts_fut.result()
        names, cwds = ({}, {}) if state_fut is None else state_fut.result()
        merged_branches = merged_fut.result()

    coworker_branches = sorted(
        {w.branch for w in wts if not w.branch.startswith(f"{self_user}/")}
    )
    try:
        prs = list_relevant_prs(
            owner, name, self_user, coworker_branches, cache=pr_cache
        )
    except RuntimeError as e:
        print(
            f"  {yellow('skip')} {owner}/{name}: list_relevant_prs failed: {e}",
            flush=True,
        )
        return None

    tracked = find_cockpit_workspaces(prs, wts, names=names, cwds=cwds)
    mine = sum(1 for pr in prs if pr.author == self_user)
    coworker_relevant = len(prs) - mine
    feature_wts = [w for w in wts if w.branch not in MAIN_BRANCHES]
    wip_count = sum(1 for w in feature_wts if w.dirty)
    ts = datetime.now().isoformat(timespec="seconds")
    print(
        f"{green(f'[{ts}]')} {bold(f'{owner}/{name}')}  mine: {mine}  "
        f"coworker-with-wt: {coworker_relevant}  worktrees: {len(feature_wts)}  "
        f"tracked: {len(tracked)}  wip: {wip_count}",
        flush=True,
    )
    return RepoCycle(
        cfg=cfg,
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
        pill_state=pill_state,
        nudge_state=nudge_state,
        keep_stale=keep_stale,
        no_spawn=no_spawn,
        dry=dry,
        verbose=verbose,
        headless=headless,
    )


def _write_pr_caches(ctx: RepoCycle) -> None:
    """Refresh base-distance cache + PR caches for the cship statusline.

    Mirroring PR fields into the cship cache lets `starship.toml [custom.*]`
    modules render fresh on the first session render without each field
    having to spawn its own `gh pr view` from cold.
    """
    if ctx.dry:
        return
    ctx.base_distance = _refresh_base_distance(ctx.repo_path, ctx.wts)
    wt_by_branch = {wt.branch: wt for wt in ctx.wts}
    for pr in ctx.prs:
        write_pr_cache(ctx.name, pr, wt_by_branch.get(pr.branch))
        write_branch_pr_cache(
            pr.branch,
            state=pr.state,
            is_draft=pr.is_draft,
            review_decision=pr.review_decision,
            number=pr.number,
            title=pr.title,
            ci_glyph=_ci_glyph(pr.ci),
        )


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
        refs_sorted = sorted(refs, key=lambda r: int(r.split(":")[1]))
        keep_refs.add(refs_sorted[0])
        _close_extras(refs_sorted, "keeping {first}")

    feature_wt_paths = {
        wt.path.resolve() for wt in ctx.wts if wt.branch not in MAIN_BRANCHES
    }
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
        refs_sorted = sorted(refs, key=lambda r: int(r.split(":")[1]))
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
            label = ctx.names.get(ref, ref)
            desired = frozenset(status_pills(pr, wt, ctx.self_user))
            changed = ctx.pill_state.get(ref) != desired
            if changed and not ctx.dry:
                apply_pills(ref, pr, wt, ctx.self_user)
            if changed or ctx.verbose:
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
            if pr.display_issue in ACTIONABLE_ISSUES:
                maybe_nudge(
                    ref,
                    f"PR #{pr.number}: {_NUDGE_DESC[pr.display_issue](pr)}.",
                    ctx.nudge_state,
                    ctx.dry,
                    label,
                    pr_number=pr.number,
                    category=pr.display_issue,
                )
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
    """For each surviving workspace whose worktree branch has no open PR:
    mine → orphan pills + nudge; coworker → keep (if keep_stale) or close.
    """
    wt_by_name = {wt.short: wt for wt in ctx.wts}
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
        if wt.branch.startswith(my_prefix):
            _refresh_orphan(ctx, ref, wt, ws_name)
            continue
        if ctx.keep_stale:
            print(
                f"  {verb('stale')} {dim(f'{ws_name} → {ref}  (kept; branch {wt.branch} has no open PR)')}",
                flush=True,
            )
            continue
        print(
            f"  {verb('closing')} {ws_name} → {ref}  (branch {wt.branch} has no open PR)",
            flush=True,
        )
        if not ctx.dry:
            cmux_close_workspace_best_effort(ref)


def _refresh_orphan(ctx: RepoCycle, ref: str, wt: Worktree, ws_name: str) -> None:
    """Apply orphan/wip/stale pills and nudge if the orphan worktree is mine."""
    if _is_post_merge_stale(wt, ctx.merged_branches):
        print(
            f"  {verb('orphan')} {dim(f'{ws_name} ({wt.branch}) merged — autoclose may handle')}",
            flush=True,
        )
        return
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
    if changed or ctx.verbose:
        print(
            f"  {verb('refreshed')} {cyan(ws_name)} → {ref}  [{yellow(tag)}]",
            flush=True,
        )
    if changed and not ctx.dry:
        ctx.pill_state[ref] = orphan_snap
    maybe_nudge(
        ref,
        f"Worktree {wt.short} on {wt.branch} still has no open PR. "
        f"Push commits and open a PR, or close the worktree if abandoned.",
        ctx.nudge_state,
        ctx.dry,
        ws_name,
    )


def _spawn_missing_workspaces(ctx: RepoCycle) -> None:
    """Spawn cmux workspaces for PR-matched worktrees that lack one, and for
    my-prefix orphan worktrees not yet covered by any workspace cwd.
    """
    matched, skipped_self = match_worktrees(ctx.prs, ctx.wts, ctx.self_user)
    for pr in skipped_self:
        print(
            f"  {bold(red('WARN:'))} my PR #{pr.number} has no worktree for "
            f"branch {pr.branch} — create one with /cockpit:new",
            file=sys.stderr,
            flush=True,
        )
    tracked_pr_numbers = {pr.number for pr, _ in ctx.tracked.values()}
    for pr, wt in matched:
        if pr.number not in tracked_pr_numbers:
            spawn_pr_workspace(pr, wt, self_user=ctx.self_user, dry=ctx.dry)
    pr_branches = {pr.branch for pr in ctx.prs}
    my_prefix = f"{ctx.self_user}/"
    covered_paths = {p.resolve() for p in ctx.cwds.values()}
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
        spawn_orphan_workspace(wt, dry=ctx.dry)


def cycle_repo(
    repo_entry: dict,
    self_user: str,
    *,
    keep_stale: bool,
    no_spawn: bool,
    dry: bool,
    pr_cache: dict,
    nudge_state: dict,
    pill_state: dict,
    verbose: bool,
    cfg: dict,
) -> None:
    ctx = _prepare_cycle(
        repo_entry,
        self_user,
        cfg=cfg,
        pr_cache=pr_cache,
        pill_state=pill_state,
        nudge_state=nudge_state,
        keep_stale=keep_stale,
        no_spawn=no_spawn,
        dry=dry,
        verbose=verbose,
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
    if not no_spawn:
        _spawn_missing_workspaces(ctx)
    _maybe_autoclose(
        cfg, ctx.repo_path, ctx.name, ctx.wts, ctx.merged_branches, ctx.cwds, dry=dry
    )
    log_ff_advances(
        ff_default_branch_worktrees(ctx.repo_path, ctx.wts, dry=dry), dry=dry
    )


def _drain_close_requests(dry: bool) -> None:
    """Process pending `/cockpit:close` markers through the shared teardown.

    Refused markers (blockers reappeared between probe and drain) are dropped
    with a log line — the user re-runs `cockpit:close --force` to retry.
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
            for wt in worktrees(repo_path):
                all_wts.append(wt)
                repo_lookup[wt.path.resolve()] = (repo_name, repo_path)
        except RuntimeError:
            continue

    wt_by_path = {wt.path.resolve(): wt for wt in all_wts}
    wt_by_name = {wt.short: wt for wt in all_wts}

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


def cycle_all(
    cfg: dict,
    self_user: str,
    *,
    keep_stale: bool,
    no_spawn: bool,
    dry: bool,
    pr_cache: dict,
    nudge_state: dict,
    pill_state: dict,
    verbose: bool,
) -> None:
    ensure_state_dirs()
    repos = cfg.get("repos", [])
    if not repos:
        print(
            f"  {yellow('no managed repos')} — register one via /cockpit:new in a git repo",
            flush=True,
        )
        return
    if not _cache_only(cfg):
        _drain_close_requests(dry=dry)
    if cfg.get("auto_cleanup_on_merge", True) and not _cache_only(cfg):
        close_gone_cwd_workspaces(dry=dry)
    for repo_entry in repos:
        try:
            cycle_repo(
                repo_entry,
                self_user,
                keep_stale=keep_stale,
                no_spawn=no_spawn,
                dry=dry,
                pr_cache=pr_cache,
                nudge_state=nudge_state,
                pill_state=pill_state,
                verbose=verbose,
                cfg=cfg,
            )
        except Exception as e:
            ts = datetime.now().isoformat(timespec="seconds")
            print(
                f"[{ts}] cycle error for {repo_entry.get('name')}: {e}",
                file=sys.stderr,
                flush=True,
            )
    if not _cache_only(cfg):
        _reap_workspace_orphans(repos, self_user, dry=dry)


def _build_state(args: argparse.Namespace) -> dict:
    return {
        "self_user": None,
        "keep_stale": args.keep_stale,
        "no_spawn": args.no_spawn,
        "dry": args.dry_run,
        "verbose": args.verbose,
        "pr_cache": {},
        "nudge_state": {},
        "pill_state": {},
    }


def _once_with(state: dict) -> None:
    cfg = load_config()
    self_user = state.get("self_user") or gh_self_user()
    state["self_user"] = self_user
    cycle_all(
        cfg,
        self_user,
        keep_stale=state["keep_stale"],
        no_spawn=state["no_spawn"],
        dry=state["dry"],
        pr_cache=state["pr_cache"],
        nudge_state=state["nudge_state"],
        pill_state=state["pill_state"],
        verbose=state["verbose"],
    )


def _watch(state: dict, watch_secs: int) -> None:
    self_ws = os.environ.get("CMUX_WORKSPACE_ID")
    show_loop_pill = bool(self_ws) and not state["dry"]

    def on_start() -> None:
        if show_loop_pill and self_ws is not None:
            cmux(
                "set-status",
                LOOP_KEY,
                LOOP_ICON,
                "--workspace",
                self_ws,
                "--color",
                BLUE,
                check=False,
            )

    def on_stop() -> None:
        if show_loop_pill and self_ws is not None:
            cmux("clear-status", LOOP_KEY, "--workspace", self_ws, check=False)

    def on_wake() -> None:
        state["nudge_state"].clear()
        print(f"{green('kick:')} SIGUSR1 — running cycle now", flush=True)

    run_watcher(
        lambda: _once_with(state),
        watch_secs,
        on_start=on_start,
        on_stop=on_stop,
        on_wake=on_wake,
    )


def _statusline_command() -> str:
    return f"{sys.executable} {Path(__file__).resolve().parent / 'footer.py'}"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--watch",
        nargs="?",
        const=-1,
        type=int,
        metavar="SECS",
        help="Run as a daemon. With no arg, use config.poll_interval_seconds.",
    )
    g.add_argument("--once", action="store_true")
    g.add_argument(
        "--footer",
        action="store_true",
        help="Re-run footer setup only (cship.toml + starship.toml + statusLine), then exit.",
    )
    p.add_argument("--keep-stale", action="store_true")
    p.add_argument("--no-spawn", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    ensure_state_dirs()

    if args.footer:
        install_cship_default_config()
        install_starship_default_config()
        install_cship_statusline_if_configured(_statusline_command())
        return 0

    startup_cfg = load_config()
    if startup_cfg.get("tool", "auto") == "auto":
        resolved = _resolve_tool()
        if resolved == "limux":
            print(
                f"{yellow('cockpit:')} cmux not found — using limux. "
                "Side panel disabled (limux lacks pill support); "
                "footer/statusline and slash commands work. "
                "Set 'tool': 'cmux' in config to require cmux instead.",
                file=sys.stderr,
                flush=True,
            )
        elif resolved == "none":
            print(
                f"{yellow('cockpit:')} no workspace tool on PATH (cmux/limux) — "
                "running cache-only mode. Footer/statusline works; "
                "side panel and slash commands disabled. "
                "Set 'tool': 'none' in config to suppress this warning.",
                file=sys.stderr,
                flush=True,
            )

    if args.watch is not None:
        cfg = load_config()
        secs = (
            args.watch
            if args.watch and args.watch > 0
            else cfg.get("poll_interval_seconds", DEFAULT_POLL_SECS)
        )
        if secs < MIN_POLL_SECS:
            print(f"--watch SECS must be >= {MIN_POLL_SECS}", file=sys.stderr)
            return 2
        state = _build_state(args)
        _watch(state, secs)
        return 0
    state = _build_state(args)
    _once_with(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
