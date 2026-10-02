# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for PRD v0.71 distributed ephemeris models.

Verifies serialization round-trips, frozen enforcement, discriminated union
dispatch, and backward-compatible epoch_id defaults on ClockTick and
LinkStateSnapshot.
"""

from __future__ import annotations

import pytest
from nodalarc.models.events import (
    EphemerisNodeKeplerian,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# EphemerisNodeKeplerian
# ---------------------------------------------------------------------------


class TestEphemerisNodeKeplerian:
    def test_propagator_identity_required(self):
        with pytest.raises(ValidationError, match="propagator"):
            EphemerisNodeKeplerian(
                semi_major_axis_km=6928.137,
                eccentricity=0.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                argument_of_perigee_deg=0.0,
                mean_anomaly_deg=0.0,
                plane=0,
                slot=0,
            )


# ---------------------------------------------------------------------------
# EphemerisNodeTLE
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EphemerisNodeFixed
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SessionEphemeris — discriminated union dispatch
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PlaybackState
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ClockTick — epoch_id backward compatibility
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LinkStateSnapshot — epoch_id backward compatibility
# ---------------------------------------------------------------------------
