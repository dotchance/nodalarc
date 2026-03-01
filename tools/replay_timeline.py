#!/usr/bin/env python3
"""Replay a pre-computed JSONL timeline over ZeroMQ, looping forever.

Usage:
    python tools/replay_timeline.py <timeline.jsonl> [--delay 0.2]

Publishes PositionEvent and LinkUp/LinkDown messages that VS-API consumes,
using the same encode_message format (topic\\x00payload in a single frame).
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/home/chance/nodal")

import zmq
from lib.nodalarc.zmq_channels import (
    OME_EVENTS_BIND,
    TO_EVENTS_BIND,
    encode_message,
    TOPIC_POSITION_EVENT,
    TOPIC_LINK_UP,
    TOPIC_LINK_DOWN,
)


def compute_area(plane: int | None, planes_per_stripe: int = 2) -> str | None:
    """Compute IS-IS area ID from plane index using stripe strategy."""
    if plane is None:
        return None
    stripe = plane // planes_per_stripe
    return f"49.{stripe + 1:04d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay timeline over ZMQ")
    parser.add_argument("timeline", help="Path to .jsonl timeline file")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds per event batch (default: 0.2)")
    args = parser.parse_args()

    # Read all events
    events: list[dict] = []
    with open(args.timeline) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    print(f"Loaded {len(events)} events from {args.timeline}")

    ctx = zmq.Context()

    # OME PUB socket (port 5560) - PositionEvents
    ome_pub = ctx.socket(zmq.PUB)
    ome_pub.bind(OME_EVENTS_BIND)
    print(f"OME PUB bound to {OME_EVENTS_BIND}")

    # TO PUB socket (port 5561) - LinkUp/LinkDown
    to_pub = ctx.socket(zmq.PUB)
    to_pub.bind(TO_EVENTS_BIND)
    print(f"TO PUB bound to {TO_EVENTS_BIND}")

    # Let subscribers connect
    time.sleep(1.0)
    print("Starting replay loop...")

    # Track active links for proper link up/down events
    active_links: set[tuple[str, str]] = set()

    loop_count = 0
    while True:
        loop_count += 1
        print(f"\n--- Loop {loop_count} ---")
        active_links.clear()

        # Group events by timestamp
        batches: dict[float, list[dict]] = {}
        for ev in events:
            ts = ev["timestamp_s"]
            if ts not in batches:
                batches[ts] = []
            batches[ts].append(ev)

        for ts in sorted(batches.keys()):
            batch = batches[ts]

            for ev in batch:
                etype = ev["event_type"]
                data = ev["data"]

                if etype == "Snapshot":
                    # Convert timeline Snapshot to PositionEvent format
                    positions_list = []
                    for node_id, pos in data.get("positions", {}).items():
                        node_type = "ground_station" if node_id.startswith("gs-") else "satellite"
                        plane = None
                        slot = None
                        if node_type == "satellite":
                            parts = node_id.replace("sat-P", "").split("S")
                            if len(parts) == 2:
                                plane = int(parts[0])
                                slot = int(parts[1])
                        area = compute_area(plane) if node_type == "satellite" else "49.0000"
                        positions_list.append({
                            "node_id": node_id,
                            "node_type": node_type,
                            "lat_deg": pos["lat_deg"],
                            "lon_deg": pos["lon_deg"],
                            "alt_km": pos["alt_km"],
                            "vel_x_km_s": pos.get("vel_x_km_s"),
                            "vel_y_km_s": pos.get("vel_y_km_s"),
                            "vel_z_km_s": pos.get("vel_z_km_s"),
                            "plane": plane,
                            "slot": slot,
                            "routing_area": area,
                            "neighbor_count": 0,
                            "isl_count": 0,
                            "gnd_count": 0,
                        })
                    position_data = {
                        "sim_time": data["sim_time"],
                        "positions": positions_list,
                    }
                    ome_pub.send(encode_message(
                        TOPIC_POSITION_EVENT,
                        json.dumps(position_data).encode(),
                    ))

                elif etype == "VisibilityEvent":
                    node_a = data["node_a"]
                    node_b = data["node_b"]
                    pair = (min(node_a, node_b), max(node_a, node_b))
                    visible = data["visible"]
                    scheduled = data.get("scheduled", False)

                    if visible and scheduled:
                        if pair not in active_links:
                            active_links.add(pair)
                            link_data = {
                                "sim_time": data["sim_time"],
                                "node_a": node_a,
                                "node_b": node_b,
                                "reason": data.get("terminal_type", "optical"),
                                "latency_ms": 0.0,
                                "bandwidth_mbps": 0.0,
                            }
                            to_pub.send(encode_message(
                                TOPIC_LINK_UP,
                                json.dumps(link_data).encode(),
                            ))
                    elif not visible:
                        if pair in active_links:
                            active_links.discard(pair)
                            link_data = {
                                "sim_time": data["sim_time"],
                                "node_a": node_a,
                                "node_b": node_b,
                                "reason": data.get("reason", "vis_lost"),
                            }
                            to_pub.send(encode_message(
                                TOPIC_LINK_DOWN,
                                json.dumps(link_data).encode(),
                            ))

            time.sleep(args.delay)

        print(f"Loop {loop_count} complete ({len(events)} events)")
        time.sleep(1.0)


if __name__ == "__main__":
    main()
