"""Tests for session manager — recovery, orphan cleanup, stale directory cleanup."""

import json
import os
import signal
from pathlib import Path
from unittest.mock import patch

import pytest
from vs_api.session_manager import SessionManager, _pid_alive


@pytest.fixture
def tmp_sessions(tmp_path):
    """Create a temporary sessions directory with a valid session config."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Write a minimal valid session YAML
    session_yaml = sessions_dir / "test-session.yaml"
    session_yaml.write_text(f"""
session:
  name: Test Session
  data_dir: {data_dir}
constellation: configs/constellations/custom-example.yaml
ground_stations: configs/ground-stations/default.yaml
orbit:
  propagator: keplerian-circular
routing:
  protocol: isis
  area_assignment:
    strategy: flat
""")

    return {
        "sessions_dir": sessions_dir,
        "data_dir": data_dir,
        "session_yaml": session_yaml,
    }


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
        d1 = _make_session_dir(
            tmp_sessions["data_dir"],
            "session-older",
            mi_pid=my_pid,
        )
        time.sleep(0.05)  # Ensure different mtime

        # Create newer session
        d2 = _make_session_dir(
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
        yaml2.write_text(f"""
session:
  name: Test Session 2
  data_dir: {tmp_sessions["data_dir"]}
constellation: configs/constellations/custom-example.yaml
ground_stations: configs/ground-stations/default.yaml
orbit:
  propagator: keplerian-circular
routing:
  protocol: isis
  area_assignment:
    strategy: flat
""")
        mgr = SessionManager(str(tmp_sessions["sessions_dir"]))
        dirs = mgr._collect_data_dirs()
        assert len(dirs) == 1
