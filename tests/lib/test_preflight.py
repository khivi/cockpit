"""Tests for cockpit/lib/preflight.preflight().

Verifies the unified dependency check that runs at the top of every
`cockpit.py` invocation: hard-fails on missing required binaries, soft-warns
on missing workspace backend.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from cockpit.lib import preflight as preflight_mod
from cockpit.lib.config import CONFIG_EXAMPLE
from cockpit.lib.preflight import (
    _warn_unresolvable_base,
    preflight,
    validate_config,
)
from tests.fixtures import make_bin_on_path


def _all_required(tmp_path, monkeypatch) -> None:
    # `cockpit` too — preflight soft-warns when its own console script is absent,
    # so a healthy (silent) preflight needs it on PATH alongside gh/git/cmux.
    make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux", "cockpit")


def test_preflight_passes_when_required_bins_present(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux"})
    assert capsys.readouterr().err == ""


def test_preflight_exits_when_gh_missing(tmp_path, monkeypatch, capsys):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "git", "cmux")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(SystemExit) as exc:
        preflight({})
    assert exc.value.code == 2
    assert "`gh` not found on PATH" in capsys.readouterr().err


def test_preflight_exits_when_git_missing(tmp_path, monkeypatch, capsys):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "cmux")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(SystemExit) as exc:
        preflight({})
    assert exc.value.code == 2
    assert "`git` not found on PATH" in capsys.readouterr().err


def test_preflight_exits_when_use_cship_and_cship_missing(
    tmp_path, monkeypatch, capsys
):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux", "starship")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(SystemExit) as exc:
        preflight({"use_cship": True})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "use_cship=true" in err
    assert "`cship`" in err


def test_preflight_exits_when_use_cship_and_starship_missing(
    tmp_path, monkeypatch, capsys
):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cmux", "cship")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(SystemExit) as exc:
        preflight({"use_cship": True})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "use_cship=true" in err
    assert "`starship`" in err


def test_preflight_skips_cship_check_when_use_cship_false(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    preflight({"use_cship": False})
    assert capsys.readouterr().err == ""


def test_preflight_warns_when_only_limux_present(tmp_path, monkeypatch, capsys):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "limux")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "auto"})
    err = capsys.readouterr().err
    assert "cmux not found — using limux" in err


def test_preflight_warns_when_no_workspace_backend(tmp_path, monkeypatch, capsys):
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "auto"})
    err = capsys.readouterr().err
    assert "no workspace tool on PATH" in err


def test_preflight_silent_when_tool_explicitly_set(tmp_path, monkeypatch, capsys):
    # `cockpit` on PATH too, else the soft install-hint warning fires.
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git", "cockpit")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "none"})
    assert capsys.readouterr().err == ""


def test_preflight_warns_when_cockpit_not_on_path(tmp_path, monkeypatch, capsys):
    # gh + git present (so no hard-fail) but `cockpit` absent → soft warning,
    # not an exit: the daemon runs, but the slash-commands need it installed.
    bin_dir = make_bin_on_path(tmp_path, monkeypatch, "gh", "git")
    monkeypatch.setenv("PATH", str(bin_dir))
    preflight({"tool": "none"})
    err = capsys.readouterr().err
    assert "cockpit" in err
    assert "PATH" in err
    assert "brew install" in err


def test_preflight_exits_on_invalid_sidebar_color(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "repos": [{"name": "r", "sidebar_color": "Turquoise"}]}
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "sidebar_color" in err
    assert "Turquoise" in err
    assert "Teal" in err  # the valid set is listed


def test_preflight_passes_on_valid_sidebar_color(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "sidebar_color": "Teal"}]})
    assert capsys.readouterr().err == ""


def test_preflight_ignores_repo_without_sidebar_color(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "path": "/x"}]})
    assert capsys.readouterr().err == ""


# ── _warn_unresolvable_base (bare-clone / empty-refspec detection) ──────────
# Tested directly (not via full preflight()) so it runs against real git repos
# rather than the stubbed `git` binary the preflight PATH tests install.


def test_warn_unresolvable_base_silent_for_normal_clone(cockpit_repo, capsys):
    cfg = {"repos": [{"name": "r", "path": str(cockpit_repo.repo)}]}
    _warn_unresolvable_base(cfg)
    assert capsys.readouterr().err == ""


def test_warn_unresolvable_base_warns_for_bare_clone(cockpit_repo, tmp_path, capsys):
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--bare", str(cockpit_repo.origin), str(bare)], check=True
    )
    _warn_unresolvable_base({"repos": [{"name": "beta", "path": str(bare)}]})
    err = capsys.readouterr().err
    assert "beta" in err
    assert "origin/main does not resolve" in err
    assert "git clone --bare" in err
    assert "remote.origin.fetch" in err  # the fix is spelled out


def test_warn_unresolvable_base_skips_no_worktree_repo(cockpit_repo, tmp_path, capsys):
    """A `use_worktree: false` repo never spawns worktrees and may be off-GitHub
    with no origin — it must not trip the warning even when origin/main is
    absent."""
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--bare", str(cockpit_repo.origin), str(bare)], check=True
    )
    _warn_unresolvable_base(
        {"repos": [{"name": "beta", "path": str(bare), "use_worktree": False}]}
    )
    assert capsys.readouterr().err == ""


def test_warn_unresolvable_base_skips_missing_path(capsys):
    _warn_unresolvable_base({"repos": [{"name": "gone", "path": "/no/such/repo"}]})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_bool_review_prs(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [{"name": "r", "review_prs": "yes"}]})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "review_prs" in err
    assert "'yes'" in err


def test_preflight_passes_on_bool_review_prs(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "review_prs": True}]})
    assert capsys.readouterr().err == ""


def test_preflight_ignores_repo_without_review_prs(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "path": "/x"}]})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_bool_review_external(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [{"name": "r", "review_external": "yes"}]})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "review_external" in err
    assert "'yes'" in err


def test_preflight_passes_on_bool_review_external(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "review_external": True}]})
    assert capsys.readouterr().err == ""


def test_preflight_ignores_repo_without_review_external(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "path": "/x"}]})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_bool_use_worktree(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [{"name": "r", "use_worktree": "yes"}]})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "use_worktree" in err
    assert "'yes'" in err


def test_preflight_passes_on_bool_use_worktree(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "repos": [{"name": "r", "use_worktree": False}]})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_slash_skills_review_repo(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {
                "tool": "cmux",
                "repos": [{"name": "r", "skills": {"review": "pr-review"}}],
            }
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "skills.review" in err
    assert "'pr-review'" in err


def test_preflight_exits_on_non_string_skills_review_global(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [], "skills": {"review": True}})
    assert exc.value.code == 2
    assert "skills.review" in capsys.readouterr().err


def test_preflight_passes_on_valid_skills_review(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight(
        {
            "tool": "cmux",
            "skills": {"review": "/review"},
            "repos": [{"name": "r", "skills": {"review": "/pr-review"}}],
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_passes_on_valid_skills_plan_and_actions(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    preflight(
        {
            "tool": "cmux",
            "skills": {"plan": "/plan-pr", "actions": "/actions-pr"},
            "repos": [{"name": "r", "skills": {"plan": "/plan-pr"}}],
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_unknown_skills_field(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [], "skills": {"bogus": "/foo"}})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unknown skills field" in err
    assert "'bogus'" in err


def test_preflight_exits_on_leftover_flat_review_command_global(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [], "review_command": "/review"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "review_command" in err
    assert "skills.review" in err


def test_preflight_exits_on_leftover_flat_review_command_repo(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "repos": [{"name": "r", "review_command": "/review"}]}
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "review_command" in err
    assert "skills.review" in err


def test_preflight_exits_on_leftover_flat_prompt_prefix_global(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "repos": [], "prompt_prefix": "/session-coordination"}
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "prompt_prefix" in err
    assert "skills.session" in err


def test_preflight_exits_on_leftover_flat_prompt_prefix_repo(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {
                "tool": "cmux",
                "repos": [{"name": "r", "prompt_prefix": "/session-coordination"}],
            }
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "prompt_prefix" in err
    assert "skills.session" in err


def test_preflight_exits_on_blank_base_remote_repo(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [{"name": "r", "base_remote": "  "}]})
    assert exc.value.code == 2
    assert "base_remote" in capsys.readouterr().err


def test_preflight_exits_on_non_string_base_remote_global(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [], "base_remote": 3})
    assert exc.value.code == 2
    assert "base_remote" in capsys.readouterr().err


def test_preflight_passes_on_valid_base_remote(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight(
        {
            "tool": "cmux",
            "base_remote": "upstream",
            "repos": [{"name": "r", "base_remote": "origin"}],
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_list_statusline_hide(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "statusline_hide": "cost"})
    assert exc.value.code == 2
    assert "statusline_hide must be a list" in capsys.readouterr().err


def test_preflight_exits_on_unknown_statusline_field(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "statusline_hide": ["cost", "bogus"]})
    assert exc.value.code == 2
    assert "'bogus' is not a statusline field" in capsys.readouterr().err


def test_preflight_passes_on_valid_statusline_hide(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "statusline_hide": ["cost", "session-time"]})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_bool_use_slack(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "use_slack": "yes"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "use_slack" in err
    assert "'yes'" in err


def test_preflight_passes_on_bool_use_slack(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "use_slack": True})
    assert capsys.readouterr().err == ""


def test_preflight_ignores_absent_use_slack(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux"})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_invalid_tickets(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "tickets": "gitlab"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "tickets" in err and "'gitlab'" in err


def test_preflight_exits_on_invalid_per_repo_tickets(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [{"name": "r", "tickets": "nope"}]})
    assert exc.value.code == 2
    assert "tickets" in capsys.readouterr().err


def test_preflight_exits_on_invalid_object_provider(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "tickets": {"provider": "gitlab"}})
    assert exc.value.code == 2
    assert "provider" in capsys.readouterr().err


def test_preflight_passes_on_valid_jira_object(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight(
        {
            "tool": "cmux",
            "tickets": {
                "provider": "jira",
                "site_url": "https://acme.atlassian.net",
                "email": "me@acme.com",
                "dev_done_status": "Dev Done",
                "merge_done_status": "Done",
                "close_on_merge": True,
            },
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_unknown_jira_field(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "tickets": {"provider": "jira", "dev_done_label": "x"}}
        )
    assert exc.value.code == 2
    assert "dev_done_label" in capsys.readouterr().err


def test_preflight_passes_on_valid_trello_object(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight(
        {
            "tool": "cmux",
            "tickets": {
                "provider": "trello",
                "dev_done_list": "Ready for Review",
                "merge_done_list": "Done",
                "close_on_merge": True,
            },
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_wrong_provider_field_under_trello(
    tmp_path, monkeypatch, capsys
):
    """`keys` is a Linear-only field — rejected under `provider: trello` the
    same way `dev_done_label` (GitHub-only) is rejected under `provider: jira`."""
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "tickets": {"provider": "trello", "keys": ["PE"]}})
    assert exc.value.code == 2
    assert "keys" in capsys.readouterr().err


def test_preflight_exits_on_bad_trello_dev_done_list_type(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "tickets": {"provider": "trello", "dev_done_list": 5}}
        )
    assert exc.value.code == 2
    assert "dev_done_list" in capsys.readouterr().err


def test_preflight_passes_on_tickets_object_without_provider_key(
    tmp_path, monkeypatch, capsys
):
    """A `tickets` object with fields but no `provider` key defaults the
    provider to "none" (`_check_block`'s `val.get("provider", "none")`) rather
    than rejecting the block outright. `close_on_merge` is a common field valid
    under every provider (including "none"), so it passes."""
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "tickets": {"close_on_merge": True}})
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_provider_specific_field_without_provider_key(
    tmp_path, monkeypatch, capsys
):
    """Confirms the no-`provider`-key default really is "none" (not some
    permissive catch-all): a GitHub-only field (`dev_done_label`) is rejected
    exactly as it would be under an explicit `provider: none`/unset provider."""
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "tickets": {"dev_done_label": "x"}})
    assert exc.value.code == 2
    assert "dev_done_label" in capsys.readouterr().err


def test_preflight_exits_on_leftover_use_linear(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "use_linear": True})
    assert exc.value.code == 2
    assert "use_linear" in capsys.readouterr().err


def test_preflight_passes_on_valid_tickets_string(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight({"tool": "cmux", "tickets": "github"})
    assert capsys.readouterr().err == ""


def test_preflight_passes_on_valid_tickets_object(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight(
        {
            "tool": "cmux",
            "tickets": {
                "provider": "github",
                "dev_done_label": "ready for review",
                "close_on_merge": True,
            },
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_passes_on_linear_object_with_keys(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "k")  # silence the soft-warn
    preflight(
        {
            "tool": "cmux",
            "repos": [
                {
                    "name": "r",
                    "tickets": {"provider": "linear", "keys": ["PE"]},
                }
            ],
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_bool_close_on_merge(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "tickets": {"provider": "github", "close_on_merge": "yes"}}
        )
    assert exc.value.code == 2
    assert "close_on_merge" in capsys.readouterr().err


def test_preflight_exits_on_bad_dev_done_label_type(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "tickets": {"provider": "github", "dev_done_label": 5}}
        )
    assert exc.value.code == 2
    assert "dev_done_label" in capsys.readouterr().err


def test_preflight_exits_on_bad_keys_type(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "tickets": {"provider": "linear", "keys": "PE"}})
    assert exc.value.code == 2
    assert "keys" in capsys.readouterr().err


def test_preflight_exits_on_unknown_field(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {"tool": "cmux", "tickets": {"provider": "github", "dev_done_labl": "x"}}
        )
    assert exc.value.code == 2
    assert "unknown field" in capsys.readouterr().err


def test_preflight_exits_on_linear_field_under_github(tmp_path, monkeypatch, capsys):
    # `keys` belongs to Linear; on a github provider it's an unknown field.
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "tickets": {"provider": "github", "keys": ["PE"]}})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unknown field" in err and "keys" in err


def test_preflight_exits_on_bad_start_label_type(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "tickets": {"provider": "github", "start_label": 5}})
    assert exc.value.code == 2
    assert "start_label" in capsys.readouterr().err


def test_preflight_exits_on_non_numeric_orphan_nudge_grace(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "orphan_nudge_grace_hours": "soon"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "orphan_nudge_grace_hours" in err
    assert "'soon'" in err


def test_preflight_exits_on_negative_orphan_nudge_grace(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "orphan_nudge_grace_hours": -1})
    assert exc.value.code == 2
    assert "orphan_nudge_grace_hours" in capsys.readouterr().err


def test_preflight_exits_on_bool_orphan_nudge_grace(tmp_path, monkeypatch, capsys):
    """`True` is an int in Python — reject it so a stray bool isn't read as 1h."""
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "orphan_nudge_grace_hours": True})
    assert exc.value.code == 2
    assert "orphan_nudge_grace_hours" in capsys.readouterr().err


