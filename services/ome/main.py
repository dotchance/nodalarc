# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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

# ---------------------------------------------------------------------------
# Session config bundle — shared between run() and _run_pacing()
# ---------------------------------------------------------------------------
from typing import NamedTuple

import yaml
from nodal.logging import configure as _configure_logging
from nodal.logging import connect as _connect_logging
from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.session import SessionConfig, resolve_session_epoch

from ome.event_stream import (
    precompute_timeline,
    write_timeline_jsonl,
)
from ome.propagator import orbital_period


class _SessionBundle(NamedTuple):
    """All session-derived config needed by the OME pacing loop."""

    session: SessionConfig
    constellation_config: object  # ConstellationConfig (discriminated union)
    gs_file: object  # GroundStationFile
    satellites: list
    period: float
    addressing: AddressingScheme
    neighbors: frozenset
    max_range_km: float
    max_tracking_rate_deg_s: float
    field_of_regard_deg: float
    polar_seam_enabled: bool
    latitude_threshold_deg: float
    default_min_elevation_deg: float


def _load_session_config(session_path: str | Path) -> _SessionBundle:
    """Load and validate all session config. Pure — no side effects."""
    from nodalarc.models.constellation import ParametricConstellation

    data = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(data)

    constellation_config = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)
    satellites = expand_constellation(constellation_config)
    if not satellites:
        raise ValueError("No satellites in constellation")

    first_alt = satellites[0].elements.semi_major_axis_km - 6371.0
    period = orbital_period(first_alt)
    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation_config, addressing)

    max_range_km = 5016.0
    max_tracking_rate_deg_s = 3.0
    field_of_regard_deg = 360.0
    polar_seam_enabled = False
    latitude_threshold_deg = 70.0

    if isinstance(constellation_config, ParametricConstellation):
        if constellation_config.default_terminals and constellation_config.default_terminals.isl:
            isl_term = constellation_config.default_terminals.isl[0]
            max_range_km = isl_term.max_range_km
            max_tracking_rate_deg_s = isl_term.max_tracking_rate_deg_s
            field_of_regard_deg = isl_term.field_of_regard_deg
        if constellation_config.polar_seam:
            polar_seam_enabled = constellation_config.polar_seam.enabled
            latitude_threshold_deg = constellation_config.polar_seam.latitude_threshold_deg

    default_min_elevation = gs_file.default_min_elevation_deg or 25.0

    return _SessionBundle(
        session=session,
        constellation_config=constellation_config,
        gs_file=gs_file,
        satellites=satellites,
        period=period,
        addressing=addressing,
        neighbors=neighbors,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        field_of_regard_deg=field_of_regard_deg,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_min_elevation_deg=default_min_elevation,
    )


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


