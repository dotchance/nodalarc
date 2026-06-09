# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Shared constants and enums for Nodal Arc.

Body-specific physical facts belong to body primitives and resolved session
state, not module-level constants.
"""

import math
from enum import StrEnum

# Non-body physical constants
SPEED_OF_LIGHT_KM_S: float = 299_792.458

# Derived
TWO_PI: float = 2.0 * math.pi


class LinkType(StrEnum):
    ISL = "isl"
    GROUND = "ground"


class NodeType(StrEnum):
    SATELLITE = "satellite"
    GROUND_STATION = "ground_station"


class EventType(StrEnum):
    POSITION = "PositionEvent"
    VISIBILITY = "VisibilityEvent"
    CLOCK_TICK = "ClockTick"
    LINK_UP = "LinkUp"
    LINK_DOWN = "LinkDown"
    LATENCY_UPDATE = "LatencyUpdate"
    CONVERGENCE_RESULT = "ConvergenceResult"
    PROBE_RESULT = "ProbeResult"
    ADAPTER_EVENT = "AdapterEvent"


class LinkState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class SchedulingPolicy(StrEnum):
    HIGHEST_ELEVATION = "highest-elevation"
    LOWEST_ELEVATION = "lowest-elevation"
    LONGEST_REMAINING_PASS = "longest-remaining-pass"


class AreaStrategy(StrEnum):
    STRIPE = "stripe"
    PER_PLANE = "per-plane"
    FLAT = "flat"
    EXPLICIT = "explicit"


class ConstellationPattern(StrEnum):
    WALKER_DELTA = "walker-delta"
    WALKER_STAR = "walker-star"


class TimeMode(StrEnum):
    REALTIME = "realtime"


# Logging format (Section 13.2)
LOG_FORMAT: str = "%(asctime)s %(name)s %(levelname)s %(message)s"
