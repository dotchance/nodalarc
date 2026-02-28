"""Event stream — precompute timeline and write/publish events.

Propagates all satellites, computes visibility at each step,
emits ClockTick + TimelinePositionSnapshot every step,
emits VisibilityEvents on state changes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ome.constellation_loader import SatelliteNode
from ome.propagator import (
    GeoPosition,
    Vec3,
    distance_km,
    geodetic_to_ecef,
    propagate_keplerian,
    orbital_period,
)
from ome.visibility import (
    check_ground_visibility,
    check_isl_visibility,
)
from nodalarc.constants import SPEED_OF_LIGHT_KM_S
from nodalarc.models.addressing import AddressingScheme, NeighborAssignment, neighbors_by_node
from nodalarc.models.events import (
    ClockTick,
    NodePosition,
    TimelinePositionSnapshot,
    VisibilityEvent,
)
from nodalarc.models.ground_station import GroundStationFile

logger = logging.getLogger(__name__)


class TimelineEvent:
    """A single event in the precomputed timeline."""

    __slots__ = ("timestamp_s", "event_type", "data")

    def __init__(self, timestamp_s: float, event_type: str, data: Any) -> None:
        self.timestamp_s = timestamp_s
        self.event_type = event_type
        self.data = data


def _compute_positions(
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    epoch_unix: float,
    dt: float,
) -> dict[str, tuple[Vec3, Vec3, GeoPosition]]:
    """Compute ECEF position, ECI velocity, and geodetic for all satellites at time dt."""
    positions: dict[str, tuple[Vec3, Vec3, GeoPosition]] = {}
    for sat in satellites:
        node_id = addressing.sat_id(sat.plane, sat.slot)
        ecef, vel_eci, geo = propagate_keplerian(sat.elements, epoch_unix, dt)
        positions[node_id] = (ecef, vel_eci, geo)
    return positions


def _build_snapshot(
    sat_positions: dict[str, tuple[Vec3, Vec3, GeoPosition]],
    gs_positions: dict[str, tuple[Vec3, GeoPosition]],
) -> dict[str, NodePosition]:
    """Build position snapshot for all nodes."""
    positions: dict[str, NodePosition] = {}

    for node_id, (ecef, vel, geo) in sat_positions.items():
        positions[node_id] = NodePosition(
            lat_deg=geo.lat_deg,
            lon_deg=geo.lon_deg,
            alt_km=geo.alt_km,
            vel_x_km_s=vel.x,
            vel_y_km_s=vel.y,
            vel_z_km_s=vel.z,
        )

    for node_id, (ecef, geo) in gs_positions.items():
        positions[node_id] = NodePosition(
            lat_deg=geo.lat_deg,
            lon_deg=geo.lon_deg,
            alt_km=geo.alt_km,
            vel_x_km_s=0.0,
            vel_y_km_s=0.0,
            vel_z_km_s=0.0,
        )

    return positions


def precompute_timeline(
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    gs_file: GroundStationFile | None,
    neighbors: frozenset[tuple[str, NeighborAssignment]],
    epoch_unix: float,
    duration_s: float,
    step_seconds: int = 1,
    max_range_km: float = 5016.0,
    max_tracking_rate_deg_s: float = 3.0,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    default_min_elevation_deg: float = 25.0,
) -> list[TimelineEvent]:
    """Precompute the full timeline for one orbital period.

    Emits:
    - ClockTick every step_seconds with TimelinePositionSnapshot
    - VisibilityEvent on ISL state changes
    - VisibilityEvent on ground link state changes
    """
    events: list[TimelineEvent] = []
    by_node = neighbors_by_node(neighbors)

    # Pre-compute ground station ECEF positions (fixed)
    gs_positions: dict[str, tuple[Vec3, GeoPosition]] = {}
    gs_min_elevations: dict[str, float] = {}
    if gs_file:
        for i, station in enumerate(gs_file.stations):
            node_id = addressing.gs_id(station.name)
            geo = GeoPosition(station.lat_deg, station.lon_deg, (station.alt_m or 0) / 1000.0)
            ecef = geodetic_to_ecef(geo)
            gs_positions[node_id] = (ecef, geo)
            gs_min_elevations[node_id] = station.min_elevation_deg or gs_file.default_min_elevation_deg or 25.0

    # Track ISL visibility state: (node_a, node_b) -> visible
    isl_state: dict[tuple[str, str], bool] = {}
    # Track GS visibility state: (gs_id, sat_id) -> visible
    gs_state: dict[tuple[str, str], bool] = {}

    steps = int(duration_s / step_seconds)
    for step in range(steps + 1):
        dt = step * step_seconds
        timestamp_s = dt
        sim_time = datetime.fromtimestamp(epoch_unix + dt, tz=timezone.utc)

        # 1. Compute all satellite positions
        sat_positions = _compute_positions(satellites, addressing, epoch_unix, dt)

        # 2. Build and emit ClockTick with snapshot
        snapshot_positions = _build_snapshot(sat_positions, gs_positions)
        snapshot = TimelinePositionSnapshot(
            sim_time=sim_time,
            positions=snapshot_positions,
        )
        clock_tick = ClockTick(
            sim_time=sim_time,
            wall_time=sim_time,  # In precompute mode, wall_time = sim_time
            compression_ratio=1.0,
        )
        events.append(TimelineEvent(timestamp_s, "ClockTick", clock_tick))
        events.append(TimelineEvent(timestamp_s, "Snapshot", snapshot))

        # 3. Check ISL visibility for each assigned neighbor pair
        for sat in satellites:
            node_id = addressing.sat_id(sat.plane, sat.slot)
            node_neighbors = by_node.get(node_id, [])

            if node_id not in sat_positions:
                continue
            pos_a, vel_a, geo_a = sat_positions[node_id]

            for na in node_neighbors:
                peer_id = na.peer_node_id
                if peer_id not in sat_positions:
                    continue

                # Only check each pair once (alphabetical ordering)
                pair = (min(node_id, peer_id), max(node_id, peer_id))
                if pair[0] != node_id:
                    continue

                pos_b, vel_b, geo_b = sat_positions[peer_id]

                is_cross = na.link_type == "cross"
                result = check_isl_visibility(
                    pos_a, vel_a, pos_b, vel_b,
                    max_range_km=max_range_km,
                    max_tracking_rate_deg_s=max_tracking_rate_deg_s if is_cross else None,
                    polar_seam_enabled=polar_seam_enabled and is_cross,
                    latitude_threshold_deg=latitude_threshold_deg,
                    geo_a=geo_a, geo_b=geo_b,
                )

                prev_visible = isl_state.get(pair, False)
                now_visible = result.visible

                if now_visible != prev_visible:
                    isl_state[pair] = now_visible
                    vis_event = VisibilityEvent(
                        sim_time=sim_time,
                        node_a=pair[0],
                        node_b=pair[1],
                        visible=now_visible,
                        scheduled=now_visible,
                        range_km=result.range_km,
                        elevation_deg=None,  # ISL — no elevation
                        terminal_type="optical",
                    )
                    events.append(TimelineEvent(timestamp_s, "VisibilityEvent", vis_event))

        # 4. Check ground station visibility
        for gs_id, (gs_ecef, gs_geo) in gs_positions.items():
            min_elev = gs_min_elevations.get(gs_id, 25.0)
            for sat in satellites:
                sat_id = addressing.sat_id(sat.plane, sat.slot)
                if sat_id not in sat_positions:
                    continue
                sat_ecef, _, _ = sat_positions[sat_id]

                gv = check_ground_visibility(gs_ecef, gs_geo, sat_ecef, min_elev)
                pair = (min(gs_id, sat_id), max(gs_id, sat_id))
                prev_visible = gs_state.get(pair, False)

                if gv.visible != prev_visible:
                    gs_state[pair] = gv.visible
                    vis_event = VisibilityEvent(
                        sim_time=sim_time,
                        node_a=pair[0],
                        node_b=pair[1],
                        visible=gv.visible,
                        scheduled=gv.visible,
                        range_km=gv.range_km,
                        elevation_deg=gv.elevation_deg,
                        terminal_type="optical",
                    )
                    events.append(TimelineEvent(timestamp_s, "VisibilityEvent", vis_event))

    return events


def write_timeline_jsonl(events: list[TimelineEvent], output_path: Path) -> None:
    """Write timeline events to JSON Lines file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for event in events:
            record = {
                "timestamp_s": event.timestamp_s,
                "event_type": event.event_type,
                "data": event.data.model_dump(mode="json"),
            }
            f.write(json.dumps(record) + "\n")
    logger.info(f"Wrote {len(events)} events to {output_path}")


