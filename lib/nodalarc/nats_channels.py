# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""NATS JetStream stream and subject definitions.

All NATS subject strings and stream names live here. No component
invents its own subjects or stream names.

Every subject starts with one of the declared roots, and every builder
computes from its root at call time. Session-scoped subjects come from the
function builders (e.g. ``ome_visibility_subject(session_id)``); the few
session-independent request/reply subjects are constants. The stream table
and the user table are the deployed-stream and authorization inventories
the chart renders from.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Session ID
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Subject roots. Every subject NodalArc publishes or subscribes starts with one
# of these; the stream table and the chart's inventory are built from them.
# ---------------------------------------------------------------------------

ROOT_OME = "nodalarc.ome"
ROOT_LINKS = "nodalarc.links"
ROOT_SESSION = "nodalarc.session"
ROOT_SCHEDULER = "nodalarc.scheduler"
ROOT_OPS = "nodalarc.ops"
ROOT_DEBUG = "nodalarc.debug"
ROOT_MI = "nodalarc.mi"
ROOT_NODALPATH = "nodalarc.nodalpath"
ROOT_AGENT = "nodalarc.agent"
ROOT_OME_CONTROL = "nodalarc.ome_control"
ROOT_LOGGING = "nodalarc.logging"

STREAM_OME_EVENTS = "NODALARC_OME"
STREAM_LINK_EVENTS = "NODALARC_LINKS"
STREAM_SESSION_EVENTS = "NODALARC_SESSION"
STREAM_OPS_EVENTS = "NODALARC_OPS"
STREAM_DEBUG_EVENTS = "NODALARC_DEBUG"
# Declared for the measurement and NodalPath integrations; no chart creates
# it today (the deployment gap is OS-13; the almanac subscription that names
# it is OS-02). It is not part of the deployed-stream table below.
STREAM_MI_EVENTS = "NODALARC_MI"


class StreamSpec(NamedTuple):
    """One deployed JetStream stream: its name and the subject pattern it captures."""

    name: str
    subjects: str


# The streams the platform deploys, with the subject pattern each captures.
# Retention is deployment configuration and lives in the chart values.
STREAMS: tuple[StreamSpec, ...] = (
    StreamSpec(STREAM_OME_EVENTS, f"{ROOT_OME}.>"),
    StreamSpec(STREAM_LINK_EVENTS, f"{ROOT_LINKS}.>"),
    StreamSpec(STREAM_SESSION_EVENTS, f"{ROOT_SESSION}.>"),
    StreamSpec(STREAM_OPS_EVENTS, f"{ROOT_OPS}.>"),
    StreamSpec(STREAM_DEBUG_EVENTS, f"{ROOT_DEBUG}.>"),
)


class NatsUser(NamedTuple):
    """One NATS account the chart provisions: its values key and the patterns it may use."""

    key: str
    publish: tuple[str, ...]
    subscribe: tuple[str, ...]


_JETSTREAM_API = "$JS.API.>"
_INBOX = "_INBOX.>"
_EVERYTHING = "nodalarc.>"

