# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Resolver identity modes for the segment session grammar.

Runtime identity is an explicit resolver mode, never inferred from node-ID
strings and never a silent fallback. Every resolved session records exactly one
mode. ``legacy_identity`` and ``legacy_compatible`` preserve today's runtime node
IDs exactly (``sat-P00S00``, ``gs-denver``) and the legacy session-global SID
scheme; ``segment_namespaced`` uses ``{namespace}-{local}`` normalized IDs and
per-segment SID blocks. Migrating legacy runtime identity to the namespaced
scheme is intentional M2 work, not part of the resolver-boundary migration.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


class IdentityMode(StrEnum):
    """How the resolver allocates runtime node IDs for a session."""

    # Legacy ``constellation``/``ground_stations`` shape. Derived from the session
    # shape, never declared. Preserves legacy IDs + legacy session-global SID.
    LEGACY_IDENTITY = "legacy_identity"
    # New ``segments`` shape that represents the legacy-equivalent session and
    # opts in to preserving legacy IDs (the wizard's M1 single-segment output).
    LEGACY_COMPATIBLE = "legacy_compatible"
    # New multi-segment sessions: ``{namespace}-{local}`` normalized IDs.
    SEGMENT_NAMESPACED = "segment_namespaced"


# Modes a session author may declare in ``identity.mode``. ``legacy_identity`` is
# derived from the legacy session shape and is not declarable.
DECLARABLE_IDENTITY_MODES: frozenset[IdentityMode] = frozenset(
    {IdentityMode.LEGACY_COMPATIBLE, IdentityMode.SEGMENT_NAMESPACED}
)

# Modes whose runtime node IDs are the preserved legacy IDs.
LEGACY_PRESERVING_MODES: frozenset[IdentityMode] = frozenset(
    {IdentityMode.LEGACY_IDENTITY, IdentityMode.LEGACY_COMPATIBLE}
)


class IdentityConfig(BaseModel):
    """Explicit ``identity`` block on a ``segments``-form session.

    Defaults to ``segment_namespaced``. A wizard-emitted M1 single-segment
    session that represents the legacy-equivalent session declares
    ``legacy_compatible`` to preserve today's runtime node IDs.
    """

    model_config = ConfigDict(extra="forbid")

    mode: IdentityMode = IdentityMode.SEGMENT_NAMESPACED

    @field_validator("mode")
    @classmethod
    def _declarable(cls, value: IdentityMode) -> IdentityMode:
        if value not in DECLARABLE_IDENTITY_MODES:
            raise ValueError(
                "identity.mode 'legacy_identity' is derived from the legacy "
                "constellation/ground_stations session shape and cannot be "
                "declared explicitly; use 'legacy_compatible' or "
                "'segment_namespaced'"
            )
        return value
