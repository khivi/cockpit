"""Pill decisions + consumer round-trips.

`decide_pills` is the single source of truth; cmux consumes its output via
its own kind-to-styling map. These tests pin the decisions and the cmux
mapper.
"""

from __future__ import annotations

from pathlib import Path


from lib.cmux import status_pills
from lib.gh import PR
from lib.git import Worktree
from lib.pills import decide_pills


def _pr(**overrides) -> PR:
    base: dict = dict(
        number=1,
        title="t",
        branch="khivi/feature",
        url="https://example/pr/1",
        author="khivi",
        is_draft=False,
        review_decision="REVIEW_REQUIRED",
        mergeable="MERGEABLE",
        ci="passed",
        unaddressed=0,
        total_from_others=0,
        state="OPEN",
        updated_at="",
    )
    base.update(overrides)
    return PR(**base)


def _wt(
    branch: str = "khivi/feature",
    *,
    rebasing: bool = False,
    merging: bool = False,
    dirty: int = 0,
) -> Worktree:
    return Worktree(
        path=Path("/tmp/wt"),
        branch=branch,
        rebasing=rebasing,
        merging=merging,
        dirty_count=dirty,
    )


def _expected_starship(cockpit_config) -> str:
    """The bundled starship.toml after __COCKPIT_STARSHIP__ placeholder substitution.

    `install_starship_default_config()` rewrites the placeholder to the
    resolved absolute path of `scripts/starship.py` before writing to
    ~/.config/starship.toml — assertions about installed content must
    match that substituted output, not the in-repo source.
    """
    return cockpit_config.STARSHIP_DEFAULT_TOML.read_text().replace(
        cockpit_config.STARSHIP_PLACEHOLDER, str(cockpit_config.STARSHIP_PY)
    )


# ── decide_pills ────────────────────────────────────────────────────────────


def test_clean_open_pr_with_passing_ci_emits_ci_passed():
    # All-green PR: surface a sentinel ✓ so the sidebar isn't empty.
    assert decide_pills(_pr(), _wt()) == [{"kind": "ci_passed"}]


def test_clean_open_pr_without_ci_emits_no_pills():
    # No CI configured (or not yet queued) — no sentinel.
    assert decide_pills(_pr(ci="none"), _wt()) == []


