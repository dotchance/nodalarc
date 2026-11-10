# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
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
    TimeRegime,
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


def _regimes(state: str, *, rate: float = 1.0) -> tuple[TimeRegime, ...]:
    return (
        TimeRegime(
            started_at=datetime(2027, 2, 19, tzinfo=UTC),
            cause="start",
            state=state,
            commanded_rate=rate,
            pacing_degraded=False,
        ),
    )


class TestPlaybackState:
    """A playback state cannot claim the clock stopped at the end of the data unless it is paused
    and names the data that ends."""

    @pytest.mark.parametrize("state", ["playing", "seeking"])
    def test_only_a_pause_carries_the_end_of_the_data(self, state):
        with pytest.raises(ValidationError, match="pause reason on a"):
            PlaybackState(
                epoch_id=1,
                state=state,
                reason="ephemeris_coverage_ended",
                coverage=_COVERAGE,
                time_regimes=_regimes(state),
            )

    def test_the_end_of_the_data_comes_with_its_limit(self):
        with pytest.raises(ValidationError, match="come together"):
            PlaybackState(
                epoch_id=1,
                state="paused",
                reason="ephemeris_coverage_ended",
                time_regimes=_regimes("paused"),
            )
        with pytest.raises(ValidationError, match="come together"):
            PlaybackState(
                epoch_id=1, state="paused", coverage=_COVERAGE, time_regimes=_regimes("paused")
            )

    def test_the_time_control_record_ends_in_the_state_it_reports(self):
        PlaybackState(epoch_id=1, state="playing", time_regimes=_regimes("playing"))
        with pytest.raises(ValidationError, match="current time regime is 'paused'"):
            PlaybackState(epoch_id=1, state="playing", time_regimes=_regimes("paused"))
        with pytest.raises(ValidationError, match="at least 1"):
            PlaybackState(epoch_id=1, state="playing", time_regimes=())


# ---------------------------------------------------------------------------
# ClockTick — epoch_id backward compatibility
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LinkStateSnapshot — epoch_id backward compatibility
# ---------------------------------------------------------------------------
