"""Test that every Pydantic model round-trips through JSON serialization.

Proves the contract that NATS messages, SQLite records, and config
files depend on. If a model round-trip fails, components that serialize
and deserialize will disagree on the data.
"""

from datetime import UTC, datetime

import pytest
from nodalarc.models.events import (
    ClockTick,
    NodePosition,
    PositionEvent,
    TimelinePositionSnapshot,
    VisibilityEvent,
)
from nodalarc.models.link_events import (
    LatencyUpdate,
    LinkDecisionProvenance,
    LinkDown,
    LinkUp,
)
from nodalarc.models.metrics import (
    AdapterEvent,
    ConvergenceRequest,
    ConvergenceResult,
    ProbeResult,
    TraceRequest,
    TraceResponse,
)
from nodalarc.models.vs_api import (
    ActiveFlow,
    LinkState,
    NetworkHealth,
    NodeState,
    RecentEvent,
    StateSnapshot,
    TracedPath,
)
from pydantic import ValidationError

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)


def _round_trip(model_instance):
    """Serialize to JSON and deserialize back, assert equality."""
    json_bytes = model_instance.model_dump_json()
    cls = type(model_instance)
    restored = cls.model_validate_json(json_bytes)
    assert restored == model_instance
    return restored


# --- events.py ---


class TestNodePosition:
    def test_round_trip(self):
        pos = NodePosition(
            lat_deg=33.92,
            lon_deg=-118.33,
            alt_km=550.0,
            vel_x_km_s=1.0,
            vel_y_km_s=2.0,
            vel_z_km_s=3.0,
        )
        _round_trip(pos)

    def test_frozen(self):
        pos = NodePosition(
            lat_deg=0.0,
            lon_deg=0.0,
            alt_km=550.0,
            vel_x_km_s=0.0,
            vel_y_km_s=0.0,
            vel_z_km_s=0.0,
        )
        with pytest.raises(ValidationError, match="frozen"):
            pos.lat_deg = 10.0


class TestPositionEvent:
    def test_round_trip(self):
        evt = PositionEvent(
            sim_time=NOW,
            node_id="sat-P00S00",
            lat_deg=33.92,
            lon_deg=-118.33,
            alt_km=550.0,
            vel_x_km_s=1.0,
            vel_y_km_s=2.0,
            vel_z_km_s=3.0,
        )
        _round_trip(evt)

    def test_frozen(self):
        evt = PositionEvent(
            sim_time=NOW,
            node_id="sat-P00S00",
            lat_deg=0.0,
            lon_deg=0.0,
            alt_km=550.0,
            vel_x_km_s=0.0,
            vel_y_km_s=0.0,
            vel_z_km_s=0.0,
        )
        with pytest.raises(ValidationError, match="frozen"):
            evt.node_id = "sat-P01S01"


