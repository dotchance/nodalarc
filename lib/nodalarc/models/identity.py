# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Resolved-session node identity mode."""

from enum import StrEnum


class IdentityMode(StrEnum):
    """How the resolver allocates runtime node IDs for a session."""

    SEGMENT_NAMESPACED = "segment_namespaced"
