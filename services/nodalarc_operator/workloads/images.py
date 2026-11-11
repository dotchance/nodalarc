# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""The workload image map: which image runs for a shipped profile's logical image.

A shipped profile names a NodalArc session image by a logical reference,
``registry.example/nodalarc/<name>@sha256:<64 zeros>``: the catalog is written
before any image is built, and the catalog is baked into four platform images,
so writing real digests into it would make every new session image a new
platform release. The chart hands the Operator a map, WORKLOAD_IMAGES, from each
logical reference to the image that runs: a release's published image, or a
developer's own build. A container whose image is a logical reference the map
does not name is refused; any other reference (an image in a user's registry)
runs as written.
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

WORKLOAD_IMAGES_ENV = "WORKLOAD_IMAGES"
LOGICAL_DIGEST = "sha256:" + "0" * 64


class WorkloadImageMapError(ValueError):
    """WORKLOAD_IMAGES is malformed. Never a fallback: the Operator stops."""


def is_logical_reference(image: str) -> bool:
    """True for a shipped profile's logical image: a digest of all zeros."""
    return image.endswith("@" + LOGICAL_DIGEST)


class WorkloadImageMap(BaseModel):
    """Logical image reference -> the image reference that runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    images: dict[str, str]

    @field_validator("images")
    @classmethod
    def _entries(cls, images: dict[str, str]) -> dict[str, str]:
        for logical, actual in images.items():
            if not is_logical_reference(logical):
                raise ValueError(
                    f"{logical!r} is not a logical image reference (@{LOGICAL_DIGEST})"
                )
            if not actual or any(character.isspace() for character in actual):
                raise ValueError(f"{logical!r} maps to an empty or malformed reference {actual!r}")
            if is_logical_reference(actual):
                raise ValueError(f"{logical!r} maps to another logical reference {actual!r}")
        return images


def load_workload_image_map() -> WorkloadImageMap:
    """The map from WORKLOAD_IMAGES. Unset or empty is an empty map, so every
    logical image is refused at preparation; malformed raises."""
    raw = os.environ.get(WORKLOAD_IMAGES_ENV, "").strip()
    if not raw:
        return WorkloadImageMap(images={})
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WorkloadImageMapError(f"{WORKLOAD_IMAGES_ENV} is not valid JSON: {error}") from error
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        raise WorkloadImageMapError(
            f"{WORKLOAD_IMAGES_ENV} must be a JSON object of image references to image references"
        )
    try:
        return WorkloadImageMap(images=data)
    except ValidationError as error:
        raise WorkloadImageMapError(f"{WORKLOAD_IMAGES_ENV}: {error}") from error
