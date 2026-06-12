# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME entry point — orchestration only, no logic.

Loads a resolved catalog session, builds OME physics inputs, calls
precompute_timeline(), publishes events on NATS JetStream.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from nodal.logging import configure as _configure_logging
from nodal.logging import connect as _connect_logging
from nodalarc.constellation_loader import SatelliteNode
from nodalarc.link_metadata import LinkRuleMetadata
from nodalarc.models.events import OpsEvent, PlaybackControlCommand, SchedulingCheckpoint
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.ome_lifecycle import (
    MbbPairAuthority,
    MbbTeardownLifecycleDetails,
    OmeOpsCode,
)
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.models.session import GroundSchedulingConfig, resolve_session_epoch
from nodalarc.nats_channels import MAX_TIME_ACCEL, MIN_TIME_ACCEL
from nodalarc.ome_inputs import ResolvedAddressingView, build_ome_inputs_from_resolved
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.session_identity import (
    read_runtime_session_run_id_file,
    require_resolved_session_run_id,
)

from ome.event_stream import (
    precompute_timeline,
    write_timeline_jsonl,
)
from ome.types import MbbTeardownLifecycleEvent, MbbTeardownState

if TYPE_CHECKING:
    pass


class _SessionBundle(NamedTuple):
    """All session-derived config needed by the OME pacing loop."""

    resolved: ResolvedSession
    session_id: str
    gs_file: GroundStationFile | None
    satellites: list[SatelliteNode]
    period: float
    addressing: ResolvedAddressingView
    neighbors: frozenset
    polar_seam_enabled: bool
    latitude_threshold_deg: float
    propagator_id: str
    interface_map: dict[tuple[str, str], tuple[str, str]]
    bandwidth_map: dict[tuple[str, str], float]
    rule_map: dict[tuple[str, str], LinkRuleMetadata]
    ground_candidate_satellites_by_gs: dict[str, tuple[str, ...]]
    ground_scheduling: GroundSchedulingConfig
    ground_link_model: str
    node_metadata: dict[str, dict[str, object]]
    body_ephemeris: object | None
    body_frames: dict
    active_bodies: frozenset[str]


def _validate_recovered_checkpoint(
    checkpoint: SchedulingCheckpoint,
    *,
    now_wall_s: float,
) -> float:
    """Validate retained checkpoint metadata and return its wall-clock age.

    Wall-clock age is diagnostic, not an authority boundary. The checkpoint's
    sim_time/step/seq are the retained session lineage; if the service was down
    for a long period, OME resumes from that lineage and logs the gap instead of
    inventing elapsed simulation time.
    """

    written_at = float(checkpoint.written_at)
    if written_at <= 0:
        raise RuntimeError("Recovered checkpoint has invalid written_at; refusing recovery")
    if int(checkpoint.step) < 0:
        raise RuntimeError("Recovered checkpoint has negative step; refusing recovery")
    if int(checkpoint.snapshot_seq) <= 0:
        raise RuntimeError("Recovered checkpoint has invalid snapshot_seq; refusing recovery")

    checkpoint_age = now_wall_s - written_at
    if checkpoint_age < 0:
        raise RuntimeError("Recovered checkpoint written_at is in the future; refusing recovery")
    return checkpoint_age


def _checkpoint_ground_sat_pair(
    pair: tuple[str, str],
    ground_station_ids: set[str] | frozenset[str],
) -> tuple[str, str]:
    """Return `(ground_id, satellite_id)` for a checkpoint pair.

    OME stores ground pairs in allocator-normalized endpoint order for stable
    comparisons. Checkpoint payloads are semantic and must name the ground and
    satellite roles explicitly, so serialization cannot assume tuple position.
    """

    a_is_ground = pair[0] in ground_station_ids
    b_is_ground = pair[1] in ground_station_ids
    if a_is_ground == b_is_ground:
        raise ValueError(
            f"Cannot serialize checkpoint pair {pair!r}: expected exactly one ground station "
            f"endpoint from {sorted(ground_station_ids)!r}"
        )
    return pair if a_is_ground else (pair[1], pair[0])


def _load_session_config(session_path: str | Path, *, run_id: str) -> _SessionBundle:
    """Load and validate all session config. Pure — no side effects."""
    resolution = load_session_resolution_from_file(session_path, origin="ome", run_id=run_id)
    resolved = resolution.resolved
    if resolved.time is None:
        raise ValueError("OME requires catalog session time")
    if resolved.dispatch is None:
        raise ValueError("OME requires catalog session dispatch")
    runtime = build_ome_inputs_from_resolved(resolved)
    polar_seam_enabled = False
    latitude_threshold_deg = 70.0

    return _SessionBundle(
        resolved=resolved,
        session_id=require_resolved_session_run_id(resolved),
        gs_file=runtime.gs_file,
        satellites=runtime.satellites,
        period=runtime.period,
        addressing=runtime.addressing,
        neighbors=runtime.neighbors,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        propagator_id=runtime.propagator_id,
        interface_map=runtime.interface_map,
        bandwidth_map=runtime.bandwidth_map,
        rule_map=runtime.rule_map,
        ground_candidate_satellites_by_gs=runtime.ground_candidate_satellites_by_gs,
        ground_scheduling=runtime.ground_scheduling,
        ground_link_model=runtime.ground_link_model,
        node_metadata=runtime.node_metadata,
        body_ephemeris=runtime.body_ephemeris,
        body_frames=runtime.body_frames,
        active_bodies=runtime.active_bodies,
    )


def _enforce_ground_link_model_contract(ground_link_model: str) -> None:
    """Fail before OME run if geometry-only physics is not explicitly acknowledged."""
    if ground_link_model != "geometry_only":
        return
    logging.warning(
        "catalog OME ground_link_model=geometry_only: ground links use LOS/elevation and "
        "declared candidates; terminal range, field-of-regard, and tracking-rate checks are "
        "not enforced until resolved terminal-physics inputs are wired"
    )


def _read_runtime_run_id_file(path: Path) -> str:
    """Read the operator-owned runtime lineage sidecar."""
    return read_runtime_session_run_id_file(path)


def _effective_ground_scheduling_for_runtime(
    ground_scheduling: GroundSchedulingConfig,
) -> GroundSchedulingConfig:
    """Return the session-root ground scheduling defaults used by OME.

    MBB/BBM capability is resolved per ground station. A single-terminal station
    is BBM even when the session default is MBB; a multi-terminal station can
    still explicitly choose BBM. This helper only keeps the fail-loud global guard
    for future multi-overlap MBB values that the current allocator cannot honor.
    """
    # BIG HONESTY NOTE / MBB-002:
    # The runtime must not accept a reserve value that the allocator cannot honor.
    # `mbb_reserve > 1` means multi-overlap MBB; today we only support one overlap
    # per GS. Remove this guard only when MBB-002 adds multi-overlap allocator state
    # and proves it.
    if ground_scheduling.mbb_reserve > 1:
        raise RuntimeError(
            "scheduling.ground.mbb_reserve > 1 requires future MBB-002 multi-overlap "
            "allocator support; current OME supports at most one concurrent MBB "
            "overlap per ground station"
        )
    return ground_scheduling


def _validate_sgp4_tle_freshness(cfg: _SessionBundle, epoch_unix: float) -> None:
    """Fail before dispatch if a selected SGP4 source violates its age budget."""
    if cfg.propagator_id != "sgp4-tle" and not any(
        getattr(sat, "propagator_id", None) == "sgp4-tle" for sat in cfg.satellites
    ):
        return

    raise ValueError("catalog OME SGP4/TLE runtime inputs are not implemented")


def _authority_snapshot_interval_s(
    *,
    platform_snapshot_interval_s: float,
    max_latency_age_ticks: int,
    step_seconds: int,
) -> float:
    """Bound authoritative snapshots by the Scheduler freshness contract.

    The Scheduler refuses to actuate stale OME geometry. OME therefore cannot
    publish full-state authority less frequently than the configured maximum
    geometry age and still claim active-link latency freshness.
    """
    if platform_snapshot_interval_s <= 0:
        raise ValueError("platform OME link-state snapshot interval must be > 0")
    max_authority_age_s = max_latency_age_ticks * step_seconds
    if max_authority_age_s <= 0:
        raise ValueError("dispatch.max_latency_age_ticks * time.step_seconds must be > 0")
    return min(platform_snapshot_interval_s, float(max_authority_age_s))


