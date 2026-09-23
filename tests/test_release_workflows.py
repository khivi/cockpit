"""tag.yml's `actions/checkout` must KEEP its credentials.

The checkout's persisted token is what authenticates the `git push origin
"v$v"` two steps later in tag.yml, and that push is what fires release.yml and
publish.yml (AGENTS.md "`.github/workflows/tag.yml`'s checkout must keep its
credentials"). Setting `persist-credentials: false` there breaks the release
pipeline silently: zizmor is satisfied by the change (it's the artipacked
fix everywhere else), the tag never lands, and neither downstream workflow
runs. `.github/zizmor.yml` carries a dedicated `tag.yml` exemption from
`artipacked` for exactly this reason, so this module also pins that the
exemption survives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
TAG_YML = WORKFLOWS_DIR / "tag.yml"
ZIZMOR_YML = REPO_ROOT / ".github" / "zizmor.yml"


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _checkout_steps(doc: Any) -> list[dict[str, Any]]:
    """Every step across every job whose `uses` names `actions/checkout`."""
    steps: list[dict[str, Any]] = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if step.get("uses", "").startswith("actions/checkout"):
                steps.append(step)
    return steps


def _persist_credentials(step: dict[str, Any]) -> bool | None:
    """`with.persist-credentials`, or None when the step doesn't set it (the
    action's own default is `true`, so unset is the "keeps credentials" case)."""
    value = (step.get("with") or {}).get("persist-credentials")
    if value is None:
        return None
    # YAML 1.1 coerces a bare `false` to bool; catch the quoted spelling too.
    if isinstance(value, str):
        return value.strip().lower() != "false"
    return bool(value)


def _sibling_checkout_workflows() -> list[Path]:
    """Every OTHER workflow with a checkout step — derived from what is on
    disk rather than a hardcoded list, so a new workflow is picked up instead
    of silently unchecked."""
    others = sorted(p for p in WORKFLOWS_DIR.glob("*.yml") if p != TAG_YML)
    return [p for p in others if _checkout_steps(_load_yaml(p))]


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_tag_yml_checkout_does_not_strip_credentials() -> None:
    """No checkout step in tag.yml may set `persist-credentials: false` — that
    token is what authenticates the tag push two steps later."""
    steps = _checkout_steps(_load_yaml(TAG_YML))
    assert steps, "tag.yml has no actions/checkout step to guard"
    for step in steps:
        assert (
            _persist_credentials(step) is not False
        ), f"tag.yml checkout step sets persist-credentials: false: {step}"


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_sibling_workflows_still_strip_credentials() -> None:
    """The regression this rule guards against is answering an apparent
    inconsistency by stripping `persist-credentials` everywhere, tag.yml
    included, rather than adding it only where it is missing. A blanket strip
    fails here loudly instead of waiting for someone to re-read tag.yml."""
    siblings = _sibling_checkout_workflows()
    assert siblings, "no sibling workflow with a checkout step was found"
    for path in siblings:
        for step in _checkout_steps(_load_yaml(path)):
            assert _persist_credentials(step) is False, (
                f"{path.name} checkout step no longer sets "
                f"persist-credentials: false: {step}"
            )


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_tag_yml_keeps_its_artipacked_exemption() -> None:
    """The other way this regression arrives: removing tag.yml's `artipacked`
    exemption rather than its credentials. Without the exemption zizmor's hook
    reports tag.yml, pushing someone to "fix" it the forbidden way."""
    ignore = (_load_yaml(ZIZMOR_YML).get("rules") or {}).get("artipacked", {}).get(
        "ignore"
    ) or []
    assert (
        "tag.yml" in ignore
    ), f"zizmor.yml no longer exempts tag.yml from artipacked: {ignore}"
