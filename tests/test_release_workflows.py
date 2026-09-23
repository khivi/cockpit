"""tag.yml's `actions/checkout` must KEEP its credentials.

The checkout's persisted token is what authenticates the `git push origin
"v$v"` two steps later in tag.yml, and that push is what fires release.yml and
publish.yml (AGENTS.md "`.github/workflows/tag.yml`'s checkout must keep its
credentials"). Setting `persist-credentials: false` there breaks the release
pipeline silently: zizmor is satisfied by the change (it's the artipacked fix
everywhere else), the tag never lands, and neither downstream workflow runs.
`.github/zizmor.yml` carries a dedicated `tag.yml` exemption from `artipacked`
for exactly this reason, so this module also pins that the exemption survives.

Text, not a YAML parse: the flag is forbidden ANYWHERE in tag.yml, so which
step it sits under never has to be resolved. A parser would cost a dependency
in three places — the dev group and both pre-push hooks' own environments.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
TAG_YML = WORKFLOWS_DIR / "tag.yml"
ZIZMOR_YML = REPO_ROOT / ".github" / "zizmor.yml"

# Both spellings YAML accepts for the same value, and any run of spaces.
_STRIPS_CREDENTIALS = re.compile(r"""persist-credentials:\s*["']?false["']?""")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sibling_checkout_workflows() -> list[Path]:
    """Every OTHER workflow with a checkout step — derived from what is on
    disk rather than a hardcoded list, so a new workflow is picked up instead
    of silently unchecked."""
    others = sorted(p for p in WORKFLOWS_DIR.glob("*.yml") if p != TAG_YML)
    return [p for p in others if "actions/checkout" in _text(p)]


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_tag_yml_checkout_does_not_strip_credentials() -> None:
    """tag.yml may not set `persist-credentials: false` at all — that token is
    what authenticates the tag push two steps later."""
    body = _text(TAG_YML)
    assert "actions/checkout" in body, "tag.yml has no checkout step to guard"
    assert not _STRIPS_CREDENTIALS.search(body), (
        "tag.yml sets persist-credentials: false — the tag push loses its "
        "token and release.yml/publish.yml never fire"
    )


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_sibling_workflows_still_strip_credentials() -> None:
    """The regression this rule guards against is answering an apparent
    inconsistency by stripping `persist-credentials` everywhere, tag.yml
    included, rather than adding it only where it is missing. A blanket strip
    fails here loudly instead of waiting for someone to re-read tag.yml."""
    siblings = _sibling_checkout_workflows()
    assert siblings, "no sibling workflow with a checkout step was found"
    for path in siblings:
        assert _STRIPS_CREDENTIALS.search(_text(path)), (
            f"{path.name} no longer sets persist-credentials: false — if the "
            "flag is being dropped everywhere, tag.yml's exemption stops "
            "reading as deliberate"
        )


@pytest.mark.covers("release.tag-yml.keeps-credentials")
def test_tag_yml_keeps_its_artipacked_exemption() -> None:
    """The other way this regression arrives: removing tag.yml's `artipacked`
    exemption rather than its credentials. Without the exemption zizmor's hook
    reports tag.yml, pushing someone to "fix" it the forbidden way."""
    _, found, rest = _text(ZIZMOR_YML).partition("artipacked:\n    ignore:\n")
    assert found, "zizmor.yml has no artipacked ignore list"
    # Stop at the next rule key (two spaces, then non-space) so a tag.yml
    # exemption under a *different* rule cannot satisfy this.
    entries = re.split(r"\n  (?=\S)", rest)[0]
    assert (
        "- tag.yml" in entries
    ), f"zizmor.yml no longer exempts tag.yml from artipacked: {entries!r}"
