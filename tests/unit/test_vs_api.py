"""Unit tests for VS-API — state management and snapshot construction."""

import json
import sqlite3
from datetime import UTC, datetime

from nodalarc.db.queries import (
    insert_convergence_result,
    insert_link_up,
    insert_snapshot,
    query_nearest_snapshot,
)
from nodalarc.db.schema import create_tables
from nodalarc.models.link_events import LinkUp
from nodalarc.models.metrics import ConvergenceResult
from nodalarc.models.vs_api import (
    LinkState,
    NetworkHealth,
    NodeState,
    StateSnapshot,
)
from vs_api.main import (
    _add_recent_event,
    _build_snapshot,
    _gs_elevation_map,
    _link_key,
    _propagate_positions_from_ephemeris,
    _state,
    _state_lock,
    _update_convergence,
    _update_latency,
    _update_link_down,
    _update_link_up,
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
        _state["sim_time"] = datetime.now(UTC).isoformat()


class TestEphemerisPositionPropagation:
    """Test _propagate_positions_from_ephemeris populates node state correctly."""

    def setup_method(self):
        _reset_state()
        _gs_elevation_map.clear()
        import vs_api.main as m

        m._cached_ephemeris_obj = None

    def _load_ephemeris(self):
        """Load a test ephemeris into the VS-API module state."""
        import vs_api.main as m
        from nodalarc.models.events import (
            EphemerisNodeFixed,
            EphemerisNodeKeplerian,
            SessionEphemeris,
        )

        eph = SessionEphemeris(
            epoch_id=0,
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            epoch_unix=1735689600.0,
            nodes={
                "sat-P00S00": EphemerisNodeKeplerian(
                    altitude_km=550.0,
                    inclination_deg=53.0,
                    raan_deg=0.0,
                    true_anomaly_deg=0.0,
                    plane=0,
                    slot=0,
                ),
                "sat-P00S01": EphemerisNodeKeplerian(
                    altitude_km=550.0,
                    inclination_deg=53.0,
                    raan_deg=0.0,
                    true_anomaly_deg=32.7,
                    plane=0,
                    slot=1,
                ),
                "gs-hawthorne": EphemerisNodeFixed(
                    lat_deg=33.92,
                    lon_deg=-118.33,
                    alt_km=0.0,
                ),
            },
        )
        m._cached_ephemeris_obj = eph

    def test_satellite_position_populated(self):
        """Satellite positions are computed from ephemeris."""
        self._load_ephemeris()
        _propagate_positions_from_ephemeris("2025-01-01T00:00:00+00:00")
        snapshot = _build_snapshot()
        sat_nodes = [n for n in snapshot["nodes"] if n["node_type"] == "satellite"]
        assert len(sat_nodes) == 2
        node = next(n for n in sat_nodes if n["node_id"] == "sat-P00S00")
        assert node["plane"] == 0
        assert node["slot"] == 0
        assert -90 <= node["lat_deg"] <= 90
        assert -180 <= node["lon_deg"] <= 180
        assert 540 < node["alt_km"] < 560

    def test_ground_station_position_populated(self):
        """Ground station positions come from ephemeris static coords."""
        self._load_ephemeris()
        _propagate_positions_from_ephemeris("2025-01-01T00:00:00+00:00")
        snapshot = _build_snapshot()
        gs_nodes = [n for n in snapshot["nodes"] if n["node_type"] == "ground_station"]
        assert len(gs_nodes) == 1
        gs = gs_nodes[0]
        assert abs(gs["lat_deg"] - 33.92) < 0.01
        assert abs(gs["lon_deg"] - (-118.33)) < 0.01

    def test_gs_gets_min_elevation_deg(self):
        """GS nodes get min_elevation_deg from _gs_elevation_map."""
        _gs_elevation_map["gs-hawthorne"] = 25.0
        self._load_ephemeris()
        _propagate_positions_from_ephemeris("2025-01-01T00:00:00+00:00")
        snapshot = _build_snapshot()
        gs = next(n for n in snapshot["nodes"] if n["node_id"] == "gs-hawthorne")
        assert gs["min_elevation_deg"] == 25.0

    def test_positions_change_with_time(self):
        """Satellite positions differ at different sim_times."""
        self._load_ephemeris()
        _propagate_positions_from_ephemeris("2025-01-01T00:00:00+00:00")
        snap1 = _build_snapshot()
        lon1 = next(n for n in snap1["nodes"] if n["node_id"] == "sat-P00S00")["lon_deg"]

        _propagate_positions_from_ephemeris("2025-01-01T00:10:00+00:00")
        snap2 = _build_snapshot()
        lon2 = next(n for n in snap2["nodes"] if n["node_id"] == "sat-P00S00")["lon_deg"]

        assert lon1 != lon2

    def test_no_ephemeris_no_crash(self):
        """Calling propagate without ephemeris does nothing."""
        _propagate_positions_from_ephemeris("2025-01-01T00:00:00+00:00")
        snapshot = _build_snapshot()
        assert len(snapshot["nodes"]) == 0


class TestSatelliteMovementSmoke:
    """Smoke tests that satellite positions change between snapshots via ephemeris.

    This catches the class of bugs where code changes break position
    propagation through the VS-API pipeline — the satellites appear
    frozen on the display.
    """

    def setup_method(self):
        _reset_state()
        _gs_elevation_map.clear()
        import vs_api.main as m
        from nodalarc.models.events import (
            EphemerisNodeKeplerian,
            SessionEphemeris,
        )

        m._cached_ephemeris_obj = SessionEphemeris(
            epoch_id=0,
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            epoch_unix=1735689600.0,
            nodes={
                f"sat-P00S{i:02d}": EphemerisNodeKeplerian(
                    altitude_km=550.0,
                    inclination_deg=53.0,
                    raan_deg=0.0,
                    true_anomaly_deg=float(i * 32.7),
                    plane=0,
                    slot=i,
                )
                for i in range(20)
            },
        )

    def test_positions_change_between_ticks(self):
        """Two sequential propagations at different times produce different positions."""
        _propagate_positions_from_ephemeris("2025-01-01T00:00:00+00:00")
        snap1_nodes = {n["node_id"]: n for n in _build_snapshot()["nodes"]}

        _propagate_positions_from_ephemeris("2025-01-01T00:05:00+00:00")
        snap2_nodes = {n["node_id"]: n for n in _build_snapshot()["nodes"]}

        assert snap1_nodes["sat-P00S00"]["lat_deg"] != snap2_nodes["sat-P00S00"]["lat_deg"]

    def test_all_satellites_get_positions(self):
        """All satellites in ephemeris get positions."""
        _propagate_positions_from_ephemeris("2025-01-01T00:00:00+00:00")
        snap = _build_snapshot()
        sat_nodes = [n for n in snap["nodes"] if n["node_type"] == "satellite"]
        assert len(sat_nodes) == 20
        # Each has unique position (different true_anomaly)
        lats = {round(n["lat_deg"], 4) for n in sat_nodes}
        assert len(lats) > 10  # Not all at the same latitude

    def test_position_propagation_preserves_all_fields(self):
        """Propagated positions produce NodeState dicts with all required fields."""
        _propagate_positions_from_ephemeris("2025-01-01T00:00:00+00:00")
        snapshot = _build_snapshot()
        node = snapshot["nodes"][0]
        # Position fields from propagation
        assert "lat_deg" in node
        assert "lon_deg" in node
        assert "alt_km" in node
        assert "vel_x_km_s" in node
        # Identity fields from ephemeris
        assert node["node_type"] == "satellite"
        assert "plane" in node
        assert "slot" in node


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
        assert snapshot["network_health"]["status"] == "no measurement"

    def test_snapshot_with_links(self):
        _update_link_up(
            {
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
                "latency_ms": 5.0,
                "bandwidth_mbps": 1000,
                "reason": "vis_gained",
            }
        )
        snapshot = _build_snapshot()
        assert len(snapshot["links"]) == 1
        assert snapshot["links"][0]["state"] == "active"

    def test_link_down_removes_link(self):
        _update_link_up(
            {
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
                "latency_ms": 5.0,
                "bandwidth_mbps": 1000,
                "reason": "vis_gained",
            }
        )
        _update_link_down(
            {
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
            }
        )
        snapshot = _build_snapshot()
        assert len(snapshot["links"]) == 0

    def test_latency_update(self):
        _update_link_up(
            {
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
                "latency_ms": 5.0,
                "bandwidth_mbps": 1000,
                "reason": "vis_gained",
            }
        )
        _update_latency(
            {
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
                "latency_ms": 10.0,
                "range_km": 3000.0,
            }
        )
        snapshot = _build_snapshot()
        assert snapshot["links"][0]["latency_ms"] == 10.0
        assert snapshot["links"][0]["range_km"] == 3000.0

    def test_latency_update_reversed_node_order(self):
        """Latency update with nodes in reversed order should still match."""
        _update_link_up(
            {
                "node_a": "sat-P00S00",
                "node_b": "sat-P00S01",
                "latency_ms": 5.0,
                "bandwidth_mbps": 1000,
                "reason": "vis_gained",
            }
        )
        _update_latency(
            {
                "node_a": "sat-P00S01",
                "node_b": "sat-P00S00",
                "latency_ms": 12.0,
                "range_km": 3500.0,
            }
        )
        snapshot = _build_snapshot()
        assert snapshot["links"][0]["latency_ms"] == 12.0


class TestRecentEvents:
    """Recent events list management."""

    def setup_method(self):
        _reset_state()

    def test_add_event(self):
        _add_recent_event(
            {
                "sim_time": datetime.now(UTC).isoformat(),
                "node_a": "sat-P00S00",
                "reason": "test event",
            },
            "link_up",
        )
        snapshot = _build_snapshot()
        assert len(snapshot["recent_events"]) == 1
        assert snapshot["recent_events"][0]["event_type"] == "link_up"

    def test_cap_at_50(self):
        for i in range(60):
            _add_recent_event(
                {
                    "sim_time": datetime.now(UTC).isoformat(),
                    "node_a": f"sat-P00S{i:02d}",
                    "reason": f"event {i}",
                },
                "test",
            )
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
        now = datetime.now(UTC)
        snapshot = StateSnapshot(
            sim_time=now,
            wall_time=now,
            schema_version=1,
            nodes=[
                NodeState(
                    node_id="sat-P00S00",
                    node_type="satellite",
                    lat_deg=0.0,
                    lon_deg=0.0,
                    alt_km=550.0,
                    vel_x_km_s=None,
                    vel_y_km_s=None,
                    vel_z_km_s=None,
                    plane=0,
                    slot=0,
                    routing_area="49.0001",
                    neighbor_count=2,
                    isl_count=2,
                    gnd_count=0,
                ),
            ],
            links=[
                LinkState(
                    node_a="sat-P00S00",
                    node_b="sat-P00S01",
                    state="active",
                    link_type="intra_plane_isl",
                    link_reason="vis_gained",
                    latency_ms=5.0,
                    bandwidth_mbps=1000.0,
                    range_km=1500.0,
                    traffic_load_pct=None,
                ),
            ],
            traced_paths=[],
            active_flows=[],
            recent_events=[],
            network_health=NetworkHealth(
                status="converged",
                converging_since_ms=None,
                unreachable_flows=0,
                last_convergence_ms=150.0,
            ),
        )

        json_str = snapshot.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["schema_version"] == 1
        assert len(parsed["nodes"]) == 1
        assert len(parsed["links"]) == 1
        assert parsed["network_health"]["status"] == "converged"

    def test_snapshot_is_frozen(self):
        now = datetime.now(UTC)
        snapshot = StateSnapshot(
            sim_time=now,
            wall_time=now,
            schema_version=1,
            nodes=[],
            links=[],
            traced_paths=[],
            active_flows=[],
            recent_events=[],
            network_health=NetworkHealth(
                status="converged",
                converging_since_ms=None,
                unreachable_flows=0,
                last_convergence_ms=None,
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

        now = datetime.now(UTC)
        event = LinkUp(
            sim_time=now,
            wall_time=now,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            interface_a="isl0",
            interface_b="isl0",
            latency_ms=5.0,
            bandwidth_mbps=1000,
            reason="vis_gained",
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

        now = datetime.now(UTC)
        result = ConvergenceResult(
            event_id="test-001",
            converged=True,
            duration_ms=100.0,
            packets_lost=0,
            packets_sent=10,
            sim_time_start=now,
            sim_time_end=now,
            wall_time_start=now,
            wall_time_end=now,
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

    def test_query_before_all_snapshots(self):
        """Query time before all snapshots returns the earliest one."""
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        insert_snapshot(
            conn, "2025-01-01T00:01:00", "2025-01-01T00:01:00", json.dumps({"id": "only"})
        )
        result = query_nearest_snapshot(conn, "2025-01-01T00:00:00")
        assert result is not None
        assert json.loads(result["snapshot_json"])["id"] == "only"
        conn.close()

    def test_query_after_all_snapshots(self):
        """Query time after all snapshots returns the latest one."""
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        insert_snapshot(
            conn, "2025-01-01T00:00:00", "2025-01-01T00:00:00", json.dumps({"id": "only"})
        )
        result = query_nearest_snapshot(conn, "2025-01-01T00:05:00")
        assert result is not None
        assert json.loads(result["snapshot_json"])["id"] == "only"
        conn.close()

    def test_no_snapshots_returns_none(self):
        conn = sqlite3.connect(":memory:")
        create_tables(conn)
        result = query_nearest_snapshot(conn, "2025-01-01T00:00:00")
        assert result is None
        conn.close()
