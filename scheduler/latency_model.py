"""Latency model — computes one-way latency from range.

Maintains a position table updated from TimelinePositionSnapshot events.
Determines when latency changes exceed the update threshold.

Does NOT apply tc commands, manage interfaces, or know about convergence.
"""

from __future__ import annotations

from nodalarc.geo import compute_latency_ms, compute_range_km, geodetic_to_ecef
from nodalarc.models.events import TimelinePositionSnapshot


class PositionTable:
    """Maintains current ECEF positions for all nodes.

    Updated from TimelinePositionSnapshot at each timestep. Internally
    converts geodetic NodePosition (lat/lon/alt) to ECEF for distance
    computation.
    """

    def __init__(self) -> None:
        self._positions: dict[str, tuple[float, float, float]] = {}

    def update_from_snapshot(self, snapshot: TimelinePositionSnapshot) -> None:
        """Update positions from a timeline snapshot (geodetic → ECEF)."""
        for node_id, pos in snapshot.positions.items():
            self._positions[node_id] = geodetic_to_ecef(
                pos.lat_deg,
                pos.lon_deg,
                pos.alt_km,
            )

    def get_position(self, node_id: str) -> tuple[float, float, float] | None:
        """Get the current ECEF position for a node."""
        return self._positions.get(node_id)

    def compute_link_latency(self, node_a: str, node_b: str) -> float | None:
        """Compute one-way latency between two nodes in ms.

        Returns None if either node's position is unknown.
        """
        pos_a = self._positions.get(node_a)
        pos_b = self._positions.get(node_b)
        if pos_a is None or pos_b is None:
            return None
        range_km = compute_range_km(pos_a, pos_b)
        return compute_latency_ms(range_km)

    def get_links_needing_update(
        self,
        active_links: set[tuple[str, str]],
        last_latencies: dict[tuple[str, str], float],
        threshold_ms: float = 0.1,
    ) -> list[tuple[str, str, float, float]]:
        """Find active links where latency changed beyond threshold.

        Returns list of (node_a, node_b, new_latency_ms, range_km) for
        links that need a tc netem update.
        """
        updates: list[tuple[str, str, float, float]] = []
        for node_a, node_b in active_links:
            pos_a = self._positions.get(node_a)
            pos_b = self._positions.get(node_b)
            if pos_a is None or pos_b is None:
                continue
            range_km = compute_range_km(pos_a, pos_b)
            new_latency = compute_latency_ms(range_km)
            prev = last_latencies.get((node_a, node_b))
            if prev is None or abs(new_latency - prev) >= threshold_ms:
                updates.append((node_a, node_b, new_latency, range_km))
        return updates