def run(session_path: str, output_dir: str | None = None) -> Path:
    """Run the OME pipeline (single window, batch mode) and return the output path."""
    cfg = _load_session_config(session_path)

    events = precompute_timeline(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        epoch_unix=resolve_session_epoch(cfg.session.time),
        duration_s=cfg.period,
        step_seconds=cfg.session.time.step_seconds,
        max_range_km=cfg.max_range_km,
        max_tracking_rate_deg_s=cfg.max_tracking_rate_deg_s,
        field_of_regard_deg=cfg.field_of_regard_deg,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        default_min_elevation_deg=cfg.default_min_elevation_deg,
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

    Runs precompute_timeline_window() in a daemon thread, producing events
    for the next orbital period. Results are stored for future consumption
    by NodalPath's proactive scheduling engine. Does NOT emit to the
    real-time event stream — that's the Pacemaker's job.

    Thread-safe: receives epoch and state via submit(), produces results
    retrievable via get_result(). Cancel on seek via cancel().
    """

    def __init__(self) -> None:
        import threading

        self._thread: threading.Thread | None = None
        self._result: tuple | None = None  # (events, isl_state, gs_state)
        self._ready = threading.Event()
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    def submit(
        self,
        common_args: dict,
        epoch_unix: float,
        duration_s: float,
        initial_isl_state: dict | None,
        initial_gs_state: dict | None,
        initial_associations: dict[tuple[str, str], tuple[int, int]] | None = None,
        initial_pending_teardowns: dict | None = None,
        timestamp_offset: float = 0.0,
    ) -> None:
        """Start background window precomputation. Non-blocking."""
        import threading

        from ome.event_stream import precompute_timeline_window

        # Cancel any in-flight computation
        self.cancel()

        self._ready.clear()
        self._cancelled.clear()
        with self._lock:
            self._result = None

        def _compute():
            try:
                result = precompute_timeline_window(
                    **common_args,
                    epoch_unix=epoch_unix,
                    duration_s=duration_s,
                    initial_isl_state=dict(initial_isl_state) if initial_isl_state else None,
                    initial_gs_state=dict(initial_gs_state) if initial_gs_state else None,
                    initial_associations=initial_associations,
                    initial_pending_teardowns=initial_pending_teardowns,
                    timestamp_offset=timestamp_offset,
                )
                if not self._cancelled.is_set():
                    with self._lock:
                        self._result = result
                    self._ready.set()
                    logging.info(
                        "Look-ahead: window precomputed (%.0fs from epoch %s, %d events)",
                        duration_s,
                        datetime.fromtimestamp(epoch_unix, UTC).isoformat(),
                        len(result[0]),
                    )
            except Exception:
                logging.exception("Look-ahead computation failed")

        self._thread = threading.Thread(target=_compute, name="ome-lookahead", daemon=True)
        self._thread.start()

    def get_result(self, timeout: float | None = None) -> tuple | None:
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


async def _nats_publisher_loop(event_queue, shutdown_event, session_id: str) -> None:
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
        MAX_TIME_ACCEL,
        MIN_TIME_ACCEL,
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
        global _time_accel, _paused, _seeking, _seek_target, _epoch_id
        try:
            cmd = json.loads(msg.data)
            action = cmd.get("action", "")

            # Seeking mutex: reject pause/set_speed during seek
            if _seeking and action in ("pause", "set_speed"):
                reply = {
                    "error": f"cannot {action} during seek (epoch_id={_epoch_id})",
                    "paused": _paused,
                    "speed": _time_accel,
                }
                await msg.respond(json.dumps(reply).encode())
                return

            if action == "pause":
                _paused = True
                await _publish_playback_state("paused")
                logging.info("Playback paused (speed=%.1f)", _time_accel)
            elif action == "resume":
                _paused = False
                await _publish_playback_state("playing")
                logging.info("Playback resumed (speed=%.1f)", _time_accel)
            elif action == "set_speed":
                factor = float(cmd.get("factor", 1.0))
                if factor < MIN_TIME_ACCEL or factor > MAX_TIME_ACCEL:
                    reply = {
                        "error": f"factor {factor} out of range [{MIN_TIME_ACCEL}, {MAX_TIME_ACCEL}]",
                        "paused": _paused,
                        "speed": _time_accel,
                    }
                    await msg.respond(json.dumps(reply).encode())
                    return
                _time_accel = factor
                logging.info("Playback speed set to %.1fx", factor)
            elif action == "seek":
                _epoch_id += 1
                _seeking = True
                target_str = cmd.get("target_sim_time")
                if target_str:
                    _seek_target = datetime.fromisoformat(target_str).timestamp()
                else:
                    _seek_target = datetime.now(UTC).timestamp()
                _paused = False
                await _publish_playback_state("seeking")
                target_iso = datetime.fromtimestamp(_seek_target, UTC).isoformat()
                logging.info("Seek requested: %s epoch_id=%d (auto-resumed)", target_iso, _epoch_id)
            elif action == "get_status":
                pass  # fall through to reply with current state
            else:
                reply = {
                    "error": f"unknown action: {action}",
                    "paused": _paused,
                    "speed": _time_accel,
                }
                await msg.respond(json.dumps(reply).encode())
                return
            await msg.respond(
                json.dumps(
                    {"paused": _paused, "speed": _time_accel, "epoch_id": _epoch_id}
                ).encode()
            )
        except Exception as exc:
            logging.error("Playback control error: %s", exc)
            await msg.respond(json.dumps({"error": str(exc)}).encode())

    await nc.subscribe(SUBJECT_PLAYBACK_CONTROL, cb=_handle_playback)
    logging.debug("OME playback control active on %s", SUBJECT_PLAYBACK_CONTROL)

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

    from nodalarc.models.events import ClockTick, PlaybackState, SchedulingCheckpoint, TeardownEntry
    from nodalarc.nats_channels import (
        link_state_snapshot_subject,
        ome_clock_subject,
        ome_visibility_subject,
        playback_state_subject,
        sanitize_session_id,
        scheduling_checkpoint_subject,
        session_ephemeris_subject,
    )
    from nodalarc.platform_config import get_platform_config

    from ome.event_stream import build_link_state_snapshot, build_session_ephemeris

    def _build_scheduling_checkpoint(
        sim_time: datetime,
        epoch_id: int,
        step: int,
        associations: dict[tuple[str, str], tuple[int, int]],
        teardowns: dict[tuple[str, str], tuple[int, tuple[str, str]]],
    ) -> SchedulingCheckpoint:
        """Convert OME internal association/teardown state to SchedulingCheckpoint."""
        # associations: (gs_id, sat_id) → (gs_ti, sat_ti) → flatten to gs_id → sat_id
        assoc_flat: dict[str, str] = {}
        for (gs_id, sat_id), _ in associations.items():
            assoc_flat[gs_id] = sat_id

        # teardowns: (gs_id, sat_id) → (remaining_ticks, (succ_gs, succ_sat))
        td_flat: dict[str, TeardownEntry] = {}
        for (gs_id, sat_id), (ticks, _successor) in teardowns.items():
            td_flat[f"{gs_id}:{sat_id}"] = TeardownEntry(
                remaining_ticks=ticks,
                gs_id=gs_id,
                sat_id=sat_id,
            )

        return SchedulingCheckpoint(
            sim_time=sim_time,
            epoch_id=epoch_id,
            step=step,
            associations=assoc_flat,
            pending_teardowns=td_flat,
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
    session_id = sanitize_session_id(session.session.name)
    period = cfg.period
    epoch_unix = resolve_session_epoch(session.time)
    compression = session.time.compression if session.time.compression else 1

    # Build session-scoped NATS subjects
    subj_visibility = ome_visibility_subject(session_id)
    subj_clock = ome_clock_subject(session_id)
    subj_link_snapshot = link_state_snapshot_subject(session_id)
    subj_ephemeris = session_ephemeris_subject(session_id)
    subj_playback = playback_state_subject(session_id)
    subj_checkpoint = scheduling_checkpoint_subject(session_id)
    logging.debug("OME session_id=%s — NATS subjects scoped", session_id)

    # Initialize Pacemaker rate from static compression (R-OME-008B Part 1).
    # Runtime set_speed commands replace this value dynamically.
    global _time_accel, _seek_target, _seeking
    _time_accel = float(compression)
    snapshot_interval_s = get_platform_config().ome_link_state_snapshot_interval_s

    # Build interface map for LinkStateSnapshot
    from nodalarc.models.addressing import neighbors_by_node

    by_node = neighbors_by_node(cfg.neighbors)
    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    for node_id, assignments in by_node.items():
        for na in assignments:
            pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
            if pair not in interface_map:
                if node_id == pair[0]:
                    interface_map[pair] = (na.interface, "")
                else:
                    interface_map[pair] = ("", na.interface)
            else:
                existing = interface_map[pair]
                if node_id == pair[0] and not existing[0]:
                    interface_map[pair] = (na.interface, existing[1])
                elif node_id == pair[1] and not existing[1]:
                    interface_map[pair] = (existing[0], na.interface)

    # Optional file output
    out_path = None
    sentinel = None
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{session.session.name}-timeline.jsonl"
        sentinel = out_path.with_suffix(".ready")

    def _enqueue(subject: str, payload: bytes) -> None:
        """Put event on queue. Blocks if full (backpressure)."""
        try:
            event_queue.put((subject, payload), timeout=5)
        except queue.Full:
            logging.warning("Event queue full — backpressure from NATS publisher")
            event_queue.put((subject, payload))  # blocking wait, no timeout

    # Build StepContext for per-step computation (Physicist role)
    from collections import deque
    from statistics import quantiles

    from ome.event_stream import build_step_context, compute_step

    mbb_dispatch = session.routing.mbb_dispatch if session.routing else False
    mbb_overlap_ticks = session.routing.mbb_overlap_ticks if session.routing else 3
    mbb_reserve = (
        1
        if mbb_dispatch
        and any(
            ctx_tc > 1
            for ctx_tc in (
                sum(t.tracking_capacity for t in (st.terminals or cfg.gs_file.default_terminals))
                for st in (cfg.gs_file.stations if cfg.gs_file else [])
            )
        )
        else 0
    )

    step_ctx = build_step_context(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        max_range_km=cfg.max_range_km,
        max_tracking_rate_deg_s=cfg.max_tracking_rate_deg_s,
        field_of_regard_deg=cfg.field_of_regard_deg,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        default_min_elevation_deg=cfg.default_min_elevation_deg,
        mbb_overlap_ticks=mbb_overlap_ticks,
        mbb_reserve=mbb_reserve,
    )

    step_seconds = session.time.step_seconds

    # --- MBB capability validation (R-OME-004a) ---
    is_nodalpath = session.routing.protocol == "nodalpath" if session.routing else False
    if cfg.gs_file:
        for station in cfg.gs_file.stations:
            gs_id = cfg.addressing.gs_id(station.name)
            cap = step_ctx.gs_terminal_counts.get(gs_id, 1)
            if cap <= 1 and not is_nodalpath:
                logging.warning(
                    "MBB-INFEASIBLE: %s has tracking_capacity=%d and no "
                    "proactive control plane (routing=%s). Physical-layer MBB "
                    "requires spare terminal capacity; routing-layer MBB "
                    "requires NodalPath. This segment will use cold handover "
                    "(break-before-make) with expected packet loss during "
                    "handoff events.",
                    gs_id,
                    cap,
                    session.routing.protocol if session.routing else "none",
                )

    # Look-ahead thread — background precomputation for NodalPath proactive scheduling.
    # Precomputes the next orbital period's events concurrently with real-time emission.
    # Results available for NodalPath almanac consumption.
    lookahead = _LookAheadThread()
    _lookahead_common_args = dict(
        satellites=cfg.satellites,
        addressing=cfg.addressing,
        gs_file=cfg.gs_file,
        neighbors=cfg.neighbors,
        step_seconds=step_seconds,
        max_range_km=cfg.max_range_km,
        max_tracking_rate_deg_s=cfg.max_tracking_rate_deg_s,
        field_of_regard_deg=cfg.field_of_regard_deg,
        polar_seam_enabled=cfg.polar_seam_enabled,
        latitude_threshold_deg=cfg.latitude_threshold_deg,
        default_min_elevation_deg=cfg.default_min_elevation_deg,
    )
    lookahead_launched_for_epoch: float | None = None
    isl_state: dict[tuple[str, str], tuple[bool, bool]] = {}
    gs_state: dict[tuple[str, str], tuple[bool, bool, str]] = {}
    running_isl_state: dict[tuple[str, str], tuple[bool, bool]] = {}
    running_gs_state: dict[tuple[str, str], tuple[bool, bool, str]] = {}
    current_associations: dict[tuple[str, str], tuple[int, int]] = {}
    mbb_pending_teardowns: dict[tuple[str, str], tuple[int, tuple[str, str]]] = {}
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
    # If found, recover sim_time from it. In all cases, start PAUSED
    # so consumers get a deterministic initial state (no wall-clock teleport).
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
                    decompressed = _ckpt_gzip.decompress(msg.data)
                    return SchedulingCheckpoint.model_validate_json(decompressed)
                except Exception:
                    return None
                finally:
                    await sub.unsubscribe()
            finally:
                await _nc.close()

        recovered_checkpoint = _aio.run(_read_checkpoint())
    except Exception as exc:
        logging.warning("Checkpoint recovery failed (non-fatal): %s", exc)

    if recovered_checkpoint:
        epoch_unix = recovered_checkpoint.sim_time.timestamp()
        step = recovered_checkpoint.step
        logging.info(
            "Recovered from checkpoint at T+%s (step=%d, epoch_id=%d)",
            recovered_checkpoint.sim_time.isoformat(),
            step,
            recovered_checkpoint.epoch_id,
        )
    else:
        logging.info("No checkpoint found — starting from epoch")

    # Recompute link state at recovered (or initial) sim_time before publishing.
    # Run enough steps from epoch to reach the recovered step so ISL/GS state
    # is accurate. For a fresh start (step=0), this is a no-op.
    if step > 0:
        logging.warning(
            "RecoveryReplay: replaying %d steps from checkpoint (step=%d)",
            step + 1,
            step,
            extra={"code": "RECOVERY_REPLAY", "details": {"total_steps": step + 1}},
        )

        for replay_step in range(step + 1):
            replay_events, _, current_associations, mbb_pending_teardowns = compute_step(
                step_ctx,
                epoch_unix - (step * step_seconds),  # original epoch
                replay_step,
                step_seconds,
                0.0,
                isl_state,
                gs_state,
                current_associations,
                mbb_pending_teardowns,
            )
            for te in replay_events:
                if te.event_type == "VisibilityEvent":
                    vis = te.data
                    pair = (vis.node_a, vis.node_b)
                    if vis.link_type == "ground":
                        running_gs_state[pair] = (vis.visible, vis.scheduled, vis.scheduling_state)
                    else:
                        running_isl_state[pair] = (vis.visible, vis.scheduled)
            if replay_step > 0 and replay_step % 1000 == 0:
                logging.debug("Recovery replay: %d/%d steps", replay_step, step + 1)
        logging.debug("Replayed %d steps to rebuild link state", step + 1)

    # Recovery → PAUSED (deterministic, operator must unpause).
    # Fresh deployment → PLAYING (normal UX, session starts immediately).
    _paused = recovered_checkpoint is not None

    # --- Session start sequence (epoch_id=0) ---
    # Order: SessionEphemeris → LinkStateSnapshot → PlaybackState(paused)
    # → then tick loop waits for unpause before first ClockTick.
    eph = build_session_ephemeris(step_ctx, epoch_unix, _epoch_id)
    _enqueue(subj_ephemeris, eph.model_dump_json().encode())
    logging.debug("Published SessionEphemeris epoch_id=%d (%d nodes)", _epoch_id, len(eph.nodes))

    # Force initial LinkStateSnapshot with epoch_id
    snapshot_seq += 1
    initial_snap = build_link_state_snapshot(
        isl_state=running_isl_state,
        gs_state=running_gs_state,
        interface_map=interface_map,
        sim_time=datetime.fromtimestamp(epoch_unix, UTC),
        seq=snapshot_seq,
        interval_s=snapshot_interval_s,
        positions=None,
        epoch_id=_epoch_id,
    )
    _enqueue(subj_link_snapshot, initial_snap.model_dump_json().encode())
    last_snapshot_sim_s = 0.0
    force_first_snapshot = False

    initial_playback_state = "paused" if _paused else "playing"
    ps = PlaybackState(epoch_id=_epoch_id, state=initial_playback_state)
    _enqueue(subj_playback, ps.model_dump_json().encode())
    if _paused:
        logging.info(
            "OME recovered from checkpoint, starting paused [epoch_id=%d, step=%d]",
            _epoch_id,
            step,
        )
    else:
        logging.debug("OME fresh session, auto-play [epoch_id=%d]", _epoch_id)

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
                epoch_unix = seek_to
                isl_state = {}
                gs_state = {}
                running_isl_state = {}
                running_gs_state = {}
                current_associations = {}
                mbb_pending_teardowns = {}
                step = 0
                pace_ref_wall = time.monotonic()
                pace_ref_step = 0
                current_rate = _time_accel
                last_snapshot_sim_s = -snapshot_interval_s
                force_first_snapshot = False  # we publish it explicitly below
                lookahead.cancel()
                lookahead_launched_for_epoch = None

                # Publish epoch dependencies for the new epoch_id
                # (epoch_id was already incremented by the publisher thread's seek handler)
                eph = build_session_ephemeris(step_ctx, epoch_unix, _epoch_id)
                _enqueue(subj_ephemeris, eph.model_dump_json().encode())

                snapshot_seq += 1
                seek_snap = build_link_state_snapshot(
                    isl_state=running_isl_state,
                    gs_state=running_gs_state,
                    interface_map=interface_map,
                    sim_time=datetime.fromtimestamp(epoch_unix, UTC),
                    seq=snapshot_seq,
                    interval_s=snapshot_interval_s,
                    positions=None,
                    epoch_id=_epoch_id,
                )
                _enqueue(subj_link_snapshot, seek_snap.model_dump_json().encode())
                last_snapshot_sim_s = 0.0

                ps = PlaybackState(epoch_id=_epoch_id, state="playing")
                _enqueue(subj_playback, ps.model_dump_json().encode())
                _seeking = False  # Clear seeking mutex

                logging.info(
                    "Seek applied: new epoch %s epoch_id=%d",
                    datetime.fromtimestamp(seek_to, UTC).isoformat(),
                    _epoch_id,
                )

            # --- Launch look-ahead if not already running for this epoch ---
            if lookahead_launched_for_epoch != epoch_unix:
                lookahead.submit(
                    common_args=_lookahead_common_args,
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
            step_events, current_positions, current_associations, mbb_pending_teardowns = (
                compute_step(
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
            )

            # --- Emit events for this step ---
            sim_time = datetime.fromtimestamp(epoch_unix + step * step_seconds, UTC)

            for te in step_events:
                payload = te.data.model_dump_json().encode()
                if te.event_type == "VisibilityEvent":
                    _enqueue(subj_visibility, payload)
                    vis = te.data
                    pair = (vis.node_a, vis.node_b)
                    if vis.link_type == "ground":
                        running_gs_state[pair] = (vis.visible, vis.scheduled, vis.scheduling_state)
                    else:
                        running_isl_state[pair] = (vis.visible, vis.scheduled)
            # ClockTick with real wall_time (not precomputed placeholder)
            ct = ClockTick(
                sim_time=sim_time,
                wall_time=datetime.now(UTC),
                compression_ratio=float(current_rate),
                epoch_id=_epoch_id,
            )
            _enqueue(subj_clock, ct.model_dump_json().encode())

            # LinkStateSnapshot at interval
            sim_s = step * step_seconds
            if sim_s - last_snapshot_sim_s >= snapshot_interval_s or force_first_snapshot:
                snapshot_seq += 1
                snap = build_link_state_snapshot(
                    isl_state=running_isl_state,
                    gs_state=running_gs_state,
                    interface_map=interface_map,
                    sim_time=sim_time,
                    seq=snapshot_seq,
                    interval_s=snapshot_interval_s,
                    positions=current_positions,
                    epoch_id=_epoch_id,
                    current_associations=current_associations,
                    mbb_pending_teardowns=mbb_pending_teardowns,
                    mbb_overlap_ticks=mbb_overlap_ticks,
                    current_step=step,
                )
                _enqueue(subj_link_snapshot, snap.model_dump_json().encode())
                last_snapshot_sim_s = sim_s
                force_first_snapshot = False

                # Publish SchedulingCheckpoint alongside each LinkStateSnapshot.
                # gzip-compressed to stay within NATS message size limits for
                # large constellations with many GS associations.
                ckpt = _build_scheduling_checkpoint(
                    sim_time=sim_time,
                    epoch_id=_epoch_id,
                    step=step,
                    associations=current_associations,
                    teardowns=mbb_pending_teardowns,
                )
                _enqueue(
                    subj_checkpoint,
                    _ckpt_gzip.compress(ckpt.model_dump_json().encode()),
                )

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

    except KeyboardInterrupt:
        logging.info("OME pacing interrupted")
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

    from nodalarc.nats_channels import sanitize_session_id

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
    session_id = sanitize_session_id(pre_cfg.session.session.name)
    from nodal.logging import set_session

    set_session(session_id)
    logging.debug("OME session_id=%s", session_id)

    event_queue: queue.Queue = queue.Queue(maxsize=1000)
    shutdown_event = threading.Event()

    def _signal_handler(signum, frame):
        logging.info("Shutdown signal received (%d)", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Thread 1: NATS publisher — async event loop, consumes from queue
    def _publisher_thread():
        asyncio.run(_nats_publisher_loop(event_queue, shutdown_event, session_id))

    pub_thread = threading.Thread(target=_publisher_thread, name="nats-publisher", daemon=True)
    pub_thread.start()

    # Give publisher time to connect before pacing starts
    time.sleep(1)

    # Thread 2 (main thread): Pacing — synchronous, time.sleep(), produces to queue
    _run_pacing(args.session, args.output_dir, event_queue, shutdown_event, preloaded_cfg=pre_cfg)

    # Shutdown: send sentinel and wait for publisher to drain
    event_queue.put(None)
    pub_thread.join(timeout=10)
    logging.info("OME stopped")


if __name__ == "__main__":
    main()
