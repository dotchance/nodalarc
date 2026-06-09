# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Body-fixed frame definitions used by visibility physics.

Body primitive facts such as radii and gravitational parameter come from the
resolved session. This module only owns runtime support facts that are not yet
part of the approved body primitive grammar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SupportedSurfaceBody = Literal["earth", "luna"]
SUPPORTED_BODY_NAMES: tuple[SupportedSurfaceBody, ...] = ("earth", "luna")

# Frame bodies extend surface bodies with celestial frames that are not surface
# bodies for ground-station math. "sun" is a frame body only (Lagrange /
# interplanetary frames); it has no surface-physics BodyFrame entry.
FrameBodyName = Literal["earth", "luna", "sun"]
FRAME_BODY_NAMES: tuple[FrameBodyName, ...] = ("earth", "luna", "sun")


@dataclass(frozen=True, slots=True)
class BodyFrame:
    """Body constants for propagation, body-fixed geometry, and rendering facts."""

    name: str
    mean_radius_km: float
    equatorial_radius_km: float
    polar_radius_km: float
    rotation_rate_rad_s: float
    gravitational_parameter_km3_s2: float
    j2: float = 0.0


@dataclass(frozen=True, slots=True)
class BodyRuntimeSupport:
    """Runtime support facts for a body that are not catalog primitive fields."""

    body_id: SupportedSurfaceBody
    rotation_rate_rad_s: float
    j2: float


BODY_RUNTIME_SUPPORT: dict[SupportedSurfaceBody, BodyRuntimeSupport] = {
    "earth": BodyRuntimeSupport(
        body_id="earth",
        rotation_rate_rad_s=7.2921159e-5,
        j2=1.08262668e-3,
    ),
    "luna": BodyRuntimeSupport(
        body_id="luna",
        rotation_rate_rad_s=2.6616995e-6,
        j2=0.0,
    ),
}


def body_runtime_support_for(reference_body: SupportedSurfaceBody | str) -> BodyRuntimeSupport:
    """Return runtime support facts or fail loud for unsupported bodies."""
    try:
        return BODY_RUNTIME_SUPPORT[reference_body]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported reference_body={reference_body!r} for runtime body support. "
            f"Supported surface bodies: {sorted(BODY_RUNTIME_SUPPORT)!r}."
        ) from exc
