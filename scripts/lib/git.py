"""Git/worktree helpers."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from . import run


def require_git() -> None:
    """Exit cleanly with a one-liner if `git` is not on PATH.

    Mirrors `lib.cmux.require_workspace_binary`: surfaces a structured
    install hint at startup instead of letting a cryptic FileNotFoundError
    surface deep inside a daemon cycle.
    """
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=False)
    except FileNotFoundError:
        print(
            "cockpit: `git` not found on PATH — install from https://git-scm.com",
            file=sys.stderr,
        )
        sys.exit(2)


def _git(repo: str | os.PathLike, *args: str) -> subprocess.CompletedProcess:
    """`git -C <repo> <args>` capturing stdout/stderr as text; never raises."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


@dataclass
class Worktree:
    path: Path
    branch: str
    rebasing: bool = False
    merging: bool = False
    dirty_count: int = 0
    unpushed: int = 0
    is_primary: bool = False

    @property
    def short(self) -> str:
        return self.path.name

    @property
    def dirty(self) -> bool:
        return self.dirty_count > 0


def count_dirty(wt_path: Path) -> int:
    """Count uncommitted entries (modified/added/deleted/untracked).

    Returns 0 on git failure so a transient error doesn't promote clean → WIP.
    """
    res = _git(wt_path, "status", "--porcelain")
    if res.returncode != 0:
        return 0
    return sum(1 for line in res.stdout.splitlines() if line.strip())


def _count_unpushed(wt_path: Path) -> int:
    """Commits on HEAD whose patch content is not yet on origin's default branch.

    Uses `git cherry` so individually cherry-picked commits (same content,
    different SHA upstream) are recognized as already-landed. Each output line
    is `+ <sha>` for an unmerged commit, `- <sha>` for one whose patch is
    already upstream — we count only `+` lines.

    GitHub squash-merges are NOT recognized by `git cherry`: N commits are
    collapsed into a single upstream commit with a combined patch-id that
    matches none of the originals. Autoclose handles that case separately via
    `is_ancestor(wt, headRefOid)` from `fetch_merged_branches`; this function
    intentionally over-counts there so `/cockpit:list` and `/cockpit:close`
    still surface "this branch hasn't been pushed" honestly.

    Returns 0 if the default branch (whatever `origin/HEAD` points at) cannot
    be resolved. Returns -1 if git fails outright so callers can distinguish
    "verified clean" from "could not check".
    """
    default = origin_head_branch(wt_path)
    if default is None:
        return 0
    res = _git(wt_path, "cherry", f"origin/{default}", "HEAD")
    if res.returncode != 0:
        return -1
    return sum(1 for line in res.stdout.splitlines() if line.startswith("+ "))


def commits_only_local(wt_path: Path, branch: str) -> int:
    """Commits on HEAD whose patch is not present on the branch's own remote.

    Where `_count_unpushed` baselines against origin's *default* branch, this
    baselines against `origin/<branch>` — so a pushed-but-unmerged branch reads
    as fully pushed. Used to decide whether tearing down someone else's PR
    worktree would lose anything: if every commit is already on
    `origin/<branch>`, nothing exists only locally and removal is safe (teardown
    is local-only and never touches the remote branch).

    Falls back to `_count_unpushed` when `origin/<branch>` cannot be resolved,
    so a never-pushed branch still counts as having unpushed work. Returns -1
    if git fails outright.
    """
    ref = f"origin/{branch}"
    if _git(wt_path, "rev-parse", "--verify", "--quiet", ref).returncode != 0:
        return _count_unpushed(wt_path)
    res = _git(wt_path, "cherry", ref, "HEAD")
    if res.returncode != 0:
        return -1
    return sum(1 for line in res.stdout.splitlines() if line.startswith("+ "))


def _gitdir(wt_path: Path) -> Path | None:
    res = _git(wt_path, "rev-parse", "--git-dir")
    if res.returncode != 0:
        return None
    raw = res.stdout.strip()
    return Path(raw) if Path(raw).is_absolute() else wt_path / raw


def _rebase_head_name(gitdir: Path) -> str | None:
    """If the worktree is mid-rebase, return the branch being rebased.

    During rebase, `git worktree list --porcelain` reports the worktree as
    `detached`; the original branch name lives in
    <gitdir>/rebase-{merge,apply}/head-name.
    """
    for sub in ("rebase-merge", "rebase-apply"):
        head_name = gitdir / sub / "head-name"
        if head_name.exists():
            content = head_name.read_text().strip()
            if content.startswith("refs/heads/"):
                return content.removeprefix("refs/heads/")
    return None


