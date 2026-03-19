"""Scheduler entry point — replaces orchestrator/main.py for M4+.

Loads session config, builds interface/bandwidth maps, discovers pod
locations, initializes agent pool, and runs the async dispatch loop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading
from pathlib import Path

import yaml
from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import (
    AddressingScheme,
    assign_isl_neighbors,
    neighbors_by_node,
)
from nodalarc.models.session import SessionConfig

from scheduler.agent_pool import AgentPool
from scheduler.dispatcher import Dispatcher
from scheduler.pod_locator import PodLocationMap

log = logging.getLogger(__name__)


def _build_interface_map(
    session: SessionConfig,
    addressing: AddressingScheme,
) -> tuple[dict[tuple[str, str], tuple[str, str]], dict[tuple[str, str], float]]:
    """Build interface and bandwidth maps — migrated from orchestrator/main.py."""
    from ome.constellation_loader import (
        expand_constellation,
        load_constellation,
        load_ground_stations,
    )

    constellation = load_constellation(session.constellation)
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    bandwidth_map: dict[tuple[str, str], float] = {}

    for node_id, assignments in by_node.items():
        for na in assignments:
            pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
            if pair not in interface_map:
                if node_id == pair[0]:
                    interface_map[pair] = (na.interface, "")
                else:
                    interface_map[pair] = ("", na.interface)
                bandwidth_map[pair] = 1000.0
            else:
                existing = interface_map[pair]
                if node_id == pair[0] and not existing[0]:
                    interface_map[pair] = (na.interface, existing[1])
                elif node_id == pair[1] and not existing[1]:
                    interface_map[pair] = (existing[0], na.interface)

    gs_file = load_ground_stations(session.ground_stations)
    satellites = expand_constellation(constellation)
    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        for sat in satellites:
            sat_id = addressing.sat_id(sat.plane, sat.slot)
            pair = (min(gs_id, sat_id), max(gs_id, sat_id))
            interface_map[pair] = ("gnd0", "gnd0")
            bandwidth_map[pair] = 1000.0

    return interface_map, bandwidth_map


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc Scheduler")
    parser.add_argument("--session", required=True, help="Path to session YAML")
    parser.add_argument(
        "--ome-endpoint",
        required=True,
        help="ZMQ endpoint for OME events (e.g. tcp://nodalarc-ome:5560)",
    )
    parser.add_argument("--pid-map", help="Path to pid_map.json from na-deploy")
    parser.add_argument(
        "--platform-config",
        default="configs/platform.yaml",
        help="Path to platform configuration YAML",
    )
    args = parser.parse_args()

    from nodalarc.platform import init_platform_config

    init_platform_config(Path(args.platform_config))

    data = yaml.safe_load(Path(args.session).read_text())
    session = SessionConfig.model_validate(data)
    addressing = AddressingScheme(session.addressing)
    interface_map, bandwidth_map = _build_interface_map(session, addressing)
    log.info("Interface map: %d link pairs", len(interface_map))

    # Pod location map — canonical node IDs from K8s labels
    from nodalarc.zmq_channels import node_agent_grpc_port

    loc = PodLocationMap()
    if args.pid_map:
        loc.load_from_pid_map_file(args.pid_map, agent_port=node_agent_grpc_port())
    else:
        loc.load_from_k8s_api(agent_port=node_agent_grpc_port())
    log.info("Pod locations:\n%s", loc.summary())

    # Agent pool
    pool = AgentPool()

    # Override set (shared with scenario handler thread)
    override_set: set[tuple[str, str]] = set()
    override_lock = threading.Lock()

    dispatcher = Dispatcher(
        ome_endpoint=args.ome_endpoint,
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        override_set=override_set,
        override_lock=override_lock,
        compression_factor=session.time.compression,
        latency_update_interval_s=session.time.latency_update_interval_seconds,
    )

    try:
        asyncio.run(dispatcher.run())
    except KeyboardInterrupt:
        log.info("Scheduler interrupted")
    finally:
        pool.close()


if __name__ == "__main__":
    main()
