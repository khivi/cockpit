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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.cmux import (  # noqa: E402
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
    workspace_state,
)
from lib.colors import (  # noqa: E402
    bold,
    blue,
    cyan,
    dim,
    green,
    issue_color,
    magenta,
    red,
    yellow,
)
from lib.config import (  # noqa: E402
    ensure_state_dirs,
    load_config,
    install_cship_default_config,
    install_cship_statusline_if_configured,
    install_starship_default_config,
)
from lib.daemon import run_watcher  # noqa: E402
from lib.cache import (  # noqa: E402
    write_base_ahead,
    write_base_distance,
    write_branch_pr_cache,
    write_pr_cache,
)
from lib import close_requests  # noqa: E402
from lib.gh import (  # noqa: E402
    PR,
    fetch_merged_branches,
    gh_self_user,
    list_relevant_prs,
    repo_nwo,
)
from lib.git import (  # noqa: E402
    Worktree,
    ahead_of_base,
    behind_of_base,
    count_commits_since,
    ff_default_branch_worktrees,
    origin_head_branch,
    worktrees,
)
from lib.teardown import TeardownRequest, teardown  # noqa: E402

# ── constants ───────────────────────────────────────────────────────────────
MAIN_BRANCHES = {"master", "main"}

NUDGE_INTERVAL_SECS = 300
ACTIONABLE_ISSUES = {"ci", "comments", "conflicts"}

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
        print(f"  {yellow('nudged')} {tag} → {ref}", flush=True)


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
        pr = pr_by_branch.get(wt.branch)
        if pr is None or pr.author == self_user:
            continue
        matched.append((pr, wt))
    return matched, skipped_self


