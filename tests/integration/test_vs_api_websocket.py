"""Integration test: VS-API state snapshot schema and construction.

Verifies VS-API StateSnapshot can be constructed and serialized correctly.
"""

import json

import pytest

from nodalarc.models.vs_api import (
    LinkState,
    NetworkHealth,
    NodeState,
    StateSnapshot,
)

pytestmark = pytest.mark.integration


def test_vs_api_state_snapshot_schema():
    """Verify StateSnapshot model can be constructed and serialized."""
    from datetime import datetime, timezone

    snapshot = StateSnapshot(
        sim_time=datetime.now(timezone.utc),
        wall_time=datetime.now(timezone.utc),
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

    data = json.loads(snapshot.model_dump_json())
    assert data["schema_version"] == 1
    assert len(data["nodes"]) == 1
    assert len(data["links"]) == 1
    assert data["network_health"]["status"] == "converged"


def test_vs_api_state_management():
    """Test VS-API in-memory state update functions."""
    from vs_api.main import (
        _build_snapshot,
        _state,
        _state_lock,
        _update_link_up,
        _update_link_down,
    )
    from datetime import datetime, timezone

    # Reset state
    with _state_lock:
        _state["links"].clear()
        _state["nodes"].clear()
        _state["recent_events"].clear()

    _update_link_up({
        "node_a": "sat-P00S00",
        "node_b": "sat-P00S01",
        "latency_ms": 5.0,
        "bandwidth_mbps": 1000,
        "reason": "vis_gained",
    })

    snapshot = _build_snapshot()
    assert len(snapshot["links"]) == 1

    _update_link_down({"node_a": "sat-P00S00", "node_b": "sat-P00S01"})
    snapshot = _build_snapshot()
    assert len(snapshot["links"]) == 0
