"""Single dependency preflight, called from every `cockpit.py` invocation.

Hard-fails (sys.exit(2)) on missing required binaries:
  - `gh`, `git` — always
  - `cship`, `starship` — when `use_cship: true`

Soft-warns (stderr only) on missing optional backend:
  - `cmux` / `limux` — drops cockpit into cache-only mode

The `spawn.py` entry script still calls `require_workspace_binary()` from
`lib.cmux` for its own backend-mandatory gate; that's a stricter policy than
the daemon needs.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from .colors import yellow
from .tool import resolve_tool

REQUIRED_BINARIES = ("gh", "git")
CSHIP_BINARIES = ("cship", "starship")


def _die(msg: str) -> NoReturn:
    print(f"cockpit: {msg}", file=sys.stderr, flush=True)
    sys.exit(2)


def _validate_sidebar_colors(cfg: dict) -> None:
    """Hard-fail on a repo `sidebar_color` that isn't a cmux color name.

    The field is cosmetic, but a typo is caught here (like the use_cship gate)
    so it surfaces at daemon start with the valid set listed — rather than as a
    silent no-tint discovered cycles later. cmux is imported lazily to keep
    preflight's import graph to the stdlib + leaf colors/tool modules.
    """
    from .cmux import WORKSPACE_COLORS

    for repo in cfg.get("repos", []):
        color = repo.get("sidebar_color")
        if color is None:
            continue
        if color not in WORKSPACE_COLORS:
            name = repo.get("name") or repo.get("path", "?")
            _die(
                f"repo {name!r}: sidebar_color {color!r} is not a cmux color. "
                f"Choose one of: {', '.join(sorted(WORKSPACE_COLORS))}."
            )


# Keys that identify one specific repo, so they can never be an org-wide default.
_ORG_FORBIDDEN_KEYS = ("name", "path", "org")


def _validate_orgs(cfg: dict) -> None:
    """Validate the top-level `orgs` object and every repo's `org` reference.

    An org block is merged into its member repos at load
    (`config.apply_org_defaults`), so its *values* are checked by the same
    per-repo validators as any other repo key — `validate_config` merges before
    running them. What only this can catch is the wiring: a repo pointing at an
    org that isn't defined silently loses every default it expected (no tint, no
    `use_worktree: false`), which is exactly the quiet misconfiguration preflight
    exists to turn into a startup error.
    """
    orgs = cfg.get("orgs")
    if orgs is None:
        orgs = {}
    elif not isinstance(orgs, dict):
        _die(f"orgs must be an object of org-name → defaults, got {orgs!r}.")
    for org_name, block in orgs.items():
        if not isinstance(block, dict):
            _die(
                f"orgs[{org_name!r}] must be an object of repo defaults, "
                f"got {block!r}."
            )
        for key in _ORG_FORBIDDEN_KEYS:
            if key in block:
                _die(
                    f"orgs[{org_name!r}]: {key!r} identifies one repo — "
                    "it can't be an org-wide default."
                )
    for repo in cfg.get("repos", []):
        org = repo.get("org")
        if org is None:
            continue
        name = repo.get("name") or repo.get("path", "?")
        if not isinstance(org, str) or not org:
            _die(f"repo {name!r}: org must be a non-empty string, got {org!r}.")
        if org not in orgs:
            known = ", ".join(sorted(orgs)) or "none defined"
            _die(
                f"repo {name!r}: org {org!r} has no entry in the top-level "
                f"`orgs` object (defined: {known})."
            )


def _validate_repo_bool(cfg: dict, key: str) -> None:
    """Hard-fail on a per-repo `key` that's present but isn't a bool.

    These per-repo switches (`review_prs` spawns review worktrees, `use_worktree`
    false skips all auto-spawning, `dependabot`/`review_external` opt a `review_prs`
    repo into spawning for dependabot / non-collaborator PRs) gate daemon
    behavior, so a non-bool (e.g. a stray string) would be silently truthy —
    rejected at start like `sidebar_color`.
    """
    for repo in cfg.get("repos", []):
        if key in repo and not isinstance(repo[key], bool):
            name = repo.get("name") or repo.get("path", "?")
            _die(f"repo {name!r}: {key} must be true or false, got {repo[key]!r}.")


def _validate_global_bool(cfg: dict, key: str) -> None:
    """Hard-fail on a top-level `key` that's present but isn't a bool.

    `use_slack` (gates the Slack-MCP-fetch spawn prompt) defaults false and
    gates daemon behavior, so a non-bool would be silently truthy — rejected
    like `review_prs`.
    """
    if key in cfg and not isinstance(cfg[key], bool):
        _die(f"{key} must be true or false, got {cfg[key]!r}.")


def _validate_statusline_hide(cfg: dict) -> None:
    """Hard-fail on a `statusline_hide` that isn't a list of known field names.

    A typo'd field would silently hide nothing, so it's rejected at start with
    the valid set listed — same treatment as `sidebar_color`.
    """
    from .config import STATUSLINE_FIELDS

    raw = cfg.get("statusline_hide")
    if raw is None:
        return
    if not isinstance(raw, list):
        _die(f"statusline_hide must be a list of field names, got {raw!r}.")
    for field in raw:
        if not isinstance(field, str) or field not in STATUSLINE_FIELDS:
            _die(
                f"statusline_hide: {field!r} is not a statusline field. "
                f"Choose from: {', '.join(sorted(STATUSLINE_FIELDS))}."
            )


def _validate_field(
    cfg: dict,
    key: str,
    check: Callable[[object, str], None],
    *,
    per_repo_key_suffix: bool = True,
) -> None:
    """Shared top-level-then-per-repo traversal for a scalar config field.

    Runs `check(value, where)` on `cfg[key]` if present, then on `repo[key]`
    for every repo that sets it — the pattern `_validate_review_command` /
    `_validate_base_remote` / `_validate_orphan_nudge_grace` all repeat.
    `check` owns the predicate and the `_die` message; `where` is the
    location prefix ("key" at top level, "repo {name!r}[: key]" per repo).
    `per_repo_key_suffix=False` matches `_validate_orphan_nudge_grace`'s
    existing per-repo `where` (its message already names the key, so the
    `where` prefix omits it to avoid duplication).
    """
    if key in cfg:
        check(cfg[key], key)
    for repo in cfg.get("repos", []):
        if key not in repo:
            continue
        name = repo.get("name") or repo.get("path", "?")
        where = f"repo {name!r}: {key}" if per_repo_key_suffix else f"repo {name!r}"
        check(repo[key], where)


_SKILL_FIELDS = ("session", "review", "plan", "actions")
# Flat keys the `skills` object replaced — a leftover hard-fails with its new
# home, same treatment as `use_linear` → `tickets`.
_LEGACY_SKILL_KEYS = {
    "prompt_prefix": "skills.session",
    "review_command": "skills.review",
}


def _validate_skills(cfg: dict) -> None:
    """Validate the `skills` config (top-level + per-repo).

    `skills` is `{session, review, plan, actions}` — each value a slash command
    seeded verbatim as a workspace's first turn (`session` on every spawn,
    `review` on a `review_prs` spawn, `plan` on a plan-only PR/branch spawn,
    `actions` on a GitHub-Actions-run-URL spawn). A non-`/` value would seed a
    non-command, so it's rejected at start. The legacy flat `prompt_prefix` /
    `review_command` keys are gone; a leftover hard-fails with the new location.
    """

    def _check_source(src: dict, where: str) -> None:
        for old, new in _LEGACY_SKILL_KEYS.items():
            if old in src:
                _die(
                    f"{where}: `{old}` is now `{new}` — move it into a `skills` object."
                )
        block = src.get("skills")
        if block is None:
            return
        if not isinstance(block, dict):
            _die(f"{where}: skills must be an object, got {block!r}.")
        for field, val in block.items():
            if field not in _SKILL_FIELDS:
                _die(
                    f"{where}: unknown skills field {field!r} "
                    f"(allowed: {', '.join(_SKILL_FIELDS)})."
                )
            if not isinstance(val, str) or not val.startswith("/"):
                _die(
                    f"{where}: skills.{field} must be a slash command string "
                    f"(e.g. '/review'), got {val!r}."
                )

    _check_source(cfg, "config")
    for repo in cfg.get("repos", []):
        name = repo.get("name") or repo.get("path", "?")
        _check_source(repo, f"repo {name!r}")


def _validate_base_remote(cfg: dict) -> None:
    """Hard-fail on a `base_remote` (global or per-repo) that isn't a non-empty
    string. It names the git remote the footer ahead/staleness count measures
    against (default `origin`); a blank or non-string value would build a broken
    `/<base>` ref, so it's rejected at start like `review_command`.
    """

    def _check(val: object, where: str) -> None:
        if not isinstance(val, str) or not val.strip():
            _die(f"{where}: base_remote must be a non-empty string, got {val!r}.")

    _validate_field(cfg, "base_remote", _check)


def _validate_tickets(cfg: dict) -> None:
    """Validate the `tickets` config (top-level *and* per-repo).

    `tickets` is the single provider selector that replaced the old boolean
    `use_linear`. It is either the bare string ``none|linear|github`` (shorthand)
    or an object whose accepted fields are owned by each provider — the schema
    lives in `linear.py` / `github_issues.py` (`CONFIG_FIELDS`) and is composed +
    type-checked by `tickets.tickets_field_errors`, which also rejects a field
    that doesn't belong to the chosen provider (a silent typo would otherwise
    disable that setting). Validated here so it surfaces at daemon start.
    """
    from .config import VALID_TICKETS
    from .tickets import tickets_field_errors

    def _check_block(val: object, where: str) -> None:
        if isinstance(val, str):
            provider: object = val
            block: dict = {}
        elif isinstance(val, dict):
            provider = val.get("provider", "none")
            block = val
        else:
            _die(
                f"{where}: tickets must be one of {', '.join(VALID_TICKETS)} "
                f"(or an object with a `provider`), got {val!r}."
            )
        if provider not in VALID_TICKETS:
            _die(
                f"{where}: tickets provider must be one of "
                f"{', '.join(VALID_TICKETS)}, got {provider!r}."
            )
        for err in tickets_field_errors(block, str(provider)):
            _die(f"{where}: {err}")

    if "tickets" in cfg:
        _check_block(cfg["tickets"], "tickets")
    for repo in cfg.get("repos", []):
        if "tickets" not in repo:
            continue
        name = repo.get("name") or repo.get("path", "?")
        _check_block(repo["tickets"], f"repo {name!r}")

    if "use_linear" in cfg:
        _die(
            "use_linear was replaced by the `tickets` config "
            "(set `tickets: linear`, or `tickets: {provider: linear, ...}`)."
        )


def _validate_orphan_nudge_grace(cfg: dict) -> None:
    """Hard-fail on an `orphan_nudge_grace_hours` (top-level *or* per-repo) that
    isn't a non-negative number.

    It sets how long a no-open-PR worktree is spared the "push or close" nudge
    after creation (`config.orphan_nudge_grace_seconds`). A non-numeric value
    would be silently clamped to the default, and a negative one is nonsensical
    (it'd never grace), so both are rejected at start like `review_prs`. `0`
    (disable grace) is allowed.
    """

    def _check(val: object, where: str) -> None:
        if isinstance(val, bool) or not isinstance(val, int | float):
            _die(f"{where}: orphan_nudge_grace_hours must be a number, got {val!r}.")
        if val < 0:
            _die(f"{where}: orphan_nudge_grace_hours must be >= 0, got {val!r}.")

    _validate_field(cfg, "orphan_nudge_grace_hours", _check, per_repo_key_suffix=False)


def _unset_linear_key_envs(cfg: dict, repos: list[dict | None]) -> list[str]:
    """The distinct Linear API-key env var *names* `repos` resolve to
    (`tickets.api_key_env` per-repo → org → global → `LINEAR_API_KEY`) that are
    currently unset, sorted.

    Names only — a resolved key value never reaches a warning message. `None` in
    `repos` resolves at global level (a repo-less config).
    """
    from .config import linear_api_key_env

    names = {linear_api_key_env(cfg, r) for r in repos}
    return sorted(n for n in names if not os.environ.get(n))


def _validate_linear_dev_done(cfg: dict) -> None:
    """Validate the dev-done pill config and warn on a missing API key.

    `linear_dev_done_state`, when present, must be a string (a non-string would
    silently never match a Linear state name) — rejected like `sidebar_color`.

    Then, for every repo that is Linear-configured (`tickets.keys`) whose resolved
    API-key env var is unset, the daemon can't query Linear, so the `devdone=`
    pill silently stays off. That's a soft degrade, not a config error — warn
    once per distinct env var name at start so it isn't a mystery cycles later.
    The warning names the *variable* each repo actually reads (per-org configs
    point at different ones), never its value.

    `tickets.keys` is Jira's routing field too, so the resolved *provider* gates
    the list — else every Jira repo would be warned about an unset LINEAR_API_KEY.
    """
    state = cfg.get("linear_dev_done_state")
    if state is not None and not isinstance(state, str):
        _die(f"linear_dev_done_state must be a string, got {state!r}.")

    from .config import linear_team_keys
    from .tickets import LINEAR, provider_for

    linear_repos: list[dict | None] = [
        r
        for r in cfg.get("repos", [])
        if linear_team_keys(cfg, r) and provider_for(cfg, r) is LINEAR
    ]
    for env_name in _unset_linear_key_envs(cfg, linear_repos):
        print(
            f"{yellow('cockpit:')} a repo sets Linear team keys but "
            f"{env_name} is unset — the Linear dev-done pill stays "
            f"off. Export {env_name} to enable it.",
            file=sys.stderr,
            flush=True,
        )


def _validate_linear_done_on_merge(cfg: dict) -> None:
    """Validate the merge-transition config and warn on a missing API key.

    `linear_done_on_merge` (top-level *and* per-repo) must be a bool — a stray
    truthy string would silently enable a Linear *write*, so it's rejected like
    `review_prs`. `linear_merge_done_state`, when present, must be a string.

    Then, if the feature is enabled anywhere (global or any repo) but the
    resolved Linear API-key env var is unset, the daemon can't perform the
    transition — warn once per distinct variable name (soft degrade, not an
    error), matching `_validate_linear_dev_done`.
    """
    top = cfg.get("linear_done_on_merge")
    if top is not None and not isinstance(top, bool):
        _die(f"linear_done_on_merge must be true or false, got {top!r}.")

    state = cfg.get("linear_merge_done_state")
    if state is not None and not isinstance(state, str):
        _die(f"linear_merge_done_state must be a string, got {state!r}.")

    enabled = bool(top)
    # The repos the feature is on for — every repo when the global flag is set,
    # else just those opting in. Their resolved key env var is what to check.
    on_repos: list[dict | None] = []
    for repo in cfg.get("repos", []):
        val = repo.get("linear_done_on_merge")
        if val is not None and not isinstance(val, bool):
            name = repo.get("name") or repo.get("path", "?")
            _die(
                f"repo {name!r}: linear_done_on_merge must be true or false, "
                f"got {val!r}."
            )
        if bool(top) or val:
            on_repos.append(repo)
        enabled = enabled or bool(val)

    if not enabled:
        return
    for env_name in _unset_linear_key_envs(cfg, on_repos or [None]):
        print(
            f"{yellow('cockpit:')} linear_done_on_merge is enabled but "
            f"{env_name} is unset — linked tickets won't transition "
            f"on merge. Export {env_name} to enable it.",
            file=sys.stderr,
            flush=True,
        )


def _warn_cockpit_not_on_path() -> None:
    """Soft-warn when the `cockpit` console script isn't on PATH.

    The daemon itself runs fine via `python -m cockpit.cli`, and the seeded
    statusline/starship commands use the interpreter + module dispatch — but the
    Claude Code hooks (`cockpit setup` writes) and `cockpit new`/`cockpit close`
    invoke the bare `cockpit` console script, which needs it on PATH. Warn once
    at start so a missing install surfaces here, not as an opaque
    command-not-found later.
    """
    if shutil.which("cockpit") is None:
        print(
            f"{yellow('cockpit:')} the `cockpit` command is not on PATH. The "
            "daemon runs, but the Claude Code hooks invoke it directly. "
            "Install with `brew install khivi/cockpit/cockpit`.",
            file=sys.stderr,
            flush=True,
        )


def _warn_unresolvable_base(cfg: dict) -> None:
    """Soft-warn when a managed repo's `origin/{default_base}` doesn't resolve.

    Cockpit cuts new worktrees from `origin/{base}` (`create_worktree`). A
    `git clone --bare` writes an empty fetch refspec, so it has no
    `refs/remotes/origin/*` and `origin/main` is an invalid reference — spawning
    a worktree then fails, and the failure only lands in `spawn.log`, never the
    TUI. Warn once at start with the exact fix so it isn't a silent mystery.

    `use_worktree: false` repos are skipped (they never spawn worktrees and may
    be off-GitHub with no origin). A missing path is skipped too — not this
    check's concern. Purely local (no network); runs at daemon start, not per
    statusline.
    """
    from .git import origin_base_resolves

    for repo in cfg.get("repos", []):
        if not repo.get("use_worktree", True) or not repo.get("path"):
            continue
        path = Path(repo["path"]).expanduser()
        if not path.exists():
            continue
        base = repo.get("default_base", "main")
        if origin_base_resolves(path, base):
            continue
        name = repo.get("name") or repo.get("path", "?")
        print(
            f"{yellow('cockpit:')} repo {name!r}: origin/{base} does not resolve "
            "(looks like `git clone --bare` — no origin/* tracking refs). "
            "Spawning worktrees will fail. Fix: "
            f"git -C {path} config remote.origin.fetch "
            f"'+refs/heads/*:refs/remotes/origin/*' && git -C {path} fetch origin",
            file=sys.stderr,
            flush=True,
        )


def _validate_workspace_backend() -> None:
    """Soft-warn when the resolved cmux lacks a verb or capability cockpit needs.

    `resolve_tool` checks presence, not version — so a cmux too old for
    `send-key` or `terminal.replay.v1` used to surface as a mid-cycle no-op from
    a `check=False` call. Probe once at daemon start and name exactly what's
    missing and which tier it degrades.

    Warns, never dies (`_warn_unresolvable_base`'s precedent, not
    `_validate_sidebar_colors`'s): the git+gh half of the dashboard works without
    any backend at all, so a partial one must still start. Skipped entirely on
    limux/`none` — `preflight` already warns about those.
    """
    from .capabilities import REQUIRED_CAPABILITIES, REQUIRED_VERBS, probe

    if resolve_tool() != "cmux":
        return

    found = probe()
    if not found.verbs:
        return  # couldn't ask (cmux not answering) — not a version verdict

    missing_verbs = found.missing_verbs()
    if missing_verbs:
        tiers = sorted({REQUIRED_VERBS[v] for v in missing_verbs})
        print(
            f"{yellow('cockpit:')} cmux is missing "
            f"{', '.join(f'`{v}`' for v in missing_verbs)} — "
            f"{', '.join(tiers)} disabled. Upgrade cmux.",
            file=sys.stderr,
            flush=True,
        )

    if not found.supports_capabilities:
        print(
            f"{yellow('cockpit:')} this cmux predates `cmux capabilities` — "
            "cockpit can't negotiate features and assumes none are available. "
            "Upgrade cmux.",
            file=sys.stderr,
            flush=True,
        )
        return

    missing_caps = found.missing_capabilities()
    if missing_caps:
        tiers = sorted({REQUIRED_CAPABILITIES[c] for c in missing_caps})
        print(
            f"{yellow('cockpit:')} cmux does not offer "
            f"{', '.join(missing_caps)} — "
            f"{', '.join(tiers)} disabled. Upgrade cmux.",
            file=sys.stderr,
            flush=True,
        )


def validate_config(cfg: dict) -> None:
    """Run every config-shape validator (no binary/PATH checks).

    Split out of `preflight` so the shipped `config.example.json` — which is
    both the documented schema *and* the file copied as a new user's config on
    first run (`config.py`) — can be asserted valid in CI without a real
    toolchain on PATH. Add a new `_validate_*` here and the example-config test
    covers it automatically.
    """
    from .config import apply_org_defaults

    # Orgs first (shape + a repo pointing at an undefined one), then merge them
    # down onto their repos so every validator below sees the *effective* value:
    # an org-inherited bad `sidebar_color` must fail exactly like a repo-level
    # one. The merge is idempotent, so re-running it on an already-loaded config
    # is a no-op.
    _validate_orgs(cfg)
    apply_org_defaults(cfg)

    _validate_sidebar_colors(cfg)
    _validate_repo_bool(cfg, "review_prs")
    _validate_repo_bool(cfg, "use_worktree")
    _validate_repo_bool(cfg, "dependabot")
    _validate_repo_bool(cfg, "review_external")
    _validate_skills(cfg)
    _validate_base_remote(cfg)
    _validate_global_bool(cfg, "use_slack")
    _validate_statusline_hide(cfg)
    _validate_tickets(cfg)
    _validate_orphan_nudge_grace(cfg)
    _validate_linear_dev_done(cfg)
    _validate_linear_done_on_merge(cfg)


def preflight(cfg: dict, *, for_setup: bool = False) -> None:
    for binary in REQUIRED_BINARIES:
        if shutil.which(binary) is None:
            _die(f"`{binary}` not found on PATH (required)")

    _warn_cockpit_not_on_path()

    # `cockpit setup` may be about to install cship/starship (interactive opt-in
    # or --install-deps), so it must not hard-fail on their absence here.
    if not for_setup and cfg.get("use_cship"):
        _cship_install = {
            "cship": "curl -fsSL https://cship.dev/install.sh | bash  (macOS + Linux)",
            "starship": "https://starship.rs",
        }
        for binary in CSHIP_BINARIES:
            if shutil.which(binary) is None:
                _die(
                    f"use_cship=true but `{binary}` is not on PATH. "
                    f"Install it — {_cship_install.get(binary, binary)} — "
                    "or set use_cship=false in your config."
                )

    validate_config(cfg)
    _warn_unresolvable_base(cfg)

    if cfg.get("tool", "auto") == "auto":
        resolved = resolve_tool()
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
                "side panel and workspace spawning disabled. "
                "Set 'tool': 'none' in config to suppress this warning.",
                file=sys.stderr,
                flush=True,
            )

    # Daemon-only: `cockpit setup` may be about to install/upgrade the backend,
    # and the two probe subprocesses shouldn't ride every non-daemon entry.
    if not for_setup:
        _validate_workspace_backend()
