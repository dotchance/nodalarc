# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The physics context of a resolved session, bound to the files it recorded.

The resolved session names the ephemeris files its results come from. Every
service that computes positions for a session builds its context here, so a
service whose image carries different data refuses the session instead of
publishing positions from other data under the same record. A shared
propagation function does not by itself guarantee identical inputs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodalarc.physics.context import PhysicsContext
from nodalarc.physics.errors import PhysicsDataError

if TYPE_CHECKING:
    from nodalarc.models.resolved_session import ResolvedSession


def session_active_bodies(resolved: ResolvedSession) -> frozenset[str]:
    """Every body a node of the session orbits or stands on."""
    active = frozenset(
        body
        for node in resolved.nodes
        for body in (node.central_body, node.reference_body)
        if body is not None
    )
    if not active:
        raise ValueError("resolved session contains no active body references")
    return active


def session_physics(resolved: ResolvedSession) -> PhysicsContext:
    """The session's physics context, refused unless it loads the recorded files."""
    physics = PhysicsContext(session_active_bodies(resolved))
    loaded = tuple((f.file_id, f.sha256) for f in physics.selected_files)
    recorded = tuple((f.file_id, f.sha256) for f in resolved.ephemeris_files)
    if loaded != recorded:
        raise PhysicsDataError(
            f"this service's ephemeris data {loaded} differs from the files the resolved "
            f"session recorded {recorded}"
        )
    return physics