def worktrees(repo_dir: Path) -> list[Worktree]:
    out = run(["git", "-C", str(repo_dir), "worktree", "list", "--porcelain"])
    blocks = [b for b in out.split("\n\n") if b.strip()]
    try:
        repo_resolved = repo_dir.resolve()
    except OSError:
        repo_resolved = repo_dir
    wts: list[Worktree] = []
    for block in blocks:
        path = branch = None
        detached = False
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line.startswith("branch "):
                branch = line.removeprefix("branch refs/heads/")
            elif line.strip() == "detached":
                detached = True
        if path is None:
            continue
        rebasing = merging = False
        gitdir = _gitdir(path)
        if branch is None and detached and gitdir is not None:
            branch = _rebase_head_name(gitdir)
            rebasing = branch is not None
        if gitdir is not None and (gitdir / "MERGE_HEAD").exists():
            merging = True
        if branch is not None:
            try:
                path_resolved = path.resolve()
            except OSError:
                path_resolved = path
            is_primary = path_resolved == repo_resolved
            wts.append(
                Worktree(
                    path=path,
                    branch=branch,
                    rebasing=rebasing,
                    merging=merging,
                    is_primary=is_primary,
                )
            )

    def _stats(w: Worktree) -> tuple[int, int]:
        return count_dirty(w.path), _count_unpushed(w.path)

    with ThreadPoolExecutor(max_workers=max(1, len(wts))) as ex:
        for wt, (d, u) in zip(wts, ex.map(_stats, wts), strict=False):
            wt.dirty_count, wt.unpushed = d, u
    return wts


def _rev_list_count(cwd: str | os.PathLike, rev_range: str, *, fail: int = 0) -> int:
    """`git rev-list --count <range>` → int, with `fail` returned on any error."""
    res = _git(cwd, "rev-list", "--count", rev_range)
    if res.returncode != 0:
        return fail
    out = res.stdout.strip()
    return int(out) if out.isdigit() else fail


def is_ancestor(wt_path: Path, sha: str) -> bool:
    """True if `sha` is reachable from the worktree's current HEAD.

    Used by autoclose to decide whether a merged PR's head (the `headRefOid`
    recorded by `fetch_merged_branches`) is still contained in the worktree.
    This is the right signal for "is the merged work done here", and it
    distinguishes the three lifecycle states a `rev-list`-count gate cannot:

    - HEAD == merge head (merged, untouched) → ancestor (reflexive) → reap.
    - merge head is a parent of HEAD (squash-merge then `git pull` main on top)
      → ancestor → reap. This is the case `count_commits_since(..) == 0` got
      wrong: pulling main advances HEAD, so the count is > 0 and the worktree
      would never autoclose.
    - merge head NOT reachable from HEAD (the branch name was reused — reset or
      re-created on a different lineage for new work after the old PR merged)
      → not an ancestor → keep. This is the case the presence-only check got
      wrong: it nuked a freshly re-created worktree minutes after creation.

    `git merge-base --is-ancestor A B` exits 0 when A is an ancestor of (or
    equal to) B, 1 when it is not, and >1 on error (e.g. `sha` unknown locally).
    Returns False on anything but a clean exit-0 so an unresolvable SHA never
    triggers a teardown.
    """
    return _git(wt_path, "merge-base", "--is-ancestor", sha, "HEAD").returncode == 0


def has_unique_commits(wt_path: Path, base: str) -> bool:
    """True if the worktree has committed work not in `base`.

    Used to filter empty scaffolds (fresh worktrees at base HEAD) when computing
    drift. Uncommitted dirt does not count as work for this check.
    """
    return _rev_list_count(wt_path, f"{base}..HEAD") > 0


def current_branch(cwd: str | os.PathLike) -> str:
    """Branch name, or "" if not in a git repo or fully detached.

    Recovers the original branch when detached mid-rebase by reading
    rebase-{merge,apply}/head-name from the worktree's gitdir.
    """
    res = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if res.returncode != 0:
        return ""
    branch = res.stdout.strip()
    if branch and branch != "HEAD":
        return str(branch)
    gitdir = _gitdir(Path(cwd))
    return _rebase_head_name(gitdir) or "" if gitdir else ""


def repo_state(cwd: str | os.PathLike) -> str:
    """`'rebase'`, `'merge'`, or `''` for the working tree at `cwd`."""
    gitdir = _gitdir(Path(cwd))
    if gitdir is None:
        return ""
    if (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists():
        return "rebase"
    if (gitdir / "MERGE_HEAD").exists():
        return "merge"
    return ""


def slugify(s: str, max_len: int = 30) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:max_len]


def collision_free(path: Path) -> Path:
    """Return `path` if free; otherwise append -2/-3/... until unused."""
    if not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.name}-{i}")
        if not cand.exists():
            return cand
        i += 1


def main_worktree_path(cwd: str | os.PathLike | None = None) -> Path | None:
    """Return the main (first) worktree path, or None if not in a git repo."""
    res = _git(cwd if cwd is not None else ".", "worktree", "list", "--porcelain")
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line.split(" ", 1)[1]).resolve()
    return None