# The authorization policy the chart renders when NATS auth is enabled,
# reproduced as it stands today, gaps included: the node-agent user is not
# granted the debug root it publishes to nor the debug-control subject it
# subscribes to (an auth finding, deferred; nats.auth is a support decision).
NATS_USERS: tuple[NatsUser, ...] = (
    NatsUser("admin", (_JETSTREAM_API, _INBOX, _EVERYTHING), (_JETSTREAM_API, _INBOX, _EVERYTHING)),
    NatsUser(
        "scheduler",
        (_JETSTREAM_API, _INBOX, f"{ROOT_AGENT}.*", f"{ROOT_LINKS}.>", f"{ROOT_OPS}.>"),
        (
            _JETSTREAM_API,
            _INBOX,
            f"{ROOT_OME}.>",
            f"{ROOT_SESSION}.>",
            f"{ROOT_LINKS}.>",
            f"{ROOT_SCHEDULER}.>",
        ),
    ),
    NatsUser(
        "nodeAgent",
        (_INBOX, f"{ROOT_AGENT}.progress.*", f"{ROOT_LINKS}.*.substrate", f"{ROOT_OPS}.>"),
        (f"{ROOT_AGENT}.*",),
    ),
    NatsUser(
        "service",
        (
            _JETSTREAM_API,
            _INBOX,
            f"{ROOT_OME}.>",
            f"{ROOT_OME_CONTROL}.>",
            f"{ROOT_SCHEDULER}.>",
            f"{ROOT_SESSION}.>",
            f"{ROOT_MI}.>",
            f"{ROOT_NODALPATH}.>",
            f"{ROOT_DEBUG}.>",
            f"{ROOT_OPS}.>",
        ),
        (
            _JETSTREAM_API,
            _INBOX,
            f"{ROOT_OME}.>",
            f"{ROOT_SESSION}.>",
            f"{ROOT_LINKS}.>",
            f"{ROOT_MI}.>",
            f"{ROOT_NODALPATH}.>",
            f"{ROOT_DEBUG}.>",
            f"{ROOT_OPS}.>",
            f"{ROOT_AGENT}.progress.*",
            f"{ROOT_OME_CONTROL}.>",
        ),
    ),
)


class TenantScopeUnsupported(ValueError):
    """A tenant-scoped subject operation was requested; subjects are session-scoped today."""


def session_purge_filters(session_id: str, *, tenant_id: str = "") -> tuple[tuple[str, str], ...]:
    """The (stream, subject filter) pairs that purge one session from every deployed stream.

    Subjects are session-scoped today: the session segment follows the root
    directly. Tenant support must add the tenant segment in this one place
    before tenants share NATS; until then a tenant scope is refused, and
    callers never purge a stream without a session filter.
    """
    if tenant_id:
        raise TenantScopeUnsupported(
            f"tenant-scoped purge is not implemented (tenant_id={tenant_id!r}); "
            "the tenant segment must be added to session_purge_filters first"
        )
    sid = sanitize_session_id(session_id)
    return tuple((stream.name, f"{stream.subjects[:-1]}{sid}.>") for stream in STREAMS)


# ---------------------------------------------------------------------------
# Session-scoped subject builders — primary API for services
# ---------------------------------------------------------------------------


def ome_visibility_subject(session_id: str) -> str:
    """OME visibility event subject for a specific session."""
    return f"{ROOT_OME}.{session_id}.visibility"


def ome_clock_subject(session_id: str) -> str:
    """OME clock tick subject for a specific session."""
    return f"{ROOT_OME}.{session_id}.clock"


def ome_heartbeat_subject(session_id: str) -> str:
    """OME heartbeat subject for a specific session."""
    return f"{ROOT_OME}.{session_id}.heartbeat"


def ome_all_subject(session_id: str | None = None) -> str:
    """OME wildcard subject — all events for one session, or all sessions.

    With session_id: ``nodalarc.ome.{session_id}.>`` (one session)
    Without: ``nodalarc.ome.>`` (all sessions — for cross-session consumers)
    """
    if session_id:
        return f"{ROOT_OME}.{session_id}.>"
    return f"{ROOT_OME}.>"


def link_state_snapshot_subject(session_id: str) -> str:
    """Link state snapshot subject for a specific session."""
    return f"{ROOT_LINKS}.{session_id}.state"


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
    return f"{ROOT_LINKS}.{session_id}.ground_decisions"


def link_up_subject(session_id: str) -> str:
    """Link up event subject for a specific session."""
    return f"{ROOT_LINKS}.{session_id}.up"


def link_down_subject(session_id: str) -> str:
    """Link down event subject for a specific session."""
    return f"{ROOT_LINKS}.{session_id}.down"


def latency_update_subject(session_id: str) -> str:
    """Latency update subject for a specific session."""
    return f"{ROOT_LINKS}.{session_id}.latency"


