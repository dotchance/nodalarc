"""Event stream — precompute timeline and write/publish events.

Propagates all satellites, computes visibility at each step,
emits ClockTick + TimelinePositionSnapshot every step,
emits VisibilityEvents on state changes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nodalarc.models.addressing import AddressingScheme, NeighborAssignment, neighbors_by_node
from nodalarc.models.events import (
    ClockTick,
    NodePosition,
    TimelinePositionSnapshot,
    VisibilityEvent,
)
from nodalarc.models.ground_station import GroundStationFile

from ome.constellation_loader import SatelliteNode
from ome.propagator import (
    GeoPosition,
    Vec3,
    geodetic_to_ecef,
    propagate_keplerian,
)
from ome.visibility import (
    GroundVisibility,
    check_ground_visibility,
    check_isl_visibility,
    enforce_symmetric_scheduling,
    schedule_ground_links,
    schedule_isl_terminals,
)

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

    for node_id, (_ecef, vel, geo) in sat_positions.items():
        positions[node_id] = NodePosition(
            lat_deg=geo.lat_deg,
            lon_deg=geo.lon_deg,
            alt_km=geo.alt_km,
            vel_x_km_s=vel.x,
            vel_y_km_s=vel.y,
            vel_z_km_s=vel.z,
        )

    for node_id, (_ecef, geo) in gs_positions.items():
        positions[node_id] = NodePosition(
            lat_deg=geo.lat_deg,
            lon_deg=geo.lon_deg,
            alt_km=geo.alt_km,
            vel_x_km_s=0.0,
            vel_y_km_s=0.0,
            vel_z_km_s=0.0,
        )

    return positions


def precompute_timeline_window(
    satellites: list[SatelliteNode],
    addressing: AddressingScheme,
    gs_file: GroundStationFile | None,
    neighbors: frozenset[tuple[str, NeighborAssignment]],
    epoch_unix: float,
    duration_s: float,
    step_seconds: int = 1,
    max_range_km: float = 5016.0,
    max_tracking_rate_deg_s: float = 3.0,
    field_of_regard_deg: float = 360.0,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    default_min_elevation_deg: float = 25.0,
    initial_isl_state: dict[tuple[str, str], tuple[bool, bool]] | None = None,
    initial_gs_state: dict[tuple[str, str], tuple[bool, bool]] | None = None,
    timestamp_offset: float = 0.0,
) -> tuple[
    list[TimelineEvent],
    dict[tuple[str, str], tuple[bool, bool]],
    dict[tuple[str, str], tuple[bool, bool]],
]:
    """Precompute a single window of the timeline.

    Emits:
    - ClockTick every step_seconds with TimelinePositionSnapshot
    - VisibilityEvent on ISL state changes
    - VisibilityEvent on ground link state changes

    Returns (events, isl_state, gs_state) so the caller can carry boundary
    state into the next window for continuous operation.
    """
    events: list[TimelineEvent] = []
    by_node = neighbors_by_node(neighbors)

    # Build satellite terminal count lookup
    sat_isl_terminals: dict[str, int] = {}
    for sat in satellites:
        nid = addressing.sat_id(sat.plane, sat.slot)
        sat_isl_terminals[nid] = sat.isl_terminal_count

    # Pre-compute ground station ECEF positions, terminal counts, policies
    gs_positions: dict[str, tuple[Vec3, GeoPosition]] = {}
    gs_min_elevations: dict[str, float] = {}
    gs_terminal_counts: dict[str, int] = {}
    gs_policies: dict[str, str] = {}
    if gs_file:
        default_gs_count = sum(t.count for t in gs_file.default_terminals)
        default_gs_policy = gs_file.default_scheduling_policy or "highest-elevation"
        for _i, station in enumerate(gs_file.stations):
            node_id = addressing.gs_id(station.name)
            geo = GeoPosition(station.lat_deg, station.lon_deg, (station.alt_m or 0) / 1000.0)
            ecef = geodetic_to_ecef(geo)
            gs_positions[node_id] = (ecef, geo)
            gs_min_elevations[node_id] = (
                station.min_elevation_deg or gs_file.default_min_elevation_deg or 25.0
            )
            gs_terminal_counts[node_id] = (
                sum(t.count for t in station.terminals) if station.terminals else default_gs_count
            )
            gs_policies[node_id] = station.scheduling_policy or default_gs_policy

    # Track ISL state: (node_a, node_b) -> (visible, scheduled)
    isl_state: dict[tuple[str, str], tuple[bool, bool]] = (
        dict(initial_isl_state) if initial_isl_state else {}
    )
    # Track GS state: (gs_id, sat_id) -> (visible, scheduled)
    gs_state: dict[tuple[str, str], tuple[bool, bool]] = (
        dict(initial_gs_state) if initial_gs_state else {}
    )

    steps = int(duration_s / step_seconds)
    for step in range(steps + 1):
        dt = step * step_seconds
        timestamp_s = dt + timestamp_offset
        sim_time = datetime.fromtimestamp(epoch_unix + dt, tz=UTC)

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

        # 3. Check ISL visibility for all assigned neighbor pairs
        isl_visibility: dict[tuple[str, str], tuple[bool, float]] = {}

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

                is_cross = na.link_type == "cross_plane_isl"
                result = check_isl_visibility(
                    pos_a,
                    vel_a,
                    pos_b,
                    vel_b,
                    max_range_km=max_range_km,
                    max_tracking_rate_deg_s=max_tracking_rate_deg_s if is_cross else None,
                    field_of_regard_deg=field_of_regard_deg,
                    polar_seam_enabled=polar_seam_enabled and is_cross,
                    latitude_threshold_deg=latitude_threshold_deg,
                    geo_a=geo_a,
                    geo_b=geo_b,
                )

                isl_visibility[pair] = (result.visible, result.range_km)

        # 4. Schedule ISL terminals per node
        node_feasible_isls: dict[str, list[tuple[str, int, float]]] = {}
        for pair, (visible, range_km) in isl_visibility.items():
            if not visible:
                continue
            node_a, node_b = pair
            # Find priority from each node's perspective
            for na in by_node.get(node_a, []):
                if na.peer_node_id == node_b:
                    node_feasible_isls.setdefault(node_a, []).append(
                        (node_b, na.priority, range_km),
                    )
                    break
            for na in by_node.get(node_b, []):
                if na.peer_node_id == node_a:
                    node_feasible_isls.setdefault(node_b, []).append(
                        (node_a, na.priority, range_km),
                    )
                    break

        all_isl_schedules: dict[str, list] = {}
        for nid, feasible in node_feasible_isls.items():
            tc = sat_isl_terminals.get(nid, 2)
            all_isl_schedules[nid] = schedule_isl_terminals(nid, feasible, tc)

        all_isl_schedules = enforce_symmetric_scheduling(all_isl_schedules)

        # Build pair -> scheduled lookup (both sides must agree)
        isl_scheduled: dict[tuple[str, str], bool] = {}
        for _nid, links in all_isl_schedules.items():
            for link in links:
                pair = (min(link.node_a, link.node_b), max(link.node_a, link.node_b))
                if pair not in isl_scheduled:
                    isl_scheduled[pair] = link.scheduled
                else:
                    isl_scheduled[pair] = isl_scheduled[pair] and link.scheduled

        # 5. Emit ISL visibility events on state changes
        for pair, (visible, range_km) in isl_visibility.items():
            scheduled = isl_scheduled.get(pair, False) if visible else False
            prev_state = isl_state.get(pair, (False, False))
            new_state = (visible, scheduled)

            if new_state != prev_state:
                isl_state[pair] = new_state
                vis_event = VisibilityEvent(
                    sim_time=sim_time,
                    node_a=pair[0],
                    node_b=pair[1],
                    visible=visible,
                    scheduled=scheduled,
                    range_km=range_km,
                    elevation_deg=None,  # ISL — no elevation
                    terminal_type="optical",
                )
                events.append(TimelineEvent(timestamp_s, "VisibilityEvent", vis_event))

        # 6. Check ground station visibility and schedule
        gs_vis_details: dict[tuple[str, str], tuple[bool, float, float | None]] = {}
        gs_visible_per_station: dict[str, list[GroundVisibility]] = {}

        for gs_id, (gs_ecef, gs_geo) in gs_positions.items():
            min_elev = gs_min_elevations.get(gs_id, 25.0)
            visible_sats: list[GroundVisibility] = []
            for sat in satellites:
                sat_id = addressing.sat_id(sat.plane, sat.slot)
                if sat_id not in sat_positions:
                    continue
                sat_ecef, _, _ = sat_positions[sat_id]

                gv = check_ground_visibility(gs_ecef, gs_geo, sat_ecef, min_elev)
                pair = (min(gs_id, sat_id), max(gs_id, sat_id))
                gs_vis_details[pair] = (gv.visible, gv.range_km, gv.elevation_deg)
                if gv.visible:
                    visible_sats.append(
                        GroundVisibility(sat_id, gv.visible, gv.elevation_deg, gv.range_km),
                    )
            gs_visible_per_station[gs_id] = visible_sats

        # 7. Schedule ground links per station
        gs_scheduled: dict[tuple[str, str], bool] = {}
        for gs_id, visible_sats in gs_visible_per_station.items():
            tc = gs_terminal_counts.get(gs_id, 1)
            policy = gs_policies.get(gs_id, "highest-elevation")
            for sl in schedule_ground_links(gs_id, visible_sats, tc, policy):
                pair = (min(sl.node_a, sl.node_b), max(sl.node_a, sl.node_b))
                gs_scheduled[pair] = sl.scheduled

        # 8. Emit ground visibility events on state changes
        for pair, (visible, range_km, elev_deg) in gs_vis_details.items():
            scheduled = gs_scheduled.get(pair, False) if visible else False
            prev_state = gs_state.get(pair, (False, False))
            new_state = (visible, scheduled)

            if new_state != prev_state:
                gs_state[pair] = new_state
                vis_event = VisibilityEvent(
                    sim_time=sim_time,
                    node_a=pair[0],
                    node_b=pair[1],
                    visible=visible,
                    scheduled=scheduled,
                    range_km=range_km,
                    elevation_deg=elev_deg,
                    terminal_type="optical",
                )
                events.append(TimelineEvent(timestamp_s, "VisibilityEvent", vis_event))

    return events, isl_state, gs_state


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
    field_of_regard_deg: float = 360.0,
    polar_seam_enabled: bool = False,
    latitude_threshold_deg: float = 70.0,
    default_min_elevation_deg: float = 25.0,
) -> list[TimelineEvent]:
    """Single-window convenience wrapper (backward compat).

    Returns only events, discarding boundary state.
    """
    events, _, _ = precompute_timeline_window(
        satellites=satellites,
        addressing=addressing,
        gs_file=gs_file,
        neighbors=neighbors,
        epoch_unix=epoch_unix,
        duration_s=duration_s,
        step_seconds=step_seconds,
        max_range_km=max_range_km,
        max_tracking_rate_deg_s=max_tracking_rate_deg_s,
        field_of_regard_deg=field_of_regard_deg,
        polar_seam_enabled=polar_seam_enabled,
        latitude_threshold_deg=latitude_threshold_deg,
        default_min_elevation_deg=default_min_elevation_deg,
    )
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


def append_timeline_jsonl(events: list[TimelineEvent], output_path: Path) -> None:
    """Append events to an existing JSONL file (or create it).

    Uses fsync to ensure the dispatcher's tail-reader sees complete lines.
    """
    import os

    with open(output_path, "a") as f:
        for event in events:
            record = {
                "timestamp_s": event.timestamp_s,
                "event_type": event.event_type,
                "data": event.data.model_dump(mode="json"),
            }
            f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())
    logger.info(f"Appended {len(events)} events to {output_path}")


def publish_window_zmq(events: list[TimelineEvent], pub_sock, window_num: int) -> None:
    """Publish a window of timeline events on ZMQ PUB socket.

    Interface boundary for ZMQ delivery — same role as write_timeline_jsonl()
    for file delivery. run_continuous() calls this function, not inline ZMQ code.

    Publishes each event with its event_type as the ZMQ topic, followed by a
    WindowReady message that signals the batch boundary to the orchestrator.
    """
    from nodalarc.zmq_channels import encode_message

    for event in events:
        topic = event.event_type.encode()
        payload = json.dumps(
            {
                "timestamp_s": event.timestamp_s,
                "event_type": event.event_type,
                "data": event.data.model_dump(mode="json"),
            }
        ).encode()
        pub_sock.send(encode_message(topic, payload))

    # Window boundary signal — orchestrator buffers events until this arrives
    pub_sock.send(
        encode_message(
            b"WindowReady",
            json.dumps(
                {
                    "window": window_num,
                    "event_count": len(events),
                }
            ).encode(),
        )
    )
    logger.info(f"Published window {window_num}: {len(events)} events on ZMQ")


def publish_full_state_snapshot(
    pub_sock,
    isl_state: dict[tuple[str, str], tuple[bool, bool]],
    gs_state: dict[tuple[str, str], tuple[bool, bool]],
    sim_time_iso: str,
    link_ranges: dict[tuple[str, str], float] | None = None,
    position_trajectory: list[dict] | None = None,
) -> None:
    """Publish complete link state for subscriber catchup.

    Published every 30 seconds and at window boundaries. Subscribers that
    miss events (slow joiner, restart) catch up from the next snapshot.
    This is the solution to the ZMQ slow joiner problem — no sleep after
    bind, no assumptions about subscriber timing.

    The optional link_ranges dict provides range_km for each link pair,
    enabling subscribers to compute accurate link latencies even when
    they missed the VisibilityEvents that originally carried the range.
    """
    from nodalarc.zmq_channels import encode_message

    ranges = link_ranges or {}

    # Serialize state dicts with string keys (tuple keys can't be JSON keys)
    isl_data = {}
    for (a, b), (visible, scheduled) in isl_state.items():
        entry: dict[str, Any] = {"visible": visible, "scheduled": scheduled}
        r = ranges.get((a, b), 0.0)
        if r > 0:
            entry["range_km"] = r
        isl_data[f"{a}:{b}"] = entry
    gs_data = {}
    for (a, b), (visible, scheduled) in gs_state.items():
        entry = {"visible": visible, "scheduled": scheduled}
        r = ranges.get((a, b), 0.0)
        if r > 0:
            entry["range_km"] = r
        gs_data[f"{a}:{b}"] = entry

    snapshot_dict: dict[str, Any] = {
        "sim_time": sim_time_iso,
        "isl_state": isl_data,
        "gs_state": gs_data,
    }
    if position_trajectory:
        snapshot_dict["position_trajectory"] = position_trajectory

    payload = json.dumps(snapshot_dict).encode()
    pub_sock.send(encode_message(b"FullStateSnapshot", payload))
    logger.info(
        f"Published FullStateSnapshot: {len(isl_data)} ISL + {len(gs_data)} GS pairs"
        + (f" + {len(position_trajectory)} positions" if position_trajectory else "")
    )


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
        TOPIC_CLOCK_TICK,
        TOPIC_VISIBILITY_EVENT,
        encode_message,
        ome_events_bind,
    )

    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind(ome_events_bind())
    logger.info(f"OME ZMQ publisher bound on {ome_events_bind()}")

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
