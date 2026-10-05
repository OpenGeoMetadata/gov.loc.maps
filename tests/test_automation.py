import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def automation(monkeypatch, tmp_path):
    for name in (
        "CONTINUATION",
        "AUTO_CONTINUE",
        "ROUND",
        "RETRY_FAILED",
        "MAX_REQUESTS",
        "SEED_PILOT",
        "BOOTSTRAP_FULL",
        "GITHUB_OUTPUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
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


def test_explicit_bootstrap_uses_pilot_only_without_production_state(automation, monkeypatch):
    monkeypatch.setenv("SEED_PILOT", "true")
    automation.restore_channel = Mock(side_effect=[False, True])
    automation.State = Mock()
    automation.promote_pilot = Mock()
    automation.load_state()
    assert [c.args[0] for c in automation.restore_channel.call_args_list] == ["production", "pilot"]
    automation.promote_pilot.assert_called_once()
    automation.State.return_value.close.assert_called_once()


def test_existing_production_state_never_promoted(automation, monkeypatch):
    monkeypatch.setenv("SEED_PILOT", "true")
    automation.restore_channel = Mock(return_value=True)
    automation.promote_pilot = Mock()
    automation.load_state()
    automation.restore_channel.assert_called_once_with("production")
    automation.promote_pilot.assert_not_called()


def test_cooldown_exits_batch_without_network_or_sleep(automation, monkeypatch, tmp_path):
    from loc_maps.state import State

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    state = State(Path(".state"))
    state.set("pause_until", automation.time.time() + 8000)
    state.close()
    automation.subprocess = Mock()
    automation.batch()
    automation.subprocess.run.assert_not_called()
    assert output.read_text() == "status=paused\n"
    automation.continue_run()
    automation.command.assert_not_called()


@pytest.mark.parametrize(
    "condition,dispatch",
    [("paused", True), ("complete", False), ("cooldown", False), ("running", False)],
)
def test_recovery_only_dispatches_eligible_pause(
    automation, monkeypatch, tmp_path, condition, dispatch
):
    from loc_maps.common import write_json

    monkeypatch.chdir(tmp_path)
    automation.api = Mock(
        return_value={
            "workflow_runs": [
                {
                    "id": 123,
                    "status": "in_progress" if condition == "running" else "completed",
                    "conclusion": "success",
                }
            ]
        }
    )
    write_json(
        Path("dist/recovery/status.json"),
        {
            "job": {"status": "complete" if condition == "complete" else "paused", "mode": "full"},
            "pause_until": automation.time.time() + 3600 if condition == "cooldown" else 0,
        },
    )
    automation.recover_paused()
    assert (
        any("workflow" in c.args and "run" in c.args for c in automation.command.call_args_list)
        == dispatch
    )


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_recovery_does_not_restart_failed_or_cancelled_jobs(automation, conclusion):
    automation.api = Mock(
        return_value={
            "workflow_runs": [
                {
                    "status": "completed",
                    "conclusion": conclusion,
                    "html_url": "https://github.com/example/run",
                }
            ]
        }
    )
    with pytest.raises(RuntimeError, match="needs inspection"):
        automation.recover_paused()
    automation.command.assert_not_called()


def test_daily_durable_checkpoint_is_not_replaced(automation):
    automation.api = Mock(
        return_value=[
            {
                "tag_name": f"checkpoint-production-{automation.now()[:10]}",
                "assets": [{"name": "loc-state.tar.gz"}],
            }
        ]
    )
    automation.retain_checkpoint()
    automation.command.assert_not_called()


def test_small_initial_batch_retains_pause_status(automation, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAX_REQUESTS", "5")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    automation.subprocess = Mock()
    automation.subprocess.run.return_value.returncode = 75
    automation.batch()
    args = automation.subprocess.run.call_args.args[0]
    assert args[args.index("--max-requests") + 1] == "5"
    assert (tmp_path / "output").read_text() == "status=paused\n"


def test_checkpoint_selection_uses_creation_time_not_artifact_id(automation):
    automation.api = Mock(
        return_value={
            "artifacts": [
                {
                    "id": 900,
                    "created_at": "2026-10-05T03:02:45Z",
                    "expired": False,
                    "workflow_run": {"id": 1},
                },
                {
                    "id": 800,
                    "created_at": "2026-10-05T03:07:45Z",
                    "expired": False,
                    "workflow_run": {"id": 2},
                },
                {
                    "id": 1000,
                    "created_at": "2026-10-05T04:00:00Z",
                    "expired": True,
                    "workflow_run": {"id": 3},
                },
            ]
        }
    )
    automation.restore = Mock()
    assert automation.restore_channel("production")
    assert automation.command.call_args.args[:4] == ("gh", "run", "download", "2")
    automation.restore.assert_called_once()
