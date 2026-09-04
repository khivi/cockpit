"""Tests for `cockpit config inspect` (cockpit/config_cmd.py).

CLI entry-point layer: writes a real (isolated) `config.json` and asserts on
`main()`'s stdout/stderr/exit code — the org merge and `{repo}` expansion
themselves are `lib.config`'s own tests (`tests/lib/test_config.py`), and the
`TicketProvider` strategy table is `lib.tickets`'s. This module only has to
prove it prints what `load_config()` resolved, scopes correctly, and never
lets a credential value reach output.
"""

from __future__ import annotations

import json

import cockpit.config_cmd as config_cmd
import cockpit.lib.config as config_mod


def _write_cfg(cfg: dict) -> None:
    config_mod.CONFIG_PATH.write_text(json.dumps(cfg))
    config_mod.reset_config_cache()


def _acme_cfg() -> dict:
    return {
        "repos": [
            {
                "name": "svc-auth",
                "path": "/a",
                "org": "acme",
                "tickets": {"project": "Auth"},
            },
            {"name": "svc-web", "path": "/b", "org": "acme", "sidebar_color": "Cyan"},
            {"name": "solo", "path": "/c"},
        ],
        "orgs": {
            "acme": {
                "sidebar_color": "Magenta",
                "sidebar_tag": "{repo}",
                "tickets": {"provider": "linear", "keys": ["ACME"]},
            }
        },
    }


