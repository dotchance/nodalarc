# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Runtime session identity helpers.

``session.name`` is a human experiment label. It can repeat.
``session.run_id`` is the runtime lineage. It must not repeat.
"""

from __future__ import annotations

import hashlib
from typing import Any

from nodalarc.nats_channels import sanitize_session_id


def derive_session_run_id(*, session_name: str, owner_uid: str, generation: int) -> str:
    """Derive a stable runtime identity for one CR generation."""
    if not session_name:
        raise ValueError("session_name is required to derive session_run_id")
    if not owner_uid:
        raise ValueError("owner_uid is required to derive session_run_id")
    if generation <= 0:
        raise ValueError("generation must be positive to derive session_run_id")

    digest = hashlib.sha256(f"{owner_uid}:{generation}:{session_name}".encode()).hexdigest()
    return sanitize_session_id(f"run-{digest[:20]}")


def require_session_run_id(session: Any) -> str:
    """Return the deployed runtime identity from a SessionConfig-like object."""
    session_meta = getattr(session, "session", None)
    run_id = getattr(session_meta, "run_id", None)
    if not run_id:
        name = getattr(session_meta, "name", "")
        raise ValueError(
            f"session.run_id is required in deployed runtime session config (session.name={name!r})"
        )
    return sanitize_session_id(str(run_id))
