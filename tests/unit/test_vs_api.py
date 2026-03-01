"""Unit tests for VS-API — state management and snapshot construction."""

import json
import sqlite3
from datetime import datetime, timezone

from nodalarc.db.schema import create_tables
from nodalarc.db.queries import insert_link_up, insert_convergence_result, insert_snapshot, query_nearest_snapshot
from nodalarc.models.link_events import LinkUp
from nodalarc.models.metrics import ConvergenceResult
from nodalarc.models.vs_api import (
    LinkState,
    NetworkHealth,
    NodeState,
    RecentEvent,
    StateSnapshot,
)
from vs_api.main import (
    _state,
    _state_lock,
    _build_snapshot,
    _update_link_up,
    _update_link_down,
    _update_latency,
    _update_convergence,
    _add_recent_event,
    _link_key,
)


def _reset_state():
    with _state_lock:
        _state["nodes"].clear()
        _state["links"].clear()
        _state["recent_events"].clear()
        _state["active_flows"].clear()
        _state["network_health"] = {
            "status": "converged",
            "converging_since_ms": None,
            "unreachable_flows": 0,
            "last_convergence_ms": None,
        }
        _state["sim_time"] = datetime.now(timezone.utc).isoformat()


class TestLinkKey:
    """Test link key generation for deduplication."""

    def test_ordered(self):
        assert _link_key("sat-P00S00", "sat-P00S01") == "sat-P00S00:sat-P00S01"

    def test_reversed_produces_same_key(self):
        assert _link_key("sat-P00S01", "sat-P00S00") == "sat-P00S00:sat-P00S01"


class TestStateSnapshot:
    """StateSnapshot construction from component data."""

    def setup_method(self):
        _reset_state()

    def test_empty_snapshot(self):
        snapshot = _build_snapshot()
        assert snapshot["schema_version"] == 1
        assert snapshot["nodes"] == []
        assert snapshot["links"] == []
        assert snapshot["network_health"]["status"] == "converged"

    def test_snapshot_with_links(self):
        _update_link_up({
            "node_a": "sat-P00S00",
            "node_b": "sat-P00S01",
            "latency_ms": 5.0,
            "bandwidth_mbps": 1000,
            "reason": "vis_gained",
        })
        snapshot = _build_snapshot()
        assert len(snapshot["links"]) == 1
        assert snapshot["links"][0]["state"] == "active"

    def test_link_down_removes_link(self):
        _update_link_up({
            "node_a": "sat-P00S00",
            "node_b": "sat-P00S01",
            "latency_ms": 5.0,
            "bandwidth_mbps": 1000,
            "reason": "vis_gained",
        })
        _update_link_down({
            "node_a": "sat-P00S00",
            "node_b": "sat-P00S01",
        })
        snapshot = _build_snapshot()
        assert len(snapshot["links"]) == 0

    def test_latency_update(self):
        _update_link_up({
            "node_a": "sat-P00S00",
            "node_b": "sat-P00S01",
            "latency_ms": 5.0,
            "bandwidth_mbps": 1000,
            "reason": "vis_gained",
        })
        _update_latency({
            "node_a": "sat-P00S00",
            "node_b": "sat-P00S01",
            "latency_ms": 10.0,
            "range_km": 3000.0,
        })
        snapshot = _build_snapshot()
        assert snapshot["links"][0]["latency_ms"] == 10.0
        assert snapshot["links"][0]["range_km"] == 3000.0

    def test_latency_update_reversed_node_order(self):
        """Latency update with nodes in reversed order should still match."""
        _update_link_up({
            "node_a": "sat-P00S00",
            "node_b": "sat-P00S01",
            "latency_ms": 5.0,
            "bandwidth_mbps": 1000,
            "reason": "vis_gained",
        })
        _update_latency({
            "node_a": "sat-P00S01",
            "node_b": "sat-P00S00",
            "latency_ms": 12.0,
            "range_km": 3500.0,
        })
        snapshot = _build_snapshot()
        assert snapshot["links"][0]["latency_ms"] == 12.0


class TestRecentEvents:
    """Recent events list management."""

    def setup_method(self):
        _reset_state()

    def test_add_event(self):
        _add_recent_event({
            "sim_time": datetime.now(timezone.utc).isoformat(),
            "node_a": "sat-P00S00",
            "reason": "test event",
        }, "link_up")
        snapshot = _build_snapshot()
        assert len(snapshot["recent_events"]) == 1
        assert snapshot["recent_events"][0]["event_type"] == "link_up"

    def test_cap_at_50(self):
        for i in range(60):
            _add_recent_event({
                "sim_time": datetime.now(timezone.utc).isoformat(),
                "node_a": f"sat-P00S{i:02d}",
                "reason": f"event {i}",
            }, "test")
        with _state_lock:
            assert len(_state["recent_events"]) == 50


class TestNetworkHealth:
    """Network health state updates."""

    def setup_method(self):
        _reset_state()

    def test_convergence_converged(self):
        _update_convergence({"converged": True, "duration_ms": 150.0})
        snapshot = _build_snapshot()
        assert snapshot["network_health"]["status"] == "converged"
        assert snapshot["network_health"]["last_convergence_ms"] == 150.0

    def test_convergence_failed(self):
        _update_convergence({"converged": False})
        snapshot = _build_snapshot()
        assert snapshot["network_health"]["status"] == "converging"