def test_default_output_is_the_effective_post_merge_config(capsys):
    _write_cfg(_acme_cfg())
    assert config_cmd.main(["inspect"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == config_mod.load_config()


def test_org_scalar_inherited_and_repo_still_wins(capsys):
    """`svc-auth` has no `sidebar_color` of its own, so it inherits the org's;
    `svc-web` set its own and keeps it (repo wins outright over an org scalar)."""
    _write_cfg(_acme_cfg())
    assert config_cmd.main(["inspect"]) == 0
    repos = {r["name"]: r for r in json.loads(capsys.readouterr().out)["repos"]}
    assert repos["svc-auth"]["sidebar_color"] == "Magenta"
    assert repos["svc-web"]["sidebar_color"] == "Cyan"
    assert "sidebar_color" not in repos["solo"]


def test_tickets_block_unions_per_field_with_repo_winning(capsys):
    """`svc-auth` sets only `tickets.project`; the org's `provider`/`keys` must
    still show through the merge (one level deep, per-field, repo wins) rather
    than the repo's partial block replacing the org's whole one."""
    _write_cfg(_acme_cfg())
    assert config_cmd.main(["inspect"]) == 0
    repos = {r["name"]: r for r in json.loads(capsys.readouterr().out)["repos"]}
    assert repos["svc-auth"]["tickets"] == {
        "provider": "linear",
        "keys": ["ACME"],
        "project": "Auth",
    }


def test_repo_token_expands_to_each_members_own_name(capsys):
    _write_cfg(_acme_cfg())
    assert config_cmd.main(["inspect"]) == 0
    repos = {r["name"]: r for r in json.loads(capsys.readouterr().out)["repos"]}
    assert repos["svc-auth"]["sidebar_tag"] == "svc-auth"
    assert repos["svc-web"]["sidebar_tag"] == "svc-web"
    assert "sidebar_tag" not in repos["solo"]


def test_repo_scoping_matches_by_name_case_insensitively(capsys):
    _write_cfg(_acme_cfg())
    assert config_cmd.main(["inspect", "--repo", "SVC-AUTH"]) == 0
    out = capsys.readouterr().out
    body, _, _tail = out.partition("\n\ntickets.provider:")
    assert json.loads(body)["name"] == "svc-auth"


def test_repo_scoping_falls_back_to_path_basename(capsys):
    """`_repo_label`'s contract: no `name` → the path's basename, matching
    `broadcast._repo_label` exactly."""
    _write_cfg({"repos": [{"path": "/x/y/no-name-repo"}]})
    assert config_cmd.main(["inspect", "--repo", "no-name-repo"]) == 0
    out = capsys.readouterr().out
    body, _, _tail = out.partition("\n\ntickets.provider:")
    assert json.loads(body)["path"] == "/x/y/no-name-repo"


def test_unknown_repo_exits_2_and_lists_configured_repos(capsys):
    _write_cfg(_acme_cfg())
    assert config_cmd.main(["inspect", "--repo", "bogus"]) == 2
    err = capsys.readouterr().err
    assert "unknown repo 'bogus'" in err
    assert "solo" in err
    assert "svc-auth" in err
    assert "svc-web" in err


def test_scoped_repo_surfaces_resolved_ticket_provider(capsys):
    _write_cfg(_acme_cfg())
    assert config_cmd.main(["inspect", "--repo", "svc-auth"]) == 0
    out = capsys.readouterr().out
    assert "tickets.provider: linear" in out


def test_scoped_repo_with_no_ticket_provider_reports_none(capsys):
    _write_cfg(_acme_cfg())
    assert config_cmd.main(["inspect", "--repo", "solo"]) == 0
    out = capsys.readouterr().out
    assert "tickets.provider: none" in out
    assert "credentials:" not in out


def test_credential_env_var_name_is_shown_but_never_its_value(monkeypatch, capsys):
    """The hard constraint: only the env var NAME and a set/unset flag may reach
    output — never `os.environ`'s value. Set a fake token to a sentinel and
    assert the sentinel is absent while the name is present."""
    sentinel = "sk-do-not-print-me-12345"
    monkeypatch.setenv("LINEAR_TEST_TOKEN", sentinel)
    cfg = {
        "repos": [
            {
                "name": "svc-auth",
                "path": "/a",
                "tickets": {"provider": "linear", "token_env": "LINEAR_TEST_TOKEN"},
            }
        ]
    }
    _write_cfg(cfg)
    assert config_cmd.main(["inspect", "--repo", "svc-auth"]) == 0
    out = capsys.readouterr().out
    assert "LINEAR_TEST_TOKEN: set" in out
    assert sentinel not in out


def test_unset_credential_env_var_is_flagged_unset(monkeypatch, capsys):
    monkeypatch.delenv("LINEAR_UNSET_TOKEN", raising=False)
    cfg = {
        "repos": [
            {
                "name": "svc-auth",
                "path": "/a",
                "tickets": {"provider": "linear", "token_env": "LINEAR_UNSET_TOKEN"},
            }
        ]
    }
    _write_cfg(cfg)
    assert config_cmd.main(["inspect", "--repo", "svc-auth"]) == 0
    out = capsys.readouterr().out
    assert "LINEAR_UNSET_TOKEN: unset" in out


def test_github_provider_has_no_credential_env_vars(capsys):
    """GitHub authenticates through `gh` — no `credentials:` section at all,
    same shape as `tickets: none`."""
    cfg = {
        "repos": [{"name": "svc-auth", "path": "/a", "tickets": {"provider": "github"}}]
    }
    _write_cfg(cfg)
    assert config_cmd.main(["inspect", "--repo", "svc-auth"]) == 0
    out = capsys.readouterr().out
    assert "tickets.provider: github" in out
    assert "credentials:" not in out


def test_a_resolved_tag_prints_as_the_glyph_it_will_render_as(capsys):
    """The sibling of the expansion test above, which round-trips through
    `json.loads` and so would pass with either escaping. A resolved
    `sidebar_tag` is one of the two things this command exists to show, and
    `json.dumps` defaults to `ensure_ascii=True` — which prints the tag you'd
    see in the sidebar back as `\\ud83d\\udee1`, i.e. unreadable exactly where
    it has to be read."""
    cfg = _acme_cfg()
    cfg["orgs"]["acme"]["sidebar_tag"] = "🛡️ {repo}"
    _write_cfg(cfg)
    assert config_cmd.main(["inspect", "--repo", "svc-auth"]) == 0
    out = capsys.readouterr().out
    assert "🛡️ svc-auth" in out
    assert "\\ud83d" not in out
