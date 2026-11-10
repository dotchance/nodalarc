# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""A ground station's elevation floor is bounded by the horizon it can see."""

from __future__ import annotations

import math

import pytest
from nodalarc.ome_inputs import _horizon_dip_deg

EARTH_MEAN_RADIUS_KM = 6371.0088


def test_a_station_at_the_surface_sees_its_horizon_at_zero_degrees() -> None:
    assert _horizon_dip_deg(0.0, EARTH_MEAN_RADIUS_KM) == 0.0


def test_a_mountain_station_sees_below_its_local_horizontal() -> None:
    # From 3 km the limb of a 6371 km sphere sits about 1.76 degrees down.
    assert _horizon_dip_deg(3000.0, EARTH_MEAN_RADIUS_KM) == pytest.approx(
        math.degrees(math.acos(6371008.8 / 6374008.8)), abs=1e-9
    )
    assert _horizon_dip_deg(3000.0, EARTH_MEAN_RADIUS_KM) == pytest.approx(1.7577, abs=1e-3)
