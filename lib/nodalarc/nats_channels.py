# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""NATS JetStream stream and subject definitions.

All NATS subject strings and stream names live here. No component
invents its own subjects or stream names.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stream names
# ---------------------------------------------------------------------------

STREAM_OME_EVENTS = "NODALARC_OME"
STREAM_LINK_EVENTS = "NODALARC_LINKS"
STREAM_MI_EVENTS = "NODALARC_MI"
STREAM_SESSION_EVENTS = "NODALARC_SESSION"

# ---------------------------------------------------------------------------
# Subject definitions — hierarchical, dot-separated
# ---------------------------------------------------------------------------

# OME publications (JetStream — retained)
SUBJECT_OME_ALL = "nodalarc.ome.>"
SUBJECT_VISIBILITY_EVENT = "nodalarc.ome.visibility"
# DEPRECATED (PRD v0.71): No component publishes or subscribes to Snapshot.
# Position data distributed via SessionEphemeris on NODALARC_SESSION stream.
SUBJECT_SNAPSHOT = "nodalarc.ome.snapshot"
SUBJECT_CLOCK_TICK = "nodalarc.ome.clock"
SUBJECT_HEARTBEAT = "nodalarc.ome.heartbeat"

# Link state (JetStream — retained, replace-not-merge)
SUBJECT_LINK_STATE_SNAPSHOT = "nodalarc.links.state"
SUBJECT_LINK_UP = "nodalarc.links.up"
SUBJECT_LINK_DOWN = "nodalarc.links.down"
SUBJECT_LATENCY_UPDATE = "nodalarc.links.latency"
SUBJECT_SUBSTRATE_LATENCY = "nodalarc.links.substrate"

# Session-level state (JetStream — MaxMsgsPerSubject=1 on NODALARC_SESSION)
SUBJECT_SESSION_EPHEMERIS = "nodalarc.session.ephemeris"
SUBJECT_PLAYBACK_STATE = "nodalarc.session.playback_state"

# MI publications (JetStream — retained)
SUBJECT_CONVERGENCE_RESULT = "nodalarc.mi.convergence"
SUBJECT_PROBE_RESULT = "nodalarc.mi.probe"
SUBJECT_ADAPTER_EVENT = "nodalarc.mi.adapter"

# NodalPath publications (JetStream — retained)
SUBJECT_ALMANAC_EVENT = "nodalarc.nodalpath.almanac"

# Request/reply subjects (NATS core, not JetStream)
SUBJECT_SCENARIO_INJECT = "nodalarc.scheduler.scenario"
# Playback control (pause / resume / set_speed) owned by OME Pacemaker
# (R-OME-008B). Subject is in ome_control namespace — deliberately outside
# the "nodalarc.ome.>" JetStream-captured wildcard so request/reply
# messages are not stream-retained.
SUBJECT_PLAYBACK_CONTROL = "nodalarc.ome_control.playback"
SUBJECT_MI_TRACE = "nodalarc.mi.trace"
SUBJECT_MI_CONVERGENCE_GATE = "nodalarc.mi.convergence_gate"
SUBJECT_NODE_AGENT = "nodalarc.agent.{node_id}"

# Wiring progress — transient core NATS (not JetStream, no retention).
# Hierarchical per-node subject: VS-API subscribes to wildcard nodalarc.agent.progress.*
SUBJECT_WIRING_PROGRESS = "nodalarc.agent.progress.{node_id}"

# Playback speed bounds — safety clamp on the OME Pacemaker's time_accel.
# Below MIN, callers should use pause() rather than extreme slow-motion;
# above MAX, the pacing thread cannot reliably keep up with NATS publish
# throughput on typical hardware.
MIN_TIME_ACCEL = 0.1
MAX_TIME_ACCEL = 1000.0

# ---------------------------------------------------------------------------
# Standard connection options — every component must use these
# ---------------------------------------------------------------------------

