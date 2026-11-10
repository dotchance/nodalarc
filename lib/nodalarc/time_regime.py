# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Whether a wall-clock result was measured entirely at the speed of the emulated world.

Linux queues, routing daemons and workloads run on wall time at every playback speed; OME's sim
time can run faster or slower, stop, and jump. A result NodalArc measures on wall time (a round
trip, a delay comparison, a transfer time, a convergence time, a timeout) describes the emulated
world only over an interval that ran at 1x, unpaused, with OME delivering the commanded rate,
and that no seek crossed. Any other interval marks the result non-representative. The value
stays as measured: the marking says what the measurement ran through, and returning to 1x
afterwards does not clear it.

The record is OME's: every regime OME ran in over a trailing window, each with the wall time it
started (OME's clock) and why. Checking the speed at the end of a measurement would miss a
change made and undone during it. Rejected alternative: scaling the measured values by the
speed. Applications and protocol timers on the nodes run on wall time, so a scaled value
describes no run of the experiment.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from nodalarc.models.events import TimeRegime


class TimeRegimeRecordGap(ValueError):
    """The record does not reach back to the start of the interval: the regime then is unknown."""


def regimes_during(
    start: datetime, end: datetime | None, regimes: Sequence[TimeRegime]
) -> list[TimeRegime]:
    """The regimes in effect at any time in [start, end] (end None: until now), from OME's record
    of regimes in the order they started."""
    if not regimes:
        raise TimeRegimeRecordGap("OME's time-control record is empty")
    if regimes[0].started_at > start:
        raise TimeRegimeRecordGap(
            f"OME's time-control record starts at {regimes[0].started_at.isoformat()}, after "
            f"the interval start {start.isoformat()}"
        )
    during = []
    for index, regime in enumerate(regimes):
        following = regimes[index + 1] if index + 1 < len(regimes) else None
        ended_before_start = following is not None and following.started_at <= start
        started_after_end = end is not None and regime.started_at > end
        if not ended_before_start and not started_after_end:
            during.append(regime)
    return during


def representative(during: Sequence[TimeRegime]) -> bool:
    """One regime throughout, at 1x, playing, delivering the commanded rate."""
    return len(during) == 1 and during[0].at_world_speed
