"""Pill decisions + consumer round-trips.

`decide_pills` is the single source of truth; cmux consumes its output via
its own kind-to-styling map. These tests pin the decisions and the cmux
mapper.
"""

from __future__ import annotations

import sys
from pathlib import Path


from lib.cmux import status_pills
from lib.gh import PR
from lib.git import Worktree
from lib.pills import decide_pills


def _pr(**overrides) -> PR:
    base = dict(
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


# ── decide_pills ────────────────────────────────────────────────────────────


def test_clean_open_pr_emits_no_pills():
    assert decide_pills(_pr(), _wt()) == []


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
    assert decide_pills(_pr(state="OPEN"), _wt()) == []
    assert decide_pills(_pr(state="MERGED"), _wt()) == [
        {"kind": "state", "state": "MERGED"}
    ]
    assert decide_pills(_pr(state="CLOSED"), _wt()) == [
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


_FOOTER_CMD = "/path/to/footer.py"


def test_use_cship_noop_when_flag_unset(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": False}
    )
    _stub_cship_on_path(monkeypatch, present=True)
    cockpit_config.install_cship_statusline_if_configured(_FOOTER_CMD)
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_use_cship_raises_when_cship_missing(tmp_path, monkeypatch):
    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=False)
    import pytest

    with pytest.raises(cockpit_config.CshipNotInstalledError):
        cockpit_config.install_cship_statusline_if_configured(_FOOTER_CMD)
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_use_cship_writes_footer_command(tmp_path, monkeypatch):
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=True)
    cockpit_config.install_cship_statusline_if_configured(_FOOTER_CMD)

    settings = _json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["statusLine"] == {"type": "command", "command": _FOOTER_CMD}


def test_use_cship_skips_if_already_set(tmp_path, monkeypatch):
    import json as _json

    cockpit_config = _setup_cockpit_config(
        tmp_path, monkeypatch, {"repos": [], "use_cship": True}
    )
    _stub_cship_on_path(monkeypatch, present=True)

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        _json.dumps({"statusLine": {"type": "command", "command": _FOOTER_CMD}})
    )

    cockpit_config.install_cship_statusline_if_configured(_FOOTER_CMD)

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

    cockpit_config.install_cship_statusline_if_configured(_FOOTER_CMD)

    backups = list(claude_dir.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert "/old/statusline" in backups[0].read_text()
    new = _json.loads((claude_dir / "settings.json").read_text())
    assert new["statusLine"]["command"] == _FOOTER_CMD


# ── footer shim (delegates to cship) ────────────────────────────────────────


def test_footer_shim_pipes_stdin_to_cship(monkeypatch, capsysbinary):
    """render_footer execs cship with the stdin blob and forwards stdout."""
    import io
    import subprocess as _sp

    import lib.footer as footer

    monkeypatch.setattr(footer.shutil, "which", lambda name: "/fake/cship")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    captured = {}

    class _FakeStdin:
        buffer = io.BytesIO(b'{"hello":"world"}')

        def isatty(self):
            return False

    monkeypatch.setattr("sys.stdin", _FakeStdin())

    def fake_run(cmd, input=None, capture_output=False):
        captured["cmd"] = cmd
        captured["input"] = input
        return _sp.CompletedProcess(cmd, 0, stdout=b"styled-output\n", stderr=b"")

    monkeypatch.setattr("lib.footer.subprocess.run", fake_run)

    assert footer.render_footer() == 0
    assert captured["cmd"] == ["cship"]
    assert captured["input"] == b'{"hello":"world"}'
    out, _err = capsysbinary.readouterr()
    assert out == b"styled-output\n"


def test_footer_shim_silent_when_cship_missing(monkeypatch, capsysbinary):
    """No cship on PATH → exit 0 with no output, so the statusline never breaks."""
    import lib.footer as footer

    monkeypatch.setattr(footer.shutil, "which", lambda name: None)
    called = {"ran": False}

    def fake_run(*_a, **_kw):
        called["ran"] = True
        raise AssertionError("subprocess.run must not run when cship is missing")

    monkeypatch.setattr("lib.footer.subprocess.run", fake_run)
    assert footer.render_footer() == 0
    assert called["ran"] is False
    out, err = capsysbinary.readouterr()
    assert out == b""
    assert err == b""


def test_footer_shim_propagates_cship_exit_code(monkeypatch, capsysbinary):
    import subprocess as _sp

    import lib.footer as footer

    monkeypatch.setattr(footer.shutil, "which", lambda name: "/fake/cship")

    class _FakeStdin:
        def isatty(self):
            return True  # no stdin to forward

    monkeypatch.setattr("sys.stdin", _FakeStdin())
    monkeypatch.setattr(
        "lib.footer.subprocess.run",
        lambda *a, **kw: _sp.CompletedProcess(["cship"], 17, b"", b"boom\n"),
    )

    assert footer.render_footer() == 17
    _out, err = capsysbinary.readouterr()
    assert err == b"boom\n"
