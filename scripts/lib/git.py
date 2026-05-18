"""Git/worktree helpers."""

from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import run


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
    """Commits on HEAD not reachable from any remote-tracking ref.

    A commit is "safe" if it's reachable from at least one `refs/remotes/*`
    ref — its own pushed branch, origin/main, or any other remote. This
    handles squash-merged PRs (HEAD still equals origin/<branch> even though
    origin/main has a different SHA) and misconfigured upstreams (e.g.
    @{upstream} pointing at origin/main instead of the branch's own remote).

    Returns 0 if there are no remote refs at all (nothing to push against;
    treat as safe). Returns -1 if git fails outright so callers can
    distinguish "verified clean" from "could not check".
    """
    refs_res = _git(wt_path, "for-each-ref", "--format=%(refname)", "refs/remotes/")
    if refs_res.returncode != 0:
        return -1
    remote_refs = [
        r for r in refs_res.stdout.splitlines() if r and not r.endswith("/HEAD")
    ]
    if not remote_refs:
        return 0
    res = _git(wt_path, "rev-list", "--count", "HEAD", "--not", *remote_refs)
    if res.returncode != 0:
        return -1
    out = res.stdout.strip()
    return int(out) if out.isdigit() else -1


def _gitdir(wt_path: Path) -> Path | None:
    try:
        gitdir_raw = run(["git", "-C", str(wt_path), "rev-parse", "--git-dir"]).strip()
    except RuntimeError:
        return None
    return Path(gitdir_raw) if Path(gitdir_raw).is_absolute() else wt_path / gitdir_raw


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
                return content[len("refs/heads/") :]
    return None


def worktrees(repo_dir: Path) -> list[Worktree]:
    out = run(["git", "-C", str(repo_dir), "worktree", "list", "--porcelain"])
    blocks = [b for b in out.split("\n\n") if b.strip()]
    wts: list[Worktree] = []
    for block in blocks:
        path = branch = None
        detached = False
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line[len("worktree ") :])
            elif line.startswith("branch "):
                branch = line[len("branch refs/heads/") :]
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
            wts.append(
                Worktree(path=path, branch=branch, rebasing=rebasing, merging=merging)
            )
    with ThreadPoolExecutor(max_workers=max(1, len(wts))) as ex:
        dirty = list(ex.map(lambda w: count_dirty(w.path), wts))
        unpushed = list(ex.map(lambda w: _count_unpushed(w.path), wts))
    for wt, d, u in zip(wts, dirty, unpushed):
        wt.dirty_count = d
        wt.unpushed = u
    return wts


def has_unique_commits(wt_path: Path, base: str) -> bool:
    """True if the worktree has committed work not in `base`.

    Used to filter empty scaffolds (fresh worktrees at base HEAD) when computing
    drift. Uncommitted dirt does not count as work for this check.
    """
    res = _git(wt_path, "rev-list", "--count", f"{base}..HEAD")
    if res.returncode != 0:
        return False
    out = res.stdout.strip()
    return out.isdigit() and int(out) > 0


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
        return branch
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
    """Return True and fetch branch locally if it exists on origin."""
    exists = (
        _git(repo, "ls-remote", "--exit-code", "--heads", "origin", branch).returncode
        == 0
    )
    if exists:
        run(["git", "-C", str(repo), "fetch", "origin", f"{branch}:{branch}"])
    return exists


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
      1. PR num  → fetch pull/{N}/head into local ref
      2. local   → branch already exists locally
      3. remote  → fetch from origin into local ref
      4. new     → create from origin/{base} (prefix applied to short names)
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


def remove_worktree(
    repo: Path, wt_path: Path, *, force: bool = False
) -> tuple[bool, str]:
    """Run `git worktree remove`. Returns (ok, stderr) — non-raising."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(wt_path))
    res = _git(repo, *args)
    return res.returncode == 0, res.stderr.strip()


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
        rev = _git(wt.path, "rev-list", "--count", f"HEAD..origin/{wt.branch}")
        try:
            behind = int(rev.stdout.strip())
        except ValueError:
            continue
        if behind == 0:
            continue
        advanced.append((wt, behind))
        if dry:
            continue
        _git(wt.path, "merge", "--ff-only", f"origin/{wt.branch}")
    return advanced
