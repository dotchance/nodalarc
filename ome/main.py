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

_current_pacing_sim_time: str = ""


def _handle_catchup_requests(catchup_sock, catchup_buffer: dict) -> None:
    """Poll and respond to R-OME-008 CatchupRequests (non-blocking)."""
    import zmq

    try:
        if not catchup_sock.poll(timeout=0):
            return
        raw = catchup_sock.recv_json(zmq.NOBLOCK)
        since = raw.get("since_sim_time")
        resp_events = catchup_buffer["events"]
        if since:
            resp_events = [e for e in resp_events if e.get("data", {}).get("sim_time", "") >= since]
        # Cap at current pacing position — don't return future events not yet paced
        if _current_pacing_sim_time:
            resp_events = [
                e
                for e in resp_events
                if e.get("data", {}).get("sim_time", "") <= _current_pacing_sim_time
            ]
        catchup_sock.send_json(
            {
                "window": catchup_buffer["window"],
                "window_start_sim_time": catchup_buffer["window_start"],
                "window_end_sim_time": catchup_buffer["window_end"],
                "current_pacing_sim_time": _current_pacing_sim_time,
                "events": resp_events,
            }
        )
        logging.info(
            "OME catch-up: served %d events (since=%s)",
            len(resp_events),
            since or "all",
        )
    except zmq.Again:
        pass
    except Exception as exc:
        logging.warning("OME catch-up error: %s", exc)
        import contextlib

        with contextlib.suppress(Exception):
            catchup_sock.send_json({"error": str(exc), "events": []})


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
    publish_full_state_snapshot,
    publish_window_zmq,
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
    global _current_pacing_sim_time
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
    from nodalarc.zmq_channels import TOPIC_POSITION_EVENT, encode_message, ome_events_bind

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

    # Retained window buffer for catch-up (R-OME-008)
    _catchup_buffer: dict = {"window": 0, "events": [], "window_start": "", "window_end": ""}

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
    last_snapshot_wall = 0.0

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
        while True:
            window += 1
            logging.info(f"OME continuous: computing window {window} (period={period:.0f}s)")
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

            # Publish window events + WindowReady on ZMQ
            publish_window_zmq(events, pub_sock, window)

            # R-OME-008: retain window events for catch-up requests.
            # Serialize once, serve many times.
            from datetime import datetime as _dt_buf

            _catchup_buffer["window"] = window
            _catchup_buffer["window_start"] = _dt_buf.fromtimestamp(
                epoch_for_next, tz=UTC
            ).isoformat()
            _catchup_buffer["window_end"] = _dt_buf.fromtimestamp(
                epoch_for_next + period, tz=UTC
            ).isoformat()
            _catchup_buffer["events"] = [
                {
                    "timestamp_s": evt.timestamp_s,
                    "event_type": evt.event_type,
                    "data": evt.data.model_dump(mode="json"),
                }
                for evt in events
            ]
            # Initialize pacing position to window start so catch-up callers
            # before pacing begins get a valid threshold (not empty string).
            _current_pacing_sim_time = _catchup_buffer["window_start"]
            logging.info(
                f"OME catch-up buffer: {len(_catchup_buffer['events'])} events for window {window}"
            )

            # Service any pending catch-up requests immediately
            _handle_catchup_requests(catchup_sock, _catchup_buffer)

            # Build link_ranges and current position snapshot for FullStateSnapshot.
            # Only the LAST Snapshot is included — current positions, not full trajectory.
            # Full trajectory replay is served by R-OME-008 catch-up (port 5568).
            link_ranges: dict[tuple[str, str], float] = {}
            last_snapshot_entry: dict | None = None
            for evt in events:
                if evt.event_type == "VisibilityEvent":
                    pair = (evt.data.node_a, evt.data.node_b)
                    link_ranges[pair] = evt.data.range_km
                elif evt.event_type == "Snapshot":
                    last_snapshot_entry = {
                        "timestamp_s": evt.timestamp_s,
                        "event_type": evt.event_type,
                        "data": evt.data.model_dump(mode="json"),
                    }
            position_trajectory = [last_snapshot_entry] if last_snapshot_entry else []

            # Compute sim_time for the end of this window
            from datetime import datetime as _dt

            window_end_epoch = epoch_for_next + period
            sim_time_iso = _dt.fromtimestamp(window_end_epoch, tz=UTC).isoformat()

            # Publish FullStateSnapshot at window boundary (link state + current positions)
            publish_full_state_snapshot(
                pub_sock,
                isl_state,
                gs_state,
                sim_time_iso,
                link_ranges,
                position_trajectory=position_trajectory,
            )
            last_snapshot_wall = time.monotonic()

            # Also write JSONL if --output-dir provided (debug/development)
            if out_path is not None:
                if window == 1:
                    write_timeline_jsonl(events, out_path)
                    sentinel.write_text(str(out_path))
                else:
                    append_timeline_jsonl(events, out_path)

            epoch_for_next += period

            # R-OME-006: Paced PositionEvent publication through precomputed Snapshots.
            # Instead of sleeping the full inter-window duration with static snapshots,
            # pace through Snapshot events at wall-clock × compression so subscribers
            # (VS-API) see positions advancing continuously.
            window_duration = period / compression
            snapshot_events = [
                e for e in _catchup_buffer.get("events", []) if e.get("event_type") == "Snapshot"
            ]

            if snapshot_events:
                first_ts = snapshot_events[0]["timestamp_s"]
                last_ts = snapshot_events[-1]["timestamp_s"]
                sim_span = last_ts - first_ts if last_ts > first_ts else 1.0
                pace = window_duration / sim_span
                logging.info(
                    f"OME pacing: {len(snapshot_events)} snapshots over "
                    f"{window_duration:.0f}s wall ({pace:.3f}s/tick)"
                )

                for snap in snapshot_events:
                    sim_offset = snap["timestamp_s"] - first_ts
                    wall_target = window_start + sim_offset * pace

                    # Sleep in ≤1s chunks for catchup REP responsiveness
                    while True:
                        now = time.monotonic()
                        if now >= wall_target:
                            break
                        if now >= window_start + window_duration:
                            break
                        time.sleep(min(wall_target - now, 1.0))
                        _handle_catchup_requests(catchup_sock, _catchup_buffer)

                    if time.monotonic() >= window_start + window_duration:
                        break

                    # Convert positions dict → list for _update_position()
                    snap_data = snap.get("data", {})
                    positions_dict = snap_data.get("positions", {})
                    position_list = [
                        {
                            "node_id": nid,
                            "node_type": "ground_station" if nid.startswith("gs-") else "satellite",
                            "lat_deg": p.get("lat_deg", 0.0),
                            "lon_deg": p.get("lon_deg", 0.0),
                            "alt_km": p.get("alt_km", 0.0),
                            "vel_x_km_s": p.get("vel_x_km_s", 0.0),
                            "vel_y_km_s": p.get("vel_y_km_s", 0.0),
                            "vel_z_km_s": p.get("vel_z_km_s", 0.0),
                        }
                        for nid, p in positions_dict.items()
                    ]

                    sim_time_str = snap_data.get("sim_time", "")
                    _current_pacing_sim_time = sim_time_str
                    for node in position_list:
                        payload = json.dumps(
                            {
                                "sim_time": sim_time_str,
                                "node_id": node["node_id"],
                                "lat_deg": node["lat_deg"],
                                "lon_deg": node["lon_deg"],
                                "alt_km": node["alt_km"],
                                "vel_x_km_s": node["vel_x_km_s"],
                                "vel_y_km_s": node["vel_y_km_s"],
                                "vel_z_km_s": node["vel_z_km_s"],
                            }
                        ).encode()
                        pub_sock.send(encode_message(TOPIC_POSITION_EVENT, payload))

                    # FullStateSnapshot at configured interval (for link state catch-up)
                    snapshot_interval = get_platform_config().ome_full_state_snapshot_interval_s
                    if time.monotonic() - last_snapshot_wall >= snapshot_interval:
                        publish_full_state_snapshot(
                            pub_sock,
                            isl_state,
                            gs_state,
                            sim_time_iso,
                            link_ranges,
                            position_trajectory=position_trajectory,
                        )
                        last_snapshot_wall = time.monotonic()

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