def test_preflight_exits_on_non_numeric_repo_orphan_nudge_grace(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {
                "tool": "cmux",
                "repos": [{"name": "r", "orphan_nudge_grace_hours": "soon"}],
            }
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "r" in err and "orphan_nudge_grace_hours" in err


def test_preflight_passes_on_numeric_orphan_nudge_grace(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    preflight(
        {
            "tool": "cmux",
            "orphan_nudge_grace_hours": 0,
            "repos": [{"name": "r", "orphan_nudge_grace_hours": 2.5}],
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_exits_on_non_string_dev_done_state(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [], "linear_dev_done_state": 5})
    assert exc.value.code == 2
    assert "linear_dev_done_state" in capsys.readouterr().err


def test_preflight_warns_when_linear_repo_but_no_api_key(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "repos": [{"name": "r", "linear_keys": ["PE"]}]})
    err = capsys.readouterr().err
    assert "LINEAR_API_KEY" in err
    assert "dev-done pill" in err


def test_preflight_silent_when_linear_repo_and_api_key_set(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_xxx")
    preflight({"tool": "cmux", "repos": [{"name": "r", "linear_keys": ["PE"]}]})
    assert capsys.readouterr().err == ""


def test_preflight_silent_when_no_linear_repo_even_without_key(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "repos": [{"name": "r", "path": "/x"}]})
    assert capsys.readouterr().err == ""


# ── linear_done_on_merge (the opt-in Linear write) ──────────────────────────


def test_preflight_exits_on_non_bool_global_done_on_merge(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "linear_done_on_merge": "yes"})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "linear_done_on_merge" in err
    assert "'yes'" in err


def test_preflight_exits_on_non_bool_repo_done_on_merge(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "repos": [{"name": "r", "linear_done_on_merge": 1}]})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "linear_done_on_merge" in err
    assert "'r'" in err