# ---------------------------------------------------------------------------
# Shared playback state (Pacemaker role)
# ---------------------------------------------------------------------------
# Mutated by the NATS publisher thread (async subscriber callback) and
# read by the pacing thread each tick.  Python GIL guarantees atomic
# reads/writes on single scalar values (float, bool).  No lock needed.

_time_accel: float = 1.0  # current d(sim)/d(wall) multiplier
_paused: bool = False  # emission halted?
_seek_target: float | None = None  # Unix timestamp to seek to, or None
_epoch_id: int = 0  # current epoch, incremented on each Tier 2 seek
_seeking: bool = False  # True while seek in progress — mutex for pause/set_speed
_initial_epoch_committed: bool = (
    False  # playback controls other than status wait for step-0 authority
)


def _playback_control_state() -> str:
    if not _initial_epoch_committed:
        return "bootstrapping"
    if _seeking:
        return "seeking"
    return "paused" if _paused else "playing"


async def _handle_playback_control_command(
    cmd: PlaybackControlCommand,
    publish_playback_state,
) -> dict:
    """Apply one playback-control command and return the API reply.

    Playback control owns operator intent only. The out-of-band
    ``PlaybackState(seeking)`` publish is deliberate: it is the epoch-boundary
    signal that makes Scheduler consumers suspend before the pacing thread
    publishes the committed step-0 authority. The pacing thread, not this
    helper, publishes ``PlaybackState(playing)`` after the new snapshot,
    decision snapshot, and checkpoint have committed.
    """
    global _time_accel, _paused, _seeking, _seek_target, _epoch_id

    action = cmd.action
    if not _initial_epoch_committed and action in (
        "pause",
        "resume",
        "set_speed",
        "seek",
    ):
        return {
            "error": "session bootstrapping; retry after ready",
            "state": "bootstrapping",
            "paused": _paused,
            "speed": _time_accel,
            "epoch_id": _epoch_id,
        }

    if _seeking and action in ("pause", "resume", "set_speed"):
        return {
            "error": f"cannot {action} during seek (epoch_id={_epoch_id})",
            "state": "seeking",
            "paused": _paused,
            "speed": _time_accel,
            "epoch_id": _epoch_id,
        }

    if action == "pause":
        _paused = True
        await publish_playback_state("paused")
        logging.info("Playback paused (speed=%.1f)", _time_accel)
    elif action == "resume":
        _paused = False
        await publish_playback_state("playing")
        logging.info("Playback resumed (speed=%.1f)", _time_accel)
    elif action == "set_speed":
        if cmd.factor is None:
            raise ValueError("set_speed requires factor")
        factor = float(cmd.factor)
        if factor < MIN_TIME_ACCEL or factor > MAX_TIME_ACCEL:
            return {
                "error": f"factor {factor} out of range [{MIN_TIME_ACCEL}, {MAX_TIME_ACCEL}]",
                "paused": _paused,
                "speed": _time_accel,
            }
        _time_accel = factor
        logging.info("Playback speed set to %.1fx", factor)
    elif action == "seek":
        _epoch_id += 1
        _seeking = True
        if cmd.target_sim_time:
            _seek_target = cmd.target_sim_time.timestamp()
        else:
            _seek_target = datetime.now(UTC).timestamp()
        _paused = False
        await publish_playback_state("seeking")
        target_iso = datetime.fromtimestamp(_seek_target, UTC).isoformat()
        logging.info("Seek requested: %s epoch_id=%d (auto-resumed)", target_iso, _epoch_id)
    elif action == "get_status":
        pass
    else:
        return {
            "error": f"unknown action: {action}",
            "paused": _paused,
            "speed": _time_accel,
        }

    return {
        "paused": _paused,
        "speed": _time_accel,
        "epoch_id": _epoch_id,
        "state": _playback_control_state(),
    }


def run(session_path: str, output_dir: str | None = None, *, run_id: str) -> Path:
    """Run the OME pipeline (single window, batch mode) and return the output path."""
    cfg = _load_session_config(session_path, run_id=run_id)
    _enforce_ground_link_model_contract(cfg.ground_link_model)
    epoch_unix = resolve_session_epoch(cfg.resolved.time)
    _validate_sgp4_tle_freshness(cfg, epoch_unix)
    effective_ground_scheduling = _effective_ground_scheduling_for_runtime(cfg.ground_scheduling)
    events = precompute_timeline(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        epoch_unix=epoch_unix,
        duration_s=cfg.period,
        propagator_id=cfg.propagator_id,
        step_seconds=int(cfg.resolved.time.step_seconds),
        ground_scheduling=effective_ground_scheduling,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_link_model=cfg.ground_link_model,
        ground_candidate_satellites_by_gs=cfg.ground_candidate_satellites_by_gs,
        body_frames=cfg.body_frames,
        body_ephemeris=cfg.body_ephemeris,
        active_bodies=cfg.active_bodies,
    )

    out_dir = Path(output_dir) if output_dir else Path("output")
    out_path = out_dir / f"{cfg.resolved.session.name}-timeline.jsonl"
    write_timeline_jsonl(events, out_path)

    logging.info(
        "OME complete: %d events, %d satellites, period=%.0fs",
        len(events),
        len(cfg.satellites),
        cfg.period,
    )
    return out_path


def _start_health_server(port: int = 8081) -> None:
    """Minimal HTTP health endpoint for K8s readiness/liveness probe.

    Temporary scaffolding — in the end state, health/metrics/observability
    will be a sidecar container, not application code. This function is
    isolated and called from one place so it can be trivially removed
    when the sidecar pattern is adopted.
    """
    import contextlib
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.debug("Health server listening on :%d", port)


# ---------------------------------------------------------------------------
# Producer-consumer architecture: pacing thread + NATS publisher thread
# ---------------------------------------------------------------------------


async def _nats_publisher_loop(
    event_queue,
    shutdown_event,
    session_id: str,
    ready_event=None,
) -> None:
    """NATS publisher — runs in its own async event loop in its own thread.

    Consumes (subject, payload) tuples from the queue and publishes to NATS.
    Handles HeartbeatTick via the queue (pacing thread sends them during
    window computation). Handles reconnection transparently via nats-py.

    Also subscribes to SUBJECT_PLAYBACK_CONTROL for runtime pause/resume/
    set_speed commands. The subscriber callback
    mutates module-level _time_accel and _paused; the pacing thread reads
    them each tick.
    """
    import asyncio
    import json
    import queue

    import nats
    from nodalarc.models.events import PlaybackState
    from nodalarc.nats_channels import (
        NATS_CONNECT_OPTIONS,
        SUBJECT_PLAYBACK_CONTROL,
        nats_url,
        playback_state_subject,
        replay_anchor_subject,
        scheduling_checkpoint_subject,
    )
    from nodalarc.scheduling_checkpoint import (
        encode_retained_replay_anchor,
        encode_retained_scheduling_checkpoint,
    )

    if not session_id:
        logging.error("FATAL: OME NATS publisher started with no session_id")
        raise ValueError("session_id is required for OME NATS publisher")

    _subj_playback = playback_state_subject(session_id)
    subj_checkpoint_retained = scheduling_checkpoint_subject(session_id)
    subj_anchor_retained = replay_anchor_subject(session_id)

    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    js = nc.jetstream()
    await _connect_logging(nc)
    logging.debug("OME NATS publisher connected to %s (session_id=%s)", nats_url(), session_id)

    async def _publish_playback_state(state: str) -> None:
        """Publish PlaybackState to NODALARC_SESSION stream."""
        ps = PlaybackState(epoch_id=_epoch_id, state=state)
        await js.publish(_subj_playback, ps.model_dump_json().encode())
        logging.debug("PlaybackState published: state=%s epoch_id=%d", state, _epoch_id)

    # --- Playback control subscriber ---

    async def _handle_playback(msg) -> None:
        try:
            cmd = PlaybackControlCommand.model_validate_json(msg.data)
            reply = await _handle_playback_control_command(cmd, _publish_playback_state)
            await msg.respond(json.dumps(reply).encode())
        except Exception as exc:
            logging.error("Playback control error: %s", exc)
            await msg.respond(json.dumps({"error": str(exc)}).encode())

    await nc.subscribe(SUBJECT_PLAYBACK_CONTROL, cb=_handle_playback)
    logging.debug("OME playback control active on %s", SUBJECT_PLAYBACK_CONTROL)
    if ready_event is not None:
        ready_event.set()

    # Per-message retry: exponential backoff 0.5s, 1s, 2s, 4s, 8s = 15.5s total.
    # Rationale: pacing thread produces at 1Hz (step_seconds=1). The queue
    # (maxsize=1000) absorbs ~16 minutes of events, so a 15s retry window
    # doesn't cause backpressure. If NATS can't accept a single publish in
    # 5 attempts over 15s, the connection is dead — not a transient hiccup.
    # These numbers are initial estimates and will need tuning against the
    # operational system under load.
    _MAX_RETRIES = 5
    _BACKOFF_BASE_S = 0.5

    try:
        while not shutdown_event.is_set():
            try:
                item = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: event_queue.get(timeout=0.1)
                )
            except queue.Empty:
                continue

            if item is None:  # shutdown sentinel
                break

            subject, payload = item
            if hasattr(payload, "build"):
                # Deferred wire materialization: the pacer committed the
                # tick's typed inputs; the conversion to wire models runs
                # here, in its sleep window. A build failure kills this
                # loop loudly and the pacer exits on the full queue.
                payload = payload.build()
            if not isinstance(payload, (bytes, bytearray)):
                # Ownership transfer from the pacing thread: frozen wire
                # models serialize here, in the pacer's sleep window, so
                # serialization never rides the tick's critical path.
                # Pacemaker facts (sim/wall stamps, snapshot_seq) were
                # captured at construction. A serialization failure kills
                # this loop loudly; the pacer then exits on a full queue.
                if subject == subj_checkpoint_retained:
                    payload = encode_retained_scheduling_checkpoint(payload)
                elif subject == subj_anchor_retained:
                    payload = encode_retained_replay_anchor(payload)
                else:
                    payload = payload.model_dump_json().encode()
            published = False
            for attempt in range(_MAX_RETRIES):
                try:
                    await js.publish(subject, payload)
                    published = True
                    break
                except Exception as exc:
                    backoff = _BACKOFF_BASE_S * (2**attempt)
                    logging.warning(
                        "JetStream publish failed (attempt %d/%d) subject=%s: %s — retrying in %.1fs",
                        attempt + 1,
                        _MAX_RETRIES,
                        subject,
                        exc,
                        backoff,
                    )
                    # This is publisher-loop retry backoff, not pacing. The
                    # wall-clock pacer never awaits and never depends on this
                    # event loop for tick timing.
                    await asyncio.sleep(backoff)

            if not published:
                logging.error(
                    "FATAL: JetStream publish failed after %d consecutive attempts "
                    "subject=%s — NATS connection is dead, shutting down",
                    _MAX_RETRIES,
                    subject,
                )
                shutdown_event.set()
                break
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logging.error("NATS publisher fatal error: %s", exc, exc_info=True)
        shutdown_event.set()
    finally:
        await nc.drain()
        await nc.close()
        logging.debug("NATS publisher stopped")


