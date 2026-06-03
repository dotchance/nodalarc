"""Tests for session manager — recovery, orphan cleanup, stale directory cleanup."""

import asyncio
import json
import os
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from nodalarc.catalog_paths import CatalogPathError
from vs_api.session_manager import SessionManager, _pid_alive

from tests.conftest import build_segment_session_dict


def _segment_session_yaml(name: str, data_dir: Path) -> str:
    return yaml.dump(
        build_segment_session_dict(
            name=name,
            data_dir=str(data_dir),
            constellation="configs/constellations/demo-36.yaml",
            ground_stations="configs/ground-stations/sets/demo.yaml",
            protocol="isis",
            orbit_propagator="keplerian-circular",
        ),
        sort_keys=False,
    )


@pytest.fixture
def tmp_sessions(tmp_path):
    """Create a temporary sessions directory with a valid session config."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Write a minimal valid session YAML
    session_yaml = sessions_dir / "test-session.yaml"
    session_yaml.write_text(_segment_session_yaml("Test Session", data_dir))

    return {
        "sessions_dir": sessions_dir,
        "data_dir": data_dir,
        "session_yaml": session_yaml,
    }


class TestSessionCatalog:
    def test_scan_sessions_reports_resolved_constellation_name(self, tmp_sessions):
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))

        sessions = mgr.list_sessions()

        assert len(sessions) == 1
        assert sessions[0]["name"] == "Test Session"
        assert sessions[0]["constellation"] == "demo-36"
        assert sessions[0]["routing_stack"] == "isis-plain"

    def test_scan_sessions_reports_multi_segment_label(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        source = Path("configs/sessions/earth-leo-meo-geo.yaml")
        if not source.exists():
            pytest.skip("earth-leo-meo-geo.yaml not available")
        (sessions_dir / "earth-leo-meo-geo.yaml").write_text(source.read_text())
        mgr = SessionManager(str(sessions_dir))

        sessions = mgr.list_sessions()

        assert len(sessions) == 1
        assert sessions[0]["name"] == "earth-leo-meo-geo"
        assert sessions[0]["constellation"] == "leo + meo + geo"


def _make_session_dir(data_dir: Path, session_id: str, mi_pid: int = 0, orch_pid: int = 0) -> Path:
    """Create a session directory with session-state.json."""
    d = data_dir / session_id
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "session_id": session_id,
        "data_dir": str(d),
        "mi_pid": mi_pid,
        "vsapi_pid": 0,
        "orchestrator_pid": orch_pid,
        "session_config": "configs/sessions/test-session.yaml",
        "db_path": str(d / "session.db"),
    }
    (d / "session-state.json").write_text(json.dumps(state))
    # Touch a fake db
    (d / "session.db").write_text("")
    return d


class TestPidAlive:
    def test_zero_pid(self):
        assert _pid_alive(0) is False

    def test_negative_pid(self):
        assert _pid_alive(-1) is False

    def test_current_process(self):
        assert _pid_alive(os.getpid()) is True

    def test_dead_pid(self):
        # Use a very high PID that's almost certainly not in use
        assert _pid_alive(4194300) is False


class TestRecoverSession:
    def test_no_data_dirs(self, tmp_path):
        """Recovery returns None when no session configs exist."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        mgr = SessionManager(str(sessions_dir))
        assert mgr.recover_session() is None

    def test_no_live_sessions(self, tmp_sessions):
        """Recovery returns None when all PIDs are dead."""
        _make_session_dir(
            tmp_sessions["data_dir"],
            "old-session-001",
            mi_pid=4194301,  # Dead PID
            orch_pid=4194302,  # Dead PID
        )
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        assert mgr.recover_session() is None
        assert mgr.status == "idle"

    def test_recover_live_session(self, tmp_sessions):
        """Recovery finds a session with live PIDs."""
        my_pid = os.getpid()  # Use our own PID as "live"
        _make_session_dir(
            tmp_sessions["data_dir"],
            "live-session-001",
            mi_pid=my_pid,
            orch_pid=0,
        )
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        result = mgr.recover_session()
        assert result is not None
        assert result["session_id"] == "live-session-001"
        assert result["mi_pid"] == my_pid
        assert mgr.status == "ready"

    def test_recover_newest_live(self, tmp_sessions):
        """When multiple live sessions exist, recover the newest one."""
        import time

        my_pid = os.getpid()

        # Create older session (also "live" via our PID)
        _make_session_dir(
            tmp_sessions["data_dir"],
            "session-older",
            mi_pid=my_pid,
        )
        time.sleep(0.05)  # Ensure different mtime

        # Create newer session
        _make_session_dir(
            tmp_sessions["data_dir"],
            "session-newer",
            orch_pid=my_pid,
        )

        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        result = mgr.recover_session()
        assert result is not None
        assert result["session_id"] == "session-newer"

    def test_recover_skips_dead_finds_live(self, tmp_sessions):
        """Dead sessions are skipped even if newer; live one is found."""
        import time

        my_pid = os.getpid()

        # Older but live
        _make_session_dir(
            tmp_sessions["data_dir"],
            "session-live",
            mi_pid=my_pid,
        )
        time.sleep(0.05)

        # Newer but dead
        _make_session_dir(
            tmp_sessions["data_dir"],
            "session-dead",
            mi_pid=4194301,
            orch_pid=4194302,
        )

        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        result = mgr.recover_session()
        assert result is not None
        assert result["session_id"] == "session-live"

    def test_recover_sets_data_dir(self, tmp_sessions):
        """Recovery sets _current_data_dir to the session directory."""
        my_pid = os.getpid()
        d = _make_session_dir(
            tmp_sessions["data_dir"],
            "live-session-002",
            mi_pid=my_pid,
        )
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        mgr.recover_session()
        assert mgr._current_data_dir == d


