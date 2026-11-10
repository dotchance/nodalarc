# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Typed failures of the shared physics module."""

from __future__ import annotations


class PhysicsDataError(ValueError):
    """Physical data is missing, unidentified, unconverted or out of coverage.

    The physics module raises instead of substituting a nearby value: a
    position or orientation computed from the wrong data, or extrapolated past
    the data, would look plausible and be false.
    """
