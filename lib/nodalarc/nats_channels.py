"""NATS JetStream stream and subject definitions.

All NATS subject strings and stream names live here. No component
invents its own subjects or stream names. Same discipline as zmq_channels.py.

M9: replaces zmq_channels.py entirely. After Phase 7, zmq_channels.py is deleted.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stream names
# ---------------------------------------------------------------------------

STREAM_OME_EVENTS = "NODALARC_OME"
STREAM_LINK_EVENTS = "NODALARC_LINKS"
STREAM_MI_EVENTS = "NODALARC_MI"

# ---------------------------------------------------------------------------
# Subject definitions — hierarchical, dot-separated
# ---------------------------------------------------------------------------

# OME publications (JetStream — retained)
SUBJECT_VISIBILITY_EVENT = "nodalarc.ome.visibility"
SUBJECT_SNAPSHOT = "nodalarc.ome.snapshot"
SUBJECT_CLOCK_TICK = "nodalarc.ome.clock"
SUBJECT_HEARTBEAT = "nodalarc.ome.heartbeat"

# Link state (JetStream — retained, replace-not-merge)
SUBJECT_LINK_STATE_SNAPSHOT = "nodalarc.links.state"
SUBJECT_LINK_UP = "nodalarc.links.up"
SUBJECT_LINK_DOWN = "nodalarc.links.down"
SUBJECT_LATENCY_UPDATE = "nodalarc.links.latency"

# MI publications (JetStream — retained)
SUBJECT_CONVERGENCE_RESULT = "nodalarc.mi.convergence"
SUBJECT_PROBE_RESULT = "nodalarc.mi.probe"
SUBJECT_ADAPTER_EVENT = "nodalarc.mi.adapter"

# NodalPath publications (JetStream — retained)
SUBJECT_ALMANAC_EVENT = "nodalarc.nodalpath.almanac"

# Request/reply subjects (NATS core, not JetStream)
SUBJECT_SCENARIO_INJECT = "nodalarc.scheduler.scenario"
SUBJECT_PLAYBACK_CONTROL = "nodalarc.scheduler.playback"
SUBJECT_MI_TRACE = "nodalarc.mi.trace"
SUBJECT_MI_CONVERGENCE_GATE = "nodalarc.mi.convergence_gate"
SUBJECT_NODE_AGENT = "nodalarc.agent.{node_id}"

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


def nats_url() -> str:
    """Get NATS server URL from platform config."""
    from nodalarc.platform import get_platform_config

    return get_platform_config().nats_url


def node_agent_subject(node_id: str) -> str:
    """Build per-node subject for Node Agent request/reply."""
    return SUBJECT_NODE_AGENT.format(node_id=node_id)


# ---------------------------------------------------------------------------
# JetStream stream configurations — used during stream creation
# ---------------------------------------------------------------------------

# Orbital period for a 550km LEO orbit in nanoseconds (2 periods for retention)
_ORBITAL_PERIOD_NS = 5730_000_000_000
_TWO_PERIODS_NS = 2 * _ORBITAL_PERIOD_NS


def ome_stream_config() -> dict:
    """StreamConfig for NODALARC_OME stream."""
    return {
        "name": STREAM_OME_EVENTS,
        "subjects": ["nodalarc.ome.>"],
        "retention": "limits",
        "storage": "memory",
        "max_msgs_per_subject": -1,
        "max_age": _TWO_PERIODS_NS,
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
        "max_age": _TWO_PERIODS_NS,
        "max_bytes": 64 * 1024 * 1024,
    }


def mi_stream_config() -> dict:
    """StreamConfig for NODALARC_MI stream."""
    return {
        "name": STREAM_MI_EVENTS,
        "subjects": ["nodalarc.mi.>"],
        "retention": "limits",
        "storage": "memory",
        "max_msgs_per_subject": -1,
        "max_age": _TWO_PERIODS_NS,
        "max_bytes": 64 * 1024 * 1024,
    }
