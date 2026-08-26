"""The TUI's outward row keys must refuse under --dry."""

from unittest.mock import patch

from cockpit.tui.app import CockpitApp


def _app(dry):
    return CockpitApp(
        slow_tick=lambda *a, **k: None,
        fast_tick=lambda: None,
        slow_secs=300,
        fast_secs=30,
        dry=dry,
    )


def test_outward_actions_refuse_under_dry():
    app = _app(True)
    seen = []
    with patch.object(CockpitApp, "notify", lambda self, m, **k: seen.append(m)):
        assert app._blocked_by_dry("new workspace") is True
    assert "disabled under --dry" in seen[0]


def test_outward_actions_run_when_not_dry():
    app = _app(False)
    assert app._blocked_by_dry("new workspace") is False