def actuation_state_subject(session_id: str, gs_id: str) -> str:
    """Per-GS retained actuation state (latest only).

    Lives under ``nodalarc.links.>`` so it rides the NODALARC_LINKS stream, where
    MaxMsgsPerSubject=1 keeps only the latest message per subject — i.e. the
    current actuation state per ground station, replace-not-merge. Distinct from
    the append-only ops event log (ops_event_subject): the Scheduler publishes the
    audit event there AND the current state here, so VS-API can recover the full
    per-GS health roster via LAST_PER_SUBJECT on (re)subscribe instead of missing
    the one-time startup roster.
    """
    return f"{ROOT_LINKS}.{session_id}.actuation.{gs_id}"


def actuation_state_subscribe_subject(session_id: str) -> str:
    """Wildcard for recovering every ground station's retained actuation state."""
    return f"{ROOT_LINKS}.{session_id}.actuation.>"


def actual_links_subject(session_id: str, scheduler_instance_id: str) -> str:
    """Per-instance retained set of kernel-actual links (latest only).

    The Scheduler's ``_actual_links`` is what the Node Agents have CONFIRMED
    active (verified=true proof) — the "kernel actual" truth, distinct from the
    OME ``LinkStateSnapshot`` (OME's desired/visible model). LinkUp/LinkDown ride
    ``.up``/``.down`` as ``DeliverPolicy.NEW`` events and do not survive a VS-API
    resubscribe, so this retained, replace-not-merge subject (MaxMsgsPerSubject=1
    on NODALARC_LINKS, recovered via LAST_PER_SUBJECT) is the only recoverable
    source of which pairs the kernel actually has up. Keyed per
    ``scheduler_instance_id`` so a restarted instance does not clobber a dead
    predecessor's retained message; under the single-Scheduler-owner-per-session
    model the consumer tracks the current owner and prunes dead predecessors
    (N>1 live schedulers per session need the same queue-group/leader-election
    redesign the dispatcher already notes). ``scheduler_instance_id`` is
    ``{hostname}-{pid}-{ms}`` — no dots, so it is a single safe subject token.
    """
    return f"{ROOT_LINKS}.{session_id}.actual.{scheduler_instance_id}"


def actual_links_subscribe_subject(session_id: str) -> str:
    """Wildcard for recovering every Scheduler instance's kernel-actual link set."""
    return f"{ROOT_LINKS}.{session_id}.actual.>"


def session_ephemeris_subject(session_id: str) -> str:
    """Session ephemeris subject for a specific session."""
    return f"{ROOT_SESSION}.{session_id}.ephemeris"


def playback_state_subject(session_id: str) -> str:
    """Playback state subject for a specific session."""
    return f"{ROOT_SESSION}.{session_id}.playback_state"


def scheduling_checkpoint_subject(session_id: str) -> str:
    """Scheduling checkpoint subject for a specific session."""
    return f"{ROOT_SESSION}.{session_id}.scheduling_checkpoint"


def replay_anchor_subject(session_id: str) -> str:
    """Bounded-replay anchor subject for a specific session."""
    return f"{ROOT_SESSION}.{session_id}.replay_anchor"


def scenario_inject_subject(session_id: str) -> str:
    """Scenario injection subject for a specific session (core NATS request/reply)."""
    return f"{ROOT_SCHEDULER}.{session_id}.scenario"


def scheduler_repair_subject(session_id: str) -> str:
    """Explicit operator repair command subject for one Scheduler session."""
    return f"{ROOT_SCHEDULER}.{session_id}.repair"


def convergence_result_subject(session_id: str) -> str:
    """MI convergence result subject for a specific session."""
    return f"{ROOT_MI}.{session_id}.convergence"


def probe_result_subject(session_id: str) -> str:
    """MI probe result subject for a specific session."""
    return f"{ROOT_MI}.{session_id}.probe"


def adapter_event_subject(session_id: str) -> str:
    """MI adapter event subject for a specific session."""
    return f"{ROOT_MI}.{session_id}.adapter"


def almanac_event_subject(session_id: str) -> str:
    """NodalPath almanac event subject for a specific session."""
    return f"{ROOT_NODALPATH}.{session_id}.almanac"