def test_preflight_exits_on_non_string_merge_done_state(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight({"tool": "cmux", "linear_merge_done_state": 5})
    assert exc.value.code == 2
    assert "linear_merge_done_state" in capsys.readouterr().err


def test_preflight_warns_when_done_on_merge_enabled_but_no_api_key(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "linear_done_on_merge": True, "repos": []})
    err = capsys.readouterr().err
    assert "linear_done_on_merge is enabled" in err
    assert "LINEAR_API_KEY" in err


def test_preflight_warns_when_repo_done_on_merge_enabled_but_no_api_key(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "repos": [{"name": "r", "linear_done_on_merge": True}]})
    assert "linear_done_on_merge is enabled" in capsys.readouterr().err


def test_preflight_silent_when_done_on_merge_enabled_and_api_key_set(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_xxx")
    preflight({"tool": "cmux", "linear_done_on_merge": True, "repos": []})
    assert capsys.readouterr().err == ""


def test_preflight_silent_when_done_on_merge_disabled_without_key(
    tmp_path, monkeypatch, capsys
):
    # Default-off: a missing key is irrelevant, so no warning.
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    preflight({"tool": "cmux", "linear_done_on_merge": False, "repos": []})
    assert capsys.readouterr().err == ""


# ── per-repo credential env-var names in the soft warnings ──────────────────


def test_preflight_warns_naming_the_repos_resolved_key_env(
    tmp_path, monkeypatch, capsys
):
    """The warning must name the variable *that repo* reads — with per-org
    credentials the global default is the wrong thing to tell the user to set."""
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "default-secret")
    monkeypatch.delenv("LIN_ACME", raising=False)
    preflight(
        {
            "tool": "cmux",
            "repos": [
                {
                    "name": "r",
                    "linear_keys": ["PE"],
                    "tickets": {"provider": "linear", "api_key_env": "LIN_ACME"},
                }
            ],
        }
    )
    err = capsys.readouterr().err
    assert "LIN_ACME is unset" in err
    assert "dev-done pill" in err


