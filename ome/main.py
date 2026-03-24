"""OME entry point — orchestration only, no logic.

Loads configs via YAML + Pydantic, creates AddressingScheme,
computes ISL neighbor assignments (frozen), calls precompute_timeline(),
writes JSON Lines output.

Under 100 lines.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime


def _handle_catchup_requests(catchup_sock, catchup_state: dict) -> None:
    """Poll and respond to R-OME-008 CatchupRequests (non-blocking).

    catchup_state has: "log" (list of VisibilityEvent dicts) and
    "current_sim_time" (ISO8601 string).
    Per streaming architecture v1.2 Section 3.3: VisibilityEvents only.
    """
    import zmq

    try:
        if not catchup_sock.poll(timeout=0):
            return
        raw = catchup_sock.recv_json(zmq.NOBLOCK)
        since = raw.get("since_sim_time")
        log = catchup_state.get("log", [])
        current_sim = catchup_state.get("current_sim_time", "")

        resp_events = [e for e in log if e.get("sim_time", "") >= since] if since else list(log)

        catchup_sock.send_json(
            {
                "events": resp_events,
                "current_sim_time": current_sim,
            }
        )
        logging.info(
            "OME catch-up: served %d VisibilityEvents (since=%s, current=%s)",
            len(resp_events),
            since or "all",
            current_sim[:19] if current_sim else "none",
        )
    except zmq.Again:
        pass
    except Exception as exc:
        logging.warning("OME catch-up error: %s", exc)
        import contextlib

        with contextlib.suppress(Exception):
            catchup_sock.send_json({"error": str(exc), "events": [], "current_sim_time": ""})


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


# Timeline output goes through write_timeline_jsonl() and append_timeline_jsonl()
# exclusively. These are the interface boundary for future ZMQ migration —
# swapping to ZMQ PUB requires changing only these two functions in event_stream.py.
def run_continuous(session_path: str, output_dir: str | None = None) -> None:
    """Long-lived OME: compute rolling windows until interrupted.

    Each window covers one orbital period. Boundary ISL/GS state is carried
    across windows so link events are seamless. The timeline file grows
    continuously and a .ready sentinel signals when the first window is
    available for the dispatcher to start tailing.
    """
    _start_health_server()

    # Wait for session config to appear (Operator creates it after CRD apply)
    session_file = Path(session_path)
    while not session_file.is_file():
        logging.info(f"Waiting for session config at {session_path}...")
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
            isl = constellation_config.default_terminals.isl[0]
            max_range_km = isl.max_range_km
            max_tracking_rate_deg_s = isl.max_tracking_rate_deg_s
            field_of_regard_deg = isl.field_of_regard_deg
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

    # ZMQ PUB socket — primary delivery mechanism
    import zmq
    from nodalarc.zmq_channels import encode_message, ome_events_bind

    ctx = zmq.Context()
    pub_sock = ctx.socket(zmq.PUB)
    pub_sock.bind(ome_events_bind())
    logging.info(f"OME ZMQ PUB bound to {ome_events_bind()}")

    # R-OME-008: REP socket for on-connect catch-up
    from nodalarc.platform import get_platform_config

    catchup_sock = ctx.socket(zmq.REP)
    catchup_bind = get_platform_config().ome_catchup_bind
    catchup_sock.bind(catchup_bind)
    logging.info(f"OME catch-up REP bound to {catchup_bind}")

    # Optional file output for debugging/development
    out_path = None
    sentinel = None
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{session.session.name}-timeline.jsonl"
        sentinel = out_path.with_suffix(".ready")

    window = 0
    epoch_for_next = epoch_unix
    isl_state = None
    gs_state = None
    window_start = time.monotonic()

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

    # Rolling catch-up log: VisibilityEvents only, append-only, dual-bound eviction.
    # Per streaming architecture v1.2 Section 3.3.
    _catchup_log: list[dict] = []
    _current_sim_time_iso: str = ""

    # Eviction config
    max_log_age_s = 2 * period  # 2 orbital periods
    max_log_bytes = 64 * 1024 * 1024  # 64MB default

    from nodalarc.models.events import ClockTick, HeartbeatTick

    try:
        while True:
            window += 1
            logging.info(f"OME continuous: computing window {window} (period={period:.0f}s)")

            # Publish HeartbeatTick every 5s during window computation
            # to prevent subscriber watchdog false triggers.
            import threading

            stop_heartbeat = threading.Event()

            def _heartbeat_loop(stop_event: threading.Event) -> None:
                while not stop_event.is_set():
                    hb = HeartbeatTick(wall_time=datetime.now(UTC), status="computing")
                    pub_sock.send(encode_message(b"HeartbeatTick", hb.model_dump_json().encode()))
                    stop_event.wait(5)

            hb_thread = threading.Thread(
                target=_heartbeat_loop, args=(stop_heartbeat,), daemon=True
            )
            hb_thread.start()

            events, isl_state, gs_state = precompute_timeline_window(
                **_common_args,
                epoch_unix=epoch_for_next,
                duration_s=period,
                **(
                    {
                        "initial_isl_state": isl_state,
                        "initial_gs_state": gs_state,
                        "timestamp_offset": period * (window - 1),
                    }
                    if window > 1
                    else {}
                ),
            )

            stop_heartbeat.set()
            hb_thread.join(timeout=1)

            # Do NOT publish VisibilityEvents at computation time.
            # They will be published during pacing at their sim_time.

            # Write JSONL if --output-dir provided
            if out_path is not None:
                if window == 1:
                    write_timeline_jsonl(events, out_path)
                    sentinel.write_text(str(out_path))
                else:
                    append_timeline_jsonl(events, out_path)

            epoch_for_next += period

            # Pacing loop: iterate ALL precomputed events in sim_time order.
            # Publish each event when its sim_time is reached by the pacing clock.
            # Per streaming architecture v1.2 Section 3.2.
            window_start = time.monotonic()  # Reset AFTER computation
            window_duration = period / compression
            if not events:
                window_start = time.monotonic()
                continue

            first_ts = events[0].timestamp_s
            last_ts = events[-1].timestamp_s
            sim_span = last_ts - first_ts if last_ts > first_ts else 1.0
            pace = window_duration / sim_span

            logging.info(
                f"OME pacing: {len(events)} events over "
                f"{window_duration:.0f}s wall ({pace:.3f}s/tick)"
            )

            # Group events by timestamp_s for per-tick processing
            current_tick_ts: float | None = None
            tick_events: list = []

            for evt in events:
                if current_tick_ts is not None and evt.timestamp_s != current_tick_ts:
                    # New tick — sleep until this tick's wall target, then publish
                    sim_offset = current_tick_ts - first_ts
                    wall_target = window_start + sim_offset * pace

                    while True:
                        now = time.monotonic()
                        if now >= wall_target:
                            break
                        if now >= window_start + window_duration:
                            break
                        time.sleep(min(wall_target - now, 1.0))
                        _handle_catchup_requests(
                            catchup_sock,
                            {"log": _catchup_log, "current_sim_time": _current_sim_time_iso},
                        )

                    if time.monotonic() >= window_start + window_duration:
                        break

                    # Publish all events for this tick
                    tick_vis: list[dict] = []
                    for te in tick_events:
                        topic = te.event_type.encode()
                        payload = te.data.model_dump_json().encode()
                        pub_sock.send(encode_message(topic, payload))

                        if te.event_type == "VisibilityEvent":
                            tick_vis.append(te.data.model_dump(mode="json"))

                        if te.event_type == "Snapshot":
                            _current_sim_time_iso = te.data.sim_time.isoformat()

                    # Publish ClockTick for this tick
                    ct = ClockTick(
                        sim_time=datetime.fromisoformat(_current_sim_time_iso)
                        if _current_sim_time_iso
                        else datetime.now(UTC),
                        wall_time=datetime.now(UTC),
                        compression_ratio=float(compression),
                    )
                    pub_sock.send(encode_message(b"ClockTick", ct.model_dump_json().encode()))

                    # Append VisibilityEvents atomically to catch-up log
                    if tick_vis:
                        _catchup_log.extend(tick_vis)

                    # Service catch-up requests
                    _handle_catchup_requests(
                        catchup_sock,
                        {"log": _catchup_log, "current_sim_time": _current_sim_time_iso},
                    )

                    # Evict old entries (dual-bound)
                    if _catchup_log and _current_sim_time_iso:
                        cutoff = (
                            datetime.fromisoformat(_current_sim_time_iso)
                            - __import__("datetime").timedelta(seconds=max_log_age_s)
                        ).isoformat()
                        while _catchup_log and _catchup_log[0].get("sim_time", "") < cutoff:
                            _catchup_log.pop(0)
                        total_bytes = sum(len(json.dumps(e)) for e in _catchup_log[:100]) * (
                            len(_catchup_log) / max(len(_catchup_log[:100]), 1)
                        )
                        while _catchup_log and total_bytes > max_log_bytes:
                            _catchup_log.pop(0)
                            total_bytes = sum(len(json.dumps(e)) for e in _catchup_log[:100]) * (
                                len(_catchup_log) / max(len(_catchup_log[:100]), 1)
                            )

                    tick_events = []

                current_tick_ts = evt.timestamp_s
                tick_events.append(evt)

            # Publish final tick
            if tick_events:
                sim_offset = current_tick_ts - first_ts
                wall_target = window_start + sim_offset * pace
                while True:
                    now = time.monotonic()
                    if now >= wall_target:
                        break
                    if now >= window_start + window_duration:
                        break
                    time.sleep(min(wall_target - now, 1.0))
                    _handle_catchup_requests(
                        catchup_sock,
                        {"log": _catchup_log, "current_sim_time": _current_sim_time_iso},
                    )

                tick_vis = []
                for te in tick_events:
                    topic = te.event_type.encode()
                    payload = te.data.model_dump_json().encode()
                    pub_sock.send(encode_message(topic, payload))
                    if te.event_type == "VisibilityEvent":
                        tick_vis.append(te.data.model_dump(mode="json"))
                    if te.event_type == "Snapshot":
                        _current_sim_time_iso = te.data.sim_time.isoformat()

                ct = ClockTick(
                    sim_time=datetime.fromisoformat(_current_sim_time_iso)
                    if _current_sim_time_iso
                    else datetime.now(UTC),
                    wall_time=datetime.now(UTC),
                    compression_ratio=float(compression),
                )
                pub_sock.send(encode_message(b"ClockTick", ct.model_dump_json().encode()))
                if tick_vis:
                    _catchup_log.extend(tick_vis)

            window_start = time.monotonic()
    except KeyboardInterrupt:
        logging.info("OME continuous mode interrupted, exiting")
    finally:
        pub_sock.close()
        ctx.term()


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
        help="Run in continuous mode (rolling windows + ZMQ publish)",
    )
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    args = parser.parse_args()

    from nodalarc.platform import init_platform_config

    init_platform_config(Path(args.platform_config))

    if args.continuous:
        run_continuous(args.session, args.output_dir)
    else:
        run(args.session, args.output_dir)


if __name__ == "__main__":
    main()