def _scoped_event_subject(
    root: str, session_id: str, source: str, code: str, *, tenant_id: str
) -> str:
    """The one scope hierarchy under an event root.

    - Infrastructure (no tenant, no session): {root}._infra.{source}[.{code}]
    - Tenant (tenant, no session): {root}.{tenant}._tenant.{source}[.{code}]
    - Session (no tenant): {root}.{session}.{source}[.{code}]
    - Session (with tenant): {root}.{tenant}.{session}.{source}[.{code}]
    """
    code_lower = code.lower() if code else ""
    if not tenant_id and not session_id:
        base = f"{root}._infra.{source}"
    elif not tenant_id:
        base = f"{root}.{sanitize_session_id(session_id)}.{source}"
    elif not session_id:
        base = f"{root}.{tenant_id}._tenant.{source}"
    else:
        base = f"{root}.{tenant_id}.{sanitize_session_id(session_id)}.{source}"

    if code_lower:
        return f"{base}.{code_lower}"
    return base


def ops_event_subject(session_id: str, source: str, code: str = "", *, tenant_id: str = "") -> str:
    """Build a scoped ops event subject (INFO and above; see `_scoped_event_subject`)."""
    return _scoped_event_subject(ROOT_OPS, session_id, source, code, tenant_id=tenant_id)


def debug_event_subject(
    session_id: str, source: str, code: str = "", *, tenant_id: str = ""
) -> str:
    """Build a scoped debug event subject (below INFO), the same hierarchy under the debug root."""
    return _scoped_event_subject(ROOT_DEBUG, session_id, source, code, tenant_id=tenant_id)


def ops_subscribe_subject(session_id: str, *, tenant_id: str = "") -> str:
    """Wildcard subject for subscribing to all ops events for a session."""
    if not tenant_id and not session_id:
        return f"{ROOT_OPS}._infra.>"
    if not tenant_id:
        return f"{ROOT_OPS}.{sanitize_session_id(session_id)}.>"
    if not session_id:
        return f"{ROOT_OPS}.{tenant_id}._tenant.>"
    return f"{ROOT_OPS}.{tenant_id}.{sanitize_session_id(session_id)}.>"


def ops_subscribe_all_subject() -> str:
    """Wildcard subject for every ops event of every scope (the VS-API system log)."""
    return f"{ROOT_OPS}.>"


def debug_subscribe_all_subject() -> str:
    """Wildcard subject for every debug event of every scope (the VS-API debug stream)."""
    return f"{ROOT_DEBUG}.>"


# Request/reply subjects (NATS core, not JetStream)
# Playback control (pause / resume / set_speed) owned by OME Pacemaker
# Subject is in ome_control namespace — deliberately outside
# the "nodalarc.ome.>" JetStream-captured wildcard so request/reply
# messages are not stream-retained.
SUBJECT_PLAYBACK_CONTROL = f"{ROOT_OME_CONTROL}.playback"
SUBJECT_MI_TRACE = f"{ROOT_MI}.trace"
SUBJECT_MI_CONVERGENCE_GATE = f"{ROOT_MI}.convergence_gate"

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


NATS_URL_ENV = "NODALARC_NATS_URL"


def nats_url() -> str:
    """The NATS server URL this process connects to, from ``NODALARC_NATS_URL`` only.

    The chart sets the variable for every workload (host-network address,
    port and per-service credentials included); a process outside the
    cluster sets it itself. There is no default: an unset or blank value
    is refused here, before any connection attempt.
    """
    import os

    url = os.environ.get(NATS_URL_ENV, "").strip()
    if not url:
        raise RuntimeError(
            f"{NATS_URL_ENV} is not set; the chart sets it for every workload and a "
            "process outside the cluster must export it (nats://[user:pass@]host:port)"
        )
    return url


def messaging_inventory() -> dict[str, list[dict[str, object]]]:
    """The deployed-stream and authorization inventories, as plain data for the chart."""
    return {
        "streams": [{"name": s.name, "subjects": s.subjects} for s in STREAMS],
        "users": [
            {"key": u.key, "publish": list(u.publish), "subscribe": list(u.subscribe)}
            for u in NATS_USERS
        ],
    }


