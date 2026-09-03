"""Tests for `cockpit broadcast` (cockpit/broadcast.py).

CLI entry-point layer: mock at the `cockpit.lib.cmux` boundary
(`workspace_cwds` / `nudge_if_idle`). The idle gate itself is
`nudge_if_idle`'s own responsibility and is covered by its tests in
`tests/lib/test_cmux.py` — re-exercising it here would be noise.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import call

import cockpit.broadcast as broadcast
from cockpit.lib.cmux import CmuxUnavailable


def _cwds(*refs: str) -> dict[str, Path]:
    return {ref: Path(f"/tmp/{ref}") for ref in refs}


def test_all_idle_sends_to_every_ref(monkeypatch, capsys):
    monkeypatch.setattr(
        broadcast,
        "workspace_cwds",
        lambda *, include_self=False: _cwds("workspace:a", "workspace:b"),
    )
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    assert broadcast.main(["/compact"]) == 0
    out = capsys.readouterr().out
    assert "sent to 2/2" in out


def test_mixed_idle_busy_reports_skipped_and_still_sends(monkeypatch, capsys):
    monkeypatch.setattr(
        broadcast,
        "workspace_cwds",
        lambda *, include_self=False: _cwds("workspace:idle", "workspace:busy"),
    )

    def fake_nudge(ref, message, *, dry=False, tag="", skips=None):
        if ref == "workspace:idle":
            return True
        skips["workspace:busy"] = "mid-turn"
        return False

    monkeypatch.setattr(broadcast, "nudge_if_idle", fake_nudge)

    assert broadcast.main(["/compact"]) == 0
    out = capsys.readouterr().out
    assert "sent to 1/2" in out
    assert "skipped 1" in out
    assert "mid-turn (1): workspace:busy" in out
    assert "re-run to retry" in out


def test_dry_run_sends_nothing(monkeypatch, capsys):
    monkeypatch.setattr(
        broadcast,
        "workspace_cwds",
        lambda *, include_self=False: _cwds("workspace:a", "workspace:b"),
    )
    calls = []

    def fake_nudge(ref, message, *, dry=False, tag="", skips=None):
        calls.append((ref, message, dry, tag))
        return False  # nudge_if_idle always returns False under dry=True

    monkeypatch.setattr(broadcast, "nudge_if_idle", fake_nudge)

    assert broadcast.main(["/compact", "--dry"]) == 0
    assert calls == [
        ("workspace:a", "/compact", True, "broadcast"),
        ("workspace:b", "/compact", True, "broadcast"),
    ]
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "sent to" not in out


def test_dry_run_counts_eligible_from_the_skip_set_not_the_return(monkeypatch, capsys):
    """`nudge_if_idle` returns False for everything under `dry` — it sent
    nothing. Counting eligibility off that return reported 0 would-receive on a
    fully idle fleet, so the count comes from the complement of `skips`."""
    monkeypatch.setattr(
        broadcast,
        "workspace_cwds",
        lambda *, include_self=False: _cwds(
            "workspace:a", "workspace:b", "workspace:c"
        ),
    )

    def fake_nudge(ref, message, *, dry=False, tag="", skips=None):
        if ref == "workspace:c":
            skips[ref] = "parked"
        return False  # dry: nothing is ever actually sent

    monkeypatch.setattr(broadcast, "nudge_if_idle", fake_nudge)

    assert broadcast.main(["/compact", "--dry"]) == 0
    out = capsys.readouterr().out
    assert "2/3 workspace(s) would receive it" in out
    assert "skipped 1" in out
    assert "parked (1): workspace:c" in out
    assert "re-run to retry" not in out  # nothing was attempted, nothing to retry


def test_skips_grouped_by_reason_largest_first(monkeypatch, capsys):
    refs = [f"workspace:{i}" for i in range(5)]
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: _cwds(*refs)
    )
    reasons = {
        "workspace:0": "mid-turn",
        "workspace:1": "not at rest (Needs input)",
        "workspace:2": "not at rest (Needs input)",
        "workspace:3": "not at rest (Needs input)",
        "workspace:4": "parked",
    }

    def fake_nudge(ref, message, *, dry=False, tag="", skips=None):
        skips[ref] = reasons[ref]
        return False

    monkeypatch.setattr(broadcast, "nudge_if_idle", fake_nudge)

    assert broadcast.main(["/compact"]) == 0
    lines = [ln.strip() for ln in capsys.readouterr().out.splitlines()]
    assert "sent to 0/5 workspace(s)" in lines[0]
    assert lines[2].startswith("not at rest (Needs input) (3):")  # biggest first
    assert {lines[3], lines[4]} == {
        "mid-turn (1): workspace:0",
        "parked (1): workspace:4",
    }


def test_cmux_unavailable_returns_nonzero_and_no_success(monkeypatch, capsys):
    def raise_unavailable(*, include_self=False):
        raise CmuxUnavailable("rpc failed")

    monkeypatch.setattr(broadcast, "workspace_cwds", raise_unavailable)
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    rc = broadcast.main(["/compact"])
    assert rc != 0
    captured = capsys.readouterr()
    assert "sent to" not in captured.out
    assert "unavailable" in captured.err


def test_no_workspaces_is_not_an_error(monkeypatch, capsys):
    monkeypatch.setattr(broadcast, "workspace_cwds", lambda *, include_self=False: {})
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    assert broadcast.main(["/compact"]) == 0
    assert "no other workspaces" in capsys.readouterr().out


def test_message_passed_through_verbatim(monkeypatch):
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: _cwds("workspace:a")
    )
    seen = []

    def fake_nudge(ref, message, *, dry=False, tag="", skips=None):
        seen.append(message)
        return True

    monkeypatch.setattr(broadcast, "nudge_if_idle", fake_nudge)

    slash_command = "/compact keep the last 3 turns, don't summarize tool output"
    broadcast.main([slash_command])
    assert seen == [slash_command]


def _repo_fixture(monkeypatch, tmp_path, *, name="svc-auth", worktree_dirs=("scope",)):
    """A configured repo at `tmp_path/<name>` whose worktrees are SIBLINGS of it.

    The sibling layout is the point: a path-prefix filter would drop every one
    of them, so a test using a nested layout would pass against the wrong
    implementation.
    """
    repo_path = tmp_path / name
    repo_path.mkdir()
    wts = []
    for d in worktree_dirs:
        p = tmp_path / d
        p.mkdir()
        wts.append(type("WT", (), {"path": p})())
    monkeypatch.setattr(
        broadcast,
        "load_config",
        lambda: {"repos": [{"name": name, "path": str(repo_path)}]},
    )
    monkeypatch.setattr(broadcast, "worktrees", lambda path, prefix="": wts)
    return repo_path, [wt.path for wt in wts]


def test_repo_scopes_the_fan_out_to_that_repos_worktrees(monkeypatch, tmp_path, capsys):
    repo_path, wt_paths = _repo_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        broadcast,
        "workspace_cwds",
        lambda *, include_self=False: {
            "workspace:mine": wt_paths[0],
            "workspace:root": repo_path,
            "workspace:other": tmp_path / "unrelated",
        },
    )
    seen = []

    def fake_nudge(ref, message, *, dry=False, tag="", skips=None):
        seen.append(ref)
        return True

    monkeypatch.setattr(broadcast, "nudge_if_idle", fake_nudge)

    assert broadcast.main(["/compact", "--repo", "svc-auth"]) == 0
    assert seen == ["workspace:mine", "workspace:root"]
    assert "sent to 2/2 workspace(s) in svc-auth" in capsys.readouterr().out


def test_repo_match_is_case_insensitive(monkeypatch, tmp_path, capsys):
    """A config `name` is a display string — 'Cockpit' must answer to
    `--repo cockpit`."""
    repo_path, wt_paths = _repo_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        broadcast,
        "load_config",
        lambda: {"repos": [{"name": "Cockpit", "path": str(repo_path)}]},
    )
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: {"w:1": wt_paths[0]}
    )
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    assert broadcast.main(["/compact", "--repo", "cockpit"]) == 0
    assert "sent to 1/1 workspace(s) in cockpit" in capsys.readouterr().out


def test_unnamed_repo_falls_back_to_its_directory(monkeypatch, tmp_path, capsys):
    repo_path, wt_paths = _repo_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        broadcast, "load_config", lambda: {"repos": [{"path": str(repo_path)}]}
    )
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: {"w:1": wt_paths[0]}
    )
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    assert broadcast.main(["/compact", "--repo", repo_path.name]) == 0
    assert "sent to 1/1" in capsys.readouterr().out


def test_bare_repo_basename_is_not_a_second_spelling(monkeypatch, tmp_path, capsys):
    """Under a bare clone every repo's path ends in `.bare`. If the basename
    were accepted alongside the name, `--repo .bare` would resolve to whichever
    bare repo sorted first in the config and broadcast into the wrong one."""
    for name in ("Cockpit", "cockpit-app"):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".bare").mkdir()
    monkeypatch.setattr(
        broadcast,
        "load_config",
        lambda: {
            "repos": [
                {"name": "Cockpit", "path": str(tmp_path / "Cockpit" / ".bare")},
                {
                    "name": "cockpit-app",
                    "path": str(tmp_path / "cockpit-app" / ".bare"),
                },
            ]
        },
    )
    monkeypatch.setattr(broadcast, "worktrees", lambda path, prefix="": [])
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: _cwds("workspace:a")
    )
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    assert broadcast.main(["/compact", "--repo", ".bare"]) == 2
    assert "sent to" not in capsys.readouterr().out


def test_unknown_repo_exits_2_and_names_the_configured_ones(
    monkeypatch, tmp_path, capsys
):
    _repo_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: _cwds("workspace:a")
    )
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    assert broadcast.main(["/compact", "--repo", "typo"]) == 2
    captured = capsys.readouterr()
    assert "sent to" not in captured.out
    assert "unknown repo 'typo'" in captured.err
    assert "svc-auth" in captured.err


def test_repo_with_no_open_workspaces_is_not_an_error(monkeypatch, tmp_path, capsys):
    _repo_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        broadcast,
        "workspace_cwds",
        lambda *, include_self=False: {"w:1": tmp_path / "unrelated"},
    )
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    assert broadcast.main(["/compact", "--repo", "svc-auth"]) == 0
    out = capsys.readouterr().out
    assert "no other workspaces in svc-auth" in out
    assert "sent to" not in out


def test_repo_dry_run_reports_the_scoped_denominator(monkeypatch, tmp_path, capsys):
    """The denominator must be the SCOPED count, not the whole fleet — a
    '1/9 would receive it' on a two-workspace repo reads as a broken filter."""
    repo_path, wt_paths = _repo_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        broadcast,
        "workspace_cwds",
        lambda *, include_self=False: {
            "workspace:mine": wt_paths[0],
            "workspace:root": repo_path,
            "workspace:other": tmp_path / "unrelated",
        },
    )

    def fake_nudge(ref, message, *, dry=False, tag="", skips=None):
        if ref == "workspace:root":
            skips[ref] = "mid-turn"
        return False

    monkeypatch.setattr(broadcast, "nudge_if_idle", fake_nudge)

    assert broadcast.main(["/compact", "--repo", "svc-auth", "--dry"]) == 0
    assert "1/2 workspace(s) in svc-auth would receive it" in capsys.readouterr().out


def test_worktree_enumeration_failure_exits_1_without_sending(
    monkeypatch, tmp_path, capsys
):
    _repo_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: _cwds("workspace:a")
    )
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    def boom(path, prefix=""):
        raise RuntimeError("git worktree list failed")

    monkeypatch.setattr(broadcast, "worktrees", boom)

    assert broadcast.main(["/compact", "--repo", "svc-auth"]) == 1
    captured = capsys.readouterr()
    assert "sent to" not in captured.out
    assert "could not enumerate svc-auth worktrees" in captured.err


def test_without_repo_nothing_reads_the_config(monkeypatch):
    """The unscoped path must stay config-free: broadcast reaches workspaces
    cockpit doesn't manage, so a config read there could only narrow it."""

    def boom():
        raise AssertionError("load_config must not be read without --repo")

    monkeypatch.setattr(broadcast, "load_config", boom)
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: _cwds("workspace:a")
    )
    monkeypatch.setattr(broadcast, "nudge_if_idle", lambda *a, **k: True)

    assert broadcast.main(["/compact"]) == 0


def test_nudge_called_with_broadcast_tag(monkeypatch):
    monkeypatch.setattr(
        broadcast, "workspace_cwds", lambda *, include_self=False: _cwds("workspace:a")
    )
    recorded = []

    def fake_nudge(*a, **k):
        recorded.append(call(*a, **k))
        return True

    monkeypatch.setattr(broadcast, "nudge_if_idle", fake_nudge)

    broadcast.main(["/compact"])
    assert recorded == [
        call("workspace:a", "/compact", dry=False, tag="broadcast", skips={})
    ]
