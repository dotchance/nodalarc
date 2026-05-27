# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""NATS JetStream stream and subject definitions.

All NATS subject strings and stream names live here. No component
invents its own subjects or stream names.

Session-scoped subjects: services use the function builders (e.g.
``ome_visibility_subject(session_id)``) to publish/subscribe to
session-specific subjects. The ``SUBJECT_*`` constants use
``_DEFAULT_SESSION_ID`` ("default") for test compatibility and
migration — they are NOT for production services.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Session ID
# ---------------------------------------------------------------------------

_DEFAULT_SESSION_ID = "default"

# NATS uses dots as segment separators and ``*``/``>`` as wildcards.
# A session_id containing any of these would break subject routing.
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")


def sanitize_session_id(raw: str) -> str:
    """Sanitize a session name for use as a NATS subject segment.

    Replaces dots and wildcards with hyphens, strips leading/trailing
    whitespace. Raises ValueError if the result is empty or still invalid.
    """
    cleaned = raw.strip().replace(".", "-").replace("*", "-").replace(">", "-")
    if not cleaned:
        raise ValueError(f"session_id is empty after sanitization (raw={raw!r})")
    if not _SESSION_ID_RE.match(cleaned):
        raise ValueError(f"session_id {cleaned!r} invalid — must match {_SESSION_ID_RE.pattern}")
    return cleaned


# ---------------------------------------------------------------------------
# Stream names
# ---------------------------------------------------------------------------

STREAM_OME_EVENTS = "NODALARC_OME"
STREAM_LINK_EVENTS = "NODALARC_LINKS"
STREAM_MI_EVENTS = "NODALARC_MI"
STREAM_SESSION_EVENTS = "NODALARC_SESSION"
STREAM_OPS_EVENTS = "NODALARC_OPS"
STREAM_DEBUG_EVENTS = "NODALARC_DEBUG"

DEBUG_CTRL_SUBJECT_PREFIX = "nodalarc.logging.debug_ctrl"

# ---------------------------------------------------------------------------
# Session-scoped subject builders — primary API for services
# ---------------------------------------------------------------------------


def ome_visibility_subject(session_id: str) -> str:
    """OME visibility event subject for a specific session."""
    return f"nodalarc.ome.{session_id}.visibility"


def ome_snapshot_subject(session_id: str) -> str:
    """DEPRECATED — OME snapshot subject for a specific session."""
    return f"nodalarc.ome.{session_id}.snapshot"


def ome_clock_subject(session_id: str) -> str:
    """OME clock tick subject for a specific session."""
    return f"nodalarc.ome.{session_id}.clock"


def ome_heartbeat_subject(session_id: str) -> str:
    """OME heartbeat subject for a specific session."""
    return f"nodalarc.ome.{session_id}.heartbeat"


def ome_all_subject(session_id: str | None = None) -> str:
    """OME wildcard subject — all events for one session, or all sessions.

    With session_id: ``nodalarc.ome.{session_id}.>`` (one session)
    Without: ``nodalarc.ome.>`` (all sessions — for cross-session consumers)
    """
    if session_id:
        return f"nodalarc.ome.{session_id}.>"
    return "nodalarc.ome.>"


def link_state_snapshot_subject(session_id: str) -> str:
    """Link state snapshot subject for a specific session."""
    return f"nodalarc.links.{session_id}.state"


def ground_link_decision_snapshot_subject(session_id: str) -> str:
    """OME GROUND-link decision snapshot subject for a specific session.

    Ground-scoped — covers only GS↔satellite pair decisions. The OME
    publishes ground decisions here today; ISL decisions are not yet
    snapshotted and a separate ISL subject will be introduced when
    they are.

    The decision snapshot is the diagnostic companion to
    ``link_state_snapshot_subject``: same ``snapshot_seq`` and
    ``sim_time``, separate payload. ``LinkStateSnapshot`` describes the
    actuated forwarding-plane state (carrier UP/DOWN, applied
    range/latency); ``GroundLinkDecisionSnapshot`` describes the OME's
    visibility and scheduling decisions for every GROUND pair the OME
    considered — including visible-but-unscheduled ground pairs and
    the reasons for non-allocation.

    The subject lives on the ``NODALARC_LINKS`` stream which already
    enforces ``MaxMsgsPerSubject=1``. Replace-not-merge: only the
    latest decision snapshot is retained per subject. A late-joining
    Scheduler receives the current decisions without history replay.

    Same-stream colocation does NOT pair the two snapshots — the state
    and decision snapshots are independent NATS messages with separate
    ``MaxMsgsPerSubject=1`` retention. Consumers pair them by
    ``(epoch_id, snapshot_seq, sim_time)``; see
    ``scheduler.dispatcher.paired_decision_snapshot()``. Treating the
    shared stream as pairing is wrong and will deliver mismatched
    state/decision pairs on restart or restream.
    """
    return f"nodalarc.links.{session_id}.ground_decisions"


