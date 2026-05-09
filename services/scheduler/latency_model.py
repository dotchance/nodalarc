# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Latency model — computes one-way latency from local Keplerian propagation.

Holds orbital elements from SessionEphemeris. Propagates only the two
endpoints of a link on demand (never the full constellation). Ground
station positions are static ECEF.

Does NOT apply tc commands, manage interfaces, or know about convergence.
"""

from __future__ import annotations

from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.geo import compute_latency_ms, compute_range_km, geodetic_to_ecef
from nodalarc.models.events import (
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    SessionEphemeris,
)
from nodalarc.orbital import elements_from_params
from nodalarc.propagator import propagate_keplerian


class PositionTable:
    """Local Keplerian propagator for on-demand latency computation.

    Initialized from SessionEphemeris. Propagates satellite positions
    from orbital elements at the requested sim_time. Ground stations
    are static ECEF positions.
    """

    def __init__(self) -> None:
        self._sat_elements: dict[str, object] = {}  # node_id -> OrbitalElements
        self._gs_ecef: dict[str, EcefVec3] = {}
        self._epoch_unix: float = 0.0
        self._loaded = False

    @property
    def loaded(self) -> bool:
        """True if ephemeris has been loaded."""
        return self._loaded

    def load_ephemeris(self, ephemeris: SessionEphemeris) -> None:
        """Load orbital elements from SessionEphemeris.

        Satellites get OrbitalElements for on-demand propagation.
        Ground stations get static ECEF positions (no propagation needed).
        """
        self._sat_elements.clear()
        self._gs_ecef.clear()
        self._epoch_unix = ephemeris.epoch_unix

        for node_id, node in ephemeris.nodes.items():
            if isinstance(node, EphemerisNodeKeplerian):
                self._sat_elements[node_id] = elements_from_params(
                    node.altitude_km,
                    node.inclination_deg,
                    node.raan_deg,
                    node.true_anomaly_deg,
                )
            elif isinstance(node, EphemerisNodeFixed):
                ecef = geodetic_to_ecef(GeoPosition(node.lat_deg, node.lon_deg, node.alt_km))
                self._gs_ecef[node_id] = ecef

        self._loaded = True

    def _get_ecef(self, node_id: str, sim_time_unix: float) -> EcefVec3 | None:
        """Get ECEF position for a node at the given sim_time.

        Satellites: propagate from orbital elements.
        Ground stations: return cached static position.
        """
        if node_id in self._gs_ecef:
            return self._gs_ecef[node_id]

        elements = self._sat_elements.get(node_id)
        if elements is None:
            return None

        dt = sim_time_unix - self._epoch_unix
        pos_ecef, _vel, _geo = propagate_keplerian(elements, self._epoch_unix, dt)
        return pos_ecef

    def compute_link_range(
        self, node_a: str, node_b: str, sim_time_unix: float = 0.0
    ) -> float | None:
        """Compute range between two nodes in km at the given sim_time.

        Returns None if either node's elements are unknown.
        """
        pos_a = self._get_ecef(node_a, sim_time_unix)
        pos_b = self._get_ecef(node_b, sim_time_unix)
        if pos_a is None or pos_b is None:
            return None
        return compute_range_km(pos_a, pos_b)

    def compute_link_latency(
        self, node_a: str, node_b: str, sim_time_unix: float = 0.0
    ) -> float | None:
        """Compute one-way latency between two nodes in ms at the given sim_time.

        Returns None if either node's elements are unknown.
        """
        pos_a = self._get_ecef(node_a, sim_time_unix)
        pos_b = self._get_ecef(node_b, sim_time_unix)
        if pos_a is None or pos_b is None:
            return None
        range_km = compute_range_km(pos_a, pos_b)
        return compute_latency_ms(range_km)

    def get_links_needing_update(
        self,
        active_links: set[tuple[str, str]],
        last_latencies: dict[tuple[str, str], float],
        sim_time_unix: float = 0.0,
        threshold_ms: float = 0.1,
    ) -> list[tuple[str, str, float, float]]:
        """Find active links where latency changed beyond threshold.

        Propagates only the endpoints of active links at sim_time_unix.
        Returns list of (node_a, node_b, new_latency_ms, range_km).
        """
        updates: list[tuple[str, str, float, float]] = []
        for node_a, node_b in active_links:
            pos_a = self._get_ecef(node_a, sim_time_unix)
            pos_b = self._get_ecef(node_b, sim_time_unix)
            if pos_a is None or pos_b is None:
                continue
            range_km = compute_range_km(pos_a, pos_b)
            new_latency = compute_latency_ms(range_km)
            prev = last_latencies.get((node_a, node_b))
            if prev is None or abs(new_latency - prev) >= threshold_ms:
                updates.append((node_a, node_b, new_latency, range_km))
        return updates
