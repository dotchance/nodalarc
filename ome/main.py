"""OME entry point — orchestration only, no logic.

Loads configs via YAML + Pydantic, creates AddressingScheme,
computes ISL neighbor assignments (frozen), calls precompute_timeline(),
publishes events on NATS JetStream.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.session import SessionConfig

from ome.constellation_loader import expand_constellation, load_constellation, load_ground_stations
from ome.event_stream import (
    append_timeline_jsonl,
    precompute_timeline,
    precompute_timeline_window,
    write_timeline_jsonl,
)
from ome.propagator import orbital_period


def run(session_path: str, output_dir: str | None = None) -> Path:
    """Run the OME pipeline and return the output path."""
    # Load session config
    data = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(data)

    # Resolve paths relative to CWD (paths in session YAML are project-relative)
    constellation_config = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)

    # Expand constellation to satellite nodes
    satellites = expand_constellation(constellation_config)
    if not satellites:
        raise ValueError("No satellites in constellation")

    # Determine orbital period from first satellite's altitude
    first_alt = satellites[0].elements.semi_major_axis_km - 6371.0
    period = orbital_period(first_alt)

    # Create addressing scheme
    addressing = AddressingScheme(session.addressing)

    # Compute ISL neighbor assignments (frozen, computed once)
    neighbors = assign_isl_neighbors(constellation_config, addressing)

    # Extract visibility parameters from constellation config
    max_range_km = 5016.0
    max_tracking_rate_deg_s = 3.0
    field_of_regard_deg = 360.0
    polar_seam_enabled = False
    latitude_threshold_deg = 70.0

    from nodalarc.models.constellation import ParametricConstellation

    if isinstance(constellation_config, ParametricConstellation):
        if constellation_config.default_terminals.isl:
            isl = constellation_config.default_terminals.isl[0]
            max_range_km = isl.max_range_km
            max_tracking_rate_deg_s = isl.max_tracking_rate_deg_s
            field_of_regard_deg = isl.field_of_regard_deg
        if constellation_config.polar_seam:
            polar_seam_enabled = constellation_config.polar_seam.enabled
            latitude_threshold_deg = constellation_config.polar_seam.latitude_threshold_deg

    # Default min elevation from GS file
    default_min_elevation = gs_file.default_min_elevation_deg or 25.0

    # Precompute timeline
    events = precompute_timeline(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=(
            datetime.fromisoformat(session.time.start_time).timestamp()
            if session.time.start_time
            else time.time()
        ),
        duration_s=period,
        step_seconds=session.time.step_seconds,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        field_of_regard_deg=field_of_regard_deg,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_min_elevation_deg=default_min_elevation,
    )

    # Write output
    out_dir = Path(output_dir) if output_dir else Path("output")
    out_path = out_dir / f"{session.session.name}-timeline.jsonl"
    write_timeline_jsonl(events, out_path)

    logging.info(
        f"OME complete: {len(events)} events, {len(satellites)} satellites, period={period:.0f}s"
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
    logging.info(f"Health server listening on :{port}")


# ---------------------------------------------------------------------------
# Producer-consumer architecture: pacing thread + NATS publisher thread
# ---------------------------------------------------------------------------


async def _nats_publisher_loop(event_queue, shutdown_event) -> None:
    """NATS publisher — runs in its own async event loop in its own thread.

    Consumes (subject, payload) tuples from the queue and publishes to NATS.
    Handles HeartbeatTick via the queue (pacing thread sends them during
    window computation). Handles reconnection transparently via nats-py.

    Never touches timing. Never sleeps for pacing. Only I/O.
    """
    import asyncio
    import queue

    import nats
    from nodalarc.nats_channels import NATS_CONNECT_OPTIONS, nats_url

    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    logging.info("OME NATS publisher connected to %s", nats_url())

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
            await nc.publish(subject, payload)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logging.error("NATS publisher error: %s", exc, exc_info=True)
    finally:
        await nc.drain()
        await nc.close()
        logging.info("NATS publisher stopped")


def _run_pacing(session_path, output_dir, event_queue, shutdown_event) -> None:
    """Pacing loop — synchronous, dedicated thread, wall-clock precise.

    Never awaits. Never yields. Never touches NATS.
    Puts (subject, payload) tuples into the queue.
    Uses time.sleep() for precise wall-clock timing.
    Blocks on queue.put() if queue is full (backpressure from publisher).
    """
    import queue
    import threading

    from nodalarc.models.events import ClockTick, HeartbeatTick
    from nodalarc.nats_channels import (
        SUBJECT_CLOCK_TICK,
        SUBJECT_HEARTBEAT,
        SUBJECT_LINK_STATE_SNAPSHOT,
        SUBJECT_SNAPSHOT,
        SUBJECT_VISIBILITY_EVENT,
    )
    from nodalarc.platform import get_platform_config

    from ome.event_stream import build_link_state_snapshot

    _start_health_server()

    # Wait for session config (synchronous — blocking is fine in this thread)
    session_file = Path(session_path)
    while not session_file.is_file():
        logging.info("Waiting for session config at %s...", session_path)
        time.sleep(5)
    data = yaml.safe_load(session_file.read_text())
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

    # Extract visibility parameters
    max_range_km = 5016.0
    max_tracking_rate_deg_s = 3.0
    field_of_regard_deg = 360.0
    polar_seam_enabled = False
    latitude_threshold_deg = 70.0

    from nodalarc.models.constellation import ParametricConstellation

    if isinstance(constellation_config, ParametricConstellation):
        if constellation_config.default_terminals.isl:
            isl_term = constellation_config.default_terminals.isl[0]
            max_range_km = isl_term.max_range_km
            max_tracking_rate_deg_s = isl_term.max_tracking_rate_deg_s
            field_of_regard_deg = isl_term.field_of_regard_deg
        if constellation_config.polar_seam:
            polar_seam_enabled = constellation_config.polar_seam.enabled
            latitude_threshold_deg = constellation_config.polar_seam.latitude_threshold_deg

    default_min_elevation = gs_file.default_min_elevation_deg or 25.0
    epoch_unix = (
        datetime.fromisoformat(session.time.start_time).timestamp()
        if session.time.start_time
        else time.time()
    )
    compression = session.time.compression if session.time.compression else 1
    snapshot_interval_s = get_platform_config().ome_link_state_snapshot_interval_s

    # Build interface map for LinkStateSnapshot
    from nodalarc.models.addressing import neighbors_by_node

    by_node = neighbors_by_node(neighbors)
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

    window = 0
    epoch_for_next = epoch_unix
    isl_state = None
    gs_state = None
    snapshot_seq = 0
    _common_args = dict(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        step_seconds=session.time.step_seconds,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        field_of_regard_deg=field_of_regard_deg,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_min_elevation_deg=default_min_elevation,
    )

    try:
        while not shutdown_event.is_set():
            window += 1
            logging.info("OME continuous: computing window %d (period=%.0fs)", window, period)

            # HeartbeatTick during window computation — sent via queue
            hb_stop = threading.Event()

            def _heartbeat_sender(stop=hb_stop):
                while not stop.is_set():
                    hb = HeartbeatTick(wall_time=datetime.now(UTC), status="computing")
                    _enqueue(SUBJECT_HEARTBEAT, hb.model_dump_json().encode())
                    stop.wait(5)

            hb_thread = threading.Thread(target=_heartbeat_sender, daemon=True)
            hb_thread.start()

            # Compute window (CPU-bound, synchronous)
            kw = dict(**_common_args, epoch_unix=epoch_for_next, duration_s=period)
            if window > 1:
                kw["initial_isl_state"] = isl_state
                kw["initial_gs_state"] = gs_state
                kw["timestamp_offset"] = period * (window - 1)

            pre_window_isl = dict(isl_state) if isl_state else {}
            pre_window_gs = dict(gs_state) if gs_state else {}

            events, isl_state, gs_state = precompute_timeline_window(**kw)

            hb_stop.set()
            hb_thread.join(timeout=1)

            # Write JSONL if --output-dir provided
            if out_path is not None:
                if window == 1:
                    write_timeline_jsonl(events, out_path)
                    sentinel.write_text(str(out_path))
                else:
                    append_timeline_jsonl(events, out_path)

            epoch_for_next += period

            # --- Pacing loop: wall-clock precise event delivery ---
            window_start = time.monotonic()
            window_duration = period / compression
            if not events:
                continue

            first_ts = events[0].timestamp_s
            last_ts = events[-1].timestamp_s
            sim_span = last_ts - first_ts if last_ts > first_ts else 1.0
            pace = window_duration / sim_span

            logging.info(
                "OME pacing: %d events over %.0fs wall (%.3fs/tick)",
                len(events),
                window_duration,
                pace,
            )

            current_sim_time_iso: str = ""
            last_snapshot_sim_s: float = 0.0
            running_isl_state = dict(pre_window_isl)
            running_gs_state = dict(pre_window_gs)

            current_tick_ts: float | None = None
            tick_events: list = []

            for evt in events:
                if shutdown_event.is_set():
                    break

                if current_tick_ts is not None and evt.timestamp_s != current_tick_ts:
                    # Precise wall-clock sleep — blocking, no yield
                    sim_offset = current_tick_ts - first_ts
                    wall_target = window_start + sim_offset * pace
                    now = time.monotonic()
                    if now < wall_target and now < window_start + window_duration:
                        time.sleep(wall_target - now)

                    if time.monotonic() >= window_start + window_duration:
                        break

                    # Enqueue all events for this tick
                    for te in tick_events:
                        payload = te.data.model_dump_json().encode()
                        if te.event_type == "VisibilityEvent":
                            _enqueue(SUBJECT_VISIBILITY_EVENT, payload)
                            vis = te.data
                            pair = (vis.node_a, vis.node_b)
                            is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                            if is_gs:
                                running_gs_state[pair] = (vis.visible, vis.scheduled)
                            else:
                                running_isl_state[pair] = (vis.visible, vis.scheduled)
                        elif te.event_type == "Snapshot":
                            _enqueue(SUBJECT_SNAPSHOT, payload)
                            current_sim_time_iso = te.data.sim_time.isoformat()

                    # ClockTick
                    ct = ClockTick(
                        sim_time=datetime.fromisoformat(current_sim_time_iso)
                        if current_sim_time_iso
                        else datetime.now(UTC),
                        wall_time=datetime.now(UTC),
                        compression_ratio=float(compression),
                    )
                    _enqueue(SUBJECT_CLOCK_TICK, ct.model_dump_json().encode())

                    # LinkStateSnapshot at interval
                    if current_sim_time_iso:
                        current_sim_s = current_tick_ts
                        if current_sim_s - last_snapshot_sim_s >= snapshot_interval_s:
                            snapshot_seq += 1
                            snap = build_link_state_snapshot(
                                isl_state=running_isl_state,
                                gs_state=running_gs_state,
                                interface_map=interface_map,
                                sim_time=datetime.fromisoformat(current_sim_time_iso),
                                seq=snapshot_seq,
                                interval_s=snapshot_interval_s,
                            )
                            _enqueue(SUBJECT_LINK_STATE_SNAPSHOT, snap.model_dump_json().encode())
                            last_snapshot_sim_s = current_sim_s

                    tick_events = []

                current_tick_ts = evt.timestamp_s
                tick_events.append(evt)

            # Publish final tick
            if tick_events and not shutdown_event.is_set():
                for te in tick_events:
                    payload = te.data.model_dump_json().encode()
                    if te.event_type == "VisibilityEvent":
                        _enqueue(SUBJECT_VISIBILITY_EVENT, payload)
                        vis = te.data
                        pair = (vis.node_a, vis.node_b)
                        is_gs = vis.node_a.startswith("gs-") or vis.node_b.startswith("gs-")
                        if is_gs:
                            running_gs_state[pair] = (vis.visible, vis.scheduled)
                        else:
                            running_isl_state[pair] = (vis.visible, vis.scheduled)
                    elif te.event_type == "Snapshot":
                        _enqueue(SUBJECT_SNAPSHOT, payload)
                        current_sim_time_iso = te.data.sim_time.isoformat()

                ct = ClockTick(
                    sim_time=datetime.fromisoformat(current_sim_time_iso)
                    if current_sim_time_iso
                    else datetime.now(UTC),
                    wall_time=datetime.now(UTC),
                    compression_ratio=float(compression),
                )
                _enqueue(SUBJECT_CLOCK_TICK, ct.model_dump_json().encode())

    except KeyboardInterrupt:
        logging.info("OME pacing interrupted")
    finally:
        shutdown_event.set()


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
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

    from nodalarc.platform import init_platform_config

    init_platform_config(Path(args.platform_config))

    if not args.continuous:
        run(args.session, args.output_dir)
        return

    # --- Continuous mode: producer-consumer with two threads ---
    import asyncio
    import queue
    import signal
    import threading

    event_queue: queue.Queue = queue.Queue(maxsize=1000)
    shutdown_event = threading.Event()

    def _signal_handler(signum, frame):
        logging.info("Shutdown signal received (%d)", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Thread 1: NATS publisher — async event loop, consumes from queue
    def _publisher_thread():
        asyncio.run(_nats_publisher_loop(event_queue, shutdown_event))

    pub_thread = threading.Thread(target=_publisher_thread, name="nats-publisher", daemon=True)
    pub_thread.start()

    # Give publisher time to connect before pacing starts
    time.sleep(1)

    # Thread 2 (main thread): Pacing — synchronous, time.sleep(), produces to queue
    _run_pacing(args.session, args.output_dir, event_queue, shutdown_event)

    # Shutdown: send sentinel and wait for publisher to drain
    event_queue.put(None)
    pub_thread.join(timeout=10)
    logging.info("OME stopped")


if __name__ == "__main__":
    main()