_STREAM_NAME_RE = re.compile(r"^NODALARC_[A-Z]+$")
_ROOT_WILDCARD_RE = re.compile(r"^nodalarc\.[a-z_]+\.>$")
_USER_KEY_RE = re.compile(r"^[a-zA-Z]+$")
# A subject pattern: dot-separated non-empty tokens, `*` only as a whole token, `>` only last.
_SUBJECT_PATTERN_RE = re.compile(r"^(\$?[A-Za-z0-9_-]+|\*)(\.(\$?[A-Za-z0-9_-]+|\*))*(\.>)?$")


class MessagingInventoryError(ValueError):
    """The messaging inventory would not be accepted by the chart's consumers."""


def validate_messaging_inventory(inventory: dict[str, list[dict[str, object]]]) -> None:
    """Refuse an inventory the chart would refuse, before it is rendered.

    The chart's consumers apply the same rules at render time and refuse on
    their own; this check proves the producer's output and never stands in
    for that refusal.
    """
    streams = inventory.get("streams")
    users = inventory.get("users")
    if not isinstance(streams, list) or not streams:
        raise MessagingInventoryError("streams must be a non-empty list")
    if not isinstance(users, list) or not users:
        raise MessagingInventoryError("users must be a non-empty list")
    for stream in streams:
        if not isinstance(stream, dict):
            raise MessagingInventoryError("every stream must be a mapping")
        name, subjects = stream.get("name"), stream.get("subjects")
        if not isinstance(name, str) or not _STREAM_NAME_RE.match(name):
            raise MessagingInventoryError(f"stream name {name!r} is not a NODALARC_* name")
        if not isinstance(subjects, str) or not _ROOT_WILDCARD_RE.match(subjects):
            raise MessagingInventoryError(
                f"stream {name} subject pattern {subjects!r} is not a root wildcard"
            )
    for user in users:
        if not isinstance(user, dict):
            raise MessagingInventoryError("every user must be a mapping")
        key = user.get("key")
        if not isinstance(key, str) or not _USER_KEY_RE.match(key):
            raise MessagingInventoryError(f"user key {key!r} is not a values key")
        for field in ("publish", "subscribe"):
            patterns = user.get(field)
            if not isinstance(patterns, list):
                raise MessagingInventoryError(f"user {key} {field} must be a list")
            for pattern in patterns:
                if not isinstance(pattern, str) or not _SUBJECT_PATTERN_RE.match(pattern):
                    raise MessagingInventoryError(
                        f"user {key} {field} entry {pattern!r} is not a subject pattern"
                    )


def render_messaging_inventory() -> str:
    """The chart's ``files/nats-messaging.yaml``; ``scripts/na-render-helm-chart.sh`` writes it."""
    import yaml

    inventory = messaging_inventory()
    validate_messaging_inventory(inventory)
    return yaml.safe_dump(inventory, sort_keys=False)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m nodalarc.nats_channels",
        description="Render the NATS messaging inventory the chart consumes.",
    )
    parser.add_argument(
        "--render-messaging",
        action="store_true",
        help="write the streams and users inventory as YAML to stdout",
    )
    args = parser.parse_args(argv)
    if not args.render_messaging:
        parser.error("nothing to do: pass --render-messaging")
    sys.stdout.write(render_messaging_inventory())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


def node_agent_subject(node_id: str) -> str:
    """Build per-node subject for Node Agent request/reply (NATS core, not JetStream)."""
    return f"{ROOT_AGENT}.{node_id}"


def wiring_progress_subject(node_id: str) -> str:
    """Build per-node subject for wiring progress updates.

    Transient core NATS (not JetStream, no retention). Hierarchical per
    node so the VS-API subscribes once to `wiring_progress_subscribe_subject()`.
    """
    return f"{ROOT_AGENT}.progress.{node_id}"


def wiring_progress_subscribe_subject() -> str:
    """Wildcard subject for the wiring progress of every node."""
    return f"{ROOT_AGENT}.progress.*"


def debug_ctrl_subject(source: str) -> str:
    """Build the NATS request/reply subject for debug level control.

    The VS-API sends enable/disable requests to this subject. The
    logging library in the target service subscribes and responds.
    Core NATS, not JetStream: no retention and no stream.
    """
    return f"{ROOT_LOGGING}.debug_ctrl.{source}"
