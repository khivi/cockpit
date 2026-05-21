"""cship cache writer + field printers used by starship.toml [custom.*] modules.

Replaces the retired `~/bin/cship/cship-*.sh` shell helpers with a single
in-repo Python module. The 8 `[custom.*]` blocks in
`scripts/defaults/starship.toml` invoke `scripts/cship.py <field>` which
calls `print_<field>()` here.

Three writer paths populate the cache files those printers read:
- `lib.claude.stash_from_stdin`  : session-scoped (context, rate-limit, transcript)
- `refresh_pr_data` / `refresh_pr_checks` (60s stale-triggered background)
- `write_branch_pr_cache`        : daemon tick (every poll_interval_seconds)

Readers:
- `print_context` / `print_session_time` / `print_rate_limit` /
  `print_linear` / `print_pr_state` / `print_pr_num` / `print_pr_checks` /
  `print_pr_title` — each returns a string and never raises.

`invoke_cship` is the binary-exec helper used by the statusLine entry
point (scripts/claude.py) once `lib.claude.stash_from_stdin` has captured
the session caches.

Cache files live under `$TMPDIR/cship-cache/`, same scheme the historical
shell scripts used (file-mtime-based 60s TTL, atomic .tmp→final rename).
"""

from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CACHE_DIR = Path(tempfile.gettempdir()) / "cship-cache"
PR_CACHE_TTL_SECS = 60
SESSION_TIME_MIN_SECS = 10
LINEAR_RE = re.compile(r"[A-Z]{2,6}-[0-9]+")


