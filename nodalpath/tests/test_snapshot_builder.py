"""Tests for nodalpath.orchestrator.snapshot_builder."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.models.events import (
    NodePosition,
    TimelinePositionSnapshot,
    VisibilityEvent,
)
from nodalpath.models.topology import TopologyNode
from nodalpath.orchestrator.snapshot_builder import SnapshotBuilder


class TestSnapshotBuilder:
    """Tests for SnapshotBuilder."""

    def _make_builder(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> SnapshotBuilder:
        return SnapshotBuilder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )

    def _link_up(self, a: str, b: str, range_km: float = 2000.0) -> VisibilityEvent:
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        return VisibilityEvent(
            sim_time=t0, node_a=a, node_b=b,
            visible=True, scheduled=True, range_km=range_km,
            elevation_deg=None, terminal_type="optical",
        )

    def _link_down(self, a: str, b: str) -> VisibilityEvent:
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        return VisibilityEvent(
            sim_time=t0, node_a=a, node_b=b,
            visible=False, scheduled=True, range_km=5500.0,
            elevation_deg=None, terminal_type="optical",
        )

    def test_link_up_in_active_set(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> None:
        """A link_up event adds the pair to the active link set."""
        builder = self._make_builder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )
        builder.apply_link_event(self._link_up("sat-P00S00", "sat-P00S01"))
        assert ("sat-P00S00", "sat-P00S01") in builder.active_link_set

    def test_link_down_removed(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> None:
        """A link_down event removes the pair from the active link set."""
        builder = self._make_builder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )
        builder.apply_link_event(self._link_up("sat-P00S00", "sat-P00S01"))
        assert ("sat-P00S00", "sat-P00S01") in builder.active_link_set

        builder.apply_link_event(self._link_down("sat-P00S00", "sat-P00S01"))
        assert ("sat-P00S00", "sat-P00S01") not in builder.active_link_set

    def test_build_snapshot_edge_count(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> None:
        """build_snapshot produces one edge per active link."""
        builder = self._make_builder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )
        builder.apply_link_event(self._link_up("sat-P00S00", "sat-P00S01"))
        builder.apply_link_event(self._link_up("sat-P00S00", "sat-P01S00", 5000.0))

        snapshot = builder.build_snapshot("2026-03-01T14:30:00+00:00")
        assert len(snapshot.edges) == 2
        assert len(snapshot.nodes) == 6

    def test_correct_interfaces_from_map(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> None:
        """Edges use interfaces from the interface_map."""
        builder = self._make_builder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )
        builder.apply_link_event(self._link_up("sat-P00S00", "sat-P00S01"))

        snapshot = builder.build_snapshot("2026-03-01T14:30:00+00:00")
        edge = snapshot.edges[0]
        assert edge.src_interface == "isl0"
        assert edge.dst_interface == "isl0"

    def test_positive_latency(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> None:
        """Edge latency is positive and computed from range_km."""
        builder = self._make_builder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )
        range_km = 2000.0
        builder.apply_link_event(self._link_up("sat-P00S00", "sat-P00S01", range_km))

        snapshot = builder.build_snapshot("2026-03-01T14:30:00+00:00")
        expected_ms = range_km / SPEED_OF_LIGHT_KM_S * 1000.0
        assert snapshot.edges[0].latency_ms == pytest.approx(expected_ms, rel=1e-6)
        assert snapshot.edges[0].latency_ms > 0

    def test_canonical_pairs(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> None:
        """Active link set uses canonical (alphabetically ordered) pairs."""
        builder = self._make_builder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )
        # VisibilityEvent already orders node_a < node_b
        builder.apply_link_event(self._link_up("sat-P00S00", "sat-P00S01"))
        links = builder.active_link_set
        for a, b in links:
            assert a < b

    def test_link_type_detection(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> None:
        """link_type is 'ground' for GS links and 'isl' for satellite links."""
        builder = self._make_builder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        # ISL link
        builder.apply_link_event(self._link_up("sat-P00S00", "sat-P00S01"))
        # GS link
        gs_event = VisibilityEvent(
            sim_time=t0, node_a="gs-alpha", node_b="sat-P00S00",
            visible=True, scheduled=True, range_km=600.0,
            elevation_deg=45.0, terminal_type="optical",
        )
        builder.apply_link_event(gs_event)

        snapshot = builder.build_snapshot("2026-03-01T14:30:00+00:00")
        types = {e.link_type for e in snapshot.edges}
        assert "isl" in types
        assert "ground" in types

    def test_position_update(
        self, simple_node_registry, simple_interface_map, simple_bandwidth_map,
    ) -> None:
        """apply_position_record stores ECEF positions for all nodes."""
        builder = self._make_builder(
            simple_node_registry, simple_interface_map, simple_bandwidth_map,
        )
        t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
        positions = {
            "sat-P00S00": NodePosition(
                lat_deg=45.0, lon_deg=0.0, alt_km=550.0,
                vel_x_km_s=0.0, vel_y_km_s=7.5, vel_z_km_s=0.0,
            ),
        }
        snap = TimelinePositionSnapshot(sim_time=t0, positions=positions)
        builder.apply_position_record(snap)
        assert "sat-P00S00" in builder._positions
        ecef = builder._positions["sat-P00S00"]
        assert len(ecef) == 3
        assert all(isinstance(v, float) for v in ecef)