def test_ci_passed_suppressed_when_other_pills_present():
    # `approved` already conveys readiness; don't double up with ci_passed.
    pills = decide_pills(_pr(review_decision="APPROVED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert kinds == ["approved"]


def test_ci_passed_suppressed_when_unaddressed_present():
    pills = decide_pills(_pr(unaddressed=1), _wt())
    kinds = [p["kind"] for p in pills]
    assert "ci_passed" not in kinds
    assert "unaddressed" in kinds


def test_ci_passed_suppressed_for_merged_pr():
    # State pill (cmux-dropped) still counts as "other pill" → no sentinel.
    pills = decide_pills(_pr(state="MERGED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert "ci_passed" not in kinds
    assert kinds == ["state"]


def test_ci_failed_carries_phase():
    pills = decide_pills(_pr(ci="failed:lint"), _wt())
    assert pills == [{"kind": "ci_failed", "phase": "lint"}]


def test_ci_failed_without_phase_marker():
    # `ci` is "failed" with no `:phase`; phase becomes empty string.
    pills = decide_pills(_pr(ci="failed"), _wt())
    assert pills == [{"kind": "ci_failed", "phase": ""}]


def test_ci_pending():
    assert decide_pills(_pr(ci="pending"), _wt()) == [{"kind": "ci_pending"}]


def test_unaddressed_supersedes_changes_requested():
    pills = decide_pills(_pr(unaddressed=3, review_decision="CHANGES_REQUESTED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert "unaddressed" in kinds
    assert "changes_requested" not in kinds


def test_changes_requested_alone():
    pills = decide_pills(_pr(review_decision="CHANGES_REQUESTED"), _wt())
    assert pills == [{"kind": "changes_requested"}]


def test_conflict_pill():
    pills = decide_pills(_pr(mergeable="CONFLICTING"), _wt())
    assert pills == [{"kind": "conflict"}]


def test_draft_and_approved_coexist():
    pills = decide_pills(_pr(is_draft=True, review_decision="APPROVED"), _wt())
    kinds = [p["kind"] for p in pills]
    assert kinds == ["draft", "approved"]


def test_state_pill_only_for_non_open():
    # OPEN + ci=none → no pills; MERGED/CLOSED → state pill (and ci_passed is
    # suppressed by the state pill, see test_ci_passed_suppressed_for_merged_pr).
    assert decide_pills(_pr(state="OPEN", ci="none"), _wt()) == []
    assert decide_pills(_pr(state="MERGED", ci="none"), _wt()) == [
        {"kind": "state", "state": "MERGED"}
    ]
    assert decide_pills(_pr(state="CLOSED", ci="none"), _wt()) == [
        {"kind": "state", "state": "CLOSED"}
    ]


def test_worktree_pills_independent_of_pr():
    pills = decide_pills(_pr(), _wt(rebasing=True, dirty=4))
    assert pills == [
        {"kind": "rebase"},
        {"kind": "wip", "count": 4},
    ]


def test_wip_dropped_when_no_worktree():
    # PR exists but worktree is unknown (e.g. external repo): no wip pill.
    pills = decide_pills(_pr(ci="failed:test"), None)
    kinds = [p["kind"] for p in pills]
    assert "wip" not in kinds
    assert "ci_failed" in kinds


def test_full_house_canonical_order():
    pills = decide_pills(
        _pr(
            is_draft=True,
            review_decision="APPROVED",
            mergeable="CONFLICTING",
            ci="failed:tests",
            unaddressed=2,
            state="OPEN",
        ),
        _wt(merging=True, dirty=3),
    )
    assert [p["kind"] for p in pills] == [
        "merge",
        "wip",
        "ci_failed",
        "unaddressed",
        "conflict",
        "draft",
        "approved",
    ]


# ── cmux mapper ─────────────────────────────────────────────────────────────


def test_cmux_status_pills_matches_decisions():
    out = status_pills(_pr(ci="failed:lint", unaddressed=2), _wt(dirty=1))
    assert out == [
        ("wip", "✏️ 1 dirty", "#ff9500"),
        ("ci", "❌ ci:lint", "#eb445a"),
        ("comments", "💬 2 unaddressed", "#eb445a"),
    ]


def test_cmux_drops_state_pill():
    out = status_pills(_pr(state="MERGED"), _wt())
    assert out == []


def test_cmux_conflict_emits_merge_key():
    out = status_pills(_pr(mergeable="CONFLICTING"), _wt())
    assert out == [("merge", "⚠️ conflict", "#ff9500")]


# ── cache round-trip ────────────────────────────────────────────────────────


def test_write_pr_cache_includes_pills(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import lib.config as cockpit_config

    importlib.reload(cockpit_config)
    import lib.cache as cache_mod

    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint", review_decision="APPROVED")
    wt = _wt(dirty=2)
    payload = cache_mod.write_pr_cache("testrepo", pr, wt)

    assert "pills" in payload
    kinds = [p["kind"] for p in payload["pills"]]
    assert kinds == ["wip", "ci_failed", "approved"]

    on_disk = cache_mod.find_pr_payload("khivi/feature", repo_name="testrepo")
    assert on_disk is not None
    assert [p["kind"] for p in on_disk["pills"]] == kinds


def test_write_pr_cache_without_worktree(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    import lib.config as cockpit_config

    importlib.reload(cockpit_config)
    import lib.cache as cache_mod

    importlib.reload(cache_mod)

    pr = _pr(ci="failed:lint")
    payload = cache_mod.write_pr_cache("testrepo", pr)

    assert "pills" in payload
    # Without wt, no rebase/merge/wip pills appear.
    kinds = [p["kind"] for p in payload["pills"]]
    assert "wip" not in kinds
    assert "ci_failed" in kinds


# ── use_cship gating ────────────────────────────────────────────────────────


def _setup_cockpit_config(tmp_path, monkeypatch, cfg: dict):
    """Stand up an isolated cockpit config + fake $HOME, return reloaded module."""
    import importlib
    import json as _json

    monkeypatch.setenv("COCKPIT_HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / "config.json").write_text(_json.dumps(cfg))

    import lib.config as cockpit_config

    importlib.reload(cockpit_config)
    return cockpit_config


def _stub_cship_on_path(monkeypatch, present: bool):
    """Replace `shutil.which("cship")` inside lib.config so tests don't depend
    on the host having (or not having) a real cship binary on $PATH."""
    monkeypatch.setattr(
        "lib.config.shutil.which",
        lambda name: "/fake/bin/cship" if (present and name == "cship") else None,
    )


_STATUSLINE_CMD = "/path/to/footer.py"


def test_use_cship_noop_when_flag_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": False}
    )
    _stub_cship_on_path(monkeypatch, present=True)
    cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_use_cship_raises_when_cship_missing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=False)
    import pytest

    with pytest.raises(cockpit_config.CshipNotInstalledError):
        cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_use_cship_writes_footer_command(tmp_path, monkeypatch):
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=True)
    cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)

    settings = _json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["statusLine"] == {"type": "command", "command": _STATUSLINE_CMD}


def test_use_cship_skips_if_already_set(tmp_path, monkeypatch):
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=True)

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        _json.dumps({"statusLine": {"type": "command", "command": _STATUSLINE_CMD}})
    )

    cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)

    backups = list(claude_dir.glob("settings.json.bak.*"))
    assert backups == []


def test_use_cship_backs_up_existing_statusline(tmp_path, monkeypatch):
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=True)

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        _json.dumps({"statusLine": {"type": "command", "command": "/old/statusline"}})
    )

    cockpit_config.install_cship_statusline_if_configured(_STATUSLINE_CMD)

    backups = list(claude_dir.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert "/old/statusline" in backups[0].read_text()
    new = _json.loads((claude_dir / "settings.json").read_text())
    assert new["statusLine"]["command"] == _STATUSLINE_CMD


# ── default cship.toml seeding ──────────────────────────────────────────────


def test_cship_default_noop_when_flag_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": False}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit_config.install_cship_default_config()
    assert not (tmp_path / "xdg" / "cship.toml").exists()


def test_cship_default_installed_when_missing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit_config.install_cship_default_config()
    dest = tmp_path / "xdg" / "cship.toml"
    assert dest.exists()
    assert dest.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()


def test_cship_default_overwrites_existing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    dest = tmp_path / "xdg" / "cship.toml"
    dest.parent.mkdir(parents=True)
    dest.write_text("# my custom cship config\n[time]\ndisabled = true\n")
    cockpit_config.install_cship_default_config()
    assert dest.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()


def test_cship_default_missing_package_file_is_soft_fail(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        cockpit_config, "CSHIP_DEFAULT_TOML", tmp_path / "does-not-exist.toml"
    )
    cockpit_config.install_cship_default_config()
    assert not (tmp_path / "xdg" / "cship.toml").exists()


def test_cli_footer_flag_runs_only_footer_setup(tmp_path, monkeypatch):
    """`--footer` installs cship.toml + starship.toml + statusLine and exits."""
    import importlib
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit

    importlib.reload(cockpit)

    def _explode(*_a, **_kw):
        raise AssertionError("--footer must not trigger a reconcile cycle")

    monkeypatch.setattr(cockpit, "gh_self_user", _explode)
    monkeypatch.setattr(cockpit, "cycle_all", _explode)

    assert cockpit.main(["--footer"]) == 0

    cship_toml = tmp_path / "xdg" / "cship.toml"
    assert cship_toml.exists()
    assert cship_toml.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()

    starship_toml = tmp_path / "xdg" / "starship.toml"
    assert starship_toml.exists()
    assert starship_toml.read_text() == _expected_starship(cockpit_config)

    settings = _json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["statusLine"]["type"] == "command"
    assert settings["statusLine"]["command"].endswith("/footer.py")


def test_cli_once_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--once` is pure reconcile — never seeds either toml or writes statusLine."""
    import importlib

    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _stub_cship_on_path(monkeypatch, present=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_once_with", lambda _s: None)

    assert cockpit.main(["--once"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_cli_watch_does_not_touch_footer_files(tmp_path, monkeypatch):
    """`--watch` is pure reconcile — never seeds either toml or writes statusLine."""
    import importlib

    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _stub_cship_on_path(monkeypatch, present=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_watch", lambda _s, _secs: None)

    assert cockpit.main(["--watch", "60"]) == 0
    assert not (tmp_path / "xdg" / "cship.toml").exists()
    assert not (tmp_path / "xdg" / "starship.toml").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_cli_once_does_not_raise_when_cship_missing(tmp_path, monkeypatch):
    """`--once` must not invoke the cship-on-PATH check; missing cship is a `--footer` concern."""
    import importlib

    _setup_cockpit_config(tmp_path, monkeypatch, {"repos": [], "use_cship": True})
    _stub_cship_on_path(monkeypatch, present=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    import cockpit

    importlib.reload(cockpit)
    monkeypatch.setattr(cockpit, "_build_state", lambda _a: {"dry": True})
    monkeypatch.setattr(cockpit, "_once_with", lambda _s: None)

    assert cockpit.main(["--once"]) == 0


def test_cship_default_honors_xdg_config_home(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))
    cockpit_config.install_cship_default_config()
    assert (tmp_path / "elsewhere" / "cship.toml").exists()
    assert not (tmp_path / ".config" / "cship.toml").exists()


# ── default starship.toml seeding ───────────────────────────────────────────


def test_starship_default_noop_when_flag_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": False}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit_config.install_starship_default_config()
    assert not (tmp_path / "xdg" / "starship.toml").exists()


def test_starship_default_installed_when_missing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cockpit_config.install_starship_default_config()
    dest = tmp_path / "xdg" / "starship.toml"
    assert dest.exists()
    assert dest.read_text() == _expected_starship(cockpit_config)


def test_starship_default_overwrites_existing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    dest = tmp_path / "xdg" / "starship.toml"
    dest.parent.mkdir(parents=True)
    dest.write_text("# my custom starship config\nformat = ''\n")
    cockpit_config.install_starship_default_config()
    assert dest.read_text() == _expected_starship(cockpit_config)


def test_starship_default_missing_package_file_is_soft_fail(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        cockpit_config, "STARSHIP_DEFAULT_TOML", tmp_path / "does-not-exist.toml"
    )
    cockpit_config.install_starship_default_config()
    assert not (tmp_path / "xdg" / "starship.toml").exists()


def test_starship_default_renders_custom_modules_via_starship_prompt(
    tmp_path, monkeypatch
):
    """The bundled cship.toml must reference $starship_prompt; otherwise cship's
    line renderer ignores [custom.*] and the chain is dead even with both files
    installed. Pin this so the two configs can't silently drift apart again.
    """
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    cship_default = cockpit_config.CSHIP_DEFAULT_TOML.read_text()
    starship_default = cockpit_config.STARSHIP_DEFAULT_TOML.read_text()
    assert "$starship_prompt" in cship_default
    assert "[custom.linear]" in starship_default
    assert "[custom.pr_state]" in starship_default
    # The eight custom modules whose chain commit 8ab5889 broke.
    for mod in (
        "custom.context",
        "custom.session_time",
        "custom.ratelimit",
        "custom.linear",
        "custom.pr_state",
        "custom.pr_num",
        "custom.pr_checks",
        "custom.pr_title",
    ):
        assert f"[{mod}]" in starship_default, f"{mod} missing from starship.toml"
        assert (
            mod not in cship_default
        ), f"{mod} still defined in cship.toml — cship cannot render [custom.*]"


# ── symlink-aware seeding ───────────────────────────────────────────────────


def test_seed_replaces_dangling_symlink_with_real_file(tmp_path, monkeypatch):
    """If ~/.config/starship.toml is a dangling symlink (the exact state the
    deleted dotfiles file left behind), --footer must replace it with a real
    file rather than write through to the missing target.
    """
    import os

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    missing_target = tmp_path / "dotfiles" / "starship.toml"  # never created
    dest = xdg / "starship.toml"
    os.symlink(missing_target, dest)
    assert dest.is_symlink()
    assert not dest.exists()  # dangling

    cockpit_config.install_starship_default_config()

    assert dest.exists()
    assert not dest.is_symlink()
    assert dest.read_text() == _expected_starship(cockpit_config)
    # Target never existed, so nothing to back up.
    assert not missing_target.exists()
    assert not (tmp_path / "dotfiles").exists()


def test_seed_backs_up_live_symlink_target(tmp_path, monkeypatch):
    """If the symlink resolves to a real file, --footer backs that file up
    before unlinking the symlink and writing the bundled default."""
    import os

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    target_dir = tmp_path / "dotfiles"
    target_dir.mkdir()
    target = target_dir / "starship.toml"
    target.write_text("# user's existing dotfiles content\n")
    dest = xdg / "starship.toml"
    os.symlink(target, dest)

    cockpit_config.install_starship_default_config()

    assert dest.exists()
    assert not dest.is_symlink()
    assert dest.read_text() == _expected_starship(cockpit_config)
    # Original target moved aside, not deleted.
    assert not target.exists()
    backups = list(target_dir.glob("starship.toml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "# user's existing dotfiles content\n"


def test_seed_replaces_dangling_cship_symlink(tmp_path, monkeypatch):
    """Same symlink-aware behavior for cship.toml — both installers share the
    same _seed_default_toml helper, but pin cship.toml independently."""
    import os

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    dest = xdg / "cship.toml"
    os.symlink(tmp_path / "nowhere" / "cship.toml", dest)

    cockpit_config.install_cship_default_config()

    assert dest.exists()
    assert not dest.is_symlink()
    assert dest.read_text() == cockpit_config.CSHIP_DEFAULT_TOML.read_text()


# ── invoke_cship (cship-binary exec, no stdin reading) ─────────────────────


def test_invoke_cship_pipes_blob_and_forwards_stdout(monkeypatch, capsysbinary):
    """invoke_cship pipes the given blob to cship and forwards its stdout."""
    import subprocess as _sp

    import lib.cship as cship_mod

    monkeypatch.setattr(cship_mod.shutil, "which", lambda name: "/fake/cship")

    captured = {}

    def fake_run(cmd, input=None, capture_output=False, env=None):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["env"] = env
        return _sp.CompletedProcess(cmd, 0, stdout=b"styled-output\n", stderr=b"")

    monkeypatch.setattr("lib.cship.subprocess.run", fake_run)

    assert cship_mod.invoke_cship(b'{"hello":"world"}', "sess1") == 0
    assert captured["cmd"] == ["cship"]
    assert captured["input"] == b'{"hello":"world"}'
    assert captured["env"]["CSHIP_SESSION_ID"] == "sess1"
    out, _err = capsysbinary.readouterr()
    assert out == b"styled-output\n"


def test_invoke_cship_errors_when_missing(monkeypatch, capsysbinary):
    """No cship on PATH → non-zero exit + stderr message. use_cship=true
    implies cship is installed; a missing binary is misconfiguration and
    should surface loudly, not silently return 0."""
    import lib.cship as cship_mod

    monkeypatch.setattr(cship_mod.shutil, "which", lambda name: None)
    called = {"ran": False}

    def fake_run(*_a, **_kw):
        called["ran"] = True
        raise AssertionError("subprocess.run must not run when cship is missing")

    monkeypatch.setattr("lib.cship.subprocess.run", fake_run)
    assert cship_mod.invoke_cship(b'{"x":1}', None) != 0
    assert called["ran"] is False
    _out, err = capsysbinary.readouterr()
    assert b"cship" in err and b"not on PATH" in err


def test_invoke_cship_propagates_exit_code(monkeypatch, capsysbinary):
    import subprocess as _sp

    import lib.cship as cship_mod

    monkeypatch.setattr(cship_mod.shutil, "which", lambda name: "/fake/cship")
    monkeypatch.setattr(
        "lib.cship.subprocess.run",
        lambda *a, **kw: _sp.CompletedProcess(["cship"], 17, b"", b"boom\n"),
    )

    assert cship_mod.invoke_cship(b"", None) == 17
    _out, err = capsysbinary.readouterr()
    assert err == b"boom\n"


def test_invoke_cship_no_session_id_omits_env_export(monkeypatch):
    """When sid is None, CSHIP_SESSION_ID must not be exported into cship's env."""
    import subprocess as _sp

    import lib.cship as cship_mod

    monkeypatch.setattr(cship_mod.shutil, "which", lambda name: "/fake/cship")
    monkeypatch.delenv("CSHIP_SESSION_ID", raising=False)
    captured = {}

    def fake_run(cmd, input=None, capture_output=False, env=None):
        captured["env"] = env
        return _sp.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("lib.cship.subprocess.run", fake_run)
    cship_mod.invoke_cship(b"{}", None)
    assert "CSHIP_SESSION_ID" not in captured["env"]