def test_preflight_warning_never_contains_a_resolved_secret(
    tmp_path, monkeypatch, capsys
):
    # Config holds env var *names*; a value must never reach a message.
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_super_secret_value")
    monkeypatch.delenv("LIN_ACME", raising=False)
    preflight(
        {
            "tool": "cmux",
            "linear_done_on_merge": True,
            "repos": [
                {
                    "name": "r",
                    "linear_keys": ["PE"],
                    "tickets": {"provider": "linear", "api_key_env": "LIN_ACME"},
                }
            ],
        }
    )
    err = capsys.readouterr().err
    assert "LIN_ACME" in err
    assert "lin_super_secret_value" not in err


def test_preflight_warns_once_per_distinct_env_var(tmp_path, monkeypatch, capsys):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LIN_A", raising=False)
    monkeypatch.delenv("LIN_B", raising=False)
    preflight(
        {
            "tool": "cmux",
            "repos": [
                {
                    "name": "a1",
                    "linear_keys": ["A"],
                    "tickets": {"provider": "linear", "api_key_env": "LIN_A"},
                },
                {
                    "name": "a2",
                    "linear_keys": ["A"],
                    "tickets": {"provider": "linear", "api_key_env": "LIN_A"},
                },
                {
                    "name": "b1",
                    "linear_keys": ["B"],
                    "tickets": {"provider": "linear", "api_key_env": "LIN_B"},
                },
            ],
        }
    )
    err = capsys.readouterr().err
    assert err.count("LIN_A is unset") == 1  # two repos, one variable, one warning
    assert err.count("LIN_B is unset") == 1