def read_timeline_jsonl(path: Path) -> list[dict]:
    """Read timeline events from JSON Lines file."""
    events = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def publish_timeline_zmq(
    timeline_path: Path,
    compression_factor: float = 1.0,
) -> None:
    """Read pre-computed timeline and publish on ZMQ PUB with timing.

    Binds to OME_EVENTS_BIND (port 5560). Publishes each event with
    topic prefix. Paces at wall-clock × compression_factor.

    Used in RT mode to replay a pre-computed timeline.
    """
    import time
    import zmq
    from nodalarc.zmq_channels import (
        OME_EVENTS_BIND,
        TOPIC_CLOCK_TICK,
        TOPIC_VISIBILITY_EVENT,
        encode_message,
    )

    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind(OME_EVENTS_BIND)
    logger.info(f"OME ZMQ publisher bound on {OME_EVENTS_BIND}")

    # Allow subscribers to connect
    time.sleep(0.5)

    records = read_timeline_jsonl(timeline_path)
    if not records:
        logger.warning("Empty timeline, nothing to publish")
        pub.close()
        ctx.term()
        return

    prev_ts = records[0]["timestamp_s"]
    for record in records:
        # Pace: sleep for the time delta scaled by compression
        ts = record["timestamp_s"]
        delta = (ts - prev_ts) / compression_factor if compression_factor > 0 else 0
        if delta > 0:
            time.sleep(delta)
        prev_ts = ts

        event_type = record["event_type"]
        payload = json.dumps(record["data"]).encode()

        if event_type == "ClockTick":
            pub.send(encode_message(TOPIC_CLOCK_TICK, payload))
        elif event_type == "Snapshot":
            pub.send(encode_message(b"Snapshot", payload))
        elif event_type == "VisibilityEvent":
            pub.send(encode_message(TOPIC_VISIBILITY_EVENT, payload))

    logger.info("OME ZMQ replay complete")
    pub.close()
    ctx.term()