def _run_pacing(
    session_path,
    output_dir,
    event_queue,
    shutdown_event,
    preloaded_cfg: _SessionBundle | None = None,
    run_id: str | None = None,
) -> None:
    """Pacing loop — synchronous, dedicated thread, wall-clock precise.

    Never awaits. Never yields. Never touches NATS.
    Puts (subject, payload) tuples into the queue.
    Uses time.sleep() for precise wall-clock timing.
    Blocks on queue.put() if queue is full (backpressure from publisher).
    """
    import json as _json
    import queue

    from nodalarc.models.events import (
        CheckpointAssociation,
        ClockTick,
        HeartbeatTick,
        PlaybackState,
        SchedulingCheckpoint,
        TeardownEntry,
    )
    from nodalarc.nats_channels import (
        ground_link_decision_snapshot_subject,
        link_state_snapshot_subject,
        ome_clock_subject,
        ome_heartbeat_subject,
        ome_visibility_subject,
        ops_event_subject,
        playback_state_subject,
        replay_anchor_subject,
        scheduling_checkpoint_subject,
        session_ephemeris_subject,
    )
    from nodalarc.platform_config import get_platform_config

    from ome.event_stream import build_session_ephemeris
    from ome.replay_anchor import DeferredReplayAnchor
    from ome.snapshot_builder import (
        DeferredLinkDecisionSnapshot,
        DeferredLinkStateSnapshot,
    )

    def _build_scheduling_checkpoint(
        sim_time: datetime,
        epoch_id: int,
        snapshot_seq: int,
        step: int,
        associations: dict[tuple[str, str], tuple[int, int]],
        teardowns: MbbTeardownState,
        mbb_overlap_ticks_by_gs: dict[str, int],
    ) -> SchedulingCheckpoint:
        """Convert OME internal association/teardown state to SchedulingCheckpoint."""
        if snapshot_seq <= 0:
            raise ValueError("SchedulingCheckpoint requires a published snapshot_seq")
        if step < 0:
            raise ValueError("SchedulingCheckpoint step must be non-negative")

        ground_station_ids = frozenset(mbb_overlap_ticks_by_gs)
        assoc_flat: dict[str, CheckpointAssociation] = {}
        for pair, (gs_ti, sat_ti) in associations.items():
            gs_id, sat_id = _checkpoint_ground_sat_pair(pair, ground_station_ids)
            assoc_flat[f"{gs_id}:{sat_id}"] = CheckpointAssociation(
                gs_id=gs_id,
                sat_id=sat_id,
                gs_terminal_index=gs_ti,
                sat_terminal_index=sat_ti,
            )

        # Internal teardown state stores the absolute start tick because the
        # allocator needs elapsed simulation ticks. The checkpoint also stores
        # remaining_ticks as an audit value so recovery tests can prove that a
        # no-time-advanced restart preserved MBB overlap semantics exactly.
        td_flat: dict[str, TeardownEntry] = {}
        for pair, teardown in teardowns.items():
            gs_id, sat_id = _checkpoint_ground_sat_pair(pair, ground_station_ids)
            if gs_id not in mbb_overlap_ticks_by_gs:
                raise ValueError(
                    f"Cannot build SchedulingCheckpoint for pending teardown {gs_id}:{sat_id}: "
                    "missing per-ground-station MBB overlap policy"
                )
            remaining_ticks = max(0, mbb_overlap_ticks_by_gs[gs_id] - (step - teardown.start_step))
            td_flat[f"{gs_id}:{sat_id}"] = TeardownEntry(
                start_step=teardown.start_step,
                remaining_ticks=remaining_ticks,
                gs_id=gs_id,
                sat_id=sat_id,
                successor_node_a=teardown.successor_pair[0],
                successor_node_b=teardown.successor_pair[1],
            )

        return SchedulingCheckpoint(
            sim_time=sim_time,
            epoch_id=epoch_id,
            snapshot_seq=snapshot_seq,
            step=step,
            associations=assoc_flat,
            pending_teardowns=td_flat,
            paused=_paused,
            time_accel=_time_accel,
            written_at=time.time(),
            # Closure read, like _paused/_time_accel above: the current
            # epoch anchor (rebound on seek) recorded bit-exactly so
            # recovery replays from identical float inputs.
            epoch_unix=epoch_unix,
        )

    # Health server is started by main() before _run_pacing is called.
    # Infrastructure (health checks, NATS connections, signal handlers)
    # belongs in the process entry point, not the business logic.

    # Use preloaded config if provided (avoids re-parsing the same file).
    # Fall back to loading from disk if not provided (batch mode).
    if preloaded_cfg is not None:
        cfg = preloaded_cfg
    else:
        session_file = Path(session_path)
        while not session_file.is_file():
            logging.debug("Waiting for session config at %s...", session_path)
            time.sleep(5)
        if run_id is None:
            raise RuntimeError("OME pacing requires operator-owned runtime session run-id")
        cfg = _load_session_config(session_path, run_id=run_id)
    session = cfg.resolved
    session_id = cfg.session_id
    epoch_unix = resolve_session_epoch(session.time)
    _validate_sgp4_tle_freshness(cfg, epoch_unix)
    compression = session.time.compression if session.time.compression else 1

    # Build session-scoped NATS subjects
    subj_visibility = ome_visibility_subject(session_id)
    subj_clock = ome_clock_subject(session_id)
    subj_link_snapshot = link_state_snapshot_subject(session_id)
    subj_link_decisions = ground_link_decision_snapshot_subject(session_id)
    subj_ephemeris = session_ephemeris_subject(session_id)
    subj_playback = playback_state_subject(session_id)
    subj_checkpoint = scheduling_checkpoint_subject(session_id)
    subj_mbb_lifecycle = ops_event_subject(
        session_id, "ome", OmeOpsCode.MBB_TEARDOWN_TERMINAL.value
    )
    subj_pacing_telemetry = ops_event_subject(session_id, "ome", OmeOpsCode.PACING_TELEMETRY.value)
    subj_rate_honesty = ops_event_subject(session_id, "ome", "RATE")
    subj_heartbeat = ome_heartbeat_subject(session_id)
    subj_replay_anchor = replay_anchor_subject(session_id)
    ome_hostname = os.environ.get("HOSTNAME") or os.uname().nodename
    logging.debug("OME session_id=%s — NATS subjects scoped", session_id)

    # Initialize Pacemaker rate from static compression.
    # Runtime set_speed commands replace this value dynamically.
    global _time_accel, _seek_target, _seeking, _paused, _epoch_id, _initial_epoch_committed
    _time_accel = float(compression)
    _seek_target = None
    _seeking = False
    _paused = False
    _epoch_id = 0
    _initial_epoch_committed = False
    platform_snapshot_interval_s = get_platform_config().ome_link_state_snapshot_interval_s

    interface_map = cfg.interface_map
    bandwidth_map = cfg.bandwidth_map
    rule_map = cfg.rule_map

    # Optional file output
    out_path = None
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{session.session.name}-timeline.jsonl"

    def _enqueue(subject: str, payload) -> None:
        """Put event on queue: pre-encoded bytes OR a frozen wire model.

        Models serialize on the PUBLISHER thread (ownership transfer):
        the pacing thread stamps Pacemaker facts at construction and
        hands the immutable model off, so serialization cost rides the
        pacer's sleep window instead of the tick's critical path.

        If the queue is full after 10 seconds, the NATS publisher is
        dead — exit the process so K8s restarts us."""
        try:
            event_queue.put((subject, payload), timeout=10)
        except queue.Full:
            logging.error(
                "FATAL: Event queue full for 10s — NATS publisher is not draining. "
                "Exiting so K8s can restart the pod."
            )
            shutdown_event.set()
            raise SystemExit(1) from None

    def _ground_pair_gs_id(pair: tuple[str, str]) -> str:
        if pair[0] in step_ctx.gs_positions:
            return pair[0]
        if pair[1] in step_ctx.gs_positions:
            return pair[1]
        raise ValueError(f"MBB lifecycle pair {pair!r} does not contain a known ground station")

    def _pair_authority(
        pair: tuple[str, str],
        *,
        associations: dict[tuple[str, str], tuple[int, int]],
        teardowns: MbbTeardownState,
        decisions=None,
    ) -> MbbPairAuthority:
        indices = associations.get(pair)
        decision = decisions.get(pair) if decisions is not None else None
        return MbbPairAuthority(
            pair=list(pair),
            scheduled=pair in associations,
            pending_teardown=pair in teardowns,
            visible=decision.visible if decision is not None else None,
            terminal_indices=list(indices) if indices is not None else None,
        )

    def _authority_after(
        lifecycle_event: MbbTeardownLifecycleEvent,
        step_result,
    ) -> dict[str, MbbPairAuthority]:
        return {
            "old_pair": _pair_authority(
                lifecycle_event.old_pair,
                associations=step_result.associations,
                teardowns=step_result.pending_teardowns,
                decisions=step_result.ground_decisions,
            ),
            "successor_pair": _pair_authority(
                lifecycle_event.successor_pair,
                associations=step_result.associations,
                teardowns=step_result.pending_teardowns,
                decisions=step_result.ground_decisions,
            ),
        }

    def _authority_before_from_lifecycle(
        lifecycle_event: MbbTeardownLifecycleEvent,
    ) -> dict[str, MbbPairAuthority]:
        return {
            key: MbbPairAuthority.model_validate(value)
            for key, value in lifecycle_event.authority_before.items()
        }

    def _enqueue_ome_lifecycle_ops_event(details: MbbTeardownLifecycleDetails) -> None:
        event = OpsEvent(
            timestamp=datetime.now(UTC),
            session_id=session_id,
            source="ome",
            hostname=ome_hostname,
            level="info",
            code=OmeOpsCode.MBB_TEARDOWN_TERMINAL.value,
            message=details.message,
            details=details.model_dump(mode="json"),
        )
        _enqueue(subj_mbb_lifecycle, event)

    def _publish_mbb_lifecycle_events(step_result, *, epoch_id: int) -> None:
        for lifecycle_event in step_result.ground_allocation.lifecycle_events:
            details = MbbTeardownLifecycleDetails(
                session_id=session_id,
                epoch_id=epoch_id,
                snapshot_seq=snapshot_seq,
                allocator_step=step_result.step,
                master_sim_time=step_result.sim_time,
                gs_id=lifecycle_event.gs_id,
                teardown_id=lifecycle_event.teardown_id,
                old_pair=list(lifecycle_event.old_pair),
                successor_pair=list(lifecycle_event.successor_pair),
                terminal_outcome=lifecycle_event.category,
                source_allocation_event_category=lifecycle_event.source_allocation_event_category,
                message=lifecycle_event.message,
                authority_before=_authority_before_from_lifecycle(lifecycle_event),
                authority_after=_authority_after(lifecycle_event, step_result),
                ground_policy_audit_ref={"epoch_id": epoch_id, "snapshot_seq": snapshot_seq},
                terminal_indices={
                    key: list(value) for key, value in lifecycle_event.terminal_indices.items()
                },
            )
            _enqueue_ome_lifecycle_ops_event(details)

    def _publish_epoch_invalidation_lifecycle_events(
        *,
        old_epoch_id: int,
        old_step: int,
        old_sim_time: datetime,
        seek_target_sim_time: datetime,
        associations: dict[tuple[str, str], tuple[int, int]],
        teardowns: MbbTeardownState,
    ) -> None:
        for old_pair, teardown in sorted(teardowns.items()):
            successor_pair = teardown.successor_pair
            gs_id = _ground_pair_gs_id(old_pair)
            authority_before = {
                "old_pair": _pair_authority(
                    old_pair, associations=associations, teardowns=teardowns
                ),
                "successor_pair": _pair_authority(
                    successor_pair, associations=associations, teardowns=teardowns
                ),
            }
            message = (
                f"MBB teardown {old_pair!r}->{successor_pair!r} invalidated by seek "
                f"from epoch_id={old_epoch_id} to target={seek_target_sim_time.isoformat()}"
            )
            details = MbbTeardownLifecycleDetails(
                session_id=session_id,
                epoch_id=old_epoch_id,
                snapshot_seq=snapshot_seq if snapshot_seq > 0 else None,
                allocator_step=old_step,
                master_sim_time=old_sim_time,
                gs_id=gs_id,
                teardown_id=(
                    f"{old_pair[0]}:{old_pair[1]}->{successor_pair[0]}:{successor_pair[1]}"
                ),
                old_pair=list(old_pair),
                successor_pair=list(successor_pair),
                terminal_outcome="teardown_invalidated_by_epoch",
                source_allocation_event_category=None,
                message=message,
                authority_before=authority_before,
                authority_after=None,
                seek_target_sim_time=seek_target_sim_time,
                ground_policy_audit_ref={"epoch_id": old_epoch_id, "snapshot_seq": snapshot_seq}
                if snapshot_seq > 0
                else None,
                terminal_indices={
                    key: list(value)
                    for key, value in (
                        ("old_pair", associations.get(old_pair)),
                        ("successor_pair", associations.get(successor_pair)),
                    )
                    if value is not None
                },
            )
            _enqueue_ome_lifecycle_ops_event(details)

    # Build StepContext for per-step computation (Physicist role)
    from ome.event_stream import build_step_context, compute_step
    from ome.ground_visibility_engine import DwellPassState
    from ome.telemetry import (
        SEG_OUTPUT_FILE,
        SEG_PUBLISH_AUTHORITY,
        SEG_PUBLISH_CHECKPOINT,
        SEG_PUBLISH_EVENTS,
        SEG_SLEEP,
        PacingTelemetryWindow,
    )

    effective_ground_scheduling = _effective_ground_scheduling_for_runtime(cfg.ground_scheduling)

    step_ctx = build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        propagator_id=cfg.propagator_id,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_scheduling=effective_ground_scheduling,
        ground_link_model=cfg.ground_link_model,
        ground_defaults_applied=True,
        ground_candidate_satellites_by_gs=cfg.ground_candidate_satellites_by_gs,
        node_metadata=cfg.node_metadata,
        body_frames=cfg.body_frames,
        body_ephemeris=cfg.body_ephemeris,
        active_bodies=cfg.active_bodies,
    )

    step_seconds = int(session.time.step_seconds)
    anchor_interval_ticks = max(1, int(300 / step_seconds))
    snapshot_interval_s = _authority_snapshot_interval_s(
        platform_snapshot_interval_s=platform_snapshot_interval_s,
        max_latency_age_ticks=session.dispatch.max_latency_age_ticks,
        step_seconds=step_seconds,
    )
    if snapshot_interval_s < platform_snapshot_interval_s:
        logging.info(
            "Tightening LinkStateSnapshot interval from %.3fs to %.3fs to satisfy "
            "dispatch.max_latency_age_ticks=%d",
            platform_snapshot_interval_s,
            snapshot_interval_s,
            session.dispatch.max_latency_age_ticks,
        )

    isl_state: dict[tuple[str, str], tuple[bool, bool]] = {}
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]] = {}
    dwell_state: dict[tuple[str, str], DwellPassState] = {}
    current_associations: dict[tuple[str, str], tuple[int, int]] = {}
    mbb_pending_teardowns: MbbTeardownState = {}
    step = 0
    snapshot_seq = 0
    last_snapshot_sim_s: float = -snapshot_interval_s  # force immediate on first step
    force_first_snapshot = True

    # Reference-point pacing model.
    # Resets on rate change, unpause, or seek to avoid drift.
    pace_ref_wall = time.monotonic()
    pace_ref_step = 0
    current_rate = _time_accel

    # Per-segment pacing telemetry: every tick's wall
    # time is attributed by segment (physics inside compute_step, publish
    # work here, sleep-vs-overrun against the pacing schedule); aggregated
    # stats are logged at INFO and published as a typed ops event so "where
    # does the time go" is answered by production data.
    telemetry_window = PacingTelemetryWindow()
    last_telemetry_emit = time.monotonic()
    _TELEMETRY_EMIT_INTERVAL_S = 60.0

    # Rate-honesty state machine: the Pacemaker judges its own
    # delivery. Degraded when the measured rate sustainedly falls below the
    # commanded rate; recovered with hysteresis so boundary noise cannot
    # flap the alarm.
    _pacing_degraded = False
    _DEGRADED_BELOW = 0.90  # enter: achieved < 90% of commanded
    _RECOVERED_ABOVE = 0.97  # leave: achieved >= 97% of commanded

    def _update_rate_honesty() -> None:
        nonlocal _pacing_degraded
        achieved = telemetry_window.achieved_ratio(step_seconds=float(step_seconds))
        if achieved is None or current_rate <= 0:
            return
        ratio = achieved / current_rate
        if not _pacing_degraded and ratio < _DEGRADED_BELOW:
            _pacing_degraded = True
            _emit_rate_transition(OmeOpsCode.RATE_DEGRADED, "warning", achieved)
        elif _pacing_degraded and ratio >= _RECOVERED_ABOVE:
            _pacing_degraded = False
            _emit_rate_transition(OmeOpsCode.RATE_RECOVERED, "info", achieved)

    def _emit_rate_transition(code: OmeOpsCode, level: str, achieved: float) -> None:
        stats = telemetry_window.snapshot(
            step_seconds=float(step_seconds), requested_ratio=current_rate
        )
        message = (
            f"OME pacing {'degraded' if code is OmeOpsCode.RATE_DEGRADED else 'recovered'}: "
            f"commanded {current_rate:g}x, delivering {achieved:.2f}x"
        )
        if level == "warning":
            logging.warning(message)
        else:
            logging.info(message)
        event = OpsEvent(
            timestamp=datetime.now(UTC),
            session_id=session_id,
            source="ome",
            hostname=ome_hostname,
            level=level,
            code=code.value,
            message=message,
            details=stats.model_dump(mode="json") if stats else None,
        )
        _enqueue(subj_rate_honesty, event)

    def _emit_pacing_telemetry() -> None:
        stats = telemetry_window.snapshot(
            step_seconds=float(step_seconds), requested_ratio=current_rate
        )
        if stats is None:
            return
        logging.info(
            "OME pacing [%s]: compute p50=%.1fms p95=%.1fms budget=%.1fms "
            "requested=%.1fx achieved=%.1fx overruns=%d/%d segments_p50=%s "
            "segments_p95=%s",
            session_id,
            stats.compute_p50_ms,
            stats.compute_p95_ms,
            stats.budget_ms_per_tick,
            stats.requested_ratio,
            stats.achieved_ratio,
            stats.overrun_ticks,
            stats.window_ticks,
            stats.segments_p50_ms,
            # The tail's attribution, not only the median's: a p95 miss
            # was once attributed to the wrong segment because this line
            # hid the per-segment tails.
            stats.segments_p95_ms,
        )
        event = OpsEvent(
            timestamp=datetime.now(UTC),
            session_id=session_id,
            source="ome",
            hostname=ome_hostname,
            level="info",
            code=OmeOpsCode.PACING_TELEMETRY.value,
            message=(
                f"OME pacing: compute p50 {stats.compute_p50_ms}ms vs budget "
                f"{stats.budget_ms_per_tick}ms; requested {stats.requested_ratio}x, "
                f"achieved {stats.achieved_ratio}x"
            ),
            details=stats.model_dump(mode="json"),
        )
        _enqueue(subj_pacing_telemetry, event)

    def _publish_link_authority(step_result, *, epoch_id: int) -> None:
        """Publish paired authoritative state/decision snapshots for a committed tick."""
        nonlocal snapshot_seq, last_snapshot_sim_s, force_first_snapshot
        snapshot_seq += 1
        # Wire materialization is deferred to the publisher thread: the
        # measured authority-tick cost was the dataclass-to-pydantic
        # conversion itself (asdict + construction of ~hundreds of
        # decisions), not validation or serialization alone. Pacemaker
        # facts — sim_time, snapshot_seq, epoch_id — are stamped HERE,
        # at commit, on the pacing thread.
        _enqueue(
            subj_link_snapshot,
            DeferredLinkStateSnapshot(
                source=step_result.link_snapshot_source,
                interface_map=interface_map,
                bandwidth_map=bandwidth_map,
                rule_map=rule_map,
                sim_time=step_result.sim_time,
                seq=snapshot_seq,
                interval_s=snapshot_interval_s,
                fixed_positions=step_ctx.gs_positions,
                epoch_id=epoch_id,
                mbb_overlap_ticks_by_gs=step_ctx.gs_mbb_overlap_ticks,
                current_step=step_result.step,
            ),
        )
        _enqueue(
            subj_link_decisions,
            DeferredLinkDecisionSnapshot(
                decisions=step_result.ground_decisions,
                unscheduled_pairs=step_result.ground_allocation.unscheduled_pairs,
                policy_audit=step_result.ground_allocation.policy_audit,
                allocation_events=step_result.ground_allocation.allocation_events,
                sim_time=step_result.sim_time,
                snapshot_seq=snapshot_seq,
                epoch_id=epoch_id,
            ),
        )
        last_snapshot_sim_s = step_result.step * step_seconds
        force_first_snapshot = False

    def _publish_checkpoint(step_result, *, epoch_id: int) -> None:
        ckpt = _build_scheduling_checkpoint(
            sim_time=step_result.sim_time,
            epoch_id=epoch_id,
            snapshot_seq=snapshot_seq,
            step=step_result.step,
            associations=step_result.associations,
            teardowns=step_result.pending_teardowns,
            mbb_overlap_ticks_by_gs=step_ctx.gs_mbb_overlap_ticks,
        )
        _enqueue(subj_checkpoint, ckpt)

    def _publish_replay_anchor(step_result, *, epoch_id: int) -> None:
        """Commit a bounded-replay anchor: copies of the replay-carried
        state frozen at this tick; the publisher materializes and
        serializes it off the pacing thread."""
        _enqueue(
            subj_replay_anchor,
            DeferredReplayAnchor(
                epoch_id=epoch_id,
                step=step_result.step,
                isl_state=dict(isl_state),
                gs_state=dict(gs_state),
                associations=step_result.associations,
                teardowns=step_result.pending_teardowns,
                ground_station_ids=frozenset(step_ctx.gs_mbb_overlap_ticks),
                written_at=time.time(),
            ),
        )

    def _publish_heartbeat() -> None:
        hb = HeartbeatTick(wall_time=datetime.now(UTC), status="paused")
        _enqueue(subj_heartbeat, hb)

    def _publish_clock_tick(step_result, rate: float, *, epoch_id: int) -> None:
        ct = ClockTick(
            sim_time=step_result.sim_time,
            wall_time=datetime.now(UTC),
            compression_ratio=float(rate),
            epoch_id=epoch_id,
            achieved_ratio=telemetry_window.achieved_ratio(step_seconds=float(step_seconds)),
            pacing_degraded=_pacing_degraded,
        )
        _enqueue(subj_clock, ct)

    def _sleep_until_next_step_due() -> None:
        if _paused or shutdown_event.is_set():
            return
        wall_target = pace_ref_wall + (step - pace_ref_step) * (step_seconds / current_rate)
        now_mono = time.monotonic()
        if now_mono < wall_target:
            time.sleep(wall_target - now_mono)

    logging.info(
        "OME starting [build=%s, session_id=%s, sat_count=%d, gs_count=%d, epoch=%s, step=%ds, accel=%.1fx]",
        os.environ.get("NODAL_BUILD", "dev"),
        session_id,
        len(cfg.satellites),
        len(cfg.gs_file.stations) if cfg.gs_file else 0,
        datetime.fromtimestamp(epoch_unix, UTC).isoformat(),
        step_seconds,
        current_rate,
    )

    # --- Checkpoint recovery (warm restart) ---
    # Try to read the retained SchedulingCheckpoint from JetStream.
    # If a checkpoint exists, it is the session-lineage authority for epoch,
    # snapshot sequence, simulation step, and playback state. We do not ignore
    # malformed retained state because that can make the event stream look
    # healthy while silently regressing simulation state. A valid checkpoint
    # remains authoritative across wall-clock gaps; recovery resumes from it
    # instead of inventing elapsed sim_time while the service was down.
    recovered_checkpoint = None
    recovered_anchor = None
    try:
        import asyncio as _aio

        async def _read_checkpoint():
            import nats as _nats
            from nodalarc.nats_channels import (
                NATS_CONNECT_OPTIONS as _OPTS,
            )
            from nodalarc.nats_channels import (
                STREAM_SESSION_EVENTS as _STREAM,
            )
            from nodalarc.nats_channels import (
                nats_url as _nats_url,
            )

            _nc = await _nats.connect(_nats_url(), **_OPTS)
            try:
                _js = _nc.jetstream()
                from nats.js.api import DeliverPolicy
                from nodalarc.scheduling_checkpoint import (
                    decode_retained_replay_anchor,
                    decode_retained_scheduling_checkpoint,
                )

                async def _last_retained(subject: str) -> bytes | None:
                    sub = await _js.subscribe(
                        subject,
                        stream=_STREAM,
                        ordered_consumer=True,
                        deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
                    )
                    try:
                        msg = await sub.next_msg(timeout=2.0)
                        return msg.data
                    except TimeoutError:
                        return None
                    finally:
                        await sub.unsubscribe()

                ckpt_bytes = await _last_retained(subj_checkpoint)
                if ckpt_bytes is None:
                    return None, None
                anchor_bytes = await _last_retained(subj_replay_anchor)
                anchor = (
                    decode_retained_replay_anchor(anchor_bytes)
                    if anchor_bytes is not None
                    else None
                )
                return decode_retained_scheduling_checkpoint(ckpt_bytes), anchor
            finally:
                await _nc.close()

        recovered_checkpoint, recovered_anchor = _aio.run(_read_checkpoint())
    except Exception as exc:
        raise RuntimeError(
            "OME checkpoint recovery failed; refusing to start from unknown state"
        ) from exc

    if recovered_checkpoint:
        checkpoint_age = _validate_recovered_checkpoint(
            recovered_checkpoint,
            now_wall_s=time.time(),
        )
        step = recovered_checkpoint.step
        snapshot_seq = recovered_checkpoint.snapshot_seq
        _epoch_id = recovered_checkpoint.epoch_id
        # Keep epoch_unix as the original epoch anchor. The checkpoint stores
        # the tick sim_time, so using it directly as epoch_unix would make the
        # next live tick jump by step * step_seconds. Prefer the bit-exact
        # anchor; checkpoints written before that field existed force the
        # microsecond-quantized reconstruction, which is only exact for
        # whole-second epochs.
        if recovered_checkpoint.epoch_unix is not None:
            epoch_unix = recovered_checkpoint.epoch_unix
        else:
            epoch_unix = recovered_checkpoint.sim_time.timestamp() - (step * step_seconds)
            logging.warning(
                "Recovered checkpoint predates the exact epoch anchor; "
                "reconstructed epoch_unix=%s from microsecond-quantized "
                "sim_time - replay determinism holds only for whole-second "
                "epochs from this lineage",
                epoch_unix,
            )
        logging.info(
            "Recovered from checkpoint (age=%.1fs, step=%d, seq=%d, sim=%s)",
            checkpoint_age,
            step,
            snapshot_seq,
            recovered_checkpoint.sim_time.isoformat(),
        )
        if checkpoint_age > 30.0:
            logging.warning(
                "Recovered checkpoint after extended wall-clock gap "
                "(age=%.1fs, step=%d, seq=%d, sim=%s); simulation resumes from "
                "the retained checkpoint instead of inventing elapsed sim_time",
                checkpoint_age,
                step,
                snapshot_seq,
                recovered_checkpoint.sim_time.isoformat(),
                extra={
                    "code": "CHECKPOINT_RECOVERY_DELAY",
                    "details": {
                        "age_s": checkpoint_age,
                        "step": step,
                        "snapshot_seq": snapshot_seq,
                        "sim_time": recovered_checkpoint.sim_time.isoformat(),
                    },
                },
            )
    else:
        logging.info("No checkpoint found — starting from epoch")

    # Compute committed state before publishing any authoritative snapshot.
    # Fresh start commits step 0. Warm restart replays to the retained checkpoint
    # step and publishes the final replay StepResult. In both cases the snapshot
    # is serialized from StepResult authority, not from replayed VisibilityEvents.
    initial_step_result = None
    if recovered_checkpoint is not None:
        # Bounded replay: a valid anchor lets recovery rebuild only the
        # gap from the anchor to the checkpoint instead of the session's
        # whole life. Validity is strict — same epoch (a seek bumps the
        # epoch and orphans old anchors) and strictly older than the
        # checkpoint (replaying the anchor's own step against post-step
        # state is not idempotent). Anything else: full replay from
        # zero — slower, never wrong.
        replay_start = 0
        if (
            recovered_anchor is not None
            and recovered_anchor.epoch_id == recovered_checkpoint.epoch_id
            and recovered_anchor.step < recovered_checkpoint.step
        ):
            from ome.replay_anchor import replay_state_from_anchor

            seeded = replay_state_from_anchor(recovered_anchor)
            isl_state.update(seeded[0])
            gs_state.update(seeded[1])
            current_associations = seeded[2]
            mbb_pending_teardowns = seeded[3]
            replay_start = recovered_anchor.step + 1
            logging.info(
                "Bounded replay: anchor at step %d covers %d steps; replaying %d",
                recovered_anchor.step,
                recovered_anchor.step + 1,
                recovered_checkpoint.step - recovered_anchor.step,
                extra={
                    "code": "RECOVERY_REPLAY_BOUNDED",
                    "details": {
                        "anchor_step": recovered_anchor.step,
                        "checkpoint_step": recovered_checkpoint.step,
                    },
                },
            )
        elif recovered_anchor is not None:
            logging.warning(
                "Discarding replay anchor (epoch %d step %d) against checkpoint "
                "(epoch %d step %d); replaying from step zero",
                recovered_anchor.epoch_id,
                recovered_anchor.step,
                recovered_checkpoint.epoch_id,
                recovered_checkpoint.step,
            )
        logging.warning(
            "RecoveryReplay: replaying %d steps from checkpoint (step=%d)",
            step + 1 - replay_start,
            step,
            extra={
                "code": "RECOVERY_REPLAY",
                "details": {"total_steps": step + 1 - replay_start},
            },
        )

        replay_started = time.monotonic()
        for replay_step in range(replay_start, step + 1):
            replay_result = compute_step(
                step_ctx,
                epoch_unix,
                replay_step,
                step_seconds,
                0.0,
                isl_state,
                gs_state,
                current_associations,
                mbb_pending_teardowns,
                dwell_state=dwell_state,
            )
            initial_step_result = replay_result
            current_associations = replay_result.associations
            mbb_pending_teardowns = replay_result.pending_teardowns
            if replay_step > replay_start and (replay_step - replay_start) % 200 == 0:
                elapsed = time.monotonic() - replay_started
                done = replay_step - replay_start
                rate = done / elapsed if elapsed > 0 else 0.0
                eta_s = (step + 1 - replay_step) / rate if rate > 0 else 0.0
                logging.info(
                    "Recovery replay: %d/%d steps (%.1f steps/s, ~%.0fs remaining)",
                    replay_step,
                    step + 1,
                    rate,
                    eta_s,
                )
        logging.debug("Replayed %d steps to rebuild committed link state", step + 1)
    else:
        initial_step_result = compute_step(
            step_ctx,
            epoch_unix,
            0,
            step_seconds,
            0.0,
            isl_state,
            gs_state,
            current_associations,
            mbb_pending_teardowns,
            dwell_state=dwell_state,
        )
        current_associations = initial_step_result.associations
        mbb_pending_teardowns = initial_step_result.pending_teardowns

    if initial_step_result is None:
        raise RuntimeError("Initial epoch commit produced no StepResult; refusing to publish state")

    # Restore playback state from checkpoint. A restart is not a user action —
    # if the session was playing, it resumes playing. If the user had paused,
    # it stays paused. Fresh deployment starts playing (normal UX).
    if recovered_checkpoint is not None:
        _paused = recovered_checkpoint.paused
        _time_accel = recovered_checkpoint.time_accel
        current_rate = _time_accel
    else:
        _paused = False

    # --- Session start sequence (epoch_id=0) ---
    # Order: SessionEphemeris → StepResult-sourced LinkStateSnapshot +
    # GroundLinkDecisionSnapshot → SchedulingCheckpoint → PlaybackState →
    # ClockTick if playing. No pre-compute empty snapshot is ever published.
    eph = build_session_ephemeris(step_ctx, epoch_unix, _epoch_id)
    _enqueue(subj_ephemeris, eph)
    logging.debug("Published SessionEphemeris epoch_id=%d (%d nodes)", _epoch_id, len(eph.nodes))

    initial_epoch_id = _epoch_id
    active_epoch_id = initial_epoch_id
    _publish_link_authority(initial_step_result, epoch_id=initial_epoch_id)
    if recovered_checkpoint is None and initial_step_result.ground_allocation.lifecycle_events:
        _publish_mbb_lifecycle_events(initial_step_result, epoch_id=initial_epoch_id)
    _publish_checkpoint(initial_step_result, epoch_id=initial_epoch_id)

    initial_playback_state = "paused" if _paused else "playing"
    ps = PlaybackState(epoch_id=initial_epoch_id, state=initial_playback_state)
    _enqueue(subj_playback, ps)
    if not _paused:
        _publish_clock_tick(initial_step_result, current_rate, epoch_id=initial_epoch_id)

    _initial_epoch_committed = True

    # Latest committed state for Pacemaker-transition checkpoints. The
    # per-tick checkpoint stream stops with the clock, so pause/resume/
    # rate transitions must commit their own checkpoint or recovery
    # resurrects the pre-transition state (a paused engine restarting
    # into PLAY — found live 2026-06-11).
    last_step_result = initial_step_result
    last_epoch_id = initial_epoch_id

    if recovered_checkpoint is not None:
        logging.info(
            "OME recovered from checkpoint [epoch_id=%d, step=%d, paused=%s, speed=%.1fx]",
            _epoch_id,
            step,
            _paused,
            _time_accel,
        )
    else:
        logging.debug("OME fresh session, auto-play [epoch_id=%d]", _epoch_id)

    step = initial_step_result.step + 1
    pace_ref_wall = time.monotonic()
    pace_ref_step = initial_step_result.step
    _sleep_until_next_step_due()

    try:
        while not shutdown_event.is_set():
            # --- Seek check ---
            seek_to = _seek_target
            if seek_to is not None:
                _seek_target = None
                seek_epoch_id = _epoch_id
                old_epoch_id = active_epoch_id
                old_step = max(0, step - 1)
                old_epoch_unix = epoch_unix
                old_sim_time = datetime.fromtimestamp(old_epoch_unix + old_step * step_seconds, UTC)
                seek_target_sim_time = datetime.fromtimestamp(seek_to, UTC)
                if mbb_pending_teardowns:
                    _publish_epoch_invalidation_lifecycle_events(
                        old_epoch_id=old_epoch_id,
                        old_step=old_step,
                        old_sim_time=old_sim_time,
                        seek_target_sim_time=seek_target_sim_time,
                        associations=current_associations,
                        teardowns=mbb_pending_teardowns,
                    )
                epoch_unix = seek_to
                isl_state = {}
                gs_state = {}
                dwell_state = {}
                current_associations = {}
                mbb_pending_teardowns = {}
                step = 0
                pace_ref_wall = time.monotonic()
                pace_ref_step = 0
                current_rate = _time_accel
                telemetry_window.clear()
                force_first_snapshot = False  # the committed step-0 snapshot is published below

                # Compute first, publish after commit. No empty new-epoch
                # snapshot is allowed.
                try:
                    seek_result = compute_step(
                        step_ctx,
                        epoch_unix,
                        0,
                        step_seconds,
                        0.0,
                        isl_state,
                        gs_state,
                        current_associations,
                        mbb_pending_teardowns,
                        dwell_state=dwell_state,
                    )
                except Exception as exc:
                    target_master_sim_time = datetime.fromtimestamp(epoch_unix, UTC).isoformat()
                    logging.exception(
                        "FATAL: seek step-0 compute failed [epoch_id=%d, target_master_sim_time=%s]",
                        seek_epoch_id,
                        target_master_sim_time,
                        extra={
                            "code": "SEEK_STEP0_COMPUTE_FAILED",
                            "details": {
                                "epoch_id": seek_epoch_id,
                                "target_master_sim_time": target_master_sim_time,
                                "exception_type": type(exc).__name__,
                                "exception": str(exc),
                            },
                        },
                    )
                    raise
                current_associations = seek_result.associations
                mbb_pending_teardowns = seek_result.pending_teardowns

                if _epoch_id != seek_epoch_id or _seek_target is not None:
                    logging.info(
                        "Seek epoch_id=%d abandoned before publish because a newer seek arrived",
                        seek_epoch_id,
                    )
                    continue

                # The publisher thread already emitted PlaybackState(seeking)
                # and incremented _epoch_id. The authoritative state payloads
                # below are the first facts of that epoch. Step-0 VisibilityEvents
                # are intentionally not published; StepResult is the state carrier.
                eph = build_session_ephemeris(step_ctx, epoch_unix, seek_epoch_id)
                _enqueue(subj_ephemeris, eph)
                _publish_link_authority(seek_result, epoch_id=seek_epoch_id)
                if seek_result.ground_allocation.lifecycle_events:
                    _publish_mbb_lifecycle_events(seek_result, epoch_id=seek_epoch_id)
                _publish_checkpoint(seek_result, epoch_id=seek_epoch_id)

                ps = PlaybackState(epoch_id=seek_epoch_id, state="playing")
                _enqueue(subj_playback, ps)
                _publish_clock_tick(seek_result, current_rate, epoch_id=seek_epoch_id)
                last_step_result = seek_result
                last_epoch_id = seek_epoch_id
                if _epoch_id == seek_epoch_id and _seek_target is None:
                    _seeking = False  # Clear seeking mutex only for the committed active seek
                    active_epoch_id = seek_epoch_id

                logging.info(
                    "Seek committed: new epoch %s epoch_id=%d snapshot_seq=%d",
                    datetime.fromtimestamp(seek_to, UTC).isoformat(),
                    seek_epoch_id,
                    snapshot_seq,
                )

                step = 1
                _sleep_until_next_step_due()
                continue

            # --- Pause gate ---
            if _paused:
                # Operator-commanded state must survive a restart: the
                # per-tick checkpoint stream stops with the clock, so the
                # last durable checkpoint predates the pause command and
                # recovery would resurrect a PLAYING engine the operator
                # paused. Commit the transition before going quiet.
                _publish_checkpoint(last_step_result, epoch_id=last_epoch_id)
                # Liveness while the clock is deliberately silent: no
                # ClockTicks flow during pause, so without a heartbeat
                # consumers cannot distinguish a healthy pause from a
                # dead engine — a stale banner on a healthy pause and a
                # healthy display over a dead engine are both false-state
                # displays. The heartbeat carries the engine's own wall
                # stamp so displays can show true sim/wall divergence.
                last_heartbeat_mono = 0.0
                while _paused and not shutdown_event.is_set():
                    if _seek_target is not None:
                        break
                    now_mono = time.monotonic()
                    if now_mono - last_heartbeat_mono >= 1.0:
                        _publish_heartbeat()
                        last_heartbeat_mono = now_mono
                    time.sleep(0.1)
                # Resume is a transition too — durable before the first
                # post-resume tick, or a crash in that window restores a
                # pause the operator already lifted. Seek and shutdown
                # exits commit their own state.
                if not _paused and not shutdown_event.is_set() and _seek_target is None:
                    _publish_checkpoint(last_step_result, epoch_id=last_epoch_id)
                # Reset reference on unpause — time spent paused
                # must not count toward wall-clock budget.
                pace_ref_wall = time.monotonic()
                pace_ref_step = step
                current_rate = _time_accel
                telemetry_window.clear()
                continue  # re-check seek at top

            # --- Rate-change detection ---
            new_rate = _time_accel
            if new_rate != current_rate:
                pace_ref_wall = time.monotonic()
                pace_ref_step = step
                current_rate = new_rate
                telemetry_window.clear()
                # Durable speed: recovery restores time_accel from the
                # checkpoint, which otherwise lags the command by a tick.
                _publish_checkpoint(last_step_result, epoch_id=last_epoch_id)

            # --- Compute one step (Physicist role) ---
            tick_epoch_id = _epoch_id
            step_result = compute_step(
                step_ctx,
                epoch_unix,
                step,
                step_seconds,
                0.0,
                isl_state,
                gs_state,
                current_associations,
                mbb_pending_teardowns,
                dwell_state=dwell_state,
            )
            if _epoch_id != tick_epoch_id or _seek_target is not None:
                logging.info(
                    "Tick step=%d epoch_id=%d abandoned before publish because seek epoch_id=%d is pending",
                    step,
                    tick_epoch_id,
                    _epoch_id,
                )
                continue
            step_events = step_result.events
            current_associations = step_result.associations
            mbb_pending_teardowns = step_result.pending_teardowns

            # --- Emit events for this step ---
            tick_timings = step_result.timings
            with tick_timings.measure(SEG_PUBLISH_EVENTS):
                for te in step_events:
                    if te.event_type == "VisibilityEvent":
                        _enqueue(subj_visibility, te.data)
                # ClockTick with real wall_time (not precomputed placeholder)
                _publish_clock_tick(step_result, current_rate, epoch_id=tick_epoch_id)

            # LinkStateSnapshot at interval, and immediately for terminal MBB
            # lifecycle outcomes so the OpsEvent can reference a same-tick
            # GroundPolicyAudit by (epoch_id, snapshot_seq).
            sim_s = step * step_seconds
            force_lifecycle_snapshot = bool(step_result.ground_allocation.lifecycle_events)
            with tick_timings.measure(SEG_PUBLISH_AUTHORITY):
                if (
                    sim_s - last_snapshot_sim_s >= snapshot_interval_s
                    or force_first_snapshot
                    or force_lifecycle_snapshot
                ):
                    # Companion GroundLinkDecisionSnapshot is published by the same
                    # helper with the same (epoch_id, snapshot_seq, sim_time). Both
                    # snapshots are serialized from committed StepResult state.
                    _publish_link_authority(step_result, epoch_id=tick_epoch_id)

                if step_result.ground_allocation.lifecycle_events:
                    _publish_mbb_lifecycle_events(step_result, epoch_id=tick_epoch_id)

            # Retain the latest authoritative scheduling state every tick, not
            # only at snapshot cadence. Otherwise a crash between snapshots can
            # force recovery to replay stale sim_time and duplicate decisions.
            with tick_timings.measure(SEG_PUBLISH_CHECKPOINT):
                _publish_checkpoint(step_result, epoch_id=tick_epoch_id)
                # Bounded-replay anchor every ~300 sim-seconds: recovery
                # replays only the gap from the newest anchor instead of
                # the session's whole life.
                if step % anchor_interval_ticks == 0:
                    _publish_replay_anchor(step_result, epoch_id=tick_epoch_id)
            last_step_result = step_result
            last_epoch_id = tick_epoch_id

            # Write JSONL if --output-dir provided
            if out_path is not None:
                with tick_timings.measure(SEG_OUTPUT_FILE), open(out_path, "a") as f:
                    for te in step_events:
                        f.write(
                            _json.dumps(
                                {
                                    "timestamp_s": te.timestamp_s,
                                    "event_type": te.event_type,
                                    "data": te.data.model_dump(mode="json"),
                                }
                            )
                            + "\n"
                        )

            # --- Sleep until next step (Pacemaker role) ---
            step += 1
            wall_target = pace_ref_wall + (step - pace_ref_step) * (step_seconds / current_rate)
            now_mono = time.monotonic()
            # Positive = slack slept away; negative = the tick missed its
            # wall target by that much (the saturation signal).
            tick_timings.add(SEG_SLEEP, wall_target - now_mono)
            telemetry_window.record(tick_timings, wall_mark=now_mono)
            _update_rate_honesty()
            if now_mono - last_telemetry_emit >= _TELEMETRY_EMIT_INTERVAL_S:
                _emit_pacing_telemetry()
                last_telemetry_emit = now_mono
            if now_mono < wall_target:
                time.sleep(wall_target - now_mono)

    except Exception:
        logging.exception("FATAL: OME pacing thread crashed")
    finally:
        shutdown_event.set()