def test_preflight_silent_when_the_orgs_key_env_is_set(tmp_path, monkeypatch, capsys):
    # The org rung comes from apply_org_defaults, which validate_config runs.
    _all_required(tmp_path, monkeypatch)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.setenv("LIN_ACME", "acme-secret")
    preflight(
        {
            "tool": "cmux",
            "repos": [{"name": "r", "path": "/r", "org": "acme"}],
            "orgs": {
                "acme": {
                    "tickets": {
                        "provider": "linear",
                        "keys": ["PE"],
                        "api_key_env": "LIN_ACME",
                    }
                }
            },
        }
    )
    assert capsys.readouterr().err == ""


def test_preflight_accepts_credential_env_name_fields(tmp_path, monkeypatch):
    _all_required(tmp_path, monkeypatch)
    monkeypatch.setenv("JIRA_ACME", "x")
    monkeypatch.setenv("TRELLO_K", "x")
    monkeypatch.setenv("TRELLO_T", "x")
    preflight(
        {
            "tool": "cmux",
            "repos": [
                {
                    "name": "j",
                    "tickets": {"provider": "jira", "token_env": "JIRA_ACME"},
                },
                {
                    "name": "t",
                    "tickets": {
                        "provider": "trello",
                        "key_env": "TRELLO_K",
                        "token_env": "TRELLO_T",
                    },
                },
            ],
        }
    )