NATS_CONNECT_OPTIONS: dict = {
    "connect_timeout": 5,
    "max_reconnect_attempts": -1,  # unlimited
    "reconnect_time_wait": 1,
    "ping_interval": 10,
    "max_outstanding_pings": 3,
}


def probe_daemon_port() -> int:
    """HTTP port for the per-pod probe daemon sidecar."""
    from nodalarc.platform_config import get_platform_config

    return get_platform_config().probe_daemon_http_api_port


def nodalpath_console_port() -> int:
    """HTTP port for the NodalPath console server."""
    from nodalarc.platform_config import get_platform_config

    return get_platform_config().nodalpath_console_http_port


def nats_url() -> str:
    """Get NATS server URL from platform config.

    Falls back to localhost if platform config is not initialized
    (test environment, development).
    """
    try:
        from nodalarc.platform_config import get_platform_config

        return get_platform_config().nats_url
    except RuntimeError:
        return "nats://localhost:4222"


def node_agent_subject(node_id: str) -> str:
    """Build per-node subject for Node Agent request/reply."""
    return SUBJECT_NODE_AGENT.format(node_id=node_id)


def wiring_progress_subject(node_id: str) -> str:
    """Build per-node subject for wiring progress updates."""
    return SUBJECT_WIRING_PROGRESS.format(node_id=node_id)


# ---------------------------------------------------------------------------
# JetStream stream configurations — used during stream creation
# ---------------------------------------------------------------------------

# Orbital period for a 550km LEO orbit — retention = 2 periods in seconds
_TWO_PERIODS_S = 2 * 5730  # ~3.18 hours


def ome_stream_config() -> dict:
    """StreamConfig for NODALARC_OME stream."""
    return {
        "name": STREAM_OME_EVENTS,
        "subjects": ["nodalarc.ome.>"],
        "retention": "limits",
        "storage": "memory",
        "max_msgs_per_subject": -1,
        "max_age": _TWO_PERIODS_S,
        "max_bytes": 128 * 1024 * 1024,
    }


def link_stream_config() -> dict:
    """StreamConfig for NODALARC_LINKS stream.

    MaxMsgsPerSubject=1 on nodalarc.links.state ensures only the latest
    LinkStateSnapshot is retained. Any subscriber reading from this stream
    gets the most recent snapshot without replaying history.
    """
    return {
        "name": STREAM_LINK_EVENTS,
        "subjects": ["nodalarc.links.>"],
        "retention": "limits",
        "storage": "memory",
        "max_msgs_per_subject": 1,
        "max_age": _TWO_PERIODS_S,
        "max_bytes": 64 * 1024 * 1024,
    }


def session_stream_config() -> dict:
    """StreamConfig for NODALARC_SESSION stream.

    MaxMsgsPerSubject=1 ensures late-joining subscribers always receive
    exactly the current SessionEphemeris and PlaybackState — no history
    replay, no application-level filtering. The infrastructure enforces
    single-message-per-subject state.

    Separate from NODALARC_OME because OME needs unlimited per-subject
    retention for VisibilityEvent history. NATS JetStream does not support
    per-subject MaxMsgsPerSubject within a single stream.
    """
    return {
        "name": STREAM_SESSION_EVENTS,
        "subjects": ["nodalarc.session.>"],
        "retention": "limits",
        "storage": "memory",
        "max_msgs_per_subject": 1,
        "max_age": _TWO_PERIODS_S,
        "max_bytes": 1 * 1024 * 1024,
    }


def mi_stream_config() -> dict:
    """StreamConfig for NODALARC_MI stream."""
    return {
        "name": STREAM_MI_EVENTS,
        "subjects": ["nodalarc.mi.>"],
        "retention": "limits",
        "storage": "memory",
        "max_msgs_per_subject": -1,
        "max_age": _TWO_PERIODS_S,
        "max_bytes": 64 * 1024 * 1024,
    }
