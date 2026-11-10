# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Sampled body states for the page.

The page has no model of body motion or orientation. OME publishes the states
of every session body, and the Sun, at fixed instants; the page interpolates
positions with cubic Hermite polynomials (position and velocity at both ends)
and rotations at constant angular rate between samples. OME and VS-API's
Builder preview both produce windows with ``body_sample_window``.

Sample spacing. Measured worst interpolation error over one day for Earth and
Luna, at the quarter, half and three-quarter points between samples:

- 600 s: Moon position 0.1 mm; Earth orientation 0.4 mm at GEO radius.
- 3600 s: Moon position 9.7 mm; Earth orientation 12 mm at GEO radius.

600 s is used. A newly supported body is measured the same way before it is
sampled at this spacing.

Window span. A window starts one hour before the instant it is produced and
ends six hours after it. OME produces the next window when simulation time
passes the halfway point of the six hours, so the page holds three hours of
samples ahead when the replacement is produced. At 1,000 times real time
that is 10.8 s of wall time between windows, and the one hour behind the
production instant covers a page that renders up to 3.6 s of wall time
behind OME. For Earth and Luna a window holds 44 samples, takes 1.1 ms to
produce and serializes to about 45 KB.
"""

from __future__ import annotations

import math

from nodalarc.models.events import BodySample, BodySampleInstant, BodySamples, SunSample
from nodalarc.physics.context import PhysicsContext

SAMPLE_INTERVAL_S = 600.0
WINDOW_BEFORE_S = 3600.0
WINDOW_AFTER_S = 21600.0


def body_sample_window(
    physics: PhysicsContext, *, epoch_id: int, produced_at_unix: float
) -> BodySamples:
    """The window of body samples around ``produced_at_unix``.

    Sample instants are multiples of the interval since the Unix epoch, so
    every window places samples at the same instants.
    """
    start = math.floor((produced_at_unix - WINDOW_BEFORE_S) / SAMPLE_INTERVAL_S) * SAMPLE_INTERVAL_S
    end = produced_at_unix + WINDOW_AFTER_S
    count = math.ceil((end - start) / SAMPLE_INTERVAL_S) + 1
    instants = []
    for index in range(count):
        t = start + index * SAMPLE_INTERVAL_S
        bodies = {}
        for body_id in physics.body_ids:
            state = physics.body_state(body_id, t)
            orientation = physics.orientation(body_id, t)
            rows = orientation.rotation
            bodies[body_id] = BodySample(
                position_km=tuple(state.position_km),
                velocity_km_s=tuple(state.velocity_km_s),
                rotation=(tuple(rows[0]), tuple(rows[1]), tuple(rows[2])),
                angular_velocity_rad_s=tuple(orientation.angular_velocity_rad_s),
            )
        sun = physics.sun_state(t)
        instants.append(
            BodySampleInstant(
                sim_time_unix=t,
                bodies=bodies,
                sun=SunSample(
                    position_km=tuple(sun.position_km), velocity_km_s=tuple(sun.velocity_km_s)
                ),
            )
        )
    return BodySamples(epoch_id=epoch_id, interval_s=SAMPLE_INTERVAL_S, samples=tuple(instants))


def next_window_due_unix(produced_at_unix: float) -> float:
    """When OME produces the window after the one produced at ``produced_at_unix``."""
    return produced_at_unix + WINDOW_AFTER_S / 2.0