class TestSnapshotModel:
    """Test StateSnapshot Pydantic model serialization."""

    def test_full_snapshot_round_trip(self):
        now = datetime.now(timezone.utc)
        snapshot = StateSnapshot(
            sim_time=now,
            wall_time=now,
            schema_version=1,
            nodes=[
                NodeState(
                    node_id="sat-P00S00", node_type="satellite",
                    lat_deg=0.0, lon_deg=0.0, alt_km=550.0,
                    vel_x_km_s=None, vel_y_km_s=None, vel_z_km_s=None,
                    plane=0, slot=0, routing_area="49.0001",
                    neighbor_count=2, isl_count=2, gnd_count=0,
                ),
            ],
            links=[
                LinkState(
                    node_a="sat-P00S00", node_b="sat-P00S01",
                    state="active", link_type="intra_plane_isl",
                    link_reason="vis_gained", latency_ms=5.0,
                    bandwidth_mbps=1000.0, range_km=1500.0,
                    traffic_load_pct=None,
                ),
            ],
            traced_paths=[],
            active_flows=[],
            recent_events=[],
            network_health=NetworkHealth(
                status="converged", converging_since_ms=None,
                unreachable_flows=0, last_convergence_ms=150.0,
            ),
        )

        json_str = snapshot.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["schema_version"] == 1
        assert len(parsed["nodes"]) == 1
        assert len(parsed["links"]) == 1
        assert parsed["network_health"]["status"] == "converged"

    def test_snapshot_is_frozen(self):
        now = datetime.now(timezone.utc)
        snapshot = StateSnapshot(
            sim_time=now, wall_time=now, schema_version=1,
            nodes=[], links=[], traced_paths=[], active_flows=[],
            recent_events=[],
            network_health=NetworkHealth(
                status="converged", converging_since_ms=None,
                unreachable_flows=0, last_convergence_ms=None,
            ),
        )
        # Frozen model — should not allow mutation
        import pydantic
        try:
            snapshot.schema_version = 2
            assert False, "Should have raised"
        except (pydantic.ValidationError, AttributeError):
            pass


class TestSQLiteQueries:
    """Test VS-API query routing to SQLite."""

    def test_query_link_events(self):
        from nodalarc.db.queries import query_link_events
        conn = sqlite3.connect(":memory:")
        create_tables(conn)

        now = datetime.now(timezone.utc)
        event = LinkUp(
            sim_time=now, wall_time=now,
            node_a="sat-P00S00", node_b="sat-P00S01",
            interface_a="isl0", interface_b="isl0",
            latency_ms=5.0, bandwidth_mbps=1000, reason="vis_gained",
        )
        insert_link_up(conn, event)

        results = query_link_events(conn)
        assert len(results) == 1
        assert results[0]["event_type"] == "LinkUp"
        conn.close()

    def test_query_convergence_events(self):
        from nodalarc.db.queries import query_convergence_events
        conn = sqlite3.connect(":memory:")
        create_tables(conn)

        now = datetime.now(timezone.utc)
        result = ConvergenceResult(
            event_id="test-001", converged=True, duration_ms=100.0,
            packets_lost=0, packets_sent=10,
            sim_time_start=now, sim_time_end=now,
        )
        insert_convergence_result(conn, result)

        results = query_convergence_events(conn)
        assert len(results) == 1
        assert results[0]["converged"] == 1
        conn.close()


class TestSnapshotStorage:
    """Test periodic snapshot storage and nearest-time retrieval."""

    def test_insert_and_query_snapshot(self):
        conn = sqlite3.connect(":memory:")
        create_tables(conn)

        snapshot_data = json.dumps({"schema_version": 1, "nodes": [], "links": []})
        insert_snapshot(conn, "2025-01-01T00:00:00", "2025-01-01T00:00:00", snapshot_data)

        result = query_nearest_snapshot(conn, "2025-01-01T00:00:00")
        assert result is not None
        assert result["sim_time"] == "2025-01-01T00:00:00"
        parsed = json.loads(result["snapshot_json"])
        assert parsed["schema_version"] == 1
        conn.close()

    def test_nearest_snapshot_selection(self):
        """Given two snapshots, nearest query returns the closest one."""
        conn = sqlite3.connect(":memory:")
        create_tables(conn)

        snap1 = json.dumps({"id": "snap1", "sim_time": "2025-01-01T00:00:00"})
        snap2 = json.dumps({"id": "snap2", "sim_time": "2025-01-01T00:01:00"})
        insert_snapshot(conn, "2025-01-01T00:00:00", "2025-01-01T00:00:00", snap1)
        insert_snapshot(conn, "2025-01-01T00:01:00", "2025-01-01T00:01:00", snap2)

        # Query closer to snap1
        result = query_nearest_snapshot(conn, "2025-01-01T00:00:10")
        assert result is not None
        parsed = json.loads(result["snapshot_json"])
        assert parsed["id"] == "snap1"

        # Query closer to snap2
        result = query_nearest_snapshot(conn, "2025-01-01T00:00:50")
        assert result is not None
        parsed = json.loads(result["snapshot_json"])
        assert parsed["id"] == "snap2"
        conn.close()

    def test_no_snapshots_returns_none(self):
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        result = query_nearest_snapshot(conn, "2025-01-01T00:00:00")
        assert result is None
        conn.close()