def test_preflight_rejects_a_credential_field_for_the_wrong_provider(
    tmp_path, monkeypatch, capsys
):
    _all_required(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        preflight(
            {
                "tool": "cmux",
                "repos": [
                    {
                        "name": "r",
                        "tickets": {"provider": "linear", "token_env": "NOPE"},
                    }
                ],
            }
        )
    assert exc.value.code == 2
    assert "token_env" in capsys.readouterr().err


# ── shipped config.example.json must be accepted ─────────────────────────────


def test_config_example_passes_validation():
    # config.example.json is the documented schema users copy settings from, so
    # a key the daemon rejects (e.g. the removed `use_linear`) turns the docs
    # into a trap. Running it through the real validators here makes that drift
    # a CI failure, not a bug report.
    cfg = json.loads(CONFIG_EXAMPLE.read_text())
    validate_config(cfg)  # raises SystemExit on any rejected key


def test_config_example_would_catch_a_dead_key():
    # Guard the guard: prove validate_config actually rejects the key we removed,
    # so a future re-introduction of `use_linear` (or similar) can't slip past.
    cfg = json.loads(CONFIG_EXAMPLE.read_text())
    cfg["use_linear"] = False
    with pytest.raises(SystemExit):
        validate_config(cfg)


def test_preflight_for_setup_skips_cship_hardfail(monkeypatch):
    """`cockpit setup` may be about to install cship/starship, so preflight with
    `for_setup=True` must not hard-fail on their absence — but the default
    (watch/other) path still does."""
    import pytest

    import cockpit.lib.preflight as pf

    def _which(binary):
        return f"/x/{binary}" if binary in ("gh", "git") else None

    monkeypatch.setattr(pf.shutil, "which", _which)
    cfg = {"use_cship": True, "repos": []}
    pf.preflight(cfg, for_setup=True)  # must not raise
    with pytest.raises(SystemExit) as exc:
        pf.preflight(cfg)  # default: hard-fails on missing cship
    assert exc.value.code == 2


# ── orgs: the wiring only preflight can catch ────────────────────────────────


def _org_cfg(**over) -> dict:
    cfg = {
        "repos": [{"name": "svc-auth", "path": "/a", "org": "acme"}],
        "orgs": {"acme": {"sidebar_color": "Magenta", "use_worktree": False}},
    }
    cfg.update(over)
    return cfg


def test_validate_orgs_accepts_a_well_formed_block():
    validate_config(_org_cfg())


def test_validate_orgs_rejects_a_repo_naming_an_undefined_org():
    # The silent failure this exists for: a typo'd org means the repo quietly
    # loses every default it expected (no tint, no use_worktree: false).
    cfg = _org_cfg()
    cfg["repos"][0]["org"] = "acmee"
    with pytest.raises(SystemExit):
        validate_config(cfg)


def test_validate_orgs_rejects_a_non_string_org():
    cfg = _org_cfg()
    cfg["repos"][0]["org"] = ["acme"]
    with pytest.raises(SystemExit):
        validate_config(cfg)


def test_validate_orgs_rejects_a_non_object_orgs_key():
    with pytest.raises(SystemExit):
        validate_config(_org_cfg(orgs=["acme"]))
    with pytest.raises(SystemExit):
        validate_config(_org_cfg(orgs={"acme": "Magenta"}))


@pytest.mark.parametrize("key", ["name", "path", "org"])
def test_validate_orgs_rejects_repo_identity_keys_as_org_defaults(key):
    with pytest.raises(SystemExit):
        validate_config(_org_cfg(orgs={"acme": {key: "x"}}))


def test_validate_config_checks_org_inherited_values(capsys):
    # The org block's *values* get no validator of their own — validate_config
    # merges first, so an org-level bad sidebar_color fails exactly like a
    # repo-level one would.
    with pytest.raises(SystemExit):
        validate_config(_org_cfg(orgs={"acme": {"sidebar_color": "Chartreuse"}}))
    assert "sidebar_color 'Chartreuse'" in capsys.readouterr().err


# ── _validate_workspace_backend (cmux verb + capability gate) ───────────────
# Every case must WARN, never die: the git+gh half of the dashboard works
# without any backend at all, so a partial one must still start.


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    from cockpit.lib.capabilities import probe

    probe.cache_clear()
    yield
    probe.cache_clear()


def _probing(monkeypatch, found) -> None:
    from cockpit.lib import capabilities

    monkeypatch.setattr(capabilities, "probe", lambda: found)


def _found(verbs: set[str], caps: set[str]):
    from cockpit.lib.capabilities import BackendProbe

    return BackendProbe(frozenset(verbs), frozenset(caps))


def _healthy():
    from cockpit.lib.capabilities import REQUIRED_CAPABILITIES, REQUIRED_VERBS

    return _found(
        set(REQUIRED_VERBS) | {"capabilities"},
        set(REQUIRED_CAPABILITIES),
    )


def test_workspace_backend_silent_when_cmux_is_current(monkeypatch, capsys):
    monkeypatch.setattr(preflight_mod, "resolve_tool", lambda: "cmux")
    _probing(monkeypatch, _healthy())
    preflight_mod._validate_workspace_backend()
    assert capsys.readouterr().err == ""


def test_workspace_backend_skipped_on_limux(monkeypatch, capsys):
    monkeypatch.setattr(preflight_mod, "resolve_tool", lambda: "limux")

    def _boom():
        raise AssertionError("probed a non-cmux backend")

    from cockpit.lib import capabilities

    monkeypatch.setattr(capabilities, "probe", _boom)
    preflight_mod._validate_workspace_backend()
    assert capsys.readouterr().err == ""


def test_workspace_backend_silent_when_cmux_answers_nothing(monkeypatch, capsys):
    # An empty verb list means the probe couldn't ask (cmux not answering) —
    # that's not a version verdict, so it warns about nothing.
    monkeypatch.setattr(preflight_mod, "resolve_tool", lambda: "cmux")
    _probing(monkeypatch, _found(set(), set()))
    preflight_mod._validate_workspace_backend()
    assert capsys.readouterr().err == ""


def test_workspace_backend_warns_on_a_missing_verb(monkeypatch, capsys):
    monkeypatch.setattr(preflight_mod, "resolve_tool", lambda: "cmux")
    healthy = _healthy()
    _probing(
        monkeypatch,
        _found(set(healthy.verbs) - {"send-key"}, set(healthy.capabilities)),
    )
    preflight_mod._validate_workspace_backend()  # must not raise
    err = capsys.readouterr().err
    assert "`send-key`" in err
    assert "nudges and broadcast" in err


def test_workspace_backend_warns_when_cmux_predates_the_capabilities_verb(
    monkeypatch, capsys
):
    from cockpit.lib.capabilities import REQUIRED_VERBS

    monkeypatch.setattr(preflight_mod, "resolve_tool", lambda: "cmux")
    _probing(monkeypatch, _found(set(REQUIRED_VERBS), set()))
    preflight_mod._validate_workspace_backend()
    err = capsys.readouterr().err
    assert "predates `cmux capabilities`" in err
    # …and it does NOT also list every capability as individually missing.
    assert "terminal.replay.v1" not in err


def test_workspace_backend_warns_on_a_missing_capability(monkeypatch, capsys):
    monkeypatch.setattr(preflight_mod, "resolve_tool", lambda: "cmux")
    healthy = _healthy()
    _probing(
        monkeypatch,
        _found(set(healthy.verbs), set(healthy.capabilities) - {"terminal.replay.v1"}),
    )
    preflight_mod._validate_workspace_backend()
    err = capsys.readouterr().err
    assert "terminal.replay.v1" in err
    assert "screen preview" in err


def test_preflight_probes_the_backend_for_the_daemon_but_not_for_setup(
    tmp_path, monkeypatch
):
    _all_required(tmp_path, monkeypatch)
    calls: list[bool] = []
    monkeypatch.setattr(
        preflight_mod, "_validate_workspace_backend", lambda: calls.append(True)
    )
    preflight({"tool": "cmux"}, for_setup=True)
    assert calls == []
    preflight({"tool": "cmux"})
    assert calls == [True]
