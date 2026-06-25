"""Loader for the packaged first-turn prompt templates (`cockpit.lib.templates`)."""

from __future__ import annotations

from importlib.resources import files

import pytest

from cockpit.lib import templates

# Every template the spawn builders render, with the slots each declares.
_TEMPLATES = {
    "plan_tail": {},
    "linear": {"branch", "identifier", "plan_tail"},
    "jira": {"branch", "identifier", "plan_tail"},
    "github_issue": {"branch", "issue_ref", "view_cmd", "plan_tail"},
    "slack_fetch": {"branch", "url", "plan_tail"},
    "slack_context": {"branch", "url", "plan_tail"},
    "plan_only": {"branch", "source_block"},
    "review": {"command", "context"},
    "actions": {
        "branch",
        "source",
        "conclusion",
        "head_branch",
        "run_url",
        "related_pr_block",
        "log_cmd",
        "plan_tail",
    },
    # Per-PR / orphan worktree prompts (cockpit.lib.prompts).
    "pr": {"number", "title", "branch", "author", "url", "action", "authority"},
    "pr_authority": set(),
    "pr_action_comments": {"unaddressed"},
    "pr_action_changes_requested": set(),
    "pr_action_ci": {"number"},
    "pr_action_conflicts": set(),
    "pr_action_approved": set(),
    "pr_action_clean": set(),
    "orphan": {"short", "branch"},
}


def test_every_template_ships_as_a_txt_file():
    """The loader resolves each template from the packaged `cockpit/prompts/`."""
    for name in _TEMPLATES:
        assert (files("cockpit.prompts") / f"{name}.txt").is_file()


def test_no_template_escapes_the_registry():
    """Every `.txt` on disk is declared in `_TEMPLATES` — so a newly added
    template can't silently ship untested (the disk→dict direction the
    `_TEMPLATES`-keyed slot tests above don't cover on their own)."""
    on_disk = {
        p.name[: -len(".txt")]
        for p in files("cockpit.prompts").iterdir()
        if p.name.endswith(".txt")
    }
    assert on_disk == set(_TEMPLATES), (
        f"undeclared templates: {on_disk - set(_TEMPLATES)}; "
        f"stale entries: {set(_TEMPLATES) - on_disk}"
    )


@pytest.mark.parametrize("name,slots", _TEMPLATES.items())
def test_render_fills_every_declared_slot(name, slots):
    """Rendering with each declared slot leaves no `{...}` placeholder behind."""
    rendered = templates.render(name, **{s: f"<{s}>" for s in slots})
    assert "{" not in rendered and "}" not in rendered
    for s in slots:
        assert f"<{s}>" in rendered


def test_missing_slot_raises_loudly():
    """A forgotten field is a KeyError, not a silent stray placeholder."""
    with pytest.raises(KeyError):
        templates.render("linear", branch="b", identifier="PE-1")  # no plan_tail


def test_unknown_template_raises():
    with pytest.raises(FileNotFoundError):
        templates.render("does_not_exist")


def test_raw_is_cached():
    """Repeated loads return the same cached object (no re-read per render)."""
    assert templates._raw("plan_tail") is templates._raw("plan_tail")