def worktree_for_branch(repo_dir: Path, branch: str) -> Path | None:
    for wt in worktrees(repo_dir):
        if wt.branch == branch and wt.path.exists():
            return wt.path
    return None


def _has_local_branch(repo: Path, branch: str) -> bool:
    return (
        _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode
        == 0
    )


def _fetch_remote_branch(repo: Path, branch: str) -> bool:
    """Return True and fetch branch locally if it exists on origin.

    Uses the full `refs/heads/{branch}` ref so ls-remote's suffix matching
    cannot falsely claim a remote exists (e.g. `cship` would otherwise match
    `refs/heads/*/cship` and trip a follow-up `fetch origin cship:cship`).
    """
    exists = (
        _git(
            repo,
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
        ).returncode
        == 0
    )
    if exists:
        run(["git", "-C", str(repo), "fetch", "origin", f"{branch}:{branch}"])
    return exists


def _has_remote_branch(repo: Path, branch: str) -> bool:
    """True if `refs/heads/{branch}` exists on origin (exact match)."""
    return (
        _git(
            repo,
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
        ).returncode
        == 0
    )


def create_worktree(
    repo: Path,
    branch: str,
    wt_path: Path,
    *,
    base: str,
    pr_num: str | None = None,
    branch_prefix: str = "",
) -> str:
    """Create a worktree at `wt_path` for `branch`. Returns the final branch name.

    Resolution order:
      1. PR num         → fetch pull/{N}/head into local ref
      2. local          → branch already exists locally
      3. remote         → fetch from origin into local ref
      4. local-prefixed → `{branch_prefix}{branch}` already exists locally,
                          left behind by a forced teardown — attach to it
                          rather than crashing on `-b`
      5. new            → create from origin/{base} (prefix applied to short names)
    """
    if pr_num:
        run(
            [
                "git",
                "-C",
                str(repo),
                "fetch",
                "origin",
                f"+refs/pull/{pr_num}/head:refs/heads/{branch}",
            ]
        )
        run(["git", "-C", str(repo), "worktree", "add", str(wt_path), branch])
        return branch

    _git(repo, "fetch", "origin", base)

    if _has_local_branch(repo, branch) or _fetch_remote_branch(repo, branch):
        run(["git", "-C", str(repo), "worktree", "add", str(wt_path), branch])
        return branch

    full_branch = (
        f"{branch_prefix}{branch}" if branch_prefix and "/" not in branch else branch
    )
    if full_branch != branch and _has_local_branch(repo, full_branch):
        run(["git", "-C", str(repo), "worktree", "add", str(wt_path), full_branch])
        return full_branch
    run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-b",
            full_branch,
            str(wt_path),
            f"origin/{base}",
        ]
    )
    return full_branch


def branch_exists(repo: Path, branch: str) -> bool:
    """True if `branch` exists locally or on origin (exact ref match)."""
    return _has_local_branch(repo, branch) or _has_remote_branch(repo, branch)


def create_new_branch_worktree(
    repo: Path, branch: str, wt_path: Path, *, base: str
) -> str:
    """Create a worktree at `wt_path` on a *new* branch `branch` cut from `origin/{base}`.

    Caller has already ensured `branch` does not exist locally, remotely, or
    as a worktree. Skips the local/remote-attach resolution that
    `create_worktree` performs. Returns the branch name (unchanged).
    """
    _git(repo, "fetch", "origin", base)
    run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-b",
            branch,
            str(wt_path),
            f"origin/{base}",
        ]
    )
    return branch


def _log_lock_reason(repo: Path, wt_path: Path) -> None:
    """Best-effort: print `preempting <lock-reason>` to stderr for a locked worktree.

    Resolves the admin dir via the worktree's own `.git` file
    (`gitdir: <repo>/.git/worktrees/<wt-name>`), falling back to `wt_path.name`.
    Swallows FS read errors so a lock log never blocks remove_worktree.
    """
    try:
        wt_name = wt_path.name
        dotgit = wt_path / ".git"
        if dotgit.is_file():
            content = dotgit.read_text().strip()
            for line in content.splitlines():
                if line.startswith("gitdir:"):
                    gitdir = line.split(":", 1)[1].strip()
                    wt_name = Path(gitdir).name
                    break
        lock_file = Path(repo) / ".git" / "worktrees" / wt_name / "locked"
        if lock_file.exists():
            reason = lock_file.read_text().strip()
            print(f"preempting {reason}", file=sys.stderr)
    except (OSError, UnicodeDecodeError):
        return