def _ensure_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def _atomic_write(path: Path, payload: str) -> None:
    """Write `payload` to `path` atomically via .tmp + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    os.replace(tmp, path)


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _branch_key(branch: str) -> str:
    return branch.replace("/", "-")


def _session_suffix(sid: str | None) -> str:
    return f"-{sid}" if sid else ""


def _session_cache(stem: str, sid: str | None) -> Path:
    return _ensure_cache_dir() / f"{stem}{_session_suffix(sid)}"


def _branch_cache(stem: str, branch: str) -> Path:
    return _ensure_cache_dir() / f"{stem}-{_branch_key(branch)}"


def _current_branch() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if res.returncode != 0:
        return ""
    return res.stdout.strip()


def _fresh(path: Path, ttl_secs: int = PR_CACHE_TTL_SECS) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < ttl_secs
    except OSError:
        return False


# ── cship binary invocation ────────────────────────────────────────────────


CSHIP_BIN = "cship"


def invoke_cship(blob: bytes, sid: str | None) -> int:
    """Exec the cship binary with `blob` piped to stdin; forward output.

    Used by the statusLine entry point (`scripts/claude.py`) after the
    Claude Code-side caches have been stashed via `lib.claude.stash_from_stdin`.

    If cship isn't on PATH, returns 0 silently — the statusline must never
    crash Claude Code. The loud opt-in check is in
    `lib.config.install_cship_statusline_if_configured`.

    Exports `CSHIP_SESSION_ID=<sid>` so the field-printer subprocesses
    starship spawns under cship find the session-scoped cache entries.
    """
    if shutil.which(CSHIP_BIN) is None:
        return 0
    env = os.environ.copy()
    if sid:
        env["CSHIP_SESSION_ID"] = sid
    res = subprocess.run([CSHIP_BIN], input=blob, capture_output=True, env=env)
    if res.stdout:
        sys.stdout.buffer.write(res.stdout)
    if res.stderr:
        sys.stderr.buffer.write(res.stderr)
    return res.returncode


# ── PR-side cache writers ──────────────────────────────────────────────────


def _gh_pr_view() -> dict | None:
    """Call `gh pr view --json ...` for the current branch, return parsed
    JSON dict or None on any failure / no PR.
    """
    try:
        res = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                "--json",
                "state,isDraft,reviewDecision,number,title",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return None


def _resolve_state(state: str, is_draft: bool, review: str) -> str:
    if state == "OPEN":
        if is_draft:
            return "DRAFT"
        if review:
            return review
    return state


def refresh_pr_data(branch: str) -> None:
    """Populate pr-state / pr-num / pr-title caches for `branch` from one
    `gh pr view` round-trip. Empty (no-PR) sentinel = zero-byte file with a
    fresh mtime; that suppresses per-render gh calls during the 60s TTL.
    """
    if not branch:
        return
    data = _gh_pr_view()
    state_path = _branch_cache("pr-state", branch)
    num_path = _branch_cache("pr-num", branch)
    title_path = _branch_cache("pr-title", branch)
    if data is None:
        _atomic_write(state_path, "")
        _atomic_write(num_path, "")
        _atomic_write(title_path, "")
        return
    state = _resolve_state(
        str(data.get("state") or ""),
        bool(data.get("isDraft")),
        str(data.get("reviewDecision") or ""),
    )
    number = data.get("number")
    title = data.get("title") or ""
    _atomic_write(state_path, state)
    _atomic_write(num_path, str(number) if number else "")
    _atomic_write(title_path, str(title))


def refresh_pr_checks(branch: str) -> None:
    """Populate pr-checks cache for `branch` from one `gh pr checks` call.

    Exit codes (per gh): 0 → ✓, 8 → • (pending), other → ✗ (failing or no
    runs). Empty payload when no PR is associated with the branch.
    """
    if not branch:
        return
    cache = _branch_cache("pr-checks", branch)
    view = subprocess.run(
        ["gh", "pr", "view", "--json", "number"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if view.returncode != 0:
        _atomic_write(cache, "")
        return
    checks = subprocess.run(
        ["gh", "pr", "checks"], capture_output=True, text=True, timeout=10
    )
    glyph = {0: "✓", 8: "•"}.get(checks.returncode, "✗")
    _atomic_write(cache, glyph)


def warm_all(branch: str | None = None) -> None:
    """Synchronous prewarm for the current branch: PR data + checks + seed a
    transcript-path from the latest project JSONL if Claude Code hasn't yet
    fed one via statusLine input.
    """
    branch = branch or _current_branch()
    if not branch:
        return
    refresh_pr_data(branch)
    refresh_pr_checks(branch)
    _seed_transcript_from_project_dir()


def _seed_transcript_from_project_dir() -> None:
    """Pre-seed transcript-path cache (session-less) with the most recent
    .jsonl under ~/.claude/projects/<mangled cwd> so session-time has
    something to render on the first statusline tick.
    """
    cwd = os.getcwd()
    mangled = "-" + cwd.lstrip("/").replace("/", "-").replace(".", "-")
    project_dir = Path.home() / ".claude" / "projects" / mangled
    if not project_dir.is_dir():
        return
    candidates = sorted(
        project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not candidates:
        return
    _atomic_write(_session_cache("transcript-path", None), str(candidates[0]))


def write_branch_pr_cache(
    branch: str,
    *,
    state: str,
    is_draft: bool,
    review_decision: str,
    number: int | None,
    title: str,
    ci_glyph: str = "",
) -> None:
    """Daemon-tick entrypoint: write pre-resolved PR fields straight to the
    cship cache, no `gh` round-trip needed. Caller (cockpit.py::cycle_repo)
    already has this data from its own PR fetch.

    `ci_glyph` is empty by default — the per-render background refresh will
    repopulate `pr-checks-<branch>` from `gh pr checks` when stale.
    """
    if not branch:
        return
    resolved = _resolve_state(state, is_draft, review_decision)
    _atomic_write(_branch_cache("pr-state", branch), resolved)
    _atomic_write(_branch_cache("pr-num", branch), str(number) if number else "")
    _atomic_write(_branch_cache("pr-title", branch), title or "")
    if ci_glyph:
        _atomic_write(_branch_cache("pr-checks", branch), ci_glyph)


def _spawn_background_refresh(field: str) -> None:
    """Fire-and-forget background refresh by re-invoking `cship.py <field>-refresh`.

    Mirrors the historical `(refresh) >/dev/null 2>&1 &` pattern. The child
    is detached via start_new_session so it survives the parent's exit and
    starship's render budget is preserved.
    """
    cship_py = Path(__file__).resolve().parent.parent / "cship.py"
    try:
        subprocess.Popen(
            [sys.executable, str(cship_py), f"{field}-refresh"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


# ── field printers ─────────────────────────────────────────────────────────


def print_context(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    cache = _session_cache("context", sid)
    raw = _read(cache)
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) != 2:
        return ""
    pct_s, limit_s = parts
    try:
        pct = int(pct_s)
        limit = int(limit_s)
    except ValueError:
        return ""
    if limit <= 0:
        return ""
    if limit >= 1_000_000:
        ceiling = "1M"
    elif limit >= 1_000:
        ceiling = f"{limit // 1000}k"
    else:
        ceiling = str(limit)
    return f"{pct}%/{ceiling}"


def print_session_time(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    transcript = _read(_session_cache("transcript-path", sid))
    if not transcript:
        return ""
    transcript_path = Path(transcript)
    if not transcript_path.is_file():
        return ""
    first_ts = _first_timestamp(transcript_path)
    if not first_ts:
        return ""
    start_epoch = _parse_iso_epoch(first_ts)
    if start_epoch is None:
        return ""
    total = int(time.time()) - start_epoch
    if total < SESSION_TIME_MIN_SECS:
        return ""
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m"
    return f"{s}s"


def _first_timestamp(transcript: Path) -> str | None:
    """Return the first `timestamp` field encountered in the transcript JSONL.

    Streams line by line — transcripts can be megabytes. Matches the
    historical `jq -rs 'map(.. | objects | .timestamp? // empty) | first'`
    behavior to a useful approximation (top-level `.timestamp` on each
    record, which is where Claude Code puts it).
    """
    try:
        with transcript.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _find_first_timestamp(rec)
                if ts:
                    return ts
    except OSError:
        return None
    return None


def _find_first_timestamp(obj) -> str | None:
    if isinstance(obj, dict):
        ts = obj.get("timestamp")
        if isinstance(ts, str) and ts:
            return ts
        for v in obj.values():
            found = _find_first_timestamp(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_first_timestamp(v)
            if found:
                return found
    return None


def _parse_iso_epoch(ts: str) -> int | None:
    """Parse an ISO 8601 timestamp into a UTC epoch seconds int.

    Strips fractional seconds and trailing 'Z' so `time.strptime` accepts
    both `2024-01-02T03:04:05Z` and `2024-01-02T03:04:05.123Z`. Uses
    `calendar.timegm` (inverse of `time.gmtime`) so the timestamp is
    interpreted as UTC regardless of the host's local timezone.
    """
    clean = ts.split(".", 1)[0].rstrip("Z")
    try:
        return calendar.timegm(time.strptime(clean, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


def print_rate_limit(sid: str | None = None) -> str:
    sid = sid or os.environ.get("CSHIP_SESSION_ID") or None
    raw = _read(_session_cache("rate-limit-5h", sid))
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) < 1:
        return ""
    try:
        pct = int(parts[0])
    except ValueError:
        return ""
    return f"⌛ {pct}%/5h"


def print_linear() -> str:
    branch = _current_branch()
    if not branch:
        return ""
    m = LINEAR_RE.search(branch)
    return m.group(0) if m else ""


def _cached_or_refresh(branch: str, stem: str, field: str) -> str:
    """Return cached payload if fresh, else trigger background refresh and
    still return whatever is on disk (possibly empty / stale).
    """
    cache = _branch_cache(stem, branch)
    if _fresh(cache):
        return _read(cache)
    _spawn_background_refresh(field)
    return _read(cache)


def print_pr_state(branch: str | None = None) -> str:
    branch = branch or _current_branch()
    if not branch:
        return ""
    return _cached_or_refresh(branch, "pr-state", "pr-state")


def print_pr_num(branch: str | None = None) -> str:
    branch = branch or _current_branch()
    if not branch:
        return ""
    raw = _read(_branch_cache("pr-num", branch))
    if not raw or raw in ("0", "null"):
        return ""
    return f"#{raw}"


def print_pr_checks(branch: str | None = None) -> str:
    branch = branch or _current_branch()
    if not branch:
        return ""
    return _cached_or_refresh(branch, "pr-checks", "pr-checks")


def print_pr_title(branch: str | None = None) -> str:
    branch = branch or _current_branch()
    if not branch:
        return ""
    return _read(_branch_cache("pr-title", branch))
