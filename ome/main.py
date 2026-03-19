"""OME entry point — orchestration only, no logic.

Loads configs via YAML + Pydantic, creates AddressingScheme,
computes ISL neighbor assignments (frozen), calls precompute_timeline(),
writes JSON Lines output.

Under 100 lines.
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
    _start_health_server()

    # Load session config (same as run())
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
    from nodalarc.zmq_channels import ome_events_bind

    ctx = zmq.Context()
    pub_sock = ctx.socket(zmq.PUB)
    pub_sock.bind(ome_events_bind())
    logging.info(f"OME ZMQ PUB bound to {ome_events_bind()}")
    # No sleep after bind — FullStateSnapshot handles subscriber catchup

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

            # Extract position trajectory and VisibilityEvents from window events.
            # Both are included in FullStateSnapshot so late subscribers get:
            # 1. Orbital positions (downsampled to ~10s intervals)
            # 2. Link state changes (all VisibilityEvents preserved)
            # This lets the orchestrator replay the full window: positions move
            # AND links go up/down as the topology evolves.
            position_trajectory: list[dict] = []
            last_position_event = None
            link_ranges: dict[tuple[str, str], float] = {}
            snap_count = 0
            for evt in events:
                if evt.event_type == "Snapshot":
                    last_position_event = evt
                    snap_count += 1
                    if snap_count % 10 == 0 or snap_count == 1:
                        position_trajectory.append(
                            {
                                "timestamp_s": evt.timestamp_s,
                                "event_type": evt.event_type,
                                "data": evt.data.model_dump(mode="json"),
                            }
                        )
                elif evt.event_type == "VisibilityEvent":
                    pair = (evt.data.node_a, evt.data.node_b)
                    link_ranges[pair] = evt.data.range_km
                    # Include ALL visibility events — link transitions must
                    # be replayed for the topology view to show changes.
                    position_trajectory.append(
                        {
                            "timestamp_s": evt.timestamp_s,
                            "event_type": evt.event_type,
                            "data": evt.data.model_dump(mode="json"),
                        }
                    )
            # Always include the last snapshot
            if last_position_event and (snap_count % 10 != 0):
                position_trajectory.append(
                    {
                        "timestamp_s": last_position_event.timestamp_s,
                        "event_type": last_position_event.event_type,
                        "data": last_position_event.data.model_dump(mode="json"),
                    }
                )

            # Compute sim_time for the end of this window
            from datetime import datetime as _dt

            window_end_epoch = epoch_for_next + period
            sim_time_iso = _dt.fromtimestamp(window_end_epoch, tz=UTC).isoformat()

            # Publish FullStateSnapshot at window boundary (includes position trajectory)
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

            # Two independent timers: window pacing + periodic FullStateSnapshot every 30s
            window_duration = period * 0.5 / compression
            while True:
                elapsed = time.monotonic() - window_start
                if elapsed >= window_duration:
                    break
                # Publish periodic FullStateSnapshot every 30s during inter-window sleep
                if time.monotonic() - last_snapshot_wall >= 30.0:
                    publish_full_state_snapshot(
                        pub_sock,
                        isl_state,
                        gs_state,
                        sim_time_iso,
                        link_ranges,
                        position_trajectory=position_trajectory,
                    )
                    last_snapshot_wall = time.monotonic()
                time.sleep(min(1.0, window_duration - elapsed))

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