def remove_worktree(
    repo: Path, wt_path: Path, *, force: bool = False
) -> tuple[bool, str]:
    """Run `git worktree remove`. Returns (ok, stderr) — non-raising.

    With `force=True`, passes `--force --force` so git overrides its refusal
    to remove a locked worktree, and logs the lock reason (if any) to stderr
    first so the operator sees what was preempted.
    """
    args = ["worktree", "remove"]
    if force:
        _log_lock_reason(repo, wt_path)
        args.extend(["--force", "--force"])
    args.append(str(wt_path))
    res = _git(repo, *args)
    return res.returncode == 0, res.stderr.strip()


def ahead_of_origin(cwd: str | os.PathLike, branch: str) -> int:
    """Commits HEAD is ahead of `origin/{branch}`. Returns 0 on any failure
    (no remote ref, branch never pushed, git error). The footer pill
    treats 0 as "nothing to show", which is the right behavior in all
    failure modes.
    """
    if not branch:
        return 0
    return _rev_list_count(cwd, f"origin/{branch}..HEAD")


def behind_of_origin(cwd: str | os.PathLike, branch: str) -> int:
    """Commits HEAD is behind `origin/{branch}`. Returns 0 on any failure."""
    if not branch:
        return 0
    return _rev_list_count(cwd, f"HEAD..origin/{branch}")


def behind_of_base(cwd: str | os.PathLike, base: str) -> int:
    """Commits HEAD is behind `origin/{base}` — rebase-staleness vs the
    default branch. Returns 0 on any failure (no remote ref, base unknown,
    git error). Caller is responsible for fetching `origin/{base}` first;
    this function does not hit the network.
    """
    if not base:
        return 0
    return _rev_list_count(cwd, f"HEAD..origin/{base}")


def ahead_of_base(cwd: str | os.PathLike, base: str) -> int:
    """Commits HEAD is ahead of `origin/{base}` — branch divergence from
    the default branch. Returns 0 on any failure. Like `behind_of_base`,
    this is a local rev-list; caller is responsible for any prior fetch.
    """
    if not base:
        return 0
    return _rev_list_count(cwd, f"origin/{base}..HEAD")


class GitStatusCounts(NamedTuple):
    staged: int
    unstaged: int
    untracked: int


def count_status(wt_path: Path) -> GitStatusCounts:
    """Parse `git status --porcelain` once and return staged/unstaged/untracked counts.

    Porcelain v1 format: two status chars `XY` followed by space and path.
    X = index status, Y = worktree status. `??` marks untracked.
    """
    res = _git(wt_path, "status", "--porcelain")
    if res.returncode != 0:
        return GitStatusCounts(0, 0, 0)
    staged = unstaged = untracked = 0
    for line in res.stdout.splitlines():
        if len(line) < 2:
            continue
        xy = line[:2]
        if xy == "??":
            untracked += 1
            continue
        if line[0] in "MADRC":
            staged += 1
        if line[1] in "MD":
            unstaged += 1
    return GitStatusCounts(staged, unstaged, untracked)


def origin_head_branch(repo: Path) -> str | None:
    """Return the branch `origin/HEAD` points at (e.g. 'main'), or None."""
    r = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if r.returncode != 0:
        return None
    return r.stdout.strip().removeprefix("origin/") or None


def ff_default_branch_worktrees(
    repo: Path, wts: list[Worktree], *, dry: bool = False
) -> list[tuple[Worktree, int]]:
    """Fast-forward each clean worktree on the repo's `origin/HEAD` branch.

    Returns the (worktree, behind_count) entries that were fast-forwarded — or
    would be, when `dry=True`. Skips dirty worktrees and non-default branches.
    Uses `--ff-only` so non-fast-forward histories no-op silently.
    """
    default = origin_head_branch(repo)
    if default is None:
        return []
    advanced: list[tuple[Worktree, int]] = []
    for wt in wts:
        if wt.branch != default or wt.dirty_count > 0:
            continue
        if _git(wt.path, "fetch", "origin", wt.branch).returncode != 0:
            continue
        behind = _rev_list_count(wt.path, f"HEAD..origin/{wt.branch}", fail=-1)
        if behind <= 0:
            continue
        advanced.append((wt, behind))
        if dry:
            continue
        _git(wt.path, "merge", "--ff-only", f"origin/{wt.branch}")
    return advanced


def log_ff_advances(advances: list[tuple[Worktree, int]], *, dry: bool = False) -> None:
    """Print one `ff-main` line per (worktree, behind) from
    `ff_default_branch_worktrees`. Shared by cockpit's per-cycle log and
    teardown's post-close chore so the rendering stays in sync.
    """
    from .colors import dim
    from .log_format import verb

    action = "[dry] ff-main" if dry else "ff-main"
    for wt, behind in advances:
        plural = "s" if behind != 1 else ""
        print(
            f"  {verb(action)} {wt.short} → origin/{wt.branch}"
            f"  {dim(f'{behind} commit{plural}')}",
            flush=True,
        )
