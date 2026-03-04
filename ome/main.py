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
from datetime import datetime
from pathlib import Path

import yaml

from ome.constellation_loader import expand_constellation, load_constellation, load_ground_stations
from ome.event_stream import (
    append_timeline_jsonl,
    precompute_timeline,
    precompute_timeline_window,
    write_timeline_jsonl,
)
from ome.propagator import orbital_period
from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors
from nodalarc.models.session import SessionConfig


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
    polar_seam_enabled = False
    latitude_threshold_deg = 70.0

    from nodalarc.models.constellation import ParametricConstellation
    if isinstance(constellation_config, ParametricConstellation):
        if constellation_config.default_terminals.isl:
            isl = constellation_config.default_terminals.isl[0]
            max_range_km = isl.max_range_km
            max_tracking_rate_deg_s = isl.max_tracking_rate_deg_s
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
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_min_elevation_deg=default_min_elevation,
    )

    # Write output
    out_dir = Path(output_dir) if output_dir else Path("output")
    out_path = out_dir / f"{session.session.name}-timeline.jsonl"
    write_timeline_jsonl(events, out_path)

    logging.info(f"OME complete: {len(events)} events, {len(satellites)} satellites, period={period:.0f}s")
    return out_path


def run_continuous(session_path: str, output_dir: str | None = None) -> None:
    """Long-lived OME: compute rolling windows until interrupted.

    Each window covers one orbital period. Boundary ISL/GS state is carried
    across windows so link events are seamless. The timeline file grows
    continuously and a .ready sentinel signals when the first window is
    available for the dispatcher to start tailing.
    """
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
    polar_seam_enabled = False
    latitude_threshold_deg = 70.0

    from nodalarc.models.constellation import ParametricConstellation
    if isinstance(constellation_config, ParametricConstellation):
        if constellation_config.default_terminals.isl:
            isl = constellation_config.default_terminals.isl[0]
            max_range_km = isl.max_range_km
            max_tracking_rate_deg_s = isl.max_tracking_rate_deg_s
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

    out_dir = Path(output_dir) if output_dir else Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{session.session.name}-timeline.jsonl"
    sentinel = out_path.with_suffix(".ready")

    # Window 1: compute and write (overwrite)
    logging.info(f"OME continuous: computing window 1 (period={period:.0f}s)")
    events, isl_state, gs_state = precompute_timeline_window(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=epoch_unix,
        duration_s=period,
        step_seconds=session.time.step_seconds,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_min_elevation_deg=default_min_elevation,
    )
    write_timeline_jsonl(events, out_path)
    sentinel.write_text(str(out_path))
    logging.info(f"OME window 1 written: {len(events)} events, sentinel at {sentinel}")

    window = 1
    epoch_for_next = epoch_unix + period

    try:
        while True:
            window += 1
            compute_start = time.monotonic()
            events, isl_state, gs_state = precompute_timeline_window(
                satellites=satellites,
                addressing=addressing,
                gs_file=gs_file,
                neighbors=neighbors,
                epoch_unix=epoch_for_next,
                duration_s=period,
                step_seconds=session.time.step_seconds,
                max_range_km=max_range_km,
                max_tracking_rate_deg_s=max_tracking_rate_deg_s,
                polar_seam_enabled=polar_seam_enabled,
                latitude_threshold_deg=latitude_threshold_deg,
                default_min_elevation_deg=default_min_elevation,
                initial_isl_state=isl_state,
                initial_gs_state=gs_state,
                timestamp_offset=period * (window - 1),
            )
            append_timeline_jsonl(events, out_path)
            epoch_for_next += period
            logging.info(
                f"OME window {window} appended: {len(events)} events, "
                f"t={period * (window - 1):.0f}s – {period * window:.0f}s"
            )

            # Sleep until dispatcher is ~50% through current window.
            consumed_wall_s = period * 0.5 / compression
            compute_elapsed = time.monotonic() - compute_start
            sleep_s = max(0, consumed_wall_s - compute_elapsed)
            logging.info(f"OME sleeping {sleep_s:.1f}s before next window")
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        logging.info("OME continuous mode interrupted, exiting")


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Orbital Mechanics Engine")
    parser.add_argument("session", help="Path to session YAML config")
    parser.add_argument("--output-dir", "-o", help="Output directory", default="output")
    parser.add_argument("--continuous", action="store_true",
                        help="Run in continuous mode (rolling windows)")
    args = parser.parse_args()
    if args.continuous:
        run_continuous(args.session, args.output_dir)
    else:
        run(args.session, args.output_dir)


if __name__ == "__main__":
    main()
