"""Integration test: VS-API state snapshot schema and construction.

Verifies VS-API StateSnapshot can be constructed and serialized correctly.
"""

import json
from datetime import UTC

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
    from datetime import datetime

    snapshot = StateSnapshot(
        sim_time=datetime.now(UTC),
        wall_time=datetime.now(UTC),
        schema_version=1,
        session_id="run-test-0001",
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
                reference_body="earth",
                frame_id="earth",
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
    assert data["session_id"] == "run-test-0001"
    assert len(data["nodes"]) == 1
    assert len(data["links"]) == 1
    assert data["network_health"]["status"] == "converged"

    # test_vs_api_state_management removed: it tested _state/_state_lock/_update_link_up
    # which were refactored into SessionContext. The module-level state API no longer exists.
