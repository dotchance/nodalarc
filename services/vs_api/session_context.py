# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""SessionContext — encapsulates all per-session state for the VS-API.

Each SessionContext owns:
  - Satellite/GS node positions, link state, recent events
  - Almanac state (NodalPath)
  - NATS subscriptions (scoped to session_id)
  - Continuous tracer
  - Playback state
  - Stale detection
  - SQLite DB path

The VS-API holds one _active_context at a time. On session switch,
the old context is stopped (subscriptions closed, state cleared) and
a new one is started. The shared NATS connection outlives all contexts.

This class is the building block for multi-tenant support:
  - Single-user: one _active_context, swapped on switch
  - Option 1 (sidecar): one context per VS-API pod
  - Option 2 (aggregator): dict[session_id, SessionContext]
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import sqlite3
import threading
import time as _time
from collections import Counter, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import nats
import yaml
from nodalarc.db.queries import insert_ome_lifecycle_event, insert_operator_intervention_event
from nodalarc.explain import compose_gs_decision_timeline_sample
from nodalarc.models.decision_explanation import (
    GsDecisionReasonCount,
    GsDecisionTimelineFacts,
    PendingActuation,
)
from nodalarc.models.link_decisions import GroundLinkDecisionSnapshot
from nodalarc.models.resolved_session import ResolvedNode, SourceContext
from nodalarc.models.scheduler_ops import ActualLinkSnapshot, ActuationState, parse_actuation_state
from nodalarc.models.vs_api import (
    AlmanacState,
    LinkDecisionTrace,
    LinkState,
    NetworkHealth,
    NodeAddress,
    NodeState,
    RecentEvent,
)
from nodalarc.nats_channels import (
    STREAM_LINK_EVENTS,
    STREAM_OME_EVENTS,
    STREAM_OPS_EVENTS,
    STREAM_SESSION_EVENTS,
    actual_links_subscribe_subject,
    actuation_state_subscribe_subject,
    almanac_event_subject,
    ground_link_decision_snapshot_subject,
    latency_update_subject,
    link_down_subject,
    link_state_snapshot_subject,
    link_up_subject,
    ome_clock_subject,
    ops_subscribe_subject,
    playback_state_subject,
    session_ephemeris_subject,
)
from nodalarc.platform_config import get_platform_config
from nodalarc.resolve_session import resolve_session_with_assets
from pydantic import ValidationError

log = logging.getLogger(__name__)

STALE_THRESHOLD_S: float = 15.0
CONVERGENCE_DWELL_S: float = 15.0
BULK_CHANGE_THRESHOLD: float = 0.10
GROUND_DECISION_SAMPLE_LIMIT: int = 720


