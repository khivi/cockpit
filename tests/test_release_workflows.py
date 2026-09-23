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

try:
    import yaml

    _HAVE_YAML = True
except ImportError:  # pragma: no cover - pyyaml is a declared test dependency
    yaml = None  # type: ignore[assignment]
    _HAVE_YAML = False

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
            uses = step.get("uses", "")
            if uses.startswith("actions/checkout"):
                steps.append(step)
    return steps


def _persist_credentials(step: dict[str, Any]) -> bool | None:
    """`with.persist-credentials`, or None when the step doesn't set it (the
    action's own default is `true`, so unset is the "keeps credentials" case)."""
    value = (step.get("with") or {}).get("persist-credentials")
    if value is None:
        return None
    # YAML 1.1 coerces bare `false`/`true` to bool already; guard the
    # string-literal spelling some workflows use inside quotes.
    if isinstance(value, str):
        return value.strip().lower() != "false"
    return bool(value)


def _checkout_steps_by_line_scan(path: Path) -> list[bool | None]:
    """Fallback used only when pyyaml is unavailable: walk the file scanning
    for `uses: actions/checkout` blocks and the `persist-credentials` line
    that follows before the next `- ` step marker or dedent."""
    lines = path.read_text(encoding="utf-8").splitlines()
    results: list[bool | None] = []
    in_checkout_step = False
    step_indent = 0
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if stripped.startswith("- uses:") and "actions/checkout" in stripped:
            in_checkout_step = True
            step_indent = indent
            results.append(None)
            continue
        if in_checkout_step:
            if stripped.startswith("- ") and indent <= step_indent:
                in_checkout_step = False
            elif "persist-credentials:" in stripped:
                value = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                results[-1] = value.lower() != "false"
    return results


def _workflow_files() -> list[Path]:
    return sorted(p for p in WORKFLOWS_DIR.glob("*.yml") if p != TAG_YML)


def _sibling_checkout_workflows() -> list[Path]:
    """Every OTHER workflow file that has at least one `actions/checkout`
    step — derived from what's on disk rather than a hardcoded count, so a
    new workflow is picked up automatically instead of silently unchecked."""
    siblings = []
    for path in _workflow_files():
        if _HAVE_YAML:
            steps = _checkout_steps(_load_yaml(path))
            if steps:
                siblings.append(path)
        else:
            if _checkout_steps_by_line_scan(path):
                siblings.append(path)
    return siblings


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_tag_yml_checkout_does_not_strip_credentials() -> None:
    """No checkout step in tag.yml may set `persist-credentials: false` — that
    token is what authenticates the tag push two steps later."""
    if _HAVE_YAML:
        steps = _checkout_steps(_load_yaml(TAG_YML))
        assert steps, "tag.yml has no actions/checkout step to guard"
        for step in steps:
            assert (
                _persist_credentials(step) is not False
            ), f"tag.yml checkout step sets persist-credentials: false: {step}"
    else:
        results = _checkout_steps_by_line_scan(TAG_YML)
        assert results, "tag.yml has no actions/checkout step to guard"
        assert all(
            r is not False for r in results
        ), f"tag.yml checkout step sets persist-credentials: false: {results}"


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_sibling_workflows_still_strip_credentials() -> None:
    """The regression this rule guards against is fixing an apparent
    inconsistency by stripping `persist-credentials` everywhere, tag.yml
    included, rather than by adding it only where it's missing. Pin that
    every OTHER workflow with a checkout step still sets the flag, so a
    blanket strip fails here loudly instead of only being caught by someone
    re-reading tag.yml's comments."""
    siblings = _sibling_checkout_workflows()
    assert siblings, "no sibling workflow with a checkout step was found"

    if _HAVE_YAML:
        for path in siblings:
            for step in _checkout_steps(_load_yaml(path)):
                assert _persist_credentials(step) is False, (
                    f"{path.name} checkout step no longer sets "
                    f"persist-credentials: false: {step}"
                )
    else:
        for path in siblings:
            for value in _checkout_steps_by_line_scan(path):
                assert value is False, (
                    f"{path.name} checkout step no longer sets "
                    f"persist-credentials: false"
                )


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_tag_yml_keeps_its_artipacked_exemption() -> None:
    """The other way this regression arrives: removing tag.yml's dedicated
    `artipacked` exemption in zizmor.yml rather than the credentials
    themselves. Without the exemption, zizmor's own hook would eventually
    push someone to "fix" the finding by adding persist-credentials: false."""
    if _HAVE_YAML:
        doc = _load_yaml(ZIZMOR_YML)
        ignore = doc.get("rules", {}).get("artipacked", {}).get("ignore", []) or []
        assert (
            "tag.yml" in ignore
        ), f"zizmor.yml no longer exempts tag.yml from artipacked: {ignore}"
    else:
        text = ZIZMOR_YML.read_text(encoding="utf-8")
        assert (
            "artipacked" in text and "tag.yml" in text
        ), "zizmor.yml no longer names tag.yml under artipacked"
