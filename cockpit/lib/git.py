"""Git/worktree helpers."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
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
    branch_prefix: str = ""

    @property
    def short(self) -> str:
        """Worktree dir basename — the path handle used for cwd→worktree
        matching and the `git worktree remove` target. NOT the sidebar label;
        see `label`."""
        return self.path.name

    @property
    def label(self) -> str:
        """Branch-derived sidebar/workspace label (see `branch_label`).

        Distinct from `short`: the label tracks the *branch* so a worktree dir
        renamed out from under its branch still gets a branch-true name. Empty
        for a detached worktree (no branch). `branch_prefix` is threaded in at
        construction (`worktrees`/`worktrees_basic`) from the repo config.
        """
        return branch_label(self.branch, self.branch_prefix)

    @property
    def dirty(self) -> bool:
        return self.dirty_count > 0


def worktree_age_seconds(path: Path, *, now: float | None = None) -> float:
    """Seconds since the worktree directory was created.

    Reads the directory's birth time (`st_birthtime`, stamped by `git worktree
    add`) where the platform exposes it (macOS/BSD); falls back to the inode
    change time (`st_ctime`) on Linux, where `os.stat` surfaces no creation time.
    Derived from the filesystem on every call — never stored, honouring the
    inventory-is-derived rule.

    Returns `inf` when the path can't be stat'd, so a transient filesystem error
    fails open to the original always-nudge behaviour rather than silently
    muting the orphan nudge forever.
    """
    try:
        st = path.stat()
    except OSError:
        return float("inf")
    created = getattr(st, "st_birthtime", None)
    if created is None:
        created = st.st_ctime
    ref = time.time() if now is None else now
    return max(0.0, ref - created)


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
    intentionally over-counts there so the `cockpit watch` table and its close
    actions still surface "this branch hasn't been pushed" honestly.

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


def worktrees_basic(repo_dir: Path, branch_prefix: str = "") -> list[Worktree]:
    """List worktrees by structure only — path/branch/rebasing/merging/is_primary.

    `dirty_count` and `unpushed` are left at 0; this skips the per-worktree
    `git status` + `git cherry` forks that `worktrees()` runs. Use it when the
    caller only needs identity (path/branch), e.g. the orphan-workspace reap.

    `branch_prefix` (the repo's configured prefix) is stored on each Worktree so
    its `label` strips the prefix cleanly; callers that don't render a label can
    leave it "".
    """
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
                    branch_prefix=branch_prefix,
                )
            )
    return wts


def worktrees(repo_dir: Path, branch_prefix: str = "") -> list[Worktree]:
    """Full worktree listing with dirty/unpushed counts filled in.

    Layers the per-worktree `count_dirty` + `_count_unpushed` stats (run in
    parallel) onto `worktrees_basic`. Callers that don't need the counts
    should use `worktrees_basic` to skip those forks. `branch_prefix` is passed
    through to `worktrees_basic` for the `label` strip.
    """
    wts = worktrees_basic(repo_dir, branch_prefix)

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


def branch_commits_ahead(repo: Path, base: str, branch: str) -> int:
    """Commits on `branch` not contained in `base`, via
    `git rev-list --count {base}..{branch}`.

    Operates on a branch ref directly (not the worktree's HEAD, unlike
    `has_unique_commits`) so it works for branches that have no checked-out
    worktree — the case the daemon's branch-ref reaper handles. Returns -1 on
    git failure (unknown `base` SHA, bad ref) so a caller can refuse to delete
    on an unverifiable result rather than treating it as "0 commits, safe".
    """
    return _rev_list_count(repo, f"{base}..{branch}", fail=-1)


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
    # rstrip again after truncation: the cap can land mid-separator and leave a
    # trailing "-" that the pre-truncation strip("-") above didn't catch.
    return s[:max_len].rstrip("-")


# A leading base-branch segment (`master/`, `main/`) — it denotes the branch's
# base, not anything identifying, so it is dropped from the label. The trailing
# `/` is required so `mainframe-thing` / a branch literally named `master` are
# never touched. Same trunk set as `constants.MAIN_BRANCHES`, here as a
# prefix-stripping regex rather than a membership set.
_BASE_BRANCH_SEG_RE = re.compile(r"^(?:master|main)/")

# A leading Linear-ticket (`pe-4608-`) or bare PR/issue number (`123-`) token
# at the head of a slugified branch. The `(?=.)` lookahead refuses to strip when
# nothing descriptive follows, so a bare-ticket branch (`pe-4516`) keeps its id
# rather than collapsing to "".
_LEADING_TICKET_RE = re.compile(r"^(?:[a-z]+-\d+|\d+)-(?=.)")


def branch_label(branch: str, branch_prefix: str = "") -> str:
    """Sidebar/workspace label derived from a branch name.

    Four steps:
      1. Strip the repo's configured `branch_prefix` (e.g. `khivi/`) when the
         branch carries it.
      2. Drop a leading base-branch segment (`master/`, `main/`) — it marks the
         branch's base, not anything identifying.
      3. Slugify the remainder — collapsing any surviving `/` to `-` so a
         multi-segment branch keeps its remaining identity.
      4. Drop a leading ticket/PR token (`pe-4608-`, `123-`) so the label reads
         as the human description, NOT the tracker id — but only when something
         descriptive follows (a bare-ticket branch keeps its id).

        khivi/pe-4608-understand-dag-builder  →  understand-dag-builder
        khivi/123-fix-login-bug               →  fix-login-bug
        khivi/pe-4516            (no desc)     →  pe-4516
        khivi/master/fnox-age                 →  fnox-age
        feature/thing            (no prefix)  →  feature-thing
        ""                       (detached)   →  ""

    `branch_prefix` defaults to "" so a caller without repo config still gets a
    slugified, ticket-stripped branch, just with the user prefix left on.
    """
    if branch_prefix and branch.startswith(branch_prefix):
        branch = branch[len(branch_prefix) :]
    branch = _BASE_BRANCH_SEG_RE.sub("", branch)
    # Normalize without meaningful truncation, strip the leading ticket, then
    # apply the real 30-char cap so truncation never eats the description tail.
    slug = slugify(branch, max_len=200)
    slug = _LEADING_TICKET_RE.sub("", slug)
    return slug[:30]


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


def has_remote_branch(repo: Path, branch: str) -> bool:
    """True if `refs/heads/{branch}` exists on origin (exact match).

    Public surface for the daemon's branch-ref reaper; thin pass-through over
    the internal check `branch_exists` also shares.
    """
    return _has_remote_branch(repo, branch)


def list_local_branches(repo: Path) -> list[str]:
    """All local branch short names (`refs/heads/*`). Empty list on git failure.

    `for-each-ref` rather than `git branch` so the output is plain ref names with
    no current-branch `*` marker or column padding to strip.
    """
    res = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if res.returncode != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


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


def delete_local_branch(repo: Path, branch: str) -> tuple[bool, str]:
    """Delete `branch` from the local repo with `git branch -D`. Returns
    (ok, stderr) — non-raising, mirroring `remove_worktree`'s contract.

    Uses `-D` (force) rather than `-d` deliberately. `-d` only deletes a branch
    git considers merged into the *current* HEAD or its upstream; teardown runs
    from the main checkout (HEAD = the default branch), and a squash- or rebase-
    merged feature branch has different SHAs than the default branch, so `-d`
    would refuse for the common merge case (this is the same patch-id blind spot
    `_count_unpushed` documents). Callers establish the merge via the
    authoritative `gh pr list --state merged` signal and gate on there being no
    post-merge local commits before reaching here, so `-D` is the right tool.
    """
    res = _git(repo, "branch", "-D", branch)
    return res.returncode == 0, res.stderr.strip()


def prune_worktrees(repo: Path) -> None:
    """Run `git worktree prune` — drop admin entries for deleted directories.

    A worktree directory removed on disk out-of-band (manual `rm`, an OS
    tmpdir wipe) leaves a stale entry in `.git/worktrees/` that `worktrees()`
    still reports. Pruning before each cycle reads the list keeps downstream
    teardown/autoclose from acting on a path that no longer exists.

    Non-raising: a failed prune leaves the stale entry in place (the pre-fix
    behaviour) and logs a warning rather than aborting the cycle. Prune only
    removes entries whose directory is gone, so it can never drop a live
    worktree.
    """
    res = _git(repo, "worktree", "prune")
    if res.returncode != 0:
        print(
            f"  warn: git worktree prune failed for {repo.name}: {res.stderr.strip()}",
            file=sys.stderr,
            flush=True,
        )


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
    repo: Path, wts: list[Worktree], *, default: str | None = None, dry: bool = False
) -> list[tuple[Worktree, int]]:
    """Fast-forward each clean worktree on the repo's `origin/HEAD` branch.

    Returns the (worktree, behind_count) entries that were fast-forwarded — or
    would be, when `dry=True`. Skips dirty worktrees and non-default branches.
    Uses `--ff-only` so non-fast-forward histories no-op silently.

    `default` lets a caller that already resolved `origin/HEAD` (the slow cycle)
    pass it in to avoid a redundant `symbolic-ref`; left None it resolves here.
    """
    if default is None:
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