class SessionContext:
    """Per-session state container with NATS subscription lifecycle.

    start(nc, mode) creates JetStream subscriptions on the shared NATS
    connection. stop() unsubscribes all and clears state. The context
    does NOT own or close the NATS connection.
    """

    def __init__(self, session_id: str, session_config_path: str) -> None:
        if not session_id:
            log.error("FATAL: SessionContext created with empty session_id")
            raise ValueError("session_id is required")
        if not session_config_path:
            log.error("FATAL: SessionContext created with empty session_config_path")
            raise ValueError("session_config_path is required")

        self.session_id = session_id
        self.session_file = session_config_path

        # Parse session config for metadata through the resolver.
        session_data = yaml.safe_load(Path(session_config_path).read_text())
        resolution = resolve_session_with_assets(
            session_data,
            source_context=SourceContext(origin="vs_api.session_context"),
        )
        resolved = resolution.resolved
        platform = self._platform_config()
        self.routing_stack = self._routing_label(resolved)
        self.constellation_name = resolved.session.name

        # Load GS elevation map and beam falloff
        self.gs_elevation_map: dict[str, float] = self._load_gs_elevation_map(resolved)
        (
            self._node_addresses_by_id,
            self._node_primary_prefix_by_id,
        ) = self._build_node_network_identity_map(resolution)
        self._resolved_static_nodes_by_id = self._build_resolved_static_node_states(
            resolved,
            addresses_by_id=self._node_addresses_by_id,
            primary_prefix_by_id=self._node_primary_prefix_by_id,
            min_elevation_by_id=self.gs_elevation_map,
        )
        self.beam_falloff_exponent: float = platform.vs_api_visual_beam_falloff_exponent

        # Wall-clock actuation-latency contract (simulation.actuation) — the
        # in_flight -> faulted bound the explanation composer carries.
        self.actuation_expected_latency_ms: float = platform.vs_api_actuation_expected_latency_ms
        self.actuation_fault_after_ms: float = platform.vs_api_actuation_fault_after_ms

        self._init_runtime_state()
        self._seed_resolved_static_nodes()

    def _init_runtime_state(self) -> None:
        """Initialize all runtime state fields. Called by __init__ and
        _init_state_only. Single source of truth — adding a field here
        covers both production and test paths.
        """
        self.db_path: str = ""
        self.state_lock = threading.Lock()
        if not hasattr(self, "_node_addresses_by_id"):
            self._node_addresses_by_id = {}
        if not hasattr(self, "_node_primary_prefix_by_id"):
            self._node_primary_prefix_by_id = {}
        if not hasattr(self, "_resolved_static_nodes_by_id"):
            self._resolved_static_nodes_by_id = {}
        self.nodes: dict[str, NodeState] = {}
        self.links: dict[str, LinkState] = {}
        self.link_decision_traces: dict[str, LinkDecisionTrace] = {}
        # Latest GroundLinkDecisionSnapshot from the OME — the operator-facing
        # surface for "why isn't pair X scheduled?" Carries
        # visibility_reject_reason for every pair the OME considered
        # plus unscheduled_reason for visible-but-unallocated pairs.
        # None until the first snapshot lands.
        self.latest_ground_link_decision_snapshot: GroundLinkDecisionSnapshot | None = None
        self.ground_decision_samples_by_gs: dict[str, deque] = {}
        self.recent_events: list[RecentEvent] = []
        self.network_health: NetworkHealth = NetworkHealth(
            status="no measurement",
            converging_since_ms=None,
            unreachable_flows=0,
            last_convergence_ms=None,
        )
        self.mi_active: bool = False
        self.sim_time: str = datetime.now(UTC).isoformat()
        self.playback_paused: bool = False
        self.playback_speed: float = 1.0
        self.last_clock_tick_wall_time: float = 0.0
        self.last_link_event_wall_time: float = 0.0
        self.session_ready_time: float = 0.0
        self.prev_snapshot_active_count: int = 0
        self.curr_snapshot_active_count: int = 0
        self.last_snapshot_seq: int = 0
        self.cached_ephemeris: dict | None = None
        self.cached_ephemeris_obj: object | None = None
        self.almanac_lock = threading.Lock()
        self.almanac: AlmanacState = AlmanacState()
        self.continuous_tracer = None
        self.session_ops_events: deque = deque(maxlen=500)
        self.actuation_notices_by_key: dict[tuple[str, str], dict] = {}
        self.actuation_latest_by_gs: dict[tuple[str, str], dict] = {}
        # Recoverable kernel-actual + pending state per Scheduler instance, replace-not-
        # merge per instance, from the retained ActualLinkSnapshot. value = {"generation":
        # str, "pairs": frozenset[ordered pair], "pending": dict[ordered pair, pending_since],
        # "emitted_at": datetime|None, "received_at": datetime}. actual_kernel_pairs() unions
        # "pairs" into kernel-actual truth (for kernel_up — NOT ctx.links, OME's model);
        # pending_actuation() turns "pending" into the Scheduler-owned divergence clock.
        # "received_at" (VS-API wall clock at receipt) + "emitted_at" (Scheduler publish
        # clock) give skew-free divergence age without cross-pod NTP drift.
        self.actual_links_by_instance: dict[str, dict] = {}
        self.ome_lifecycle_notices_by_key: dict[tuple[str, str], dict] = {}
        self._subscriptions: list = []
        self._subscriber_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._ephemeris_received = False
        self._snapshot_received = False
        self._stopped = False

    def _init_state_only(self) -> None:
        """Initialize for tests that don't need a session config file.
        Sets identity fields to test defaults, then calls the shared
        runtime state initializer.
        """
        self.session_id = "test"
        self.session_file = ""
        self.routing_stack = "test"
        self.constellation_name = "test"
        self.gs_elevation_map = {}
        self._node_addresses_by_id = {}
        self._node_primary_prefix_by_id = {}
        self._resolved_static_nodes_by_id = {}
        self.beam_falloff_exponent = 2.0
        self.actuation_expected_latency_ms = 250.0
        self.actuation_fault_after_ms = 1200.0
        self._init_runtime_state()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def _check_ready(self) -> None:
        """Set ready only when BOTH ephemeris and snapshot have been received.

        Prevents ghost snapshot race: if a stale snapshot from the
        dying old OME arrives before the new OME publishes its
        ephemeris, we don't declare ready on ghost data.
        """
        if self._ephemeris_received and self._snapshot_received and not self._ready.is_set():
            self._ready.set()
            log.info(
                "SessionContext ready (ephemeris + snapshot): session_id=%s",
                self.session_id,
            )

    async def start(self, nc: nats.NATS, mode: Literal["switch", "recovery"]) -> None:
        """Start NATS subscriptions on the shared connection.

        mode="switch": DeliverPolicy.NEW — only messages published after
            subscription. Avoids stale retained snapshots from previous OME.
        mode="recovery": DeliverPolicy.LAST_PER_SUBJECT — recovers current
            state of an already-running simulation after VS-API restart.
        """
        if self._stopped:
            raise RuntimeError("Cannot start a stopped SessionContext")

        self._subscriber_task = asyncio.create_task(
            self._subscriber_loop(nc, mode),
            name=f"session-subscriber-{self.session_id}",
        )
        log.info(
            "SessionContext started: session_id=%s mode=%s",
            self.session_id,
            mode,
        )

    async def stop(self) -> None:
        """Stop all NATS subscriptions and clear state.

        Cleanup is in the subscriber task's finally block — cancelling
        the task triggers it. Timeout of 15s prevents hanging if NATS
        is unreachable (9 subscriptions × ~2s each worst case).
        """
        self._stopped = True
        if self._subscriber_task and not self._subscriber_task.done():
            self._subscriber_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._subscriber_task), timeout=15.0)
            except asyncio.CancelledError, TimeoutError:
                log.warning(
                    "SessionContext stop timed out after 15s — %d subscriptions may be orphaned",
                    len(self._subscriptions),
                )
        # Clear state after subscriptions are gone
        with self.state_lock:
            self.nodes.clear()
            self.links.clear()
            self.recent_events.clear()
        with self.almanac_lock:
            self.almanac = AlmanacState()
        self.continuous_tracer = None
        self.session_ops_events.clear()
        log.info(
            "SessionContext stopped: session_id=%s, %d subscriptions cleaned",
            self.session_id,
            len(self._subscriptions),
        )

    def is_stale(self) -> bool:
        if self.last_clock_tick_wall_time == 0.0 and self.last_link_event_wall_time == 0.0:
            return False
        latest = max(self.last_clock_tick_wall_time, self.last_link_event_wall_time)
        return (_time.monotonic() - latest) > STALE_THRESHOLD_S

    async def _subscriber_loop(self, nc: nats.NATS, mode: str) -> None:
        """Main NATS subscription loop. Cleanup in finally block."""
        from nats.js.api import DeliverPolicy

        js = nc.jetstream()
        sid = self.session_id

        # Retained subjects (ephemeris, playback, link snapshot) always use
        # LAST_PER_SUBJECT. These are published once at OME startup and retained
        # in JetStream with MaxMsgsPerSubject=1. In switch mode, the OME restarts
        # before VS-API subscribes — NEW would miss the retained messages.
        state_policy = DeliverPolicy.LAST_PER_SUBJECT

        try:
            # Subscribe to all session-scoped subjects
            self._subscriptions.append(
                await js.subscribe(
                    session_ephemeris_subject(sid),
                    stream=STREAM_SESSION_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=state_policy,
                    cb=self._on_session_ephemeris,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    playback_state_subject(sid),
                    stream=STREAM_SESSION_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=state_policy,
                    cb=self._on_playback_state,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    link_state_snapshot_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=state_policy,
                    cb=self._on_link_state_snapshot,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    ground_link_decision_snapshot_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=state_policy,
                    cb=self._on_ground_link_decision_snapshot,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    link_up_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_link_up,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    link_down_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_link_down,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    latency_update_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_latency_update,
                )
            )
            self._subscriptions.append(
                await js.subscribe(
                    ome_clock_subject(sid),
                    stream=STREAM_OME_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_clock_tick,
                )
            )
            try:
                self._subscriptions.append(
                    await js.subscribe(
                        almanac_event_subject(sid),
                        stream="NODALARC_MI",
                        ordered_consumer=True,
                        deliver_policy=DeliverPolicy.NEW,
                        cb=self._on_almanac,
                    )
                )
            except Exception as exc:
                log.info("NODALARC_MI stream not available (MI disabled): %s", exc)
            self._subscriptions.append(
                await js.subscribe(
                    ops_subscribe_subject(sid),
                    stream=STREAM_OPS_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.NEW,
                    cb=self._on_session_ops_event,
                )
            )
            # Per-GS actuation STATE on NODALARC_LINKS (MaxMsgsPerSubject=1), recovered
            # via LAST_PER_SUBJECT so the full health roster is rebuilt on (re)subscribe.
            # The ops subscription above (NEW) is the live audit log and misses the
            # Scheduler's one-time startup clean roster; this retained subject closes
            # that recovery gap. Health roster only — no event-log/persist side effects.
            self._subscriptions.append(
                await js.subscribe(
                    actuation_state_subscribe_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
                    cb=self._on_actuation_state,
                )
            )
            # Per-instance kernel-actual link set on NODALARC_LINKS (MaxMsgsPerSubject=1),
            # recovered via LAST_PER_SUBJECT. This is the Scheduler's verified _actual_links
            # ("kernel actual"), the only recoverable source of which pairs the kernel
            # actually has up — LinkUp/LinkDown (NEW) do not survive a resubscribe, so
            # without this the explanation composer falls back to the OME LinkStateSnapshot
            # and masks scheduled-but-unactuated pairs as connected.
            self._subscriptions.append(
                await js.subscribe(
                    actual_links_subscribe_subject(sid),
                    stream=STREAM_LINK_EVENTS,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
                    cb=self._on_actual_links,
                )
            )

            log.info(
                "SessionContext subscribed: session_id=%s, %d subjects, policy=%s",
                sid,
                len(self._subscriptions),
                mode,
            )

            # Keep alive until cancelled
            while not self._stopped:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("SessionContext subscriber error: %s", exc)
            raise
        finally:
            for sub in self._subscriptions:
                try:
                    await asyncio.wait_for(sub.unsubscribe(), timeout=2.0)
                except TimeoutError:
                    log.warning("Unsubscribe timed out for session %s", sid)
                except Exception as exc:
                    log.warning("Failed to unsubscribe: %s", exc)
            self._subscriptions.clear()
            log.info("SessionContext subscriptions cleaned: session_id=%s", sid)

    # ------------------------------------------------------------------
    # NATS message handlers
    # ------------------------------------------------------------------

    async def _on_session_ephemeris(self, msg) -> None:
        from nodalarc.models.events import SessionEphemeris

        try:
            eph = SessionEphemeris.model_validate_json(msg.data)
        except ValidationError as exc:
            log.warning(
                "Ignoring schema-incompatible retained SessionEphemeris on %s; "
                "waiting for OME to publish the current ephemeris: %s",
                msg.subject,
                exc,
            )
            return
        self.cached_ephemeris_obj = eph
        eph_dict = json.loads(msg.data.decode())
        eph_dict["msg_type"] = "session_ephemeris"
        self.cached_ephemeris = eph_dict
        self._propagate_positions_from_time(eph.sim_time.isoformat())
        if not self._ephemeris_received:
            self._ephemeris_received = True
            log.info("SessionContext ephemeris received: session_id=%s", self.session_id)
            self._check_ready()

    async def _on_playback_state(self, msg) -> None:
        data = json.loads(msg.data)
        state = data.get("state")
        if not state:
            log.error("Malformed PlaybackState — missing state: %s", data)
            raise ValueError("PlaybackState missing state")
        with self.state_lock:
            self.playback_paused = state == "paused"

    async def _on_clock_tick(self, msg) -> None:
        data = json.loads(msg.data)
        sim_time_str = data.get("sim_time")
        if not sim_time_str:
            log.error("Malformed ClockTick — missing sim_time: %s", data)
            raise ValueError("ClockTick missing sim_time")
        self._propagate_positions_from_time(sim_time_str)
        with self.state_lock:
            self.playback_speed = data.get("compression_ratio", 1.0)
            self.last_clock_tick_wall_time = _time.monotonic()

    async def _on_link_state_snapshot(self, msg) -> None:
        from nodalarc.models.link_state import AdminState, CarrierState, LinkStateSnapshot

        self.last_link_event_wall_time = _time.monotonic()
        try:
            snapshot = LinkStateSnapshot.model_validate_json(msg.data)
        except Exception as exc:
            log.error("FATAL: Failed to parse LinkStateSnapshot: %s", exc)
            raise

        if snapshot.snapshot_seq <= self.last_snapshot_seq:
            log.warning(
                "Stale snapshot seq=%d (last=%d) — discarding",
                snapshot.snapshot_seq,
                self.last_snapshot_seq,
            )
            return
        new_links: dict[str, LinkState] = {}
        new_traces: dict[str, LinkDecisionTrace] = {}
        for link in snapshot.links:
            if link.admin == AdminState.UP and link.carrier == CarrierState.UP:
                missing = [
                    field
                    for field in (
                        "latency_ms",
                        "bandwidth_mbps",
                        "range_km",
                        "interface_a",
                        "interface_b",
                    )
                    if getattr(link, field) in (None, "")
                ]
                if missing:
                    raise ValueError(
                        "LinkStateSnapshot active link "
                        f"{link.node_a}<->{link.node_b} is missing required "
                        f"authoritative field(s): {', '.join(missing)}"
                    )
                key = _link_key(link.node_a, link.node_b)
                new_links[key] = LinkState(
                    node_a=link.node_a,
                    node_b=link.node_b,
                    state="active",
                    link_type=_derive_link_type(
                        link.link_type,
                        link_rule_id=link.link_rule_id,
                        topology_mode=link.topology_mode,
                        endpoint_segments=link.endpoint_segments,
                    ),
                    link_reason="",
                    latency_ms=link.latency_ms,
                    bandwidth_mbps=link.bandwidth_mbps,
                    range_km=link.range_km,
                    traffic_load_pct=None,
                    interface_a=link.interface_a,
                    interface_b=link.interface_b,
                    link_rule_id=link.link_rule_id,
                    topology_mode=link.topology_mode,
                    endpoint_segments=link.endpoint_segments,
                    scheduling_state=link.scheduling_state,
                    teardown_remaining_ticks=link.teardown_remaining_ticks,
                    successor_pair=link.successor_pair,
                )
                new_traces[key] = self._trace_from_snapshot_link(link, snapshot)

        with self.state_lock:
            self.links.clear()
            self.links.update(new_links)
            self.link_decision_traces.clear()
            self.link_decision_traces.update(new_traces)
            self.prev_snapshot_active_count = self.curr_snapshot_active_count
            self.curr_snapshot_active_count = len(self.links)
            self.last_snapshot_seq = snapshot.snapshot_seq

        if not self._snapshot_received:
            self._snapshot_received = True
            log.info(
                "SessionContext first snapshot: session_id=%s, %d links",
                self.session_id,
                len(self.links),
            )
            self._check_ready()

    async def _on_ground_link_decision_snapshot(self, msg) -> None:
        """Retain the latest OME GroundLinkDecisionSnapshot.

        Every pair the OME considered carries a typed
        `visibility_reject_reason` and, for visible-but-unscheduled
        pairs, an `unscheduled_reason`. Operators query the decision
        surface via `/api/v1/ground-link-decisions` to attribute "why isn't
        this link up?" without reading scheduler logs.

        Pairing with `LinkStateSnapshot` is by
        (epoch_id, snapshot_seq, sim_time), NOT by shared stream — see
        `ground_link_decision_snapshot_subject` docstring.
        """
        try:
            snapshot = GroundLinkDecisionSnapshot.model_validate_json(msg.data)
        except Exception as exc:
            log.error("FATAL: Failed to parse GroundLinkDecisionSnapshot: %s", exc)
            raise

        with self.state_lock:
            current = self.latest_ground_link_decision_snapshot
            if current is not None and snapshot.snapshot_seq <= current.snapshot_seq:
                log.warning(
                    "Stale GroundLinkDecisionSnapshot seq=%d (last=%d) — discarding",
                    snapshot.snapshot_seq,
                    current.snapshot_seq,
                )
                return
            if current is not None and snapshot.epoch_id != current.epoch_id:
                self.ground_decision_samples_by_gs.clear()
            self.latest_ground_link_decision_snapshot = snapshot
            gs_ids = sorted(snapshot.policy_audit.selection_policies)
            for gs_id in gs_ids:
                sample = compose_gs_decision_timeline_sample(gs_id=gs_id, snapshot=snapshot)
                if sample is None:
                    continue
                samples = self.ground_decision_samples_by_gs.setdefault(
                    gs_id, deque(maxlen=GROUND_DECISION_SAMPLE_LIMIT)
                )
                if samples and samples[-1].snapshot_seq == sample.snapshot_seq:
                    samples[-1] = sample
                else:
                    samples.append(sample)

    def ground_decision_timeline(
        self, gs_id: str, *, limit: int | None = None
    ) -> GsDecisionTimelineFacts | None:
        """Return the bounded observed decision window for one GS."""
        with self.state_lock:
            samples = tuple(self.ground_decision_samples_by_gs.get(gs_id, ()))
        if limit is not None and limit > 0:
            samples = samples[-limit:]
        if not samples:
            return None

        counts = Counter((sample.state, sample.reason_code) for sample in samples)
        reason_counts = tuple(
            GsDecisionReasonCount(state=state, reason_code=reason, count=count)
            for (state, reason), count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0][0], item[0][1] or "")
            )
        )
        return GsDecisionTimelineFacts(
            gs_id=gs_id,
            sample_count=len(samples),
            window_started_sim_time=samples[0].sim_time,
            window_ended_sim_time=samples[-1].sim_time,
            samples=samples,
            reason_counts=reason_counts,
        )

    async def _on_link_up(self, msg) -> None:
        self.last_link_event_wall_time = _time.monotonic()
        data = json.loads(msg.data)
        node_a = data.get("node_a")
        node_b = data.get("node_b")
        if not node_a or not node_b:
            log.error("Malformed LinkUp — missing node_a=%r or node_b=%r", node_a, node_b)
            raise ValueError(f"LinkUp missing required fields: node_a={node_a}, node_b={node_b}")
        for field in (
            "interface_a",
            "interface_b",
            "latency_ms",
            "bandwidth_mbps",
            "range_km",
            "reason",
            "link_type",
            "provenance",
        ):
            if field not in data or data[field] is None:
                log.error("Malformed LinkUp — missing %s: %s", field, data)
                raise ValueError(f"LinkUp missing required field: {field}")
        key = _link_key(node_a, node_b)
        trace = self._trace_from_link_event(data)
        with self.state_lock:
            self.links[key] = LinkState(
                node_a=node_a,
                node_b=node_b,
                state="active",
                link_type=_derive_link_type(
                    data["link_type"],
                    link_rule_id=data.get("link_rule_id"),
                    topology_mode=data.get("topology_mode"),
                    endpoint_segments=data.get("endpoint_segments"),
                ),
                link_reason=data["reason"],
                latency_ms=data["latency_ms"],
                bandwidth_mbps=data["bandwidth_mbps"],
                range_km=data["range_km"],
                traffic_load_pct=None,
                interface_a=data["interface_a"],
                interface_b=data["interface_b"],
                link_rule_id=data.get("link_rule_id"),
                topology_mode=data.get("topology_mode"),
                endpoint_segments=data.get("endpoint_segments"),
            )
            self.link_decision_traces[key] = trace
        self._notify_topology_change(node_a, node_b)
        self._add_recent_event(data, "link_up")

    async def _on_link_down(self, msg) -> None:
        self.last_link_event_wall_time = _time.monotonic()
        data = json.loads(msg.data)
        node_a = data.get("node_a")
        node_b = data.get("node_b")
        if not node_a or not node_b:
            log.error("Malformed LinkDown — missing node_a=%r or node_b=%r", node_a, node_b)
            raise ValueError(f"LinkDown missing required fields: node_a={node_a}, node_b={node_b}")
        if data.get("link_type") is None:
            log.error("Malformed LinkDown — missing link_type: %s", data)
            raise ValueError("LinkDown missing required field: link_type")
        key = _link_key(node_a, node_b)
        with self.state_lock:
            self.links.pop(key, None)
            self.link_decision_traces.pop(key, None)
        self._notify_topology_change(node_a, node_b)
        self._add_recent_event(data, "link_down")

    async def _on_latency_update(self, msg) -> None:
        data = json.loads(msg.data)
        node_a = data.get("node_a")
        node_b = data.get("node_b")
        if not node_a or not node_b:
            log.error("Malformed LatencyUpdate — missing node_a=%r or node_b=%r", node_a, node_b)
            raise ValueError("LatencyUpdate missing required fields")
        latency_ms = data.get("latency_ms")
        range_km = data.get("range_km")
        if latency_ms is None or range_km is None:
            log.error("Malformed LatencyUpdate — missing latency_ms or range_km: %s", data)
            raise ValueError("LatencyUpdate missing latency_ms or range_km")
        if data.get("provenance") is None:
            log.error("Malformed LatencyUpdate — missing provenance: %s", data)
            raise ValueError("LatencyUpdate missing required field: provenance")
        key = _link_key(node_a, node_b)
        with self.state_lock:
            existing = self.links.get(key)
            if existing is not None:
                trace = self._trace_from_latency_update(data)
                self.links[key] = existing.model_copy(
                    update={"latency_ms": latency_ms, "range_km": range_km}
                )
                self.link_decision_traces[key] = trace

    async def _on_almanac(self, msg) -> None:
        data = json.loads(msg.data)
        event_type = data.get("event_type")
        if not event_type:
            log.error("Malformed AlmanacEvent — missing event_type: %s", data)
            raise ValueError("AlmanacEvent missing event_type")
        with self.almanac_lock:
            self.almanac = self.almanac.model_copy(update={"nodalpath_active": True})
            if event_type == "table_pushed":
                self.almanac = self.almanac.model_copy(
                    update={
                        "last_topology_state_id": data.get("topology_state_id"),
                        "last_push_sim_time": data.get("sim_time"),
                        "last_push_wall_time": data.get("wall_time"),
                        "nodes_succeeded": data.get("nodes_succeeded"),
                        "nodes_failed": data.get("nodes_failed"),
                    }
                )
            elif event_type == "deviation_detected":
                self.almanac = self.almanac.model_copy(
                    update={"deviation_count": self.almanac.deviation_count + 1}
                )
            elif event_type == "recomputation_triggered":
                self.almanac = self.almanac.model_copy(
                    update={"recomputation_count": self.almanac.recomputation_count + 1}
                )

    async def _on_session_ops_event(self, msg) -> None:
        data = json.loads(msg.data)
        with self.state_lock:
            self.session_ops_events.append(data)
            self._update_actuation_notice(data)
            self._update_ome_lifecycle_notice(data)
        self._persist_operator_intervention(data)
        self._persist_ome_lifecycle_event(data)

    async def _on_actuation_state(self, msg) -> None:
        """Retained per-GS actuation state (LAST_PER_SUBJECT recovery).

        Populates the health roster ONLY. The append-only event log, operator-
        intervention persistence, and lifecycle notices are owned by
        _on_session_ops_event on the ops stream; replaying retained state must not
        re-append log entries or re-persist interventions. _update_actuation_notice
        is idempotent, so a live event arriving on both subjects is harmless.
        """
        data = json.loads(msg.data)
        with self.state_lock:
            self._update_actuation_notice(data)

    async def _on_actual_links(self, msg) -> None:
        """Retained per-instance kernel-actual + pending link set (LAST_PER_SUBJECT recovery).

        The Scheduler's ``_actual_links`` is verified kernel truth — the only
        recoverable source of which pairs the kernel ACTUALLY has up (LinkUp/LinkDown
        are NEW-delivered and don't survive a resubscribe). The same retained snapshot
        carries ``pending_pairs`` (the Scheduler-owned in_flight -> faulted clock) and
        ``emitted_at``, so the divergence timing recovers atomically with the actual set.
        We stamp ``received_at`` on receipt so ``pending_actuation`` can derive divergence
        age skew-free. Under the single-Scheduler-owner-per-session model a same-generation
        peer with a different instance_id is a dead predecessor (instance restart) and is
        pruned, mirroring ``_update_actuation_notice``'s roster pruning, so a stale set
        never over-reports kernel-up. N>1 live schedulers per session need the
        queue-group/leader-election redesign the dispatcher already documents.
        """
        try:
            snap = ActualLinkSnapshot.model_validate_json(msg.data)
        except ValidationError as exc:
            # Retained LAST_PER_SUBJECT message: a schema-incompatible snapshot from a
            # prior-deploy Scheduler must be tolerated, not crash-looped — wait for the
            # current Scheduler to republish. Mirrors _on_session_ephemeris.
            log.warning(
                "Ignoring schema-incompatible retained ActualLinkSnapshot on %s; "
                "waiting for the Scheduler to republish: %s",
                msg.subject,
                exc,
            )
            return
        instance_id = snap.scheduler_instance_id
        if not instance_id:
            return
        pairs = frozenset(tuple(sorted((p[0], p[1]))) for p in snap.active_pairs if len(p) == 2)
        pending = {
            tuple(sorted((pp.pair[0], pp.pair[1]))): pp.pending_since
            for pp in snap.pending_pairs
            if len(pp.pair) == 2
        }
        received_at = datetime.now(UTC)
        with self.state_lock:
            for old_instance, rec in list(self.actual_links_by_instance.items()):
                if old_instance != instance_id and rec.get("generation") == snap.wiring_generation:
                    self.actual_links_by_instance.pop(old_instance, None)
            self.actual_links_by_instance[instance_id] = {
                "generation": snap.wiring_generation,
                "pairs": pairs,
                "pending": pending,
                "emitted_at": snap.emitted_at,
                "received_at": received_at,
            }

    def actual_kernel_pairs(self) -> frozenset[tuple[str, str]]:
        """Scheduler-verified kernel-actual pairs for the current session owner.

        Recoverable kernel truth (the Scheduler's ``_actual_links``), NOT the OME
        ``LinkStateSnapshot``. The union form is degenerate under single-owner-per-
        session (dead predecessors are pruned on receipt). Empty until the first
        ``ActualLinkSnapshot`` lands — honest: an unrecovered set reports no kernel
        links (reads as in-flight/divergence), never a masked connected. Caller must
        hold ``state_lock`` (mirrors ``build_actuation_health``).
        """
        if not self.actual_links_by_instance:
            return frozenset()
        return frozenset().union(*(rec["pairs"] for rec in self.actual_links_by_instance.values()))

    def pending_actuation(self, now: datetime) -> dict[tuple[str, str], PendingActuation]:
        """Scheduler-owned divergence timing for currently-pending pairs, skew-free.

        Caller holds ``state_lock``. ``pending_since`` is the Scheduler's origin (recovered
        from the retained ActualLinkSnapshot, so it survives a VS-API restart — unlike the
        old VS-API-observed onset). ``actuation_elapsed_ms`` is computed as
        ``(emitted_at - pending_since)`` [a Scheduler-clock delta] +
        ``(now - received_at)`` [a VS-API-clock delta]: each term is single-clock, so
        cross-pod NTP skew cancels and only NATS transit (sub-ms) is unaccounted. The union
        over instances is degenerate under single-owner-per-session; on the (degenerate)
        collision the earliest origin wins so the most-pending pair is never masked.
        """
        out: dict[tuple[str, str], PendingActuation] = {}
        for rec in self.actual_links_by_instance.values():
            emitted_at = rec.get("emitted_at")
            received_at = rec.get("received_at")
            for pair, since in rec.get("pending", {}).items():
                if emitted_at is not None and received_at is not None:
                    elapsed_ms = (
                        (emitted_at - since).total_seconds() + (now - received_at).total_seconds()
                    ) * 1000.0
                else:
                    # No emission stamp (legacy producer): fall back to VS-API-clock age.
                    elapsed_ms = (now - since).total_seconds() * 1000.0
                existing = out.get(pair)
                if existing is None or since < existing.pending_since:
                    out[pair] = PendingActuation(
                        pending_since=since, actuation_elapsed_ms=max(0.0, elapsed_ms)
                    )
        return out

    def _update_actuation_notice(self, event: dict) -> None:
        if event.get("source") != "scheduler":
            return
        details = event.get("details") or {}
        gs_id = details.get("gs_id")
        instance_id = details.get("scheduler_instance_id")
        if not gs_id or not instance_id:
            return
        after_state = parse_actuation_state(details.get("actuation_state_after"))
        after = after_state.value
        key = (instance_id, gs_id)
        generation = details.get("wiring_generation")
        if event.get("code") == "ACTUATION_CLEAN":
            # Single Scheduler owner per session. A startup clean roster from a
            # new instance replaces stale per-GS health from a dead instance.
            for old_key, old_event in list(self.actuation_latest_by_gs.items()):
                old_instance, old_gs = old_key
                old_generation = (old_event.get("details") or {}).get("wiring_generation")
                if old_gs == gs_id and old_instance != instance_id and old_generation == generation:
                    self.actuation_latest_by_gs.pop(old_key, None)
                    self.actuation_notices_by_key.pop(old_key, None)
        self.actuation_latest_by_gs[key] = event
        if after == "clean" or event.get("code") == "ACTUATION_CLEAN":
            self.actuation_notices_by_key.pop(key, None)
            return
        recovery = details.get("recovery_status") or {}
        notice = {
            "gs_id": gs_id,
            "actuation_state": after,
            "reason_code": event.get("code", ""),
            "message": event.get("message", ""),
            "since": event.get("timestamp"),
            "blocking_new_ground_link_up": after_state != ActuationState.CLEAN,
            "affected_pairs": details.get("affected_pairs", []),
            "desired_pairs_for_gs": details.get("desired_pairs_for_gs", []),
            "actual_pairs_for_gs": details.get("actual_pairs_for_gs", []),
            "ome_visible_scheduled_pairs_for_gs": details.get(
                "ome_visible_scheduled_pairs_for_gs", []
            ),
            "recovery_status": recovery,
            "last_event": event,
        }
        self.actuation_notices_by_key[key] = notice

    def _update_ome_lifecycle_notice(self, event: dict) -> None:
        if event.get("source") != "ome" or event.get("code") != "MBB_TEARDOWN_TERMINAL":
            return
        details = event.get("details") or {}
        gs_id = details.get("gs_id")
        teardown_id = details.get("teardown_id")
        outcome = details.get("terminal_outcome")
        if not gs_id or not teardown_id or not outcome:
            return
        notice = {
            "gs_id": gs_id,
            "teardown_id": teardown_id,
            "terminal_outcome": outcome,
            "reason_code": event.get("code", ""),
            "message": event.get("message", ""),
            "since": event.get("timestamp"),
            "epoch_id": details.get("epoch_id"),
            "snapshot_seq": details.get("snapshot_seq"),
            "allocator_step": details.get("allocator_step"),
            "master_sim_time": details.get("master_sim_time"),
            "old_pair": details.get("old_pair", []),
            "successor_pair": details.get("successor_pair", []),
            "source_allocation_event_category": details.get("source_allocation_event_category"),
            "authority_before": details.get("authority_before", {}),
            "authority_after": details.get("authority_after"),
            "seek_target_sim_time": details.get("seek_target_sim_time"),
            "last_event": event,
        }
        self.ome_lifecycle_notices_by_key[(gs_id, teardown_id)] = notice

    def _persist_operator_intervention(self, event: dict) -> None:
        details = event.get("details") or {}
        if not details.get("intervention_id") or not self.db_path:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                insert_operator_intervention_event(conn, event)
            finally:
                conn.close()
        except Exception as exc:
            log.error("Failed to persist operator intervention event: %s", exc)

    def _persist_ome_lifecycle_event(self, event: dict) -> None:
        if event.get("source") != "ome" or event.get("code") != "MBB_TEARDOWN_TERMINAL":
            return
        if not self.db_path:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                insert_ome_lifecycle_event(conn, event)
            finally:
                conn.close()
        except Exception as exc:
            log.error("Failed to persist OME lifecycle event: %s", exc)

    def build_actuation_health(self) -> dict:
        by_instance: dict[str, dict] = {}
        latest_items = list(self.actuation_latest_by_gs.items())
        for (instance_id, gs_id), event in latest_items:
            details = event.get("details") or {}
            hostname = details.get("hostname") or event.get("hostname", "")
            state = parse_actuation_state(details.get("actuation_state_after")).value
            inst = by_instance.setdefault(
                instance_id,
                {
                    "scheduler_instance_id": instance_id,
                    "hostname": hostname,
                    "status": "clean",
                    "ground_stations": [],
                },
            )
            inst["ground_stations"].append(
                {
                    "gs_id": gs_id,
                    "actuation_state": state,
                    "since": event.get("timestamp"),
                    "reason_code": event.get("code"),
                    "blocking_new_ground_link_up": state != ActuationState.CLEAN.value,
                    "recovery_status": details.get("recovery_status") or {},
                    "last_event": event,
                }
            )
        for inst in by_instance.values():
            states = {gs["actuation_state"] for gs in inst["ground_stations"]}
            if ActuationState.KERNEL_DIRTY.value in states:
                inst["status"] = "dirty"
            elif ActuationState.ACTUATION_BLOCKED.value in states:
                inst["status"] = "degraded"
            elif ActuationState.UNKNOWN.value in states:
                inst["status"] = "unknown"
            else:
                inst["status"] = "clean"
            inst["ground_stations"].sort(key=lambda item: item["gs_id"])
        latest_events = [event for _key, event in latest_items]
        latest_events.sort(key=lambda item: item.get("timestamp", ""))
        latest = latest_events[-1] if latest_events else {}
        details = latest.get("details") or {}
        return {
            "session_id": self.session_id,
            "wiring_generation": details.get("wiring_generation", ""),
            "scheduler_instances": sorted(
                by_instance.values(), key=lambda item: item["scheduler_instance_id"]
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _propagate_positions_from_time(self, sim_time_iso: str) -> None:
        """Compute and update all node positions from cached SessionEphemeris.

        Called on each ClockTick. Propagates satellite positions using the
        ephemeris model published by OME. Ground station positions are static.
        """
        from nodalarc.body_frames import BodyFrame
        from nodalarc.models.events import (
            EphemerisNodeFixed,
            EphemerisNodeKeplerian,
            EphemerisNodeTLE,
        )
        from nodalarc.orbital import OrbitalElements
        from nodalarc.propagator import (
            propagate_j2_mean_elements_for_body,
            propagate_keplerian_for_body,
            propagate_sgp4_tle,
        )

        if self.cached_ephemeris_obj is None:
            return

        try:
            sim_time_unix = datetime.fromisoformat(sim_time_iso).timestamp()
        except ValueError, TypeError:
            return

        with self.state_lock:
            self.sim_time = sim_time_iso

            for node_id, node in self.cached_ephemeris_obj.nodes.items():
                if isinstance(node, EphemerisNodeKeplerian):
                    frame = self.cached_ephemeris_obj.body_frames.get(node.reference_body)
                    if frame is None:
                        raise ValueError(
                            f"Ephemeris node {node_id!r} references body "
                            f"{node.reference_body!r}, but SessionEphemeris has no body frame"
                        )
                    body_frame = BodyFrame(
                        name=frame.body_id,
                        mean_radius_km=frame.mean_radius_km,
                        equatorial_radius_km=frame.equatorial_radius_km,
                        polar_radius_km=frame.polar_radius_km,
                        rotation_rate_rad_s=frame.rotation_rate_rad_s,
                        gravitational_parameter_km3_s2=frame.gravitational_parameter_km3_s2,
                        j2=frame.j2,
                    )
                    elements = OrbitalElements(
                        semi_major_axis_km=node.semi_major_axis_km,
                        inclination_rad=math.radians(node.inclination_deg),
                        raan_rad=math.radians(node.raan_deg),
                        mean_anomaly_rad=math.radians(node.mean_anomaly_deg),
                        eccentricity=node.eccentricity,
                        argument_of_perigee_rad=math.radians(node.argument_of_perigee_deg),
                    )
                    dt = sim_time_unix - self.cached_ephemeris_obj.epoch_unix
                    if node.propagator == "j2-mean-elements":
                        _pos_ecef, vel_ecef, geo, _pos_inertial, _vel_inertial = (
                            propagate_j2_mean_elements_for_body(
                                elements,
                                self.cached_ephemeris_obj.epoch_unix,
                                dt,
                                body_frame=body_frame,
                            )
                        )
                    else:
                        _pos_ecef, vel_ecef, geo, _pos_inertial, _vel_inertial = (
                            propagate_keplerian_for_body(
                                elements,
                                self.cached_ephemeris_obj.epoch_unix,
                                dt,
                                body_frame=body_frame,
                            )
                        )

                    existing = self.nodes.get(node_id)
                    prefix = self._node_primary_prefix_by_id.get(
                        node_id, existing.prefix if existing else None
                    )
                    addresses = self._node_addresses_by_id.get(
                        node_id, existing.addresses if existing else ()
                    )
                    self.nodes[node_id] = NodeState(
                        node_id=node_id,
                        node_type="satellite",
                        lat_deg=geo.lat_deg,
                        lon_deg=geo.lon_deg,
                        alt_km=geo.alt_km,
                        vel_x_km_s=vel_ecef.x,
                        vel_y_km_s=vel_ecef.y,
                        vel_z_km_s=vel_ecef.z,
                        plane=node.plane,
                        slot=node.slot,
                        routing_area=existing.routing_area if existing else None,
                        neighbor_count=existing.neighbor_count if existing else 0,
                        prefix=prefix,
                        addresses=addresses,
                        beam_falloff_exponent=self.beam_falloff_exponent,
                        reference_body=node.reference_body,
                        frame_id=node.frame_id,
                        tenant_id=existing.tenant_id if existing else "default",
                        segment_id=node.segment_id,
                        local_node_id=node.local_node_id,
                        namespace=node.namespace,
                        tags=tuple(node.tags),
                    )

                elif isinstance(node, EphemerisNodeTLE):
                    if node.reference_body != "earth":
                        raise ValueError(
                            f"Ephemeris node {node_id!r} uses TLE propagation with "
                            f"reference_body={node.reference_body!r}; SGP4/TLE is Earth-only"
                        )
                    dt = sim_time_unix - self.cached_ephemeris_obj.epoch_unix
                    frame = self.cached_ephemeris_obj.body_frames.get(node.reference_body)
                    if frame is None:
                        raise ValueError(
                            f"Ephemeris node {node_id!r} references missing body frame "
                            f"{node.reference_body!r}"
                        )
                    body_frame = BodyFrame(
                        name=frame.body_id,
                        mean_radius_km=frame.mean_radius_km,
                        equatorial_radius_km=frame.equatorial_radius_km,
                        polar_radius_km=frame.polar_radius_km,
                        rotation_rate_rad_s=frame.rotation_rate_rad_s,
                        gravitational_parameter_km3_s2=frame.gravitational_parameter_km3_s2,
                        j2=frame.j2,
                    )
                    _pos_ecef, vel_ecef, geo = propagate_sgp4_tle(
                        node.tle_line_1,
                        node.tle_line_2,
                        self.cached_ephemeris_obj.epoch_unix,
                        dt,
                        body_frame=body_frame,
                    )

                    existing = self.nodes.get(node_id)
                    prefix = self._node_primary_prefix_by_id.get(
                        node_id, existing.prefix if existing else None
                    )
                    addresses = self._node_addresses_by_id.get(
                        node_id, existing.addresses if existing else ()
                    )
                    self.nodes[node_id] = NodeState(
                        node_id=node_id,
                        node_type="satellite",
                        lat_deg=geo.lat_deg,
                        lon_deg=geo.lon_deg,
                        alt_km=geo.alt_km,
                        vel_x_km_s=vel_ecef.x,
                        vel_y_km_s=vel_ecef.y,
                        vel_z_km_s=vel_ecef.z,
                        plane=node.plane,
                        slot=node.slot,
                        routing_area=existing.routing_area if existing else None,
                        neighbor_count=existing.neighbor_count if existing else 0,
                        prefix=prefix,
                        addresses=addresses,
                        beam_falloff_exponent=self.beam_falloff_exponent,
                        reference_body=node.reference_body,
                        frame_id=node.frame_id,
                        tenant_id=existing.tenant_id if existing else "default",
                        segment_id=node.segment_id,
                        local_node_id=node.local_node_id,
                        namespace=node.namespace,
                        tags=tuple(node.tags),
                    )

                elif isinstance(node, EphemerisNodeFixed):
                    existing = self.nodes.get(node_id)
                    prefix = self._node_primary_prefix_by_id.get(
                        node_id, existing.prefix if existing else None
                    )
                    addresses = self._node_addresses_by_id.get(
                        node_id, existing.addresses if existing else ()
                    )
                    self.nodes[node_id] = NodeState(
                        node_id=node_id,
                        node_type="ground_station",
                        lat_deg=node.lat_deg,
                        lon_deg=node.lon_deg,
                        alt_km=node.alt_km,
                        vel_x_km_s=0.0,
                        vel_y_km_s=0.0,
                        vel_z_km_s=0.0,
                        plane=None,
                        slot=None,
                        routing_area=existing.routing_area if existing else None,
                        neighbor_count=existing.neighbor_count if existing else 0,
                        prefix=prefix,
                        addresses=addresses,
                        min_elevation_deg=self.gs_elevation_map.get(node_id),
                        reference_body=node.reference_body,
                        frame_id=node.frame_id,
                        tenant_id=existing.tenant_id if existing else "default",
                        segment_id=node.segment_id,
                        local_node_id=node.local_node_id,
                        namespace=node.namespace,
                        tags=tuple(node.tags),
                    )

    def _add_recent_event(self, event_data: dict, event_type: str) -> None:
        sim_time_raw = event_data.get("sim_time")
        if sim_time_raw is None:
            log.error("Malformed event — missing sim_time: %s", event_data)
            raise ValueError(f"Event missing sim_time: {event_type}")
        sim_time_dt = (
            datetime.fromisoformat(sim_time_raw) if isinstance(sim_time_raw, str) else sim_time_raw
        )
        node_id = event_data.get("node_id") or event_data.get("node_a")
        if not node_id:
            log.error("Malformed event — missing node_id: %s", event_data)
            raise ValueError(f"Event missing node_id: {event_type}")
        event = RecentEvent(
            sim_time=sim_time_dt,
            node_id=node_id,
            event_type=event_type,
            summary=event_data.get("reason", ""),
        )
        with self.state_lock:
            self.recent_events.append(event)
            if len(self.recent_events) > 50:
                del self.recent_events[:-50]

    def _notify_topology_change(self, node_a: str, node_b: str) -> None:
        if self.continuous_tracer is not None:
            self.continuous_tracer.notify_topology_change(node_a, node_b)

    @staticmethod
    def _trace_from_snapshot_link(link, snapshot) -> LinkDecisionTrace:
        """Build an OME-authority trace from a full-state snapshot link.

        A snapshot proves OME geometry at a specific simulation time. It does
        not prove the Node Agent's applied netem value, so substrate fields are
        explicitly null instead of invented.
        """
        return LinkDecisionTrace(
            node_a=link.node_a,
            node_b=link.node_b,
            link_type=link.link_type,
            state="active",
            interface_a=link.interface_a,
            interface_b=link.interface_b,
            reason="link_state_snapshot",
            geometry_authority="ome",
            authority_source="link_state_snapshot",
            authority_sim_time=snapshot.sim_time,
            authority_sequence=snapshot.snapshot_seq,
            authority_age_ms=0.0,
            range_km=link.range_km,
            orbital_one_way_ms=link.latency_ms,
            substrate_rtt_ms=None,
            substrate_one_way_ms=None,
            netem_one_way_ms=None,
            rtt_to_one_way_policy=None,
            link_rule_id=link.link_rule_id,
            topology_mode=link.topology_mode,
            endpoint_segments=link.endpoint_segments,
        )

    @staticmethod
    def _require_provenance(data: dict, event_type: str) -> dict:
        provenance = data.get("provenance")
        if not isinstance(provenance, dict):
            log.error("Malformed %s — provenance must be an object: %s", event_type, data)
            raise ValueError(f"{event_type} provenance must be an object")
        required = (
            "geometry_authority",
            "authority_source",
            "authority_sim_time",
            "authority_sequence",
            "authority_age_ms",
            "range_km",
            "orbital_one_way_ms",
            "substrate_rtt_ms",
            "substrate_one_way_ms",
            "netem_one_way_ms",
            "rtt_to_one_way_policy",
        )
        missing = [field for field in required if field not in provenance]
        if missing:
            raise ValueError(f"{event_type} provenance missing field(s): {', '.join(missing)}")
        return provenance

    @staticmethod
    def _assert_provenance_matches_event(data: dict, provenance: dict, event_type: str) -> None:
        if abs(float(data["range_km"]) - float(provenance["range_km"])) > 1e-9:
            raise ValueError(
                f"{event_type} range_km disagrees with provenance: "
                f"event={data['range_km']} provenance={provenance['range_km']}"
            )
        if abs(float(data["latency_ms"]) - float(provenance["orbital_one_way_ms"])) > 1e-9:
            raise ValueError(
                f"{event_type} latency_ms disagrees with provenance orbital_one_way_ms: "
                f"event={data['latency_ms']} provenance={provenance['orbital_one_way_ms']}"
            )

    def _trace_from_link_event(self, data: dict) -> LinkDecisionTrace:
        provenance = self._require_provenance(data, "LinkUp")
        self._assert_provenance_matches_event(data, provenance, "LinkUp")
        return LinkDecisionTrace(
            node_a=data["node_a"],
            node_b=data["node_b"],
            link_type=data["link_type"],
            state="active",
            interface_a=data["interface_a"],
            interface_b=data["interface_b"],
            reason=data["reason"],
            geometry_authority=provenance["geometry_authority"],
            authority_source=provenance["authority_source"],
            authority_sim_time=provenance["authority_sim_time"],
            authority_sequence=provenance["authority_sequence"],
            authority_age_ms=provenance["authority_age_ms"],
            range_km=provenance["range_km"],
            orbital_one_way_ms=provenance["orbital_one_way_ms"],
            substrate_rtt_ms=provenance["substrate_rtt_ms"],
            substrate_one_way_ms=provenance["substrate_one_way_ms"],
            netem_one_way_ms=provenance["netem_one_way_ms"],
            rtt_to_one_way_policy=provenance["rtt_to_one_way_policy"],
            link_rule_id=data.get("link_rule_id"),
            topology_mode=data.get("topology_mode"),
            endpoint_segments=data.get("endpoint_segments"),
        )

    def _trace_from_latency_update(self, data: dict) -> LinkDecisionTrace:
        provenance = self._require_provenance(data, "LatencyUpdate")
        self._assert_provenance_matches_event(data, provenance, "LatencyUpdate")
        key = _link_key(data["node_a"], data["node_b"])
        existing = self.link_decision_traces.get(key)
        if existing is None:
            raise ValueError(
                f"LatencyUpdate for {data['node_a']}<->{data['node_b']} has no existing "
                "LinkDecisionTrace"
            )
        return LinkDecisionTrace(
            node_a=data["node_a"],
            node_b=data["node_b"],
            link_type=existing.link_type,
            state="active",
            interface_a=existing.interface_a,
            interface_b=existing.interface_b,
            reason="latency_update",
            geometry_authority=provenance["geometry_authority"],
            authority_source=provenance["authority_source"],
            authority_sim_time=provenance["authority_sim_time"],
            authority_sequence=provenance["authority_sequence"],
            authority_age_ms=provenance["authority_age_ms"],
            range_km=provenance["range_km"],
            orbital_one_way_ms=provenance["orbital_one_way_ms"],
            substrate_rtt_ms=provenance["substrate_rtt_ms"],
            substrate_one_way_ms=provenance["substrate_one_way_ms"],
            netem_one_way_ms=provenance["netem_one_way_ms"],
            rtt_to_one_way_policy=provenance["rtt_to_one_way_policy"],
            link_rule_id=existing.link_rule_id,
            topology_mode=existing.topology_mode,
            endpoint_segments=existing.endpoint_segments,
        )

    def compute_convergence_state(self) -> None:
        """Update network_health based on current link counts."""
        active = self.curr_snapshot_active_count
        if self.mi_active:
            return
        if active == 0:
            self.network_health = self.network_health.model_copy(
                update={"status": "no measurement"}
            )
            return
        now = _time.monotonic()
        if self.session_ready_time > 0 and (now - self.session_ready_time) < CONVERGENCE_DWELL_S:
            self.network_health = self.network_health.model_copy(update={"status": "stabilizing"})
            return
        total = max(active, self.prev_snapshot_active_count, 1)
        delta = abs(active - self.prev_snapshot_active_count)
        if delta / total > BULK_CHANGE_THRESHOLD:
            self.network_health = self.network_health.model_copy(update={"status": "converging"})
        else:
            self.network_health = self.network_health.model_copy(update={"status": "converged"})

    @staticmethod
    def _routing_label(resolved) -> str:
        routing = resolved.routing
        if routing is None or not routing.domains:
            return "unrouted"
        return " + ".join(f"{domain.id}:{domain.protocol}" for domain in routing.domains)

    @staticmethod
    def _platform_config():
        return get_platform_config()

    @staticmethod
    def _load_gs_elevation_map(resolved) -> dict[str, float]:
        result: dict[str, float] = {}
        node_by_id = {node.node_id: node for node in resolved.nodes}
        for rule in resolved.link_rules:
            if rule.kind != "access":
                continue
            for endpoint in rule.endpoints:
                for node_id in endpoint.node_ids:
                    node = node_by_id[node_id]
                    if node.kind != "ground_station":
                        continue
                    terminal_masks = [
                        block.min_elevation_deg
                        for block in node.terminal_inventory
                        if block.endpoint_role == endpoint.terminal_role
                        and (
                            endpoint.terminal_medium is None
                            or block.medium == endpoint.terminal_medium
                        )
                        and block.min_elevation_deg is not None
                    ]
                    masks = [
                        value
                        for value in (*terminal_masks, endpoint.min_elevation_deg)
                        if value is not None
                    ]
                    if not masks:
                        raise ValueError(
                            f"no resolved min_elevation_deg for access endpoint {node_id}"
                        )
                    effective = max(masks)
                    result[node_id] = max(result.get(node_id, effective), effective)
        return result

    @staticmethod
    def _node_addr(
        *,
        purpose: Literal["router_loopback", "site_interface", "site_prefix"],
        address: str,
        interface: str | None = None,
        metric: int | None = None,
    ) -> NodeAddress:
        net = ipaddress.ip_network(address, strict=False)
        family = "ipv4" if net.version == 4 else "ipv6"
        return NodeAddress(
            purpose=purpose,
            family=family,
            address=address,
            interface=interface,
            metric=metric,
        )

    @classmethod
    def _build_node_network_identity_map(
        cls, resolution
    ) -> tuple[dict[str, tuple[NodeAddress, ...]], dict[str, str]]:
        """Build configured network identities for the node detail panels.

        These are configured addresses, not reachability claims. A route table or
        live probe is still authoritative for whether one node can reach another.
        """

        addresses_by_id: dict[str, tuple[NodeAddress, ...]] = {}
        primary_prefix_by_id: dict[str, str] = {}
        for node in resolution.resolved.nodes:
            node_addresses: list[NodeAddress] = []
            if node.interfaces is not None:
                for address in (node.interfaces.lo0.ipv4, node.interfaces.lo0.ipv6):
                    if address is None:
                        continue
                    node_addresses.append(
                        cls._node_addr(
                            purpose="router_loopback",
                            address=address,
                            interface="lo0",
                        )
                    )
                if node.interfaces.terr0 is not None:
                    for address in (node.interfaces.terr0.ipv4, node.interfaces.terr0.ipv6):
                        if address is None:
                            continue
                        node_addresses.append(
                            cls._node_addr(
                                purpose="site_interface",
                                address=address,
                                interface="terr0",
                            )
                        )
            primary_prefix: str | None = None
            if node.originated_prefixes is not None:
                for prefix in (
                    *(node.originated_prefixes.ipv4 or ()),
                    *(node.originated_prefixes.ipv6 or ()),
                ):
                    net = ipaddress.ip_network(prefix, strict=False)
                    if net.prefixlen == 0:
                        continue
                    node_addresses.append(
                        cls._node_addr(
                            purpose="site_prefix",
                            address=prefix,
                        )
                    )
                    if primary_prefix is None and net.version == 4:
                        primary_prefix = prefix
            addresses_by_id[node.node_id] = tuple(node_addresses)
            if primary_prefix is not None:
                primary_prefix_by_id[node.node_id] = primary_prefix

        return addresses_by_id, primary_prefix_by_id

    @staticmethod
    def _build_resolved_static_node_states(
        resolved,
        *,
        addresses_by_id: dict[str, tuple[NodeAddress, ...]],
        primary_prefix_by_id: dict[str, str],
        min_elevation_by_id: dict[str, float],
    ) -> dict[str, NodeState]:
        """Build VS-API state for resolved body-fixed nodes.

        OME publishes ephemeris only for the nodes it must schedule or propagate.
        The resolver is still authoritative for the deployed node roster, so
        body-fixed ground routers that do not participate in an access rule must
        be present in VS-API state even when OME has no ephemeris entry for them.
        """

        return {
            node.node_id: SessionContext._resolved_static_node_state(
                node,
                addresses=addresses_by_id.get(node.node_id, ()),
                prefix=primary_prefix_by_id.get(node.node_id),
                min_elevation_deg=min_elevation_by_id.get(node.node_id),
            )
            for node in resolved.nodes
            if node.kind == "ground_station"
        }

    @staticmethod
    def _resolved_static_node_state(
        node: ResolvedNode,
        *,
        addresses: tuple[NodeAddress, ...],
        prefix: str | None,
        min_elevation_deg: float | None,
    ) -> NodeState:
        if node.surface_position is None or node.reference_body is None:
            raise ValueError(f"resolved ground node {node.node_id!r} is missing fixed position")
        return NodeState(
            node_id=node.node_id,
            node_type="ground_station",
            lat_deg=node.surface_position.lat_deg,
            lon_deg=node.surface_position.lon_deg,
            alt_km=node.surface_position.alt_m / 1000.0,
            vel_x_km_s=0.0,
            vel_y_km_s=0.0,
            vel_z_km_s=0.0,
            plane=None,
            slot=None,
            routing_area=None,
            neighbor_count=0,
            prefix=prefix,
            addresses=addresses,
            min_elevation_deg=min_elevation_deg,
            reference_body=node.reference_body,
            frame_id=node.frame_id,
            tenant_id=node.tenant_id,
            segment_id=node.segment_id,
            local_node_id=node.local_node_id,
            namespace=node.namespace,
            tags=tuple(node.tags),
        )

    def _seed_resolved_static_nodes(self) -> None:
        if not self._resolved_static_nodes_by_id:
            return
        with self.state_lock:
            for node_id, node_state in self._resolved_static_nodes_by_id.items():
                self.nodes.setdefault(node_id, node_state)


# ------------------------------------------------------------------
# Module-level utilities (no state, pure functions)
# ------------------------------------------------------------------


def _link_key(node_a: str, node_b: str) -> str:
    return f"{min(node_a, node_b)}:{max(node_a, node_b)}"


def _derive_link_type(
    raw_type: str | None,
    *,
    link_rule_id: str | None = None,
    topology_mode: str | None = None,
    endpoint_segments: tuple[str, str] | list[str] | None = None,
) -> str:
    if raw_type and raw_type != "isl":
        return raw_type
    if raw_type is None:
        raise ValueError("link_type is required; VS-API does not infer ground links from node IDs")
    if endpoint_segments is not None and len(endpoint_segments) == 2:
        a, b = endpoint_segments
        if a != b:
            if topology_mode == "static_ip":
                return "inter_body_relay"
            return "inter_constellation"
    if link_rule_id and link_rule_id.endswith(".internal_isl"):
        return "isl"
    return "isl"
