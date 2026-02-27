"""OME entry point — orchestration only, no logic.

Loads configs via YAML + Pydantic, creates AddressingScheme,
computes ISL neighbor assignments (frozen), calls precompute_timeline(),
writes JSON Lines output.

Under 100 lines.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import yaml

from ome.constellation_loader import expand_constellation, load_constellation, load_ground_stations
from ome.event_stream import precompute_timeline, write_timeline_jsonl
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
        epoch_unix=1735689600.0,  # 2025-01-01T00:00:00 UTC
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


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Orbital Mechanics Engine")
    parser.add_argument("session", help="Path to session YAML config")
    parser.add_argument("--output-dir", "-o", help="Output directory", default="output")
    args = parser.parse_args()
    run(args.session, args.output_dir)


if __name__ == "__main__":
    main()