class TestVisibilityEvent:
    def test_round_trip(self):
        evt = VisibilityEvent(
            sim_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            visible=True,
            scheduled=True,
            range_km=1500.0,
            elevation_deg=None,
            terminal_type="optical",
        )
        _round_trip(evt)

    def test_node_ordering_enforced(self):
        """node_a must be alphabetically < node_b; validator swaps if needed."""
        evt = VisibilityEvent(
            sim_time=NOW,
            node_a="sat-P01S00",
            node_b="sat-P00S00",
            link_type="isl",
            visible=True,
            scheduled=True,
            range_km=1000.0,
            elevation_deg=None,
            terminal_type="optical",
        )
        assert evt.node_a == "sat-P00S00"
        assert evt.node_b == "sat-P01S00"

    def test_ground_link_has_elevation(self):
        evt = VisibilityEvent(
            sim_time=NOW,
            node_a="gs-ashburn",
            node_b="sat-P00S00",
            link_type="ground",
            visible=True,
            scheduled=True,
            range_km=800.0,
            elevation_deg=45.0,
            terminal_type="optical",
        )
        assert evt.elevation_deg == 45.0

    def test_visible_false_requires_non_ok_reject_reason(self):
        """Foundational: an invisible event must declare WHY. Producer
        must populate visibility_reject_reason."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="non-'ok'"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                link_type="isl",
                visible=False,
                scheduled=False,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                # visibility_reject_reason defaults to 'ok' — would be
                # impossible with visible=False, so construction fails.
            )

    def test_visible_true_with_non_ok_reject_reason_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="visible=True requires"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                link_type="isl",
                visible=True,
                scheduled=True,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="los_blocked",
            )

    def test_unscheduled_reason_on_scheduled_pair_rejected(self):
        """A scheduled pair has no unscheduled reason."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="unscheduled_reason set on a scheduled"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                link_type="isl",
                visible=True,
                scheduled=True,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="ok",
                unscheduled_reason="isl_terminal_capacity",
            )

    def test_unscheduled_reason_on_invisible_pair_rejected(self):
        """An invisible pair never reached the allocator."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="unscheduled_reason set on a non-visible"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                link_type="isl",
                visible=False,
                scheduled=False,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
                visibility_reject_reason="los_blocked",
                unscheduled_reason="isl_terminal_capacity",
            )

    def test_visible_unscheduled_event_carries_unscheduled_reason(self):
        """A visible-but-unscheduled event must carry the scheduling
        attribution so consumers can explain the transition from the
        event stream alone."""
        evt = VisibilityEvent(
            sim_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            visible=True,
            scheduled=False,
            range_km=1000.0,
            elevation_deg=None,
            terminal_type="optical",
            visibility_reject_reason="ok",
            unscheduled_reason="isl_terminal_capacity",
        )
        assert evt.unscheduled_reason == "isl_terminal_capacity"

    def test_frozen(self):
        evt = VisibilityEvent(
            sim_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            visible=True,
            scheduled=True,
            range_km=1000.0,
            elevation_deg=None,
            terminal_type="optical",
        )
        with pytest.raises(ValidationError, match="frozen"):
            evt.visible = False

    def test_link_type_required(self):
        with pytest.raises(ValidationError, match="link_type"):
            VisibilityEvent(
                sim_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                visible=True,
                scheduled=True,
                range_km=1000.0,
                elevation_deg=None,
                terminal_type="optical",
            )


class TestClockTick:
    def test_round_trip(self):
        tick = ClockTick(
            sim_time=NOW,
            wall_time=NOW,
            compression_ratio=1.0,
        )
        _round_trip(tick)


class TestTimelinePositionSnapshot:
    def test_round_trip(self):
        snap = TimelinePositionSnapshot(
            sim_time=NOW,
            positions={
                "sat-P00S00": NodePosition(
                    lat_deg=0.0,
                    lon_deg=0.0,
                    alt_km=550.0,
                    vel_x_km_s=7.0,
                    vel_y_km_s=0.0,
                    vel_z_km_s=0.0,
                ),
                "gs-ashburn": NodePosition(
                    lat_deg=39.04,
                    lon_deg=-77.49,
                    alt_km=0.1,
                    vel_x_km_s=0.0,
                    vel_y_km_s=0.0,
                    vel_z_km_s=0.0,
                ),
            },
        )
        restored = _round_trip(snap)
        assert len(restored.positions) == 2
        assert "sat-P00S00" in restored.positions
        assert "gs-ashburn" in restored.positions


# --- link_events.py ---


class TestLinkUp:
    def test_round_trip(self):
        provenance = LinkDecisionProvenance(
            authority_source="visibility_event",
            authority_sim_time=NOW,
            authority_sequence=None,
            authority_age_ms=0.0,
            range_km=1500.0,
            orbital_one_way_ms=5.0,
            substrate_rtt_ms=2.0,
            substrate_one_way_ms=1.0,
            netem_one_way_ms=4.0,
            rtt_to_one_way_policy="half-rtt",
        )
        evt = LinkUp(
            sim_time=NOW,
            wall_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            range_km=1500.0,
            reason="vis_gained",
            provenance=provenance,
        )
        _round_trip(evt)
        assert evt.provenance == provenance

    def test_frozen(self):
        evt = LinkUp(
            sim_time=NOW,
            wall_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            range_km=1500.0,
            reason="vis_gained",
        )
        with pytest.raises(ValidationError, match="frozen"):
            evt.reason = "vis_lost"

    def test_link_type_required(self):
        with pytest.raises(ValidationError, match="link_type"):
            LinkUp(
                sim_time=NOW,
                wall_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                interface_a="isl0",
                interface_b="isl1",
                latency_ms=5.0,
                bandwidth_mbps=1000.0,
                range_km=1500.0,
                reason="vis_gained",
            )


class TestLinkDown:
    def test_round_trip(self):
        evt = LinkDown(
            sim_time=NOW,
            wall_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl1",
            reason="vis_lost",
        )
        _round_trip(evt)

    def test_link_type_required(self):
        with pytest.raises(ValidationError, match="link_type"):
            LinkDown(
                sim_time=NOW,
                wall_time=NOW,
                node_a="sat-P00S00",
                node_b="sat-P00S01",
                interface_a="isl0",
                interface_b="isl1",
                reason="vis_lost",
            )


class TestLatencyUpdate:
    def test_round_trip(self):
        provenance = LinkDecisionProvenance(
            authority_source="link_state_snapshot",
            authority_sim_time=NOW,
            authority_sequence=12,
            authority_age_ms=1000.0,
            range_km=1650.0,
            orbital_one_way_ms=5.5,
            substrate_rtt_ms=1.0,
            substrate_one_way_ms=0.5,
            netem_one_way_ms=5.0,
            rtt_to_one_way_policy="half-rtt",
        )
        evt = LatencyUpdate(
            sim_time=NOW,
            wall_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            latency_ms=5.5,
            range_km=1650.0,
            provenance=provenance,
        )
        _round_trip(evt)
        assert evt.provenance == provenance


# --- metrics.py ---


class TestConvergenceRequest:
    def test_round_trip_with_link_up(self):
        link_up = LinkUp(
            sim_time=NOW,
            wall_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl1",
            latency_ms=5.0,
            bandwidth_mbps=1000.0,
            range_km=1500.0,
            reason="vis_gained",
        )
        req = ConvergenceRequest(event_id="evt-001", link_event=link_up)
        _round_trip(req)

    def test_round_trip_with_link_down(self):
        link_down = LinkDown(
            sim_time=NOW,
            wall_time=NOW,
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            link_type="isl",
            interface_a="isl0",
            interface_b="isl1",
            reason="vis_lost",
        )
        req = ConvergenceRequest(event_id="evt-002", link_event=link_down)
        _round_trip(req)


class TestConvergenceResult:
    def test_round_trip(self):
        res = ConvergenceResult(
            event_id="evt-001",
            converged=True,
            duration_ms=1500.0,
            packets_lost=0,
            packets_sent=50,
            sim_time_start=NOW,
            sim_time_end=LATER,
            wall_time_start=NOW,
            wall_time_end=LATER,
        )
        _round_trip(res)

    def test_round_trip_with_triggering_link(self):
        res = ConvergenceResult(
            event_id="evt-002",
            converged=False,
            duration_ms=30000.0,
            packets_lost=5,
            packets_sent=100,
            sim_time_start=NOW,
            sim_time_end=LATER,
            wall_time_start=NOW,
            wall_time_end=LATER,
            triggering_link_event_id=42,
        )
        restored = _round_trip(res)
        assert restored.triggering_link_event_id == 42


class TestProbeResult:
    def test_round_trip(self):
        res = ProbeResult(
            sim_time=NOW,
            wall_time=NOW,
            flow_id="ashburn-to-frankfurt",
            src_node="gs-ashburn",
            dst_node="gs-frankfurt",
            packets_sent=100,
            packets_received=98,
            latency_min_ms=20.0,
            latency_max_ms=25.0,
            latency_avg_ms=22.5,
            jitter_ms=1.2,
        )
        _round_trip(res)


class TestAdapterEvent:
    def test_round_trip(self):
        evt = AdapterEvent(
            sim_time=NOW,
            wall_time=NOW,
            node_id="sat-P00S00",
            event_type="adjacency_up",
            event_data={"neighbor": "sat-P00S01", "source": "grpc"},
        )
        _round_trip(evt)

    def test_event_data_any_type(self):
        """event_data accepts arbitrary nested data."""
        evt = AdapterEvent(
            sim_time=NOW,
            wall_time=NOW,
            node_id="sat-P00S00",
            event_type="spf_end",
            event_data={"duration_us": 1234, "paths": ["a", "b"], "source": "syslog"},
        )
        restored = _round_trip(evt)
        assert restored.event_data["duration_us"] == 1234


class TestTraceRequest:
    def test_round_trip(self):
        req = TraceRequest(src_node="gs-hawthorne", dst_node="gs-ashburn")
        _round_trip(req)


class TestTraceResponse:
    def test_round_trip_success(self):
        resp = TraceResponse(
            src_node="gs-hawthorne",
            dst_node="gs-ashburn",
            hops=["sat-P00S00", "sat-P01S00", "sat-P01S05"],
            success=True,
        )
        restored = _round_trip(resp)
        assert restored.hops == ["sat-P00S00", "sat-P01S00", "sat-P01S05"]
        assert restored.success is True
        assert restored.error is None

    def test_round_trip_failure(self):
        resp = TraceResponse(
            src_node="gs-hawthorne",
            dst_node="gs-ashburn",
            hops=[],
            success=False,
            error="no route",
        )
        restored = _round_trip(resp)
        assert restored.success is False
        assert restored.error == "no route"


# --- vs_api.py ---


class TestNodeState:
    def test_satellite_round_trip(self):
        ns = NodeState(
            node_id="sat-P03S07",
            node_type="satellite",
            lat_deg=33.0,
            lon_deg=-118.0,
            alt_km=550.0,
            vel_x_km_s=1.0,
            vel_y_km_s=2.0,
            vel_z_km_s=3.0,
            plane=3,
            slot=7,
            routing_area="49.0001",
            neighbor_count=4,
            isl_count=3,
            gnd_count=1,
        )
        _round_trip(ns)

    def test_ground_station_round_trip(self):
        ns = NodeState(
            node_id="gs-ashburn",
            node_type="ground_station",
            lat_deg=39.04,
            lon_deg=-77.49,
            alt_km=0.1,
            vel_x_km_s=None,
            vel_y_km_s=None,
            vel_z_km_s=None,
            plane=None,
            slot=None,
            routing_area="49.0000",
            neighbor_count=1,
            isl_count=0,
            gnd_count=1,
        )
        _round_trip(ns)


class TestLinkState:
    def test_round_trip(self):
        ls = LinkState(
            node_a="sat-P00S00",
            node_b="sat-P00S01",
            state="active",
            link_type="intra_plane_isl",
            link_reason=None,
            latency_ms=3.2,
            bandwidth_mbps=1000.0,
            range_km=960.0,
            traffic_load_pct=None,
        )
        _round_trip(ls)

    def test_traffic_load_null_vs_zero(self):
        """None means no probe data; 0 means probes running but no load."""
        ls_none = LinkState(
            node_a="a",
            node_b="b",
            state="active",
            link_type=None,
            link_reason=None,
            latency_ms=5.0,
            bandwidth_mbps=100.0,
            range_km=500.0,
            traffic_load_pct=None,
        )
        ls_zero = LinkState(
            node_a="a",
            node_b="b",
            state="active",
            link_type=None,
            link_reason=None,
            latency_ms=5.0,
            bandwidth_mbps=100.0,
            range_km=500.0,
            traffic_load_pct=0.0,
        )
        assert ls_none.traffic_load_pct is None
        assert ls_zero.traffic_load_pct == 0.0


class TestTracedPath:
    def test_round_trip(self):
        tp = TracedPath(
            flow_id="ashburn-to-frankfurt",
            src_node="gs-ashburn",
            dst_node="gs-frankfurt",
            hops=["gs-ashburn", "sat-P02S05", "sat-P02S06", "sat-P03S06", "gs-frankfurt"],
        )
        _round_trip(tp)


class TestNetworkHealth:
    def test_round_trip(self):
        nh = NetworkHealth(
            status="converged",
            converging_since_ms=None,
            unreachable_flows=0,
            last_convergence_ms=1500.0,
        )
        _round_trip(nh)


class TestActiveFlow:
    def test_round_trip(self):
        af = ActiveFlow(
            flow_id="ashburn-to-frankfurt",
            src_node="gs-ashburn",
            dst_node="gs-frankfurt",
            protocol="udp",
            probe_type="continuous",
        )
        _round_trip(af)


class TestRecentEvent:
    def test_round_trip(self):
        re = RecentEvent(
            sim_time=NOW,
            node_id="sat-P00S00",
            event_type="adjacency_up",
            summary="IS-IS adjacency with sat-P00S01 came up",
        )
        _round_trip(re)


class TestStateSnapshot:
    def test_round_trip(self):
        snap = StateSnapshot(
            sim_time=NOW,
            wall_time=NOW,
            schema_version=1,
            session_id="run-test-0001",
            nodes=[
                NodeState(
                    node_id="sat-P00S00",
                    node_type="satellite",
                    lat_deg=0.0,
                    lon_deg=0.0,
                    alt_km=550.0,
                    vel_x_km_s=7.0,
                    vel_y_km_s=0.0,
                    vel_z_km_s=0.0,
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
                    link_reason=None,
                    latency_ms=3.0,
                    bandwidth_mbps=1000.0,
                    range_km=900.0,
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
                last_convergence_ms=None,
            ),
        )
        restored = _round_trip(snap)
        assert len(restored.nodes) == 1
        assert len(restored.links) == 1
        assert restored.schema_version == 1
        assert restored.session_id == "run-test-0001"
