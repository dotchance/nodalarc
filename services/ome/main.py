# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""OME entry point — orchestration only, no logic.

Loads configs via YAML + Pydantic, creates AddressingScheme,
computes ISL neighbor assignments (frozen), calls precompute_timeline(),
publishes events on NATS JetStream.
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
from nodalarc.constants import EARTH_RADIUS_KM
from nodalarc.constellation_loader import SatelliteNode
from nodalarc.ground_terminals import station_ground_terminal_capacity
from nodalarc.link_metadata import build_link_metadata_maps
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.events import OpsEvent, PlaybackControlCommand
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.ome_lifecycle import (
    MbbPairAuthority,
    MbbTeardownLifecycleDetails,
    OmeOpsCode,
)
from nodalarc.models.session import GroundSchedulingConfig, SessionConfig, resolve_session_epoch
from nodalarc.nats_channels import MAX_TIME_ACCEL, MIN_TIME_ACCEL
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.session_identity import require_session_run_id
from nodalarc.tle import tle_age_days

from ome.event_stream import (
    precompute_timeline,
    write_timeline_jsonl,
)
from ome.propagator import orbital_period
from ome.types import MbbTeardownLifecycleEvent, MbbTeardownState

if TYPE_CHECKING:
    from ome.event_stream import StepContext, TimelineWindowResult


class _SessionBundle(NamedTuple):
    """All session-derived config needed by the OME pacing loop."""

    session: SessionConfig
    constellation_config: ConstellationConfig
    gs_file: GroundStationFile
    satellites: list[SatelliteNode]
    period: float
    addressing: AddressingScheme
    neighbors: frozenset
    polar_seam_enabled: bool
    latitude_threshold_deg: float
    propagator_id: str
    interface_map: dict[tuple[str, str], tuple[str, str]]
    bandwidth_map: dict[tuple[str, str], float]


def _load_session_config(session_path: str | Path) -> _SessionBundle:
    """Load and validate all session config. Pure — no side effects."""
    from nodalarc.models.constellation import ParametricConstellation, TLEConstellation

    resolution = load_session_resolution_from_file(session_path, origin="ome")
    session = resolution.runtime_session
    constellation_config = resolution.primary_constellation.config
    gs_file = resolution.primary_ground_set.config
    satellites = list(resolution.primary_constellation.satellites)
    if not satellites:
        raise ValueError("No satellites in constellation")
    if session.orbit.propagator == "sgp4-tle" and not isinstance(
        constellation_config, TLEConstellation
    ):
        raise ValueError(
            "orbit.propagator='sgp4-tle' requires constellation.mode='tle'; "
            "OME will not approximate a non-TLE source as SGP4"
        )
    if (
        isinstance(constellation_config, TLEConstellation)
        and session.orbit.propagator != "sgp4-tle"
    ):
        raise ValueError(
            "constellation.mode='tle' requires orbit.propagator='sgp4-tle'; "
            "OME will not downgrade TLEs into circular elements"
        )

    first_alt = satellites[0].elements.semi_major_axis_km - EARTH_RADIUS_KM
    period = orbital_period(first_alt)
    addressing = resolution.addressing
    neighbors = assign_isl_neighbors(constellation_config, addressing)

    polar_seam_enabled = False
    latitude_threshold_deg = 70.0

    if (
        isinstance(constellation_config, ParametricConstellation)
        and constellation_config.polar_seam
    ):
        polar_seam_enabled = constellation_config.polar_seam.enabled
        latitude_threshold_deg = constellation_config.polar_seam.latitude_threshold_deg

    metadata = build_link_metadata_maps(session, addressing)

    return _SessionBundle(
        session=session,
        constellation_config=constellation_config,
        gs_file=gs_file,
        satellites=satellites,
        period=period,
        addressing=addressing,
        neighbors=neighbors,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        propagator_id=session.orbit.propagator,
        interface_map=metadata.interface_map,
        bandwidth_map=metadata.bandwidth_map,
    )


def _enforce_ground_link_model_contract(session: SessionConfig) -> None:
    """Fail before OME run if geometry-only physics is not explicitly acknowledged."""
    if session.simulation.ground_link_model != "geometry_only":
        return
    if not session.simulation.acknowledge_geometry_only:
        raise ValueError(
            "simulation.ground_link_model=geometry_only requires "
            "simulation.acknowledge_geometry_only: true. Ground links would "
            "otherwise run without range, field-of-regard, or tracking-rate checks."
        )
    logging.warning(
        "simulation.ground_link_model=geometry_only: ground links use LOS/elevation only; "
        "range, field-of-regard, and tracking-rate constraints are not enforced"
    )


