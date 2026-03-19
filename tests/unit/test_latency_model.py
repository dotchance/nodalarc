"""Test latency model — speed-of-light, position table, threshold detection."""

from datetime import UTC, datetime

from nodalarc.models.events import NodePosition, TimelinePositionSnapshot

from orchestrator.latency_model import (
    PositionTable,
    compute_latency_ms,
    compute_range_km,
)


class TestComputeLatency:
    def test_1000km_latency(self):
        """1000 km → ~3.336 ms one-way."""
        lat = compute_latency_ms(1000.0)
        assert abs(lat - 3.336) < 0.01

    def test_zero_range(self):
        assert compute_latency_ms(0.0) == 0.0

    def test_proportional(self):
        """Double the range, double the latency."""
        lat1 = compute_latency_ms(500.0)
        lat2 = compute_latency_ms(1000.0)
        assert abs(lat2 / lat1 - 2.0) < 1e-10


class TestComputeRange:
    def test_same_point(self):
        assert compute_range_km((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) == 0.0

    def test_known_distance(self):
        d = compute_range_km((0.0, 0.0, 0.0), (3.0, 4.0, 0.0))
        assert abs(d - 5.0) < 1e-10


class TestPositionTable:
    def _make_snapshot(self, positions: dict) -> TimelinePositionSnapshot:
        nodes = {}
        for nid, (lat, lon, alt) in positions.items():
            nodes[nid] = NodePosition(
                lat_deg=lat,
                lon_deg=lon,
                alt_km=alt,
                vel_x_km_s=0.0,
                vel_y_km_s=0.0,
                vel_z_km_s=0.0,
            )
        return TimelinePositionSnapshot(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            positions=nodes,
        )

    def test_update_and_retrieve(self):
        table = PositionTable()
        snap = self._make_snapshot({"sat-A": (0.0, 0.0, 550.0)})
        table.update_from_snapshot(snap)
        pos = table.get_position("sat-A")
        assert pos is not None
        assert len(pos) == 3

    def test_unknown_node(self):
        table = PositionTable()
        assert table.get_position("unknown") is None

    def test_link_latency_between_nodes(self):
        table = PositionTable()
        # Two sats at same altitude, different longitudes on equator
        snap = self._make_snapshot(
            {
                "sat-A": (0.0, 0.0, 550.0),
                "sat-B": (0.0, 10.0, 550.0),  # ~10 degrees apart
            }
        )
        table.update_from_snapshot(snap)
        lat = table.compute_link_latency("sat-A", "sat-B")
        assert lat is not None
        assert lat > 0.0

    def test_link_latency_unknown_node(self):
        table = PositionTable()
        assert table.compute_link_latency("sat-A", "sat-B") is None


class TestThresholdDetection:
    def _make_snapshot(self, positions: dict) -> TimelinePositionSnapshot:
        nodes = {}
        for nid, (lat, lon, alt) in positions.items():
            nodes[nid] = NodePosition(
                lat_deg=lat,
                lon_deg=lon,
                alt_km=alt,
                vel_x_km_s=0.0,
                vel_y_km_s=0.0,
                vel_z_km_s=0.0,
            )
        return TimelinePositionSnapshot(
            sim_time=datetime(2025, 1, 1, tzinfo=UTC),
            positions=nodes,
        )

    def test_threshold_triggers_update(self):
        table = PositionTable()
        snap = self._make_snapshot(
            {
                "sat-A": (0.0, 0.0, 550.0),
                "sat-B": (0.0, 10.0, 550.0),
            }
        )
        table.update_from_snapshot(snap)

        active = {("sat-A", "sat-B")}
        # No previous latency → should trigger
        updates = table.get_links_needing_update(active, {}, threshold_ms=0.1)
        assert len(updates) == 1
        node_a, node_b, lat, rng = updates[0]
        assert node_a == "sat-A"
        assert node_b == "sat-B"
        assert lat > 0.0
        assert rng > 0.0

    def test_threshold_suppresses_small_change(self):
        table = PositionTable()
        snap = self._make_snapshot(
            {
                "sat-A": (0.0, 0.0, 550.0),
                "sat-B": (0.0, 10.0, 550.0),
            }
        )
        table.update_from_snapshot(snap)

        active = {("sat-A", "sat-B")}
        # First call to get the latency
        updates = table.get_links_needing_update(active, {}, threshold_ms=0.1)
        current_latency = updates[0][2]

        # Same positions — no change should be suppressed
        last = {("sat-A", "sat-B"): current_latency}
        updates2 = table.get_links_needing_update(active, last, threshold_ms=0.1)
        assert len(updates2) == 0

    def test_threshold_detects_large_change(self):
        table = PositionTable()
        snap1 = self._make_snapshot(
            {
                "sat-A": (0.0, 0.0, 550.0),
                "sat-B": (0.0, 10.0, 550.0),
            }
        )
        table.update_from_snapshot(snap1)

        active = {("sat-A", "sat-B")}
        updates1 = table.get_links_needing_update(active, {})
        lat1 = updates1[0][2]
        last = {("sat-A", "sat-B"): lat1}

        # Move sat-B significantly
        snap2 = self._make_snapshot(
            {
                "sat-A": (0.0, 0.0, 550.0),
                "sat-B": (0.0, 20.0, 550.0),
            }
        )
        table.update_from_snapshot(snap2)
        updates2 = table.get_links_needing_update(active, last, threshold_ms=0.1)
        assert len(updates2) == 1
        assert updates2[0][2] > lat1  # Latency increased
