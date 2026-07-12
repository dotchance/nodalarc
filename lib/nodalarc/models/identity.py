# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Resolved-session node identity mode."""

from enum import StrEnum


class IdentityMode(StrEnum):
    """How the resolver allocates runtime node IDs for a session."""

    SEGMENT_NAMESPACED = "segment_namespaced"
