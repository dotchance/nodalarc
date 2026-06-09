# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Latency model — computes one-way latency from SessionEphemeris geometry.

Holds OME-published ephemeris inputs for cross-check and diagnostic paths.
Propagates only the two endpoints of a link on demand (never the full
constellation). Ground station positions are static ECEF.

Does NOT apply tc commands, manage interfaces, or know about convergence.
"""

from __future__ import annotations

import math

from nodalarc.body_frames import BodyFrame
from nodalarc.frames import EcefVec3, GeoPosition
from nodalarc.geo import compute_latency_ms, compute_range_km, geodetic_to_ecef
from nodalarc.models.events import (
    EphemerisNodeFixed,
    EphemerisNodeKeplerian,
    EphemerisNodeTLE,
    SessionEphemeris,
)
from nodalarc.orbital import OrbitalElements
from nodalarc.propagator import (
    propagate_j2_mean_elements_for_body,
    propagate_keplerian_for_body,
    propagate_sgp4_tle,
)


class PositionTable:
    """Local ephemeris propagator for on-demand latency computation.

    Initialized from SessionEphemeris. Propagates satellite positions using
    the ephemeris node type OME published. Ground stations are static ECEF
    positions.
    """

    def __init__(self) -> None:
        self._sat_elements: dict[str, object] = {}  # node_id -> OrbitalElements
        self._sat_propagators: dict[str, str] = {}
        self._sat_reference_bodies: dict[str, str] = {}
        self._sat_tles: dict[str, tuple[str, str]] = {}
        self._body_frames: dict[str, BodyFrame] = {}
        self._gs_ecef: dict[str, EcefVec3] = {}
        self._epoch_unix: float = 0.0
        self._loaded = False

    @property
    def loaded(self) -> bool:
        """True if ephemeris has been loaded."""
        return self._loaded

    def load_ephemeris(self, ephemeris: SessionEphemeris) -> None:
        """Load propagation inputs from SessionEphemeris.

        Satellites get the engine-specific propagation inputs OME published.
        Ground stations get static ECEF positions.
        """
        self._sat_elements.clear()
        self._sat_propagators.clear()
        self._sat_reference_bodies.clear()
        self._sat_tles.clear()
        self._body_frames = {
            body_id: BodyFrame(
                name=frame.body_id,
                mean_radius_km=frame.mean_radius_km,
                equatorial_radius_km=frame.equatorial_radius_km,
                polar_radius_km=frame.polar_radius_km,
                rotation_rate_rad_s=frame.rotation_rate_rad_s,
                gravitational_parameter_km3_s2=frame.gravitational_parameter_km3_s2,
                j2=frame.j2,
            )
            for body_id, frame in ephemeris.body_frames.items()
        }
        self._gs_ecef.clear()
        self._epoch_unix = ephemeris.epoch_unix

        for node_id, node in ephemeris.nodes.items():
            if isinstance(node, EphemerisNodeKeplerian):
                self._sat_elements[node_id] = OrbitalElements(
                    semi_major_axis_km=node.semi_major_axis_km,
                    inclination_rad=math.radians(node.inclination_deg),
                    raan_rad=math.radians(node.raan_deg),
                    mean_anomaly_rad=math.radians(node.mean_anomaly_deg),
                    eccentricity=node.eccentricity,
                    argument_of_perigee_rad=math.radians(node.argument_of_perigee_deg),
                )
                self._sat_propagators[node_id] = node.propagator
                self._sat_reference_bodies[node_id] = node.reference_body
            elif isinstance(node, EphemerisNodeTLE):
                self._sat_tles[node_id] = (node.tle_line_1, node.tle_line_2)
                self._sat_reference_bodies[node_id] = node.reference_body
            elif isinstance(node, EphemerisNodeFixed):
                body_frame = self._required_body_frame(node.reference_body)
                ecef = geodetic_to_ecef(
                    GeoPosition(node.lat_deg, node.lon_deg, node.alt_km),
                    body_frame,
                )
                self._gs_ecef[node_id] = ecef

        self._loaded = True

    def _required_body_frame(self, body_id: str) -> BodyFrame:
        try:
            return self._body_frames[body_id]
        except KeyError as exc:
            raise ValueError(
                f"SessionEphemeris is missing body frame for reference_body={body_id!r}"
            ) from exc

    def _get_ecef(self, node_id: str, sim_time_unix: float) -> EcefVec3 | None:
        """Get ECEF position for a node at the given sim_time.

        Satellites: propagate from OME-published ephemeris inputs.
        Ground stations: return cached static position.
        """
        if node_id in self._gs_ecef:
            return self._gs_ecef[node_id]

        elements = self._sat_elements.get(node_id)
        dt = sim_time_unix - self._epoch_unix
        if elements is not None:
            propagator = self._sat_propagators[node_id]
            body_frame = self._required_body_frame(self._sat_reference_bodies[node_id])
            if propagator == "j2-mean-elements":
                pos_ecef, _vel, _geo, _pos_inertial, _vel_inertial = (
                    propagate_j2_mean_elements_for_body(
                        elements,
                        self._epoch_unix,
                        dt,
                        body_frame=body_frame,
                    )
                )
            elif propagator in ("two-body", "keplerian-circular"):
                pos_ecef, _vel, _geo, _pos_inertial, _vel_inertial = propagate_keplerian_for_body(
                    elements,
                    self._epoch_unix,
                    dt,
                    body_frame=body_frame,
                )
            else:
                raise ValueError(f"Unsupported ephemeris propagator for {node_id}: {propagator!r}")
            return pos_ecef

        tle = self._sat_tles.get(node_id)
        if tle is not None:
            body_frame = self._required_body_frame(self._sat_reference_bodies[node_id])
            pos_ecef, _vel, _geo = propagate_sgp4_tle(
                tle[0],
                tle[1],
                self._epoch_unix,
                dt,
                body_frame=body_frame,
            )
            return pos_ecef

        return None

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