def link_up_subject(session_id: str) -> str:
    """Link up event subject for a specific session."""
    return f"nodalarc.links.{session_id}.up"


def link_down_subject(session_id: str) -> str:
    """Link down event subject for a specific session."""
    return f"nodalarc.links.{session_id}.down"


def latency_update_subject(session_id: str) -> str:
    """Latency update subject for a specific session."""
    return f"nodalarc.links.{session_id}.latency"


def session_ephemeris_subject(session_id: str) -> str:
    """Session ephemeris subject for a specific session."""
    return f"nodalarc.session.{session_id}.ephemeris"


def playback_state_subject(session_id: str) -> str:
    """Playback state subject for a specific session."""
    return f"nodalarc.session.{session_id}.playback_state"


def scheduling_checkpoint_subject(session_id: str) -> str:
    """Scheduling checkpoint subject for a specific session."""
    return f"nodalarc.session.{session_id}.scheduling_checkpoint"


def scenario_inject_subject(session_id: str) -> str:
    """Scenario injection subject for a specific session (core NATS request/reply)."""
    return f"nodalarc.scheduler.{session_id}.scenario"


def convergence_result_subject(session_id: str) -> str:
    """MI convergence result subject for a specific session."""
    return f"nodalarc.mi.{session_id}.convergence"


def probe_result_subject(session_id: str) -> str:
    """MI probe result subject for a specific session."""
    return f"nodalarc.mi.{session_id}.probe"


def adapter_event_subject(session_id: str) -> str:
    """MI adapter event subject for a specific session."""
    return f"nodalarc.mi.{session_id}.adapter"


def almanac_event_subject(session_id: str) -> str:
    """NodalPath almanac event subject for a specific session."""
    return f"nodalarc.nodalpath.{session_id}.almanac"


# ---------------------------------------------------------------------------
# Legacy SUBJECT_* constants — use _DEFAULT_SESSION_ID for backward compat
#
# These exist for test compatibility and code that doesn't yet have access
# to the session_id. Services MUST use the function builders above.
# ---------------------------------------------------------------------------

# OME publications (JetStream — retained)
SUBJECT_OME_ALL = "nodalarc.ome.>"
SUBJECT_VISIBILITY_EVENT = ome_visibility_subject(_DEFAULT_SESSION_ID)
# DEPRECATED (PRD v0.71): No component publishes or subscribes to Snapshot.
# Position data distributed via SessionEphemeris on NODALARC_SESSION stream.
SUBJECT_SNAPSHOT = ome_snapshot_subject(_DEFAULT_SESSION_ID)
SUBJECT_CLOCK_TICK = ome_clock_subject(_DEFAULT_SESSION_ID)
SUBJECT_HEARTBEAT = ome_heartbeat_subject(_DEFAULT_SESSION_ID)

# Link state (JetStream — retained, replace-not-merge)
SUBJECT_LINK_STATE_SNAPSHOT = link_state_snapshot_subject(_DEFAULT_SESSION_ID)
SUBJECT_GROUND_LINK_DECISION_SNAPSHOT = ground_link_decision_snapshot_subject(_DEFAULT_SESSION_ID)
SUBJECT_LINK_UP = link_up_subject(_DEFAULT_SESSION_ID)
SUBJECT_LINK_DOWN = link_down_subject(_DEFAULT_SESSION_ID)
SUBJECT_LATENCY_UPDATE = latency_update_subject(_DEFAULT_SESSION_ID)

# Session-level state (JetStream — MaxMsgsPerSubject=1 on NODALARC_SESSION)
SUBJECT_SESSION_EPHEMERIS = session_ephemeris_subject(_DEFAULT_SESSION_ID)
SUBJECT_PLAYBACK_STATE = playback_state_subject(_DEFAULT_SESSION_ID)
SUBJECT_SCHEDULING_CHECKPOINT = scheduling_checkpoint_subject(_DEFAULT_SESSION_ID)

