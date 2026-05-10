"""B.3A Contract test: GS deallocation + distributed ephemeris contracts.

Tests the ACTUAL Dispatcher code — no logic extraction, no duplication.
Constructs a minimal Dispatcher with mocked Node Agent, feeds events through
real _build_desired_from_snapshot() and _dispatch_batch() paths, asserts
desired/active state.

Covers:
  PRD B.3A: GS links must not accumulate (two code paths)
  PRD v0.71: SessionEphemeris, PlaybackState serialization contracts;
             epoch_id presence on ClockTick and LinkStateSnapshot
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from nodalarc.models.events import VisibilityEvent
from nodalarc.models.link_state import (
    AdminState,
    CarrierState,
    LinkState,
    LinkStateSnapshot,
    RoutingState,
)
from nodalarc.proto import node_agent_pb2
from scheduler.dispatcher import ActiveLinkInfo, Dispatcher
from scheduler.pod_locator import PodLocationMap


def _make_vis(
    node_a: str, node_b: str, visible: bool, scheduled: bool, link_type: str = "isl"
) -> VisibilityEvent:
    return VisibilityEvent(
        sim_time=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        node_a=node_a,
        node_b=node_b,
        visible=visible,
        scheduled=scheduled,
        range_km=500.0,
        latency_ms=1.6678204759907602,
        elevation_deg=45.0,
        terminal_type="optical",
        link_type=link_type,
    )


def _make_link_state(
    node_a: str,
    node_b: str,
    admin: AdminState = AdminState.UP,
    carrier: CarrierState = CarrierState.UP,
    link_type: str = "isl",
) -> LinkState:
    return LinkState(
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0" if link_type == "isl" else "term0",
        interface_b="isl1" if link_type == "isl" else "gnd0",
        admin=admin,
        carrier=carrier,
        routing=RoutingState.UNKNOWN,
        range_km=900.0 if carrier == CarrierState.UP else None,
        latency_ms=3.0 if carrier == CarrierState.UP else None,
        bandwidth_mbps=1000.0 if carrier == CarrierState.UP else None,
        link_type=link_type,
        sim_time=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_dispatcher(interface_map=None):
    if interface_map is None:
        interface_map = {
            ("gs-ashburn", "sat-P00S00"): ("term0", "gnd0"),
            ("sat-P00S00", "sat-P00S01"): ("isl0", "isl1"),
        }
    bandwidth_map = {k: 1000.0 for k in interface_map}

    loc = PodLocationMap()
    for pair in interface_map:
        for nid in pair:
            loc._node_of[nid] = "nodal"
    loc._agent_addrs["nodal"] = "127.0.0.1:50100"

    pool = MagicMock()
    mock_stub = MagicMock()

    def up_resp(req):
        return node_agent_pb2.BatchLinkUpResponse(
            success=True,
            error_message="",
            interfaces_upped=len(req.interfaces),
            apply_time_ms=0.0,
            interface_results=[
                node_agent_pb2.InterfaceResult(
                    node_id=iface.node_id,
                    interface_name=iface.interface_name,
                    success=True,
                )
                for iface in req.interfaces
            ],
        )

    def down_resp(req):
        return node_agent_pb2.BatchLinkDownResponse(
            success=True,
            error_message="",
            interfaces_downed=len(req.interfaces),
            apply_time_ms=0.0,
            interface_results=[
                node_agent_pb2.InterfaceResult(
                    node_id=iface.node_id,
                    interface_name=iface.interface_name,
                    success=True,
                )
                for iface in req.interfaces
            ],
        )

    mock_stub.async_batch_link_up = AsyncMock(side_effect=up_resp)
    mock_stub.async_batch_link_down = AsyncMock(side_effect=down_resp)
    mock_stub.async_set_latency = AsyncMock(
        return_value=node_agent_pb2.SetLatencyResponse(success=True)
    )
    pool.get_stub.return_value = mock_stub

    d = Dispatcher(
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        session_id="test-session",
        max_latency_age_s=1.0,
        gs_terminal_capacities={"gs-ashburn": 1},
        sat_ground_terminal_capacities={"sat-P00S00": 1, "sat-P00S01": 1},
    )
    d._js = AsyncMock()
    d._nc = MagicMock()
    return d


class MockNats:
    """Mock NATS connection — records published messages."""

    def __init__(self):
        self.messages = []

    async def publish(self, subject, data):
        self.messages.append((subject, data))


class TestGsDeallocationSnapshot:
    """Test _build_desired_from_snapshot excludes GS links not in snapshot."""

    def test_snapshot_removes_stale_gs_link(self):
        d = _make_dispatcher()
        # Pre-seed a GS link
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo(
            "gnd0",
            "gnd0",
            3.0,
            1000.0,
        )

        # Snapshot with NO GS links — replaces everything
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(_make_link_state("sat-P00S00", "sat-P00S01"),),
            interval_s=5.0,
        )
        desired = d._build_desired_from_snapshot(snapshot)

        assert ("gs-ashburn", "sat-P00S00") not in desired
        assert ("sat-P00S00", "sat-P00S01") in desired

    def test_snapshot_handoff_keeps_new_satellite(self):
        d = _make_dispatcher(
            {
                ("gs-ashburn", "sat-P00S00"): ("term0", "gnd0"),
                ("gs-ashburn", "sat-P00S01"): ("term0", "gnd0"),
                ("sat-P00S00", "sat-P00S01"): ("isl0", "isl1"),
            }
        )
        d._active_links[("gs-ashburn", "sat-P00S00")] = ActiveLinkInfo(
            "gnd0",
            "gnd0",
            3.0,
            1000.0,
        )

        # Snapshot with different GS satellite
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(_make_link_state("gs-ashburn", "sat-P00S01", link_type="ground"),),
            interval_s=5.0,
        )
        desired = d._build_desired_from_snapshot(snapshot)

        assert ("gs-ashburn", "sat-P00S00") not in desired
        assert ("gs-ashburn", "sat-P00S01") in desired

    def test_old_snapshot_seq_discarded(self):
        d = _make_dispatcher()
        d._last_snapshot_seq = 10

        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=5,  # older than current
            links=(),
            interval_s=5.0,
        )
        d._active_links[("sat-P00S00", "sat-P00S01")] = ActiveLinkInfo(
            "isl0",
            "isl1",
            3.0,
            1000.0,
        )
        desired = d._build_desired_from_snapshot(snapshot)

        # Old snapshot ignored — returns None, active_links unchanged
        assert desired is None
        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestGsDeallocationDispatchBatch:
    """Test _dispatch_batch handles visible=True/scheduled=False for GS."""

    def test_gs_pair_removed_via_dispatch_batch(self):
        d = _make_dispatcher()
        info = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0)
        d._desired_links[("gs-ashburn", "sat-P00S00")] = info
        d._active_links[("gs-ashburn", "sat-P00S00")] = info

        vis = _make_vis("gs-ashburn", "sat-P00S00", True, False, link_type="ground")

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("gs-ashburn", "sat-P00S00") not in d._active_links

    def test_isl_deallocation_not_removed(self):
        d = _make_dispatcher()
        info = ActiveLinkInfo("isl0", "isl1", 3.0, 1000.0)
        d._desired_links[("sat-P00S00", "sat-P00S01")] = info
        d._active_links[("sat-P00S00", "sat-P00S01")] = info

        vis = _make_vis("sat-P00S00", "sat-P00S01", True, False)

        asyncio.run(d._dispatch_batch([vis], [], MockNats()))

        assert ("sat-P00S00", "sat-P00S01") in d._active_links


class TestGsDeallocationConsistency:
    """Both paths produce identical _active_links for identical scenarios."""

    def test_snapshot_and_dispatch_agree_on_gs_removal(self):
        pair = ("gs-ashburn", "sat-P00S00")

        # Path 1: snapshot with no GS link — desired dict excludes pair
        d1 = _make_dispatcher()
        d1._active_links[pair] = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0)
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(),
            interval_s=5.0,
        )
        desired = d1._build_desired_from_snapshot(snapshot)

        # Path 2: dispatch batch with deallocation event → _reconcile_links removes
        d2 = _make_dispatcher()
        info = ActiveLinkInfo("term0", "gnd0", 3.0, 1000.0)
        d2._desired_links[pair] = info
        d2._active_links[pair] = info
        vis = _make_vis("gs-ashburn", "sat-P00S00", True, False, link_type="ground")
        asyncio.run(d2._dispatch_batch([vis], [], MockNats()))

        # Both must agree
        assert pair not in desired, "snapshot desired did not exclude GS pair"
        assert pair not in d2._active_links, "_dispatch_batch did not remove GS pair"


# ---------------------------------------------------------------------------
# PRD v0.71 — Distributed ephemeris serialization contracts
# ---------------------------------------------------------------------------


class TestSessionEphemerisContract:
    """SessionEphemeris must serialize and deserialize faithfully.

    This is the contract between the OME (publisher) and every edge
    (Scheduler, VS-API, VF) that consumes ephemeris data. If any field
    is silently dropped or mistyped, edges compute wrong positions.
    """

    def test_keplerian_node_round_trip(self):
        from nodalarc.models.events import EphemerisNodeKeplerian, SessionEphemeris

        eph = SessionEphemeris(
            epoch_id=0,
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            epoch_unix=1735689600.0,
            nodes={
                "sat-P00S00": EphemerisNodeKeplerian(
                    altitude_km=550.0,
                    inclination_deg=53.0,
                    raan_deg=22.5,
                    true_anomaly_deg=45.0,
                    plane=0,
                    slot=0,
                ),
            },
        )
        restored = SessionEphemeris.model_validate_json(eph.model_dump_json())
        sat = restored.nodes["sat-P00S00"]
        assert isinstance(sat, EphemerisNodeKeplerian)
        assert sat.altitude_km == 550.0
        assert sat.inclination_deg == 53.0
        assert sat.raan_deg == 22.5
        assert sat.true_anomaly_deg == 45.0
        assert sat.plane == 0
        assert sat.slot == 0

    def test_fixed_node_round_trip(self):
        from nodalarc.models.events import EphemerisNodeFixed, SessionEphemeris

        eph = SessionEphemeris(
            epoch_id=3,
            sim_time=datetime(2025, 6, 15, tzinfo=UTC),
            epoch_unix=1750000000.0,
            nodes={
                "gs-ashburn": EphemerisNodeFixed(
                    lat_deg=39.04,
                    lon_deg=-77.49,
                    alt_km=0.095,
                ),
            },
        )
        restored = SessionEphemeris.model_validate_json(eph.model_dump_json())
        gs = restored.nodes["gs-ashburn"]
        assert isinstance(gs, EphemerisNodeFixed)
        assert gs.lat_deg == 39.04
        assert gs.lon_deg == -77.49
        assert gs.alt_km == 0.095

    def test_epoch_id_and_epoch_unix_preserved(self):
        from nodalarc.models.events import SessionEphemeris

        eph = SessionEphemeris(
            epoch_id=42,
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            epoch_unix=1735689600.0,
            nodes={},
        )
        restored = SessionEphemeris.model_validate_json(eph.model_dump_json())
        assert restored.epoch_id == 42
        assert restored.epoch_unix == 1735689600.0

    def test_discriminated_union_dispatches_correctly(self):
        """JSON with type='keplerian' must produce EphemerisNodeKeplerian,
        type='fixed' must produce EphemerisNodeFixed."""
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
                "gs-test": EphemerisNodeFixed(lat_deg=0.0, lon_deg=0.0, alt_km=0.0),
            },
        )
        restored = SessionEphemeris.model_validate_json(eph.model_dump_json())
        assert type(restored.nodes["sat-P00S00"]).__name__ == "EphemerisNodeKeplerian"
        assert type(restored.nodes["gs-test"]).__name__ == "EphemerisNodeFixed"


class TestPlaybackStateContract:
    """PlaybackState must serialize faithfully and reject invalid states."""

    def test_round_trip_all_states(self):
        from nodalarc.models.events import PlaybackState

        for state in ("seeking", "playing", "paused"):
            ps = PlaybackState(epoch_id=5, state=state)
            restored = PlaybackState.model_validate_json(ps.model_dump_json())
            assert restored.state == state
            assert restored.epoch_id == 5

    def test_invalid_state_rejected(self):
        from nodalarc.models.events import PlaybackState
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PlaybackState(epoch_id=0, state="rewinding")


class TestEpochIdOnClockTickAndLinkStateSnapshot:
    """epoch_id must be present on ClockTick and LinkStateSnapshot.

    Without epoch_id, edges cannot distinguish messages from different
    epochs and will apply stale state after a seek.
    """

    def test_clock_tick_has_epoch_id_field(self):
        from nodalarc.models.events import ClockTick

        ct = ClockTick(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            wall_time=datetime(2025, 1, 1, tzinfo=UTC),
            compression_ratio=1.0,
            epoch_id=7,
        )
        data = ct.model_dump(mode="json")
        assert "epoch_id" in data
        assert data["epoch_id"] == 7

    def test_clock_tick_epoch_id_survives_json(self):
        from nodalarc.models.events import ClockTick

        ct = ClockTick(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            wall_time=datetime(2025, 1, 1, tzinfo=UTC),
            compression_ratio=10.0,
            epoch_id=99,
        )
        restored = ClockTick.model_validate_json(ct.model_dump_json())
        assert restored.epoch_id == 99

    def test_clock_tick_epoch_id_defaults_to_zero(self):
        """Pre-v0.71 ClockTick payloads without epoch_id must default to 0."""
        from nodalarc.models.events import ClockTick

        json_str = '{"sim_time":"2025-01-01T00:00:00Z","wall_time":"2025-01-01T00:00:00Z","compression_ratio":1.0}'
        ct = ClockTick.model_validate_json(json_str)
        assert ct.epoch_id == 0

    def test_link_state_snapshot_has_epoch_id_field(self):
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            snapshot_seq=1,
            links=(),
            interval_s=5.0,
            epoch_id=3,
        )
        data = snapshot.model_dump(mode="json")
        assert "epoch_id" in data
        assert data["epoch_id"] == 3

    def test_link_state_snapshot_epoch_id_survives_json(self):
        snapshot = LinkStateSnapshot(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            snapshot_seq=50,
            links=(),
            interval_s=5.0,
            epoch_id=12,
        )
        restored = LinkStateSnapshot.model_validate_json(snapshot.model_dump_json())
        assert restored.epoch_id == 12
        assert restored.snapshot_seq == 50  # seq NOT reset across epochs

    def test_link_state_snapshot_epoch_id_defaults_to_zero(self):
        """Pre-v0.71 LinkStateSnapshot payloads without epoch_id default to 0."""
        import json

        data = {
            "sim_time": "2025-01-01T00:00:00Z",
            "snapshot_seq": 1,
            "links": [],
            "interval_s": 5.0,
        }
        snapshot = LinkStateSnapshot.model_validate_json(json.dumps(data))
        assert snapshot.epoch_id == 0
