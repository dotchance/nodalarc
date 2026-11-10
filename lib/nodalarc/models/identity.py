# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Resolved-session node identity mode."""

from enum import StrEnum


class IdentityMode(StrEnum):
    """How the resolver allocates runtime node IDs for a session."""

    SEGMENT_NAMESPACED = "segment_namespaced"
