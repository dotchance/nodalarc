# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Body-fixed frame definitions used by visibility physics."""

from __future__ import annotations

from dataclasses import dataclass

from nodalarc.constants import WGS84_A, WGS84_B


@dataclass(frozen=True, slots=True)
class BodyFrame:
    """Oblate/spherical body-fixed frame for line-of-sight and topocentric math."""

    name: str
    equatorial_radius_km: float
    polar_radius_km: float
    rotation_rate_rad_s: float


EARTH_BODY_FRAME = BodyFrame(
    name="earth",
    equatorial_radius_km=WGS84_A,
    polar_radius_km=WGS84_B,
    rotation_rate_rad_s=7.2921150e-5,
)
LUNA_BODY_FRAME = BodyFrame(
    name="luna",
    equatorial_radius_km=1737.4,
    polar_radius_km=1737.4,
    rotation_rate_rad_s=2.6616995e-6,
)
MARS_BODY_FRAME = BodyFrame(
    name="mars",
    equatorial_radius_km=3396.19,
    polar_radius_km=3376.20,
    rotation_rate_rad_s=7.0882181e-5,
)

BODY_FRAMES: dict[str, BodyFrame] = {
    EARTH_BODY_FRAME.name: EARTH_BODY_FRAME,
    LUNA_BODY_FRAME.name: LUNA_BODY_FRAME,
    MARS_BODY_FRAME.name: MARS_BODY_FRAME,
}


def body_frame_for(reference_body: str) -> BodyFrame:
    """Return the configured body frame or fail loud for unsupported bodies."""

    try:
        return BODY_FRAMES[reference_body]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported reference_body={reference_body!r} for physical_v1 "
            f"ground visibility. Supported bodies: {sorted(BODY_FRAMES)!r}."
        ) from exc