class TestSessionPathContainment:
    def test_scan_fails_on_symlink_escape(self, tmp_path):
        if not hasattr(os, "symlink"):
            pytest.skip("symlink not supported on this platform")

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        outside = tmp_path / "outside.yaml"
        outside.write_text(_segment_session_yaml("Outside", tmp_path))
        try:
            (sessions_dir / "escape.yaml").symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlink creation not permitted: {exc}")

        with pytest.raises(CatalogPathError, match="escapes sessions root"):
            SessionManager(str(sessions_dir))

    def test_valid_session_map_uses_resolved_paths(self, tmp_sessions):
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))

        assert str(tmp_sessions["session_yaml"]) in mgr._valid_session_files()
        assert (
            mgr._validated_session_path(str(tmp_sessions["session_yaml"]))
            == tmp_sessions["session_yaml"].resolve()
        )


class TestKillAllSessionProcesses:
    def test_no_sessions(self, tmp_sessions):
        """No-op when no sessions exist."""
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        assert mgr.kill_all_session_processes() == 0

    def test_kills_live_pids(self, tmp_sessions):
        """Sends SIGTERM to live PIDs."""
        _make_session_dir(
            tmp_sessions["data_dir"],
            "session-to-kill",
            mi_pid=99999,
            orch_pid=99998,
        )
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))

        with (
            patch("vs_api.session_manager._pid_alive", return_value=True),
            patch("os.kill") as mock_kill,
        ):
            killed = mgr.kill_all_session_processes()
            assert killed == 2
            mock_kill.assert_any_call(99999, signal.SIGTERM)
            mock_kill.assert_any_call(99998, signal.SIGTERM)

    def test_skips_dead_pids(self, tmp_sessions):
        """Doesn't try to kill dead PIDs."""
        _make_session_dir(
            tmp_sessions["data_dir"],
            "session-dead",
            mi_pid=4194301,
            orch_pid=4194302,
        )
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        killed = mgr.kill_all_session_processes()
        assert killed == 0


class TestCleanupOldSessions:
    def test_cleanup_keeps_newest(self, tmp_sessions):
        """Keeps the newest N sessions and removes the rest."""
        import time

        for i in range(7):
            _make_session_dir(
                tmp_sessions["data_dir"],
                f"session-{i:03d}",
                mi_pid=4194300 + i,  # Dead PIDs
            )
            time.sleep(0.02)

        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        removed = mgr.cleanup_old_sessions(keep=3)
        assert removed == 4

        # Verify 3 remain
        remaining = [d for d in tmp_sessions["data_dir"].iterdir() if d.is_dir()]
        assert len(remaining) == 3

    def test_cleanup_spares_live_sessions(self, tmp_sessions):
        """Won't remove directories with live PIDs even if old."""
        import time

        my_pid = os.getpid()

        # Old but live
        _make_session_dir(
            tmp_sessions["data_dir"],
            "session-live-old",
            mi_pid=my_pid,
        )
        time.sleep(0.02)

        # Newer dead ones
        for i in range(3):
            _make_session_dir(
                tmp_sessions["data_dir"],
                f"session-dead-{i:03d}",
                mi_pid=4194300 + i,
            )
            time.sleep(0.02)

        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        removed = mgr.cleanup_old_sessions(keep=2)

        # Should remove 1 dead one (session-dead-000) but keep session-live-old
        assert removed == 1
        remaining = [d.name for d in tmp_sessions["data_dir"].iterdir() if d.is_dir()]
        assert "session-live-old" in remaining

    def test_cleanup_noop_when_few_sessions(self, tmp_sessions):
        """No removal when count <= keep."""
        _make_session_dir(tmp_sessions["data_dir"], "session-001")
        _make_session_dir(tmp_sessions["data_dir"], "session-002")

        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        removed = mgr.cleanup_old_sessions(keep=5)
        assert removed == 0