# MI publications (JetStream — retained)
SUBJECT_CONVERGENCE_RESULT = convergence_result_subject(_DEFAULT_SESSION_ID)
SUBJECT_PROBE_RESULT = probe_result_subject(_DEFAULT_SESSION_ID)
SUBJECT_ADAPTER_EVENT = adapter_event_subject(_DEFAULT_SESSION_ID)

# NodalPath publications (JetStream — retained)
SUBJECT_ALMANAC_EVENT = almanac_event_subject(_DEFAULT_SESSION_ID)

# Ops events (JetStream — memory storage, 4h retention)
# Ops events — session-scoped to prevent cross-session telemetry leaks
SUBJECT_OPS_EVENT = f"nodalarc.ops.{_DEFAULT_SESSION_ID}.>"


def ops_event_subject(session_id: str, source: str, code: str = "", *, tenant_id: str = "") -> str:
    """Build a scoped ops event subject.

    Subject hierarchy:
      - Infrastructure (no tenant, no session): nodalarc.ops._infra.{source}[.{code}]
      - Tenant (tenant, no session): nodalarc.ops.{tenant}._tenant.{source}[.{code}]
      - Session (no tenant): nodalarc.ops.{session}.{source}[.{code}]
      - Session (with tenant): nodalarc.ops.{tenant}.{session}.{source}[.{code}]
    """
    code_lower = code.lower() if code else ""
    if not tenant_id and not session_id:
        base = f"nodalarc.ops._infra.{source}"
    elif not tenant_id:
        base = f"nodalarc.ops.{sanitize_session_id(session_id)}.{source}"
    elif not session_id:
        base = f"nodalarc.ops.{tenant_id}._tenant.{source}"
    else:
        base = f"nodalarc.ops.{tenant_id}.{sanitize_session_id(session_id)}.{source}"

    if code_lower:
        return f"{base}.{code_lower}"
    return base


def ops_subscribe_subject(session_id: str, *, tenant_id: str = "") -> str:
    """Wildcard subject for subscribing to all ops events for a session."""
    if not tenant_id and not session_id:
        return "nodalarc.ops._infra.>"
    if not tenant_id:
        return f"nodalarc.ops.{sanitize_session_id(session_id)}.>"
    if not session_id:
        return f"nodalarc.ops.{tenant_id}._tenant.>"
    return f"nodalarc.ops.{tenant_id}.{sanitize_session_id(session_id)}.>"


# Request/reply subjects (NATS core, not JetStream)
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

    ``NODALARC_NATS_URL`` may be set by deployment templates to provide
    service-specific NATS credentials for subject authorization.

    Falls back to localhost if platform config is not initialized
    (test environment, development).
    """
    import os

    env_url = os.environ.get("NODALARC_NATS_URL", "").strip()
    if env_url:
        return env_url
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


def ops_stream_config() -> dict:
    """StreamConfig for NODALARC_OPS stream.

    Memory storage, 4-hour retention. Operational events are transient
    telemetry — not replayed after restart. 128MB cap prevents unbounded
    growth during long sessions with high event rates.
    """
    return {
        "name": STREAM_OPS_EVENTS,
        "subjects": ["nodalarc.ops.>"],
        "retention": "limits",
        "storage": "memory",
        "max_msgs_per_subject": -1,
        "max_age": 14400,  # 4 hours
        "max_bytes": 128 * 1024 * 1024,
    }


def debug_stream_config() -> dict:
    """StreamConfig for NODALARC_DEBUG stream.

    On-demand debug events published when an operator enables debug
    for a service via the log panel. Short retention — debug data is
    ephemeral investigation output, not operational history.

    Services publish to this stream only when their NatsHandler level
    is lowered to DEBUG via the debug_ctrl request/reply channel.
    When no operator has debug enabled, zero messages flow.
    """
    return {
        "name": STREAM_DEBUG_EVENTS,
        "subjects": ["nodalarc.debug.>"],
        "retention": "limits",
        "storage": "memory",
        "max_age": 300,  # 5 minutes
        "max_bytes": 64 * 1024 * 1024,
        "max_msgs_per_subject": 500,
    }


def debug_ctrl_subject(source: str) -> str:
    """Build the NATS request/reply subject for debug level control.

    The VS-API sends enable/disable requests to this subject. The
    logging library in the target service subscribes and responds.
    Core NATS (not JetStream) — no retention, no stream.
    """
    return f"{DEBUG_CTRL_SUBJECT_PREFIX}.{source}"