def _mbb_capacity_shortfalls(
    ground_scheduling: GroundSchedulingConfig,
    gs_file: GroundStationFile | None,
) -> list[str]:
    if gs_file is None or ground_scheduling.handover_mode != "mbb":
        return []
    required_capacity = ground_scheduling.mbb_reserve + 1
    shortfalls: list[str] = []
    for station in gs_file.stations:
        capacity = station_ground_terminal_capacity(gs_file, station)
        if capacity < required_capacity:
            shortfalls.append(
                f"gs-{station.name}(capacity={capacity}, required>={required_capacity})"
            )
    return shortfalls


def _effective_ground_scheduling_for_runtime(
    session: SessionConfig,
    gs_file: GroundStationFile | None,
) -> GroundSchedulingConfig:
    """Return the OME runtime ground scheduling policy after explicit acknowledgements.

    Physical MBB on insufficient terminal capacity is impossible. If the operator
    explicitly acknowledges a BBM gap, OME runs BBM as the effective runtime mode
    and surfaces the degraded behavior; otherwise startup fails before publishing
    false authority.
    """
    ground = session.scheduling.ground
    # BIG HONESTY NOTE / MBB-002:
    # The runtime must not "helpfully" accept a reserve value that the allocator
    # cannot honor. `mbb_reserve > 1` means multi-overlap MBB; today we only
    # support one overlap per GS. Schema validation normally catches this, but OME
    # keeps a runtime guard because SessionConfig can be mutated in tests/tools.
    if ground.mbb_reserve > 1:
        raise RuntimeError(
            "scheduling.ground.mbb_reserve > 1 requires future MBB-002 multi-overlap "
            "allocator support; current OME supports at most one concurrent MBB "
            "overlap per ground station"
        )
    shortfalls = _mbb_capacity_shortfalls(ground, gs_file)
    if not shortfalls:
        return ground
    if not session.simulation.acknowledge_bbm_handover_gap:
        raise RuntimeError(
            "MBB handover requested but these ground stations do not have enough "
            "terminal capacity for physical overlap: "
            + ", ".join(shortfalls)
            + ". Refusing to degrade silently; fix the model, select "
            "scheduling.ground.handover_mode='bbm', or explicitly set "
            "simulation.acknowledge_bbm_handover_gap: true."
        )
    logging.warning(
        "MBB handover requested but physical overlap is impossible for %s; "
        "simulation.acknowledge_bbm_handover_gap=true, running effective BBM/degraded mode",
        ", ".join(shortfalls),
    )
    return ground.model_copy(update={"handover_mode": "bbm", "mbb_reserve": 0})


def _validate_sgp4_tle_freshness(cfg: _SessionBundle, epoch_unix: float) -> None:
    """Fail before dispatch if a selected SGP4 source violates its age budget."""
    if cfg.session.orbit.propagator != "sgp4-tle":
        return

    max_age_days = cfg.session.orbit.tle_max_age_days
    if max_age_days is None:
        raise ValueError("orbit.tle_max_age_days is required for SGP4/TLE sessions")

    stale: list[str] = []
    for sat in cfg.satellites:
        node_id = cfg.addressing.sat_id(sat.plane, sat.slot)
        if sat.tle_line_1 is None or sat.tle_line_2 is None:
            raise ValueError(f"Satellite {node_id} has no TLE record for SGP4 propagation")
        age_days = tle_age_days(sat.tle_line_1, epoch_unix)
        if age_days > max_age_days:
            stale.append(f"{node_id} NORAD {sat.norad_id} age {age_days:.2f}d")

    if stale:
        raise ValueError(
            f"TLE age exceeds orbit.tle_max_age_days={max_age_days:g}: {', '.join(stale[:5])}"
        )


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
# Shared playback state (Pacemaker role, R-OME-008B)
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


