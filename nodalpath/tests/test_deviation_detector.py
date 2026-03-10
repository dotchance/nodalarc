"""Tests for DeviationDetector."""

from __future__ import annotations

from datetime import datetime, timezone

from nodalarc.models.link_events import LinkDown, LinkUp
from nodalpath.integration.deviation import DeviationDetector
from nodalpath.models.almanac import AlmanacEntry, ForwardingTable
from nodalpath.orchestrator.almanac_store import AlmanacStore


def _make_link_down(
    node_a: str = "sat-P00S00",
    node_b: str = "sat-P00S01",
    reason: str = "scenario_inject_down",
    sim_time: datetime | None = None,
) -> LinkDown:
    return LinkDown(
        sim_time=sim_time or datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc),
        wall_time=datetime.now(timezone.utc),
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0",
        interface_b="isl0",
        reason=reason,
    )


def _make_link_up(
    node_a: str = "sat-P00S00",
    node_b: str = "sat-P00S01",
    reason: str = "scenario_inject_up",
    sim_time: datetime | None = None,
) -> LinkUp:
    return LinkUp(
        sim_time=sim_time or datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc),
        wall_time=datetime.now(timezone.utc),
        node_a=node_a,
        node_b=node_b,
        interface_a="isl0",
        interface_b="isl0",
        latency_ms=3.5,
        bandwidth_mbps=1000.0,
        reason=reason,
    )


def _make_almanac_entry(
    sim_time: str = "2026-03-01T14:30:00+00:00",
    node_ids: list[str] | None = None,
) -> AlmanacEntry:
    if node_ids is None:
        node_ids = ["sat-P00S00", "sat-P00S01"]
    tables = [
        ForwardingTable(
            node_id=nid,
            topology_state_id="topo-abc",
            sim_time=sim_time,
            lsr_bindings=[],
            ler_ingress_rules=[],
        )
        for nid in node_ids
    ]
    return AlmanacEntry(
        topology_state_id="topo-abc",
        sim_time=sim_time,
        forwarding_tables=tables,
        computed_paths=["p1"],
        computation_time_ms=10.0,
    )


def _build_detector_with_entry(
    sim_time: str = "2026-03-01T14:30:00+00:00",
    node_ids: list[str] | None = None,
) -> DeviationDetector:
    store = AlmanacStore()
    entry = _make_almanac_entry(sim_time, node_ids)
    store.store(entry)
    return DeviationDetector(store)


class TestDeviationDetectorLinkDown:
    def test_scenario_inject_down_is_deviation(self):
        det = _build_detector_with_entry()
        event = _make_link_down(reason="scenario_inject_down")
        assert det.check_link_down(event) is True

    def test_satellite_loss_is_deviation(self):
        det = _build_detector_with_entry()
        event = _make_link_down(reason="satellite_loss")
        assert det.check_link_down(event) is True

    def test_vis_lost_is_not_deviation(self):
        det = _build_detector_with_entry()
        event = _make_link_down(reason="vis_lost")
        assert det.check_link_down(event) is False

    def test_tracking_exceeded_is_not_deviation(self):
        det = _build_detector_with_entry()
        event = _make_link_down(reason="tracking_exceeded")
        assert det.check_link_down(event) is False

    def test_gs_below_horizon_is_not_deviation(self):
        det = _build_detector_with_entry()
        event = _make_link_down(reason="gs_below_horizon")
        assert det.check_link_down(event) is False

    def test_deviation_requires_both_nodes_in_almanac(self):
        # Only sat-P00S00 has a forwarding table
        det = _build_detector_with_entry(node_ids=["sat-P00S00"])
        event = _make_link_down(
            node_a="sat-P00S00", node_b="sat-P00S01",
            reason="scenario_inject_down",
        )
        assert det.check_link_down(event) is False

    def test_deviation_requires_almanac_entry_at_time(self):
        det = _build_detector_with_entry(sim_time="2026-03-01T15:00:00+00:00")
        # Event at earlier time with no almanac entry
        event = _make_link_down(
            reason="scenario_inject_down",
            sim_time=datetime(2026, 3, 1, 14, 0, 0, tzinfo=timezone.utc),
        )
        assert det.check_link_down(event) is False

    def test_deviation_count_increments(self):
        det = _build_detector_with_entry()
        assert det.deviation_count == 0
        det.check_link_down(_make_link_down(reason="scenario_inject_down"))
        assert det.deviation_count == 1
        det.check_link_down(_make_link_down(reason="satellite_loss"))
        assert det.deviation_count == 2


class TestDeviationDetectorLinkUp:
    def test_link_up_scenario_inject_up_returns_true(self):
        det = _build_detector_with_entry()
        event = _make_link_up(reason="scenario_inject_up")
        assert det.check_link_up(event) is True

    def test_link_up_vis_gained_returns_false(self):
        det = _build_detector_with_entry()
        event = _make_link_up(reason="vis_gained")
        assert det.check_link_up(event) is False