def _log_ff_main(repo_path: Path, wts: list[Worktree], *, dry: bool) -> None:
    """Fast-forward default-branch worktrees via lib.git, log each advance."""
    for wt, behind in ff_default_branch_worktrees(repo_path, wts, dry=dry):
        action = "[dry] ff-main" if dry else "ff-main:"
        print(
            f"  {magenta(action)} {wt.short} → origin/{wt.branch}"
            f"  ({behind} commit{'s' if behind != 1 else ''})",
            flush=True,
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

    Teardown delegates to `lib.teardown.teardown` (forced=True since we've
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
                f"  {dim('autoclose skipped (uncommitted)')} {wt.short} "
                f"({wt.dirty_count} dirty)",
                flush=True,
            )
            continue
        ahead = count_commits_since(wt.path, merged_head)
        if ahead < 0:
            print(
                f"  {dim('autoclose skipped (merge-head check failed)')} {wt.short}",
                flush=True,
            )
            continue
        if ahead > 0:
            print(
                f"  {dim(f'autoclose skipped ({ahead} commits after merge)')} {wt.short}",
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
    default = origin_head_branch(repo_path)
    if not default:
        for wt in feature:
            write_base_distance(wt.branch, -1, 0)
            write_base_ahead(wt.branch, -1, 0)
        return distances
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "fetch", "--quiet", "origin", default],
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        for wt in feature:
            write_base_distance(wt.branch, -1, 0)
            write_base_ahead(wt.branch, -1, 0)
        return distances
    now = int(time.time())
    for wt in feature:
        n = behind_of_base(wt.path, default)
        distances[wt.branch] = n
        write_base_distance(wt.branch, n, now)
        write_base_ahead(wt.branch, ahead_of_base(wt.path, default), now)
    return distances


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
    repo_path = Path(os.path.expanduser(repo_entry["path"]))
    if not repo_path.is_dir():
        print(
            f"  {yellow('skip')} {repo_entry.get('name', repo_path.name)}: "
            f"path does not exist ({repo_path})",
            flush=True,
        )
        return
    try:
        owner, name = repo_nwo(repo_path)
    except RuntimeError as e:
        print(f"  {yellow('skip')} {repo_path}: {e}", flush=True)
        return

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
        return

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
    base_distance: dict[str, int] = {}
    if not dry:
        base_distance = _refresh_base_distance(repo_path, wts)
        wt_by_branch = {wt.branch: wt for wt in wts}
        for pr in prs:
            write_pr_cache(name, pr, wt_by_branch.get(pr.branch))
            # Mirror PR fields into the cship cache so starship.toml [custom.*]
            # modules render fresh on the first session render, without each
            # field having to spawn its own `gh pr view` from cold.
            ci_glyph = {"passed": "✓", "pending": "•"}.get(pr.ci, "")
            if pr.ci.startswith("failed"):
                ci_glyph = "✗"
            write_branch_pr_cache(
                pr.branch,
                state=pr.state,
                is_draft=pr.is_draft,
                review_decision=pr.review_decision,
                number=pr.number,
                title=pr.title,
                ci_glyph=ci_glyph,
            )

    if headless:
        return

    by_name: dict[str, list[str]] = {}
    for ref, ws_name in names.items():
        by_name.setdefault(ws_name, []).append(ref)
    keep_refs: set[str] = set()
    for ws_name, refs in by_name.items():
        refs_sorted = sorted(refs, key=lambda r: int(r.split(":")[1]))
        keep_refs.add(refs_sorted[0])
        for extra in refs_sorted[1:]:
            print(
                f"  {red('closing duplicate')} {ws_name} → {extra}  "
                f"(keeping {refs_sorted[0]})",
                flush=True,
            )
            if not dry:
                cmux_close_workspace_best_effort(extra)

    feature_wt_paths = {
        wt.path.resolve() for wt in wts if wt.branch not in MAIN_BRANCHES
    }
    by_wt_path: dict[Path, list[str]] = {}
    for ref in keep_refs:
        cwd = cwds.get(ref)
        if cwd is None:
            continue
        resolved = cwd.resolve()
        if resolved in feature_wt_paths:
            by_wt_path.setdefault(resolved, []).append(ref)
    for _wt_path, refs in by_wt_path.items():
        if len(refs) <= 1:
            continue
        refs_sorted = sorted(refs, key=lambda r: int(r.split(":")[1]))
        for extra in refs_sorted[1:]:
            keep_refs.discard(extra)
            extra_name = names.get(extra, extra)
            keep_name = names.get(refs_sorted[0], refs_sorted[0])
            print(
                f"  {red('closing duplicate')} {extra_name} → {extra}  "
                f"(same worktree as {keep_name})",
                flush=True,
            )
            if not dry:
                cmux_close_workspace_best_effort(extra)

    printed_refresh = False
    for ref, (pr, wt) in tracked.items():
        if ref not in keep_refs:
            continue
        label = names.get(ref, ref)
        desired = frozenset(status_pills(pr, wt))
        changed = pill_state.get(ref) != desired
        if changed and not dry:
            apply_pills(ref, pr, wt)
        if changed or verbose:
            op = " rebasing" if wt.rebasing else (" merging" if wt.merging else "")
            tag = pr.display_issue + op
            print(
                f"  {dim('refreshed')} {blue(f'#{pr.number}')} → {cyan(label)}  "
                f"[{issue_color(pr.display_issue)(tag)}]",
                flush=True,
            )
            printed_refresh = True
        if changed and not dry:
            pill_state[ref] = desired
        if pr.display_issue in ACTIONABLE_ISSUES:
            if pr.display_issue == "comments":
                desc = f"{pr.unaddressed} unresolved review thread(s) — reply or push fixes"
            elif pr.display_issue == "ci":
                desc = f"CI is failing ({pr.ci}) — run `gh pr checks {pr.number}` and address it"
            else:
                desc = "merge conflicts vs base — rebase and force-push"
            maybe_nudge(
                ref,
                f"PR #{pr.number}: {desc}.",
                nudge_state,
                dry,
                label,
                pr_number=pr.number,
                category=pr.display_issue,
            )

    if tracked and not printed_refresh:
        labels = sorted(names.get(ref, ref) for ref in tracked if ref in keep_refs)
        if labels:
            print(
                f"  {dim('tracked:')} {', '.join(cyan(lbl) for lbl in labels)}",
                flush=True,
            )

    wt_by_name = {wt.short: wt for wt in wts}
    wt_by_path = {wt.path.resolve(): wt for wt in wts}
    pr_branches = {pr.branch for pr in prs}
    my_prefix = f"{self_user}/"
    for ref in keep_refs:
        ws_name = names.get(ref, "")
        cwd = cwds.get(ref)
        wt = wt_by_path.get(cwd.resolve()) if cwd is not None else None
        if wt is None:
            wt = wt_by_name.get(ws_name)
        if wt is None or wt.branch in pr_branches or wt.branch in MAIN_BRANCHES:
            continue
        is_mine = wt.branch.startswith(my_prefix)
        if is_mine:
            if wt.branch in merged_branches:
                ahead = count_commits_since(wt.path, merged_branches[wt.branch])
                if ahead == 0:
                    print(
                        f"  {dim(f'merged orphan {ws_name} ({wt.branch}) — autoclose may handle')}",
                        flush=True,
                    )
                    continue
            behind_base = base_distance.get(wt.branch, 0)
            if not dry:
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
            stale_tag = f" stale↻{behind_base}" if behind_base > 0 else ""
            tag = f"orphan{' wip' if wt.dirty else ''}{stale_tag}"
            orphan_snap = frozenset(
                [
                    ("orphan", ORPHAN_ICON),
                    ("wip", str(wt.dirty_count) if wt.dirty else ""),
                    ("stale", str(behind_base) if behind_base > 0 else ""),
                ]
            )
            changed = pill_state.get(ref) != orphan_snap
            if changed or verbose:
                print(
                    f"  {dim('refreshed')} {cyan(ws_name)} → {ref}  [{yellow(tag)}]",
                    flush=True,
                )
            if changed and not dry:
                pill_state[ref] = orphan_snap
            maybe_nudge(
                ref,
                f"Worktree {wt.short} on {wt.branch} still has no open PR. "
                f"Push commits and open a PR, or close the worktree if abandoned.",
                nudge_state,
                dry,
                ws_name,
            )
            continue
        if keep_stale:
            print(
                f"  {dim(f'stale {ws_name} → {ref}  (kept; branch {wt.branch} has no open PR)')}",
                flush=True,
            )
            continue
        print(
            f"  {red('closing')} {ws_name} → {ref}  (branch {wt.branch} has no open PR)",
            flush=True,
        )
        if not dry:
            cmux_close_workspace_best_effort(ref)

    if not no_spawn:
        matched, skipped_self = match_worktrees(prs, wts, self_user)
        for pr in skipped_self:
            print(
                f"  {bold(red('WARN:'))} my PR #{pr.number} has no worktree for "
                f"branch {pr.branch} — create one with /cockpit:new",
                file=sys.stderr,
                flush=True,
            )
        tracked_pr_numbers = {pr.number for pr, _ in tracked.values()}
        for pr, wt in matched:
            if pr.number not in tracked_pr_numbers:
                spawn_pr_workspace(pr, wt, dry=dry)
        covered_paths = {p.resolve() for p in cwds.values()}
        for wt in wts:
            if not wt.branch.startswith(my_prefix) or wt.branch in pr_branches:
                continue
            if wt.path.resolve() in covered_paths:
                continue
            if wt.branch in merged_branches:
                ahead = count_commits_since(wt.path, merged_branches[wt.branch])
                if ahead == 0:
                    print(
                        f"  {dim(f'skip orphan-spawn {wt.short} — branch {wt.branch} has merged PR')}",
                        flush=True,
                    )
                    continue
            spawn_orphan_workspace(wt, dry=dry)

    _maybe_autoclose(cfg, repo_path, name, wts, merged_branches, cwds, dry=dry)
    _log_ff_main(repo_path, wts, dry=dry)


def _drain_close_requests(dry: bool) -> None:
    """Process pending `/cockpit:close` markers through the shared teardown.

    Refused markers (blockers reappeared between probe and drain) are dropped
    with a log line — the user re-runs `cockpit:close --force` to retry.
    """
    close_requests.prune_stale()
    for path, req in close_requests.iter_pending():
        ok, blockers = teardown(req, dry=dry)
        if ok:
            if not dry:
                close_requests.pop(path)
            continue
        label = req.name or req.ref
        print(
            f"  {yellow('close-request refused')} {label}: " + "; ".join(blockers),
            file=sys.stderr,
            flush=True,
        )
        if not dry:
            close_requests.pop(path)


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
    from lib.cmux import workspace_is_idle, workspace_state as _ws_state

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

    names, cwds = _ws_state()
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
        wt = wt_by_path.get(cwd.resolve()) if cwd is not None else None
        if wt is None:
            wt = wt_by_name.get(ws_name)
        if wt is not None:
            continue
        owner = _owning_repo(cwd)
        if owner is None:
            continue
        repo_name, repo_path = owner
        label = ws_name or ref
        if not workspace_is_idle(ref):
            print(
                f"  {magenta('defer reap:')} workspace {label} ({ref}) "
                f"— not idle (Claude mid-turn)",
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
        action = "[dry] reap orphan" if dry else "reap orphan:"
        print(
            f"  {magenta(action)} workspace {label} ({ref}) "
            f"— no matching worktree (cwd={cwd})",
            flush=True,
        )
        if not dry:
            close_requests.enqueue(req)


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


def _build_state(args) -> dict:
    return {
        "self_user": None,
        "keep_stale": getattr(args, "keep_stale", False) if args else False,
        "no_spawn": getattr(args, "no_spawn", False) if args else False,
        "dry": getattr(args, "dry_run", False) if args else False,
        "verbose": getattr(args, "verbose", False) if args else False,
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


def _once_cli() -> int:
    state = _build_state(None)
    _once_with(state)
    return 0


def _watch(state: dict, watch_secs: int) -> None:
    self_ws = os.environ.get("CMUX_WORKSPACE_ID")
    show_loop_pill = bool(self_ws) and not state["dry"]

    def on_start() -> None:
        if show_loop_pill:
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
        if show_loop_pill:
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
