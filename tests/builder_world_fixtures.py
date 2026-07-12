"""Small authoritative BuilderWorld fixtures for API/service tests."""

from __future__ import annotations

from datetime import UTC, datetime

from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.events import SessionEphemeris
from nodalarc.models.segment_session import SessionMeta


def builder_world_preview(session_name: str = "preview") -> BuilderWorld:
    """Return the smallest valid backend-owned resolved preview."""

    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    return BuilderWorld(
        session=SessionMeta(name=session_name),
        epoch_unix=epoch.timestamp(),
        ephemeris=SessionEphemeris(
            epoch_id=0,
            sim_time=epoch,
            epoch_unix=epoch.timestamp(),
            nodes={},
        ),
        nodes=(),
    )
