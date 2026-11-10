# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Common-frame node positions for a resolved session, against SPICE.

Ranking, and any other consumer of ``session_node_positions``, compares
distances between nodes of different kinds and bodies. Each position must be
the node's place relative to Earth's center on ICRF axes, with its body's
position added. SPICE composes the same positions independently from the same
NAIF files.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import spiceypy
import yaml
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.models.session import session_epoch_unix
from nodalarc.physics import epoch_of
from nodalarc.physics.session import (
    body_frame_from_facts,
    session_node_positions,
    session_physics,
)
from nodalarc.resolve_session import resolve_session

SESSION = Path(__file__).resolve().parents[3] / "catalog/nodalarc/sessions/earth-luna-dtn.yaml"
SPICE_FIXED = {"earth": "ITRF93", "luna": "MOON_ME_DE440_ME421"}
SPICE_BODY = {"earth": "EARTH", "luna": "MOON"}


@pytest.fixture(scope="module")
def resolved():
    return resolve_session(
        yaml.safe_load(SESSION.read_text()),
        catalog=FilesystemCatalogReadView(CatalogRoots.from_catalog_root("catalog/nodalarc")),
    )


@pytest.mark.usefixtures("spice_reference")
def test_surface_sites_match_spice_composition(resolved) -> None:
    t = session_epoch_unix(resolved.time)
    et = epoch_of(t).to_et_seconds()
    positions = session_node_positions(resolved, session_physics(resolved), t)
    frames = {facts.body_id: body_frame_from_facts(facts) for facts in resolved.bodies}
    sites = [node for node in resolved.nodes if node.surface_position is not None]
    assert {node.surface_position.body for node in sites} == {"earth", "luna"}
    for node in sites:
        site = node.surface_position
        frame = frames[site.body]
        flattening = (
            frame.equatorial_radius_km - frame.polar_radius_km
        ) / frame.equatorial_radius_km
        fixed = np.array(
            spiceypy.georec(
                math.radians(site.lon_deg),
                math.radians(site.lat_deg),
                site.alt_m / 1000.0,
                frame.equatorial_radius_km,
                flattening,
            )
        )
        origin, _ = spiceypy.spkpos(SPICE_BODY[site.body], et, "J2000", "NONE", "EARTH")
        reference = (
            np.array(origin)
            + np.array(spiceypy.pxform(SPICE_FIXED[site.body], "J2000", et)) @ fixed
        )
        assert np.linalg.norm(np.array(positions[node.node_id]) - reference) < 0.1, node.node_id


def test_lunar_satellites_are_placed_around_the_moon(resolved) -> None:
    t = session_epoch_unix(resolved.time)
    physics = session_physics(resolved)
    positions = session_node_positions(resolved, physics, t)
    moon = np.array(physics.body_state("luna", t).position_km)
    lunar = [n for n in resolved.nodes if n.orbit is not None and n.orbit.central_body == "luna"]
    assert lunar
    for node in lunar:
        orbit = node.orbit
        radius = np.linalg.norm(np.array(positions[node.node_id]) - moon)
        perilune = orbit.semi_major_axis_km * (1.0 - orbit.eccentricity)
        apolune = orbit.semi_major_axis_km * (1.0 + orbit.eccentricity)
        assert perilune - 1e-6 <= radius <= apolune + 1e-6, node.node_id
