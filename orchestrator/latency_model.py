"""Latency model — computes one-way latency from range.

Maintains a position table updated from TimelinePositionSnapshot events.
Determines when latency changes exceed the update threshold.

Does NOT apply tc commands, manage interfaces, or know about convergence.
"""

from __future__ import annotations

import math

from nodalarc.constants import SPEED_OF_LIGHT_KM_S, WGS84_A, WGS84_E2
from nodalarc.models.events import TimelinePositionSnapshot


def _geodetic_to_ecef(
    lat_deg: float, lon_deg: float, alt_km: float,
) -> tuple[float, float, float]:
    """Convert geodetic (lat, lon, alt) to ECEF xyz in km."""
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + alt_km) * cos_lat * math.cos(lon_rad)
    y = (n + alt_km) * cos_lat * math.sin(lon_rad)
    z = (n * (1.0 - WGS84_E2) + alt_km) * sin_lat
    return (x, y, z)


def compute_latency_ms(range_km: float) -> float:
    """Compute one-way propagation delay from range in km.

    PRD R-TO-002: one_way_latency_ms = range_km / 299792.458 * 1000
    """
    return range_km / SPEED_OF_LIGHT_KM_S * 1000.0


def compute_range_km(
    pos_a: tuple[float, float, float],
    pos_b: tuple[float, float, float],
) -> float:
    """Euclidean distance between two ECEF positions in km."""
    dx = pos_a[0] - pos_b[0]
    dy = pos_a[1] - pos_b[1]
    dz = pos_a[2] - pos_b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


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
            self._positions[node_id] = _geodetic_to_ecef(
                pos.lat_deg, pos.lon_deg, pos.alt_km,
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
