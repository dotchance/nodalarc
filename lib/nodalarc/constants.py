# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Physical constants, WGS84 parameters, and shared enums for Nodal Arc."""

import math
from enum import StrEnum

# Physical constants
SPEED_OF_LIGHT_KM_S: float = 299_792.458
EARTH_RADIUS_KM: float = 6_371.0
EARTH_MU: float = 398_600.4418  # km^3/s^2, gravitational parameter

# WGS84 ellipsoid parameters
WGS84_A: float = 6_378.137  # Semi-major axis, km
WGS84_B: float = 6_356.752314245  # Semi-minor axis, km
WGS84_F: float = 1.0 / 298.257223563  # Flattening
WGS84_E2: float = 2 * WGS84_F - WGS84_F**2  # First eccentricity squared

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
    LONGEST_PASS = "longest-pass"


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
