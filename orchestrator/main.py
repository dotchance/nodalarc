"""Topology Orchestrator entry point — orchestration only, no logic.

CLI entry point. Loads configs, initializes subsystems, manages
scenario override set, starts appropriate dispatcher.

Under 100 lines.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml
import zmq

from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors, neighbors_by_node
from nodalarc.models.link_events import LinkDown, LinkUp
from nodalarc.models.session import SessionConfig
from nodalarc.zmq_channels import TO_SCENARIO_INJECT_BIND, encode_message, TOPIC_LINK_DOWN, TOPIC_LINK_UP
from orchestrator.discrete_event_dispatcher import DiscreteEventDispatcher
from orchestrator.realtime_dispatcher import RealtimeDispatcher

log = logging.getLogger(__name__)

# Scenario override set — shared between dispatcher and scenario handler
override_set: set[tuple[str, str]] = set()
override_lock = threading.Lock()


def _build_interface_map(
    session: SessionConfig,
    addressing: AddressingScheme,
) -> tuple[dict[tuple[str, str], tuple[str, str]], dict[tuple[str, str], float]]:
    """Build interface and bandwidth maps from ISL + GS neighbor assignments."""
    from pydantic import TypeAdapter
    from nodalarc.models.constellation import ConstellationConfig
    from nodalarc.models.ground_station import GroundStationFile
    from ome.constellation_loader import expand_constellation
    adapter = TypeAdapter(ConstellationConfig)
    constellation = adapter.validate_python(
        yaml.safe_load(Path(session.constellation).read_text()),
    )
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    bandwidth_map: dict[tuple[str, str], float] = {}

    # ISL links
    for node_id, assignments in by_node.items():
        for na in assignments:
            pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
            if pair not in interface_map:
                interface_map[pair] = (na.interface, "")
                bandwidth_map[pair] = 1000.0
            else:
                existing = interface_map[pair]
                if existing[0] and not existing[1]:
                    interface_map[pair] = (existing[0], na.interface)

    # GS-satellite links (all use gnd0 on both sides)
    gs_data = yaml.safe_load(Path(session.ground_stations).read_text())
    gs_file = GroundStationFile.model_validate(gs_data)
    satellites = expand_constellation(constellation)
    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        for sat in satellites:
            sat_id = addressing.sat_id(sat.plane, sat.slot)
            pair = (min(gs_id, sat_id), max(gs_id, sat_id))
            interface_map[pair] = ("gnd0", "gnd0")
            bandwidth_map[pair] = 1000.0

    return interface_map, bandwidth_map


def _scenario_handler(
    pub_sock: zmq.Socket,
    interface_map: dict[tuple[str, str], tuple[str, str]],
) -> None:
    """Handle scenario injection requests on port 5564."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REP)
    sock.bind(TO_SCENARIO_INJECT_BIND)
    log.info(f"Scenario handler bound on {TO_SCENARIO_INJECT_BIND}")

    try:
        while True:
            raw = sock.recv()
            cmd = json.loads(raw)
            action = cmd.get("action", "")
            now = datetime.now(timezone.utc)

            if action == "inject_link_down":
                pair = (cmd["node_a"], cmd["node_b"])
                pair = (min(pair), max(pair))
                with override_lock:
                    override_set.add(pair)
                event = LinkDown(
                    sim_time=now, wall_time=now,
                    node_a=pair[0], node_b=pair[1],
                    interface_a="", interface_b="",
                    reason="scenario_inject_down",
                )
                pub_sock.send(encode_message(TOPIC_LINK_DOWN, event.model_dump_json().encode()))
                sock.send(b'{"status":"ok"}')

            elif action == "inject_link_up":
                pair = (cmd["node_a"], cmd["node_b"])
                pair = (min(pair), max(pair))
                with override_lock:
                    override_set.discard(pair)
                sock.send(b'{"status":"ok"}')

            elif action == "inject_satellite_loss":
                node = cmd["node"]
                with override_lock:
                    # Add override for every link involving this node
                    for pair in list(interface_map.keys()):
                        if node in pair:
                            override_set.add(pair)
                            event = LinkDown(
                                sim_time=now, wall_time=now,
                                node_a=pair[0], node_b=pair[1],
                                interface_a="", interface_b="",
                                reason="satellite_loss",
                            )
                            pub_sock.send(encode_message(
                                TOPIC_LINK_DOWN, event.model_dump_json().encode(),
                            ))
                log.info(f"Satellite loss injected for {node}")
                sock.send(b'{"status":"ok"}')

            elif action == "clear_overrides":
                with override_lock:
                    override_set.clear()
                sock.send(b'{"status":"ok"}')

            else:
                sock.send(b'{"status":"error","msg":"unknown action"}')

    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        ctx.term()


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Topology Orchestrator")
    parser.add_argument("--session", required=True, help="Path to session YAML")
    parser.add_argument("--timeline", required=True, help="Path to timeline JSONL")
    parser.add_argument("--mode", choices=["de", "rt"], default="de")
    parser.add_argument("--pid-map", help="Path to pid_map.json from na-deploy")
    parser.add_argument("--dwell", type=float, default=1.0, help="DE mode dwell (seconds)")
    args = parser.parse_args()

    data = yaml.safe_load(Path(args.session).read_text())
    session = SessionConfig.model_validate(data)
    addressing = AddressingScheme(session.addressing)
    interface_map, bandwidth_map = _build_interface_map(session, addressing)

    # Load pid_map if provided (from na-deploy step 7)
    pid_map: dict[str, int] = {}
    if args.pid_map:
        pid_map = json.loads(Path(args.pid_map).read_text())
        log.info(f"Loaded PID map with {len(pid_map)} entries")

    # ZMQ PUB for TO events (shared with scenario handler)
    ctx = zmq.Context()
    pub_sock = ctx.socket(zmq.PUB)

    # Start scenario handler thread
    scenario_thread = threading.Thread(
        target=_scenario_handler, args=(pub_sock, interface_map), daemon=True,
    )
    scenario_thread.start()

    if args.mode == "de":
        dispatcher = DiscreteEventDispatcher(
            timeline_path=Path(args.timeline),
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            override_set=override_set,
            override_lock=override_lock,
            pid_map=pid_map,
            latency_update_interval_s=session.time.latency_update_interval_seconds,
            dwell_s=args.dwell,
        )
        dispatcher.run()
    else:
        dispatcher = RealtimeDispatcher(
            interface_map=interface_map,
            bandwidth_map=bandwidth_map,
            override_set=override_set,
            override_lock=override_lock,
            pid_map=pid_map,
            latency_update_interval_s=session.time.latency_update_interval_seconds,
        )
        dispatcher.run()


if __name__ == "__main__":
    main()
