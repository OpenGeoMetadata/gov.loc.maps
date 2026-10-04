import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def automation(monkeypatch):
    monkeypatch.setenv("REPOSITORY", "OpenGeoMetadata/gov.loc.maps")
    monkeypatch.setenv("MODE", "full")
    path = Path(__file__).parents[1] / ".github/scripts/automation.py"
    spec = importlib.util.spec_from_file_location("workflow_automation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.command = Mock(return_value="")
    return module


def test_continuation_preserves_mode_and_is_bounded(automation, monkeypatch):
    monkeypatch.setenv("AUTO_CONTINUE", "true")
    monkeypatch.setenv("ROUND", "119")
    automation.continue_run()
    assert "round=120" in automation.command.call_args.args
    assert "mode=full" in automation.command.call_args.args
    automation.command.reset_mock()
    monkeypatch.setenv("ROUND", "120")
    automation.continue_run()
    automation.command.assert_not_called()


def test_disabled_continuation_does_not_dispatch(automation, monkeypatch):
    monkeypatch.setenv("AUTO_CONTINUE", "false")
    automation.continue_run()
    automation.command.assert_not_called()


def test_release_snapshot_recovers_expired_artifacts(automation):
    automation.api = Mock(
        side_effect=[
            {"artifacts": []},
            [
                {
                    "tag_name": "snapshot-production-123",
                    "created_at": "2026-10-04",
                    "assets": [{"name": "loc-state.tar.gz"}],
                }
            ],
        ]
    )
    automation.restore = Mock()
    automation.load_state()
    automation.restore.assert_called_once_with(
        Path("dist/restore/loc-state.tar.gz"), Path(".state")
    )
    assert any(
        "download" in c.args and "snapshot-production-123" in c.args
        for c in automation.command.call_args_list
    )


def test_existing_collection_requires_recoverable_history(automation):
    automation.api = Mock(side_effect=[{"artifacts": []}, []])
    automation.command.return_value = "metadata-aardvark"
    with pytest.raises(RuntimeError, match="No recoverable checkpoint"):
        automation.load_state()