def run(session_path: str, output_dir: str | None = None) -> Path:
    """Run the OME pipeline (single window, batch mode) and return the output path."""
    cfg = _load_session_config(session_path)
    _enforce_ground_link_model_contract(cfg.session)
    epoch_unix = resolve_session_epoch(cfg.session.time)
    _validate_sgp4_tle_freshness(cfg, epoch_unix)
    effective_ground_scheduling = _effective_ground_scheduling_for_runtime(cfg.session, cfg.gs_file)
    events = precompute_timeline(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        epoch_unix=epoch_unix,
        duration_s=cfg.period,
        propagator_id=cfg.propagator_id,
        step_seconds=cfg.session.time.step_seconds,
        ground_scheduling=effective_ground_scheduling,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_link_model=cfg.session.simulation.ground_link_model,
    )

    out_dir = Path(output_dir) if output_dir else Path("output")
    out_path = out_dir / f"{cfg.session.session.name}-timeline.jsonl"
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
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.debug("Health server listening on :%d", port)


# ---------------------------------------------------------------------------
# Producer-consumer architecture: pacing thread + NATS publisher thread
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Look-ahead thread — background precomputation for NodalPath proactive scheduling
# ---------------------------------------------------------------------------


class _LookAheadThread:
    """Background precomputation of future windows for NodalPath almanac.

    Runs precompute_timeline_window_from_context() in a daemon thread, producing events
    for the next orbital period. Results are stored for future consumption
    by NodalPath's proactive scheduling engine. Does NOT emit to the
    real-time event stream — that's the Pacemaker's job.

    Thread-safe: receives epoch and state via submit(), produces results
    retrievable via get_result(). Cancel on seek via cancel().
    """

    def __init__(self) -> None:
        import threading

        self._thread: threading.Thread | None = None
        self._result: TimelineWindowResult | None = None
        self._ready = threading.Event()
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    def submit(
        self,
        step_context: StepContext,
        epoch_unix: float,
        duration_s: float,
        initial_isl_state: dict | None,
        initial_gs_state: dict | None,
        initial_associations: dict[tuple[str, str], tuple[int, int]] | None = None,
        initial_pending_teardowns: MbbTeardownState | None = None,
        timestamp_offset: float = 0.0,
    ) -> None:
        """Start background window precomputation. Non-blocking."""
        import threading

        from ome.event_stream import precompute_timeline_window_from_context

        # Cancel any in-flight computation
        self.cancel()

        self._ready.clear()
        self._cancelled.clear()
        with self._lock:
            self._result = None

        def _compute():
            try:
                result = precompute_timeline_window_from_context(
                    step_context,
                    epoch_unix=epoch_unix,
                    duration_s=duration_s,
                    initial_isl_state=dict(initial_isl_state) if initial_isl_state else None,
                    initial_gs_state=dict(initial_gs_state) if initial_gs_state else None,
                    initial_associations=initial_associations,
                    initial_pending_teardowns=initial_pending_teardowns,
                    timestamp_offset=timestamp_offset,
                    predictive=True,
                )
                if not self._cancelled.is_set():
                    with self._lock:
                        self._result = result
                    self._ready.set()
                    logging.info(
                        "Look-ahead: window precomputed (%.0fs from epoch %s, %d events)",
                        duration_s,
                        datetime.fromtimestamp(epoch_unix, UTC).isoformat(),
                        len(result.events),
                    )
            except Exception:
                logging.exception("Look-ahead computation failed")

        self._thread = threading.Thread(target=_compute, name="ome-lookahead", daemon=True)
        self._thread.start()

    def get_result(self, timeout: float | None = None) -> TimelineWindowResult | None:
        """Block until result ready or timeout. Returns None if cancelled/timeout."""
        if not self._ready.wait(timeout=timeout):
            return None
        with self._lock:
            return self._result

    def cancel(self) -> None:
        """Signal cancellation. In-flight thread runs to completion but result is discarded."""
        self._cancelled.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.1)  # don't block, daemon thread dies on exit
        self._ready.clear()
        with self._lock:
            self._result = None

    def is_ready(self) -> bool:
        """Non-blocking check if result is available."""
        return self._ready.is_set()


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
    set_speed commands (R-OME-008B Pacemaker role).  The subscriber callback
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
    )

    if not session_id:
        logging.error("FATAL: OME NATS publisher started with no session_id")
        raise ValueError("session_id is required for OME NATS publisher")

    _subj_playback = playback_state_subject(session_id)

    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    js = nc.jetstream()
    await _connect_logging(nc)
    logging.debug("OME NATS publisher connected to %s (session_id=%s)", nats_url(), session_id)

    async def _publish_playback_state(state: str) -> None:
        """Publish PlaybackState to NODALARC_SESSION stream."""
        ps = PlaybackState(epoch_id=_epoch_id, state=state)
        await js.publish(_subj_playback, ps.model_dump_json().encode())
        logging.debug("PlaybackState published: state=%s epoch_id=%d", state, _epoch_id)

    # --- Playback control subscriber (R-OME-008B Tier 1) ---

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
) -> None:
    """Pacing loop — synchronous, dedicated thread, wall-clock precise.

    Never awaits. Never yields. Never touches NATS.
    Puts (subject, payload) tuples into the queue.
    Uses time.sleep() for precise wall-clock timing.
    Blocks on queue.put() if queue is full (backpressure from publisher).
    """
    import gzip as _ckpt_gzip
    import json as _json
    import queue

    from nodalarc.models.events import (
        CheckpointAssociation,
        ClockTick,
        PlaybackState,
        SchedulingCheckpoint,
        TeardownEntry,
    )
    from nodalarc.nats_channels import (
        ground_link_decision_snapshot_subject,
        link_state_snapshot_subject,
        ome_clock_subject,
        ome_visibility_subject,
        ops_event_subject,
        playback_state_subject,
        scheduling_checkpoint_subject,
        session_ephemeris_subject,
    )
    from nodalarc.platform_config import get_platform_config

    from ome.event_stream import build_link_state_snapshot, build_session_ephemeris
    from ome.snapshot_builder import build_link_decision_snapshot

    def _build_scheduling_checkpoint(
        sim_time: datetime,
        epoch_id: int,
        snapshot_seq: int,
        step: int,
        associations: dict[tuple[str, str], tuple[int, int]],
        teardowns: MbbTeardownState,
        mbb_overlap_ticks: int,
    ) -> SchedulingCheckpoint:
        """Convert OME internal association/teardown state to SchedulingCheckpoint."""
        if snapshot_seq <= 0:
            raise ValueError("SchedulingCheckpoint requires a published snapshot_seq")
        if step < 0:
            raise ValueError("SchedulingCheckpoint step must be non-negative")

        assoc_flat: dict[str, CheckpointAssociation] = {}
        for (gs_id, sat_id), (gs_ti, sat_ti) in associations.items():
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
        for (gs_id, sat_id), teardown in teardowns.items():
            remaining_ticks = max(0, mbb_overlap_ticks - (step - teardown.start_step))
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
        cfg = _load_session_config(session_path)
    session = cfg.session
    session_id = require_session_run_id(session)
    period = cfg.period
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
    ome_hostname = os.environ.get("HOSTNAME") or os.uname().nodename
    logging.debug("OME session_id=%s — NATS subjects scoped", session_id)

    # Initialize Pacemaker rate from static compression (R-OME-008B Part 1).
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

    # Optional file output
    out_path = None
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{session.session.name}-timeline.jsonl"

    def _enqueue(subject: str, payload: bytes) -> None:
        """Put event on queue. If the queue is full after 10 seconds, the
        NATS publisher is dead — exit the process so K8s restarts us."""
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
        _enqueue(subj_mbb_lifecycle, event.model_dump_json().encode())

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
    from collections import deque
    from statistics import quantiles

    from ome.event_stream import build_step_context, compute_step

    effective_ground_scheduling = _effective_ground_scheduling_for_runtime(session, cfg.gs_file)
    mbb_overlap_ticks = effective_ground_scheduling.mbb_overlap_ticks

    step_ctx = build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        propagator_id=cfg.propagator_id,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        ground_scheduling=effective_ground_scheduling,
        ground_link_model=session.simulation.ground_link_model,
    )

    step_seconds = session.time.step_seconds
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

    # Lookahead uses the same StepContext object as the live pacing loop. That
    # keeps predictive windows on the same propagation, visibility, allocation,
    # hysteresis, and MBB settings as authoritative ticks.
    # Precomputes the next orbital period's events concurrently with real-time emission.
    # Results available for NodalPath almanac consumption.
    lookahead = _LookAheadThread()
    lookahead_launched_for_epoch: float | None = None
    isl_state: dict[tuple[str, str], tuple[bool, bool]] = {}
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]] = {}
    current_associations: dict[tuple[str, str], tuple[int, int]] = {}
    mbb_pending_teardowns: MbbTeardownState = {}
    step = 0
    snapshot_seq = 0
    last_snapshot_sim_s: float = -snapshot_interval_s  # force immediate on first step
    force_first_snapshot = True

    # Reference-point pacing model (R-OME-008B).
    # Resets on rate change, unpause, or seek to avoid drift.
    pace_ref_wall = time.monotonic()
    pace_ref_step = 0
    current_rate = _time_accel

    # Per-step timing observability — p50/p95/p99 logged every 60s
    step_timings: deque[float] = deque(maxlen=3600)  # pre-sleep compute time
    iter_timings: deque[float] = deque(maxlen=3600)  # full iteration (compute + sleep)
    last_timing_log = time.monotonic()
    last_iter_start: float = time.monotonic()

    def _publish_link_authority(step_result, *, epoch_id: int) -> None:
        """Publish paired authoritative state/decision snapshots for a committed tick."""
        nonlocal snapshot_seq, last_snapshot_sim_s, force_first_snapshot
        snapshot_seq += 1
        snap = build_link_state_snapshot(
            step_result.link_snapshot_source,
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            sim_time=step_result.sim_time,
            seq=snapshot_seq,
            interval_s=snapshot_interval_s,
            fixed_positions=step_ctx.gs_positions,
            epoch_id=epoch_id,
            mbb_overlap_ticks=mbb_overlap_ticks,
            current_step=step_result.step,
        )
        _enqueue(subj_link_snapshot, snap.model_dump_json().encode())

        decision_snap = build_link_decision_snapshot(
            decisions=step_result.ground_decisions,
            unscheduled_pairs=step_result.ground_allocation.unscheduled_pairs,
            policy_audit=step_result.ground_allocation.policy_audit,
            allocation_events=step_result.ground_allocation.allocation_events,
            sim_time=step_result.sim_time,
            snapshot_seq=snapshot_seq,
            epoch_id=epoch_id,
        )
        _enqueue(subj_link_decisions, decision_snap.model_dump_json().encode())
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
            mbb_overlap_ticks=mbb_overlap_ticks,
        )
        _enqueue(
            subj_checkpoint,
            _ckpt_gzip.compress(ckpt.model_dump_json().encode()),
        )

    def _publish_clock_tick(step_result, rate: float, *, epoch_id: int) -> None:
        ct = ClockTick(
            sim_time=step_result.sim_time,
            wall_time=datetime.now(UTC),
            compression_ratio=float(rate),
            epoch_id=epoch_id,
        )
        _enqueue(subj_clock, ct.model_dump_json().encode())

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
    # malformed or stale retained state because that can make the event stream
    # look healthy while silently regressing simulation state.
    recovered_checkpoint = None
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

                sub = await _js.subscribe(
                    subj_checkpoint,
                    stream=_STREAM,
                    ordered_consumer=True,
                    deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
                )
                try:
                    msg = await sub.next_msg(timeout=2.0)
                    from nodalarc.scheduling_checkpoint import (
                        decode_retained_scheduling_checkpoint,
                    )

                    return decode_retained_scheduling_checkpoint(msg.data)
                except TimeoutError:
                    return None
                finally:
                    await sub.unsubscribe()
            finally:
                await _nc.close()

        recovered_checkpoint = _aio.run(_read_checkpoint())
    except Exception as exc:
        raise RuntimeError(
            "OME checkpoint recovery failed; refusing to start from unknown state"
        ) from exc

    # Checkpoint staleness threshold: if the OME was down for more than 30
    # seconds (measured by wall clock, not sim_time), fail loudly. Starting
    # fresh on the same retained subjects would reset session-lineage sequence
    # state and make downstream consumers distinguish truth from restart luck.
    _CHECKPOINT_STALENESS_THRESHOLD_S = 30.0

    if recovered_checkpoint:
        if recovered_checkpoint.written_at <= 0:
            raise RuntimeError("Recovered checkpoint has invalid written_at; refusing recovery")
        if recovered_checkpoint.step < 0:
            raise RuntimeError("Recovered checkpoint has negative step; refusing recovery")
        if recovered_checkpoint.snapshot_seq <= 0:
            raise RuntimeError("Recovered checkpoint has invalid snapshot_seq; refusing recovery")

        checkpoint_age = time.time() - recovered_checkpoint.written_at
        if checkpoint_age < 0:
            raise RuntimeError(
                "Recovered checkpoint written_at is in the future; refusing recovery"
            )
        if checkpoint_age > _CHECKPOINT_STALENESS_THRESHOLD_S:
            raise RuntimeError(
                "Recovered checkpoint is stale "
                f"(age={checkpoint_age:.1f}s > {_CHECKPOINT_STALENESS_THRESHOLD_S:.1f}s); "
                "refusing to reset session state"
            )

        step = recovered_checkpoint.step
        snapshot_seq = recovered_checkpoint.snapshot_seq
        _epoch_id = recovered_checkpoint.epoch_id
        # Keep epoch_unix as the original epoch anchor. The checkpoint stores
        # the tick sim_time, so using it directly as epoch_unix would make the
        # next live tick jump by step * step_seconds.
        epoch_unix = recovered_checkpoint.sim_time.timestamp() - (step * step_seconds)
        logging.info(
            "Recovered from checkpoint (age=%.1fs, step=%d, seq=%d, sim=%s)",
            checkpoint_age,
            step,
            snapshot_seq,
            recovered_checkpoint.sim_time.isoformat(),
        )
    else:
        logging.info("No checkpoint found — starting from epoch")

    # Compute committed state before publishing any authoritative snapshot.
    # Fresh start commits step 0. Warm restart replays to the retained checkpoint
    # step and publishes the final replay StepResult. In both cases the snapshot
    # is serialized from StepResult authority, not from replayed VisibilityEvents.
    initial_step_result = None
    if recovered_checkpoint is not None:
        logging.warning(
            "RecoveryReplay: replaying %d steps from checkpoint (step=%d)",
            step + 1,
            step,
            extra={"code": "RECOVERY_REPLAY", "details": {"total_steps": step + 1}},
        )

        for replay_step in range(step + 1):
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
            )
            initial_step_result = replay_result
            current_associations = replay_result.associations
            mbb_pending_teardowns = replay_result.pending_teardowns
            if replay_step > 0 and replay_step % 1000 == 0:
                logging.debug("Recovery replay: %d/%d steps", replay_step, step + 1)
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
    _enqueue(subj_ephemeris, eph.model_dump_json().encode())
    logging.debug("Published SessionEphemeris epoch_id=%d (%d nodes)", _epoch_id, len(eph.nodes))

    initial_epoch_id = _epoch_id
    active_epoch_id = initial_epoch_id
    _publish_link_authority(initial_step_result, epoch_id=initial_epoch_id)
    if recovered_checkpoint is None and initial_step_result.ground_allocation.lifecycle_events:
        _publish_mbb_lifecycle_events(initial_step_result, epoch_id=initial_epoch_id)
    _publish_checkpoint(initial_step_result, epoch_id=initial_epoch_id)

    initial_playback_state = "paused" if _paused else "playing"
    ps = PlaybackState(epoch_id=initial_epoch_id, state=initial_playback_state)
    _enqueue(subj_playback, ps.model_dump_json().encode())
    if not _paused:
        _publish_clock_tick(initial_step_result, current_rate, epoch_id=initial_epoch_id)

    _initial_epoch_committed = True

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
    last_iter_start = time.monotonic()
    _sleep_until_next_step_due()

    try:
        while not shutdown_event.is_set():
            step_start = time.monotonic()
            if step > 0:
                iter_timings.append((step_start - last_iter_start) * 1000)
            last_iter_start = step_start

            # --- Seek check (Tier 2, R-OME-008B Part 5) ---
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
                current_associations = {}
                mbb_pending_teardowns = {}
                step = 0
                pace_ref_wall = time.monotonic()
                pace_ref_step = 0
                current_rate = _time_accel
                force_first_snapshot = False  # the committed step-0 snapshot is published below
                lookahead.cancel()
                lookahead_launched_for_epoch = None

                # Compute first, publish after commit. This is the Phase 4
                # ordering boundary; no empty new-epoch snapshot is allowed.
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
                _enqueue(subj_ephemeris, eph.model_dump_json().encode())
                _publish_link_authority(seek_result, epoch_id=seek_epoch_id)
                if seek_result.ground_allocation.lifecycle_events:
                    _publish_mbb_lifecycle_events(seek_result, epoch_id=seek_epoch_id)
                _publish_checkpoint(seek_result, epoch_id=seek_epoch_id)

                ps = PlaybackState(epoch_id=seek_epoch_id, state="playing")
                _enqueue(subj_playback, ps.model_dump_json().encode())
                _publish_clock_tick(seek_result, current_rate, epoch_id=seek_epoch_id)
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
                last_iter_start = time.monotonic()
                _sleep_until_next_step_due()
                continue

            # --- Launch look-ahead if not already running for this epoch ---
            if lookahead_launched_for_epoch != epoch_unix:
                lookahead.submit(
                    step_context=step_ctx,
                    epoch_unix=epoch_unix,
                    duration_s=period,
                    initial_isl_state=isl_state if isl_state else None,
                    initial_gs_state=gs_state if gs_state else None,
                    initial_associations=current_associations if current_associations else None,
                    initial_pending_teardowns=mbb_pending_teardowns
                    if mbb_pending_teardowns
                    else None,
                    timestamp_offset=0.0,
                )
                lookahead_launched_for_epoch = epoch_unix

            # --- Pause gate ---
            if _paused:
                while _paused and not shutdown_event.is_set():
                    if _seek_target is not None:
                        break
                    time.sleep(0.1)
                # Reset reference on unpause — time spent paused
                # must not count toward wall-clock budget.
                pace_ref_wall = time.monotonic()
                pace_ref_step = step
                current_rate = _time_accel
                continue  # re-check seek at top

            # --- Rate-change detection ---
            new_rate = _time_accel
            if new_rate != current_rate:
                pace_ref_wall = time.monotonic()
                pace_ref_step = step
                current_rate = new_rate

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
            for te in step_events:
                if te.event_type == "VisibilityEvent":
                    payload = te.data.model_dump_json().encode()
                    _enqueue(subj_visibility, payload)
            # ClockTick with real wall_time (not precomputed placeholder)
            _publish_clock_tick(step_result, current_rate, epoch_id=tick_epoch_id)

            # LinkStateSnapshot at interval, and immediately for terminal MBB
            # lifecycle outcomes so the OpsEvent can reference a same-tick
            # GroundPolicyAudit by (epoch_id, snapshot_seq).
            sim_s = step * step_seconds
            force_lifecycle_snapshot = bool(step_result.ground_allocation.lifecycle_events)
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
            _publish_checkpoint(step_result, epoch_id=tick_epoch_id)

            # Write JSONL if --output-dir provided
            if out_path is not None:
                with open(out_path, "a") as f:
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

            # --- Per-step timing observability ---
            pre_sleep_ms = (time.monotonic() - step_start) * 1000
            step_timings.append(pre_sleep_ms)
            now_mono = time.monotonic()
            if now_mono - last_timing_log >= 60.0:
                if len(step_timings) >= 10:
                    pcts = quantiles(step_timings, n=100)
                    budget_ms = (step_seconds / current_rate) * 1000
                    headroom = (1.0 - pcts[94] / budget_ms) * 100 if budget_ms > 0 else 0
                    iter_pcts = quantiles(iter_timings, n=100) if len(iter_timings) >= 10 else None
                    logging.debug(
                        "OME pacing: compute p50=%.1fms p95=%.1fms "
                        "iter p50=%.1fms p95=%.1fms "
                        "budget=%.1fms (%.0fx) headroom=%.0f%%",
                        pcts[49],
                        pcts[94],
                        iter_pcts[49] if iter_pcts else 0,
                        iter_pcts[94] if iter_pcts else 0,
                        budget_ms,
                        current_rate,
                        headroom,
                    )
                last_timing_log = now_mono

            # --- Sleep until next step (Pacemaker role) ---
            step += 1
            wall_target = pace_ref_wall + (step - pace_ref_step) * (step_seconds / current_rate)
            now_mono = time.monotonic()
            if now_mono < wall_target:
                time.sleep(wall_target - now_mono)

    except Exception:
        logging.exception("FATAL: OME pacing thread crashed")
    finally:
        lookahead.cancel()
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
    args = parser.parse_args()

    from nodalarc.platform_config import init_platform_config

    init_platform_config(Path(args.platform_config))

    if not args.continuous:
        run(args.session, args.output_dir)
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
    pre_cfg = _load_session_config(args.session)
    session_id = require_session_run_id(pre_cfg.session)
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
