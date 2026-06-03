# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Body-fixed frame definitions used by visibility physics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nodalarc.constants import WGS84_A, WGS84_B

SupportedSurfaceBody = Literal["earth", "luna", "mars"]
SUPPORTED_BODY_NAMES: tuple[SupportedSurfaceBody, ...] = ("earth", "luna", "mars")

# Frame bodies extend surface bodies with celestial frames that are not surface
# bodies for ground-station math. "sun" is a frame body only (Lagrange /
# interplanetary frames); it has no surface-physics BodyFrame entry.
FrameBodyName = Literal["earth", "luna", "mars", "sun"]
FRAME_BODY_NAMES: tuple[FrameBodyName, ...] = ("earth", "luna", "mars", "sun")


@dataclass(frozen=True, slots=True)
class BodyFrame:
    """Body constants for propagation, body-fixed geometry, and rendering facts."""

    name: str
    equatorial_radius_km: float
    polar_radius_km: float
    rotation_rate_rad_s: float
    gravitational_parameter_km3_s2: float
    j2: float = 0.0

    @property
    def mean_radius_km(self) -> float:
        return (2.0 * self.equatorial_radius_km + self.polar_radius_km) / 3.0


EARTH_BODY_FRAME = BodyFrame(
    name="earth",
    equatorial_radius_km=WGS84_A,
    polar_radius_km=WGS84_B,
    rotation_rate_rad_s=7.2921159e-5,
    gravitational_parameter_km3_s2=398600.4418,
    j2=1.08262668e-3,
)
LUNA_BODY_FRAME = BodyFrame(
    name="luna",
    equatorial_radius_km=1737.4,
    polar_radius_km=1737.4,
    rotation_rate_rad_s=2.6616995e-6,
    gravitational_parameter_km3_s2=4902.800066,
    j2=0.0,
)
MARS_BODY_FRAME = BodyFrame(
    name="mars",
    equatorial_radius_km=3396.19,
    polar_radius_km=3376.20,
    rotation_rate_rad_s=7.0882181e-5,
    gravitational_parameter_km3_s2=42828.375214,
    j2=1.96045e-3,
)

BODY_FRAMES: dict[SupportedSurfaceBody, BodyFrame] = {
    "earth": EARTH_BODY_FRAME,
    "luna": LUNA_BODY_FRAME,
    "mars": MARS_BODY_FRAME,
}


def body_frame_for(reference_body: SupportedSurfaceBody | str) -> BodyFrame:
    """Return the configured body frame or fail loud for unsupported bodies."""
    try:
        return BODY_FRAMES[reference_body]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported reference_body={reference_body!r} for terminal_physics "
            f"ground visibility. Supported surface bodies: {sorted(BODY_FRAMES)!r}."
        ) from exc