def main() -> None:
    """CLI entry point."""
    _configure_logging("nodal.arc.ome", nats_level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Orbital Mechanics Engine")
    parser.add_argument("session", help="Path to session YAML config")
    parser.add_argument(
        "--output-dir", "-o", help="Output directory (optional, enables file output)", default=None
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run in continuous mode (rolling windows + NATS publish)",
    )
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    parser.add_argument(
        "--session-run-id-file",
        default="/etc/nodalarc/session_run_id",
        help="Path to operator-owned runtime session run-id sidecar",
    )
    args = parser.parse_args()

    from nodalarc.platform_config import init_platform_config

    init_platform_config(Path(args.platform_config))

    if not args.continuous:
        run(
            args.session,
            args.output_dir,
            run_id=_read_runtime_run_id_file(Path(args.session_run_id_file)),
        )
        return

    # --- Continuous mode: producer-consumer with two threads ---
    import asyncio
    import queue
    import signal
    import threading

    # Health server must start BEFORE the session config wait.
    # K8s liveness probe hits :8081 immediately — if the health server
    # doesn't start until after config loads, the probe fails and K8s
    # kills the pod before it ever gets the config.
    _start_health_server()

    # Parse session config ONCE — both the publisher and pacing threads
    # need the session_id. The config bundle is passed to _run_pacing
    # to avoid re-parsing the same file.
    session_file = Path(args.session)
    while not session_file.is_file():
        logging.debug("Waiting for session config at %s...", args.session)
        time.sleep(5)
    run_id = _read_runtime_run_id_file(Path(args.session_run_id_file))
    pre_cfg = _load_session_config(args.session, run_id=run_id)
    session_id = pre_cfg.session_id
    from nodal.logging import set_session

    set_session(session_id)
    logging.debug("OME session_id=%s", session_id)

    event_queue: queue.Queue = queue.Queue(maxsize=1000)
    shutdown_event = threading.Event()
    publisher_ready = threading.Event()

    def _signal_handler(signum, frame):
        logging.info("Shutdown signal received (%d)", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Thread 1: NATS publisher — async event loop, consumes from queue
    def _publisher_thread():
        asyncio.run(
            _nats_publisher_loop(
                event_queue,
                shutdown_event,
                session_id,
                ready_event=publisher_ready,
            )
        )

    pub_thread = threading.Thread(target=_publisher_thread, name="nats-publisher", daemon=True)
    pub_thread.start()

    if not publisher_ready.wait(timeout=30.0):
        shutdown_event.set()
        raise RuntimeError(
            "OME NATS publisher did not signal readiness within 30s; "
            "refusing to start pacing without a connected publisher"
        )

    # Thread 2 (main thread): Pacing — synchronous, time.sleep(), produces to queue
    _run_pacing(args.session, args.output_dir, event_queue, shutdown_event, preloaded_cfg=pre_cfg)

    # Shutdown: send sentinel and wait for publisher to drain
    event_queue.put(None)
    pub_thread.join(timeout=10)
    logging.info("OME stopped")


if __name__ == "__main__":
    main()
