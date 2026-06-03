# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Resolver identity mode for the segment session grammar.

Runtime identity is explicit, deterministic, and segment-namespaced. Old
``constellation``/``ground_stations`` session YAML is not a product compatibility
target, so there are no legacy-preserving identity modes.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


class IdentityMode(StrEnum):
    """How the resolver allocates runtime node IDs for a session."""

    SEGMENT_NAMESPACED = "segment_namespaced"


class IdentityConfig(BaseModel):
    """Explicit ``identity`` block on a segment-form session.

    The field is optional at the YAML level because ``segment_namespaced`` is the
    only supported mode. If supplied, any other value is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    mode: IdentityMode = IdentityMode.SEGMENT_NAMESPACED

    @field_validator("mode")
    @classmethod
    def _only_segment_namespaced(cls, value: IdentityMode) -> IdentityMode:
        if value is not IdentityMode.SEGMENT_NAMESPACED:
            raise ValueError("identity.mode must be 'segment_namespaced'")
        return value
