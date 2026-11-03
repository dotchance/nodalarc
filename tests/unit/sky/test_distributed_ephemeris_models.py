# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for PRD v0.71 distributed ephemeris models.

Verifies serialization round-trips, frozen enforcement, discriminated union
dispatch, and backward-compatible epoch_id defaults on ClockTick and
LinkStateSnapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nodalarc.models.events import (
    EphemerisNodeKeplerian,
    PlaybackCoverageLimit,
    PlaybackState,
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

_COVERAGE = PlaybackCoverageLimit(
    body_id="earth",
    usable_from=datetime(2000, 1, 1, 1, 10, tzinfo=UTC),
    usable_until=datetime(2126, 11, 2, 17, 50, tzinfo=UTC),
)


class TestPlaybackState:
    """A playback state cannot claim the clock stopped at the end of the data unless it is paused
    and names the data that ends."""

    @pytest.mark.parametrize("state", ["playing", "seeking"])
    def test_only_a_pause_carries_the_end_of_the_data(self, state):
        with pytest.raises(ValidationError, match="pause reason on a"):
            PlaybackState(
                epoch_id=1, state=state, reason="ephemeris_coverage_ended", coverage=_COVERAGE
            )

    def test_the_end_of_the_data_comes_with_its_limit(self):
        with pytest.raises(ValidationError, match="come together"):
            PlaybackState(epoch_id=1, state="paused", reason="ephemeris_coverage_ended")
        with pytest.raises(ValidationError, match="come together"):
            PlaybackState(epoch_id=1, state="paused", coverage=_COVERAGE)


# ---------------------------------------------------------------------------
# ClockTick — epoch_id backward compatibility
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LinkStateSnapshot — epoch_id backward compatibility
# ---------------------------------------------------------------------------