class TestCollectDataDirs:
    def test_collects_from_configs(self, tmp_sessions):
        """Reads data_dir from session YAML configs."""
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        dirs = mgr._collect_data_dirs()
        assert len(dirs) == 1
        assert dirs[0] == tmp_sessions["data_dir"]

    def test_deduplicates(self, tmp_sessions):
        """Multiple YAMLs with same data_dir produce one entry."""
        # Add second session with same data_dir
        yaml2 = tmp_sessions["sessions_dir"] / "test-session-2.yaml"
        yaml2.write_text(_segment_session_yaml("Test Session 2", tmp_sessions["data_dir"]))
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        dirs = mgr._collect_data_dirs()
        assert len(dirs) == 1


class _SwitchApi:
    def __init__(
        self,
        *,
        old_cr_gets_before_404: int | None = 0,
        post_create_statuses: list[dict] | None = None,
    ) -> None:
        self.old_cr_gets_before_404 = old_cr_gets_before_404
        self.old_cr_get_count = 0
        self.post_create_get_count = 0
        self.post_create_statuses = post_create_statuses or [
            {
                "metadata": {"generation": 1},
                "status": {
                    "phase": "Ready",
                    "message": "ready",
                    "observedGeneration": 1,
                },
            }
        ]
        self.created = False

    def delete_namespaced_custom_object(self, **_kwargs):
        return {}

    def get_namespaced_custom_object(self, **_kwargs):
        from kubernetes.client.rest import ApiException

        if not self.created:
            self.old_cr_get_count += 1
            if self.old_cr_gets_before_404 is None:
                return {"metadata": {"name": "current-session"}}
            if self.old_cr_get_count <= self.old_cr_gets_before_404:
                return {"metadata": {"name": "current-session"}}
            raise ApiException(status=404, reason="Not Found")

        idx = min(self.post_create_get_count, len(self.post_create_statuses) - 1)
        self.post_create_get_count += 1
        return self.post_create_statuses[idx]

    def create_namespaced_custom_object(self, **_kwargs):
        self.created = True
        return {}


class _SwitchCoreV1:
    def __init__(self, pod_counts: list[int] | None = None) -> None:
        self.pod_counts = pod_counts or [0]
        self.calls = 0

    def list_namespaced_pod(self, *_args, **_kwargs):
        idx = min(self.calls, len(self.pod_counts) - 1)
        self.calls += 1
        return SimpleNamespace(items=[object() for _ in range(self.pod_counts[idx])])


async def _no_sleep(_seconds: float) -> None:
    return None


def _patch_switch_k8s(monkeypatch, api: _SwitchApi, core: _SwitchCoreV1) -> None:
    import kubernetes.client
    import kubernetes.config

    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "CustomObjectsApi", lambda: api)
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: core)
    monkeypatch.setattr(
        "vs_api.session_manager.get_platform_config",
        lambda: SimpleNamespace(kubernetes_namespace="nodalarc"),
    )
    monkeypatch.setattr("vs_api.session_manager.asyncio.sleep", _no_sleep)


class TestSwitchFailLoud:
    def test_switch_fails_if_old_cr_does_not_finalize(self, tmp_sessions, monkeypatch):
        api = _SwitchApi(old_cr_gets_before_404=None)
        core = _SwitchCoreV1([0])
        _patch_switch_k8s(monkeypatch, api, core)
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))

        with pytest.raises(TimeoutError, match="Old ConstellationSpec did not finalize"):
            asyncio.run(mgr.switch(str(tmp_sessions["session_yaml"])))

        assert api.created is False
        assert mgr.status == "error"

    def test_switch_fails_if_old_session_pods_remain(self, tmp_sessions, monkeypatch):
        api = _SwitchApi(old_cr_gets_before_404=0)
        core = _SwitchCoreV1([2])
        _patch_switch_k8s(monkeypatch, api, core)
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))

        with pytest.raises(TimeoutError, match="old session pod"):
            asyncio.run(mgr.switch(str(tmp_sessions["session_yaml"])))

        assert api.created is False
        assert mgr.status == "error"

    def test_switch_ignores_stale_error_until_operator_observes_generation(
        self, tmp_sessions, monkeypatch
    ):
        api = _SwitchApi(
            old_cr_gets_before_404=0,
            post_create_statuses=[
                {
                    "metadata": {"generation": 2},
                    "status": {
                        "phase": "Error",
                        "message": "old wiring failure",
                        "observedGeneration": 1,
                    },
                },
                {
                    "metadata": {"generation": 2},
                    "status": {
                        "phase": "Ready",
                        "message": "ready",
                        "observedGeneration": 2,
                    },
                },
            ],
        )
        core = _SwitchCoreV1([0])
        _patch_switch_k8s(monkeypatch, api, core)
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))

        asyncio.run(mgr.switch(str(tmp_sessions["session_yaml"])))

        assert mgr.status_detail == "ready"
        assert mgr._current_session_file == str(tmp_sessions["session_yaml"])
