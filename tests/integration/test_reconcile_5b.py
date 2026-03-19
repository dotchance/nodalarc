"""Phase 5B: Verify reconciliation on Scheduler restart."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from concurrent import futures
from pathlib import Path

import grpc
import yaml
import zmq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s — %(message)s")

KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"
NS = "nodalarc"


def kubectl(*args):
    return subprocess.run(
        ["kubectl", f"--kubeconfig={KUBECONFIG}", "-n", NS, *args],
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()


def main():
    from nodalarc.platform import init_platform_config

    init_platform_config(Path("configs/platform.yaml"))

    data = yaml.safe_load(Path("configs/sessions/_test-iridium-small-36-isis-sr.yaml").read_text())
    from nodalarc.models.session import SessionConfig

    session = SessionConfig.model_validate(data)
    from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors, neighbors_by_node

    from ome.constellation_loader import (
        expand_constellation,
        load_constellation,
        load_ground_stations,
    )

    addressing = AddressingScheme(session.addressing)
    constellation = load_constellation(session.constellation)
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)
    interface_map: dict = {}
    bandwidth_map: dict = {}
    for node_id, assignments in by_node.items():
        for na in assignments:
            pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
            if pair not in interface_map:
                interface_map[pair] = (
                    (na.interface, "") if node_id == pair[0] else ("", na.interface)
                )
                bandwidth_map[pair] = 1000.0
            else:
                existing = interface_map[pair]
                if node_id == pair[0] and not existing[0]:
                    interface_map[pair] = (na.interface, existing[1])
                elif node_id == pair[1] and not existing[1]:
                    interface_map[pair] = (existing[0], na.interface)
    gs_file = load_ground_stations(session.ground_stations)
    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        for sat in expand_constellation(constellation):
            sat_id = addressing.sat_id(sat.plane, sat.slot)
            pair = (min(gs_id, sat_id), max(gs_id, sat_id))
            interface_map[pair] = ("gnd0", "gnd0")
            bandwidth_map[pair] = 1000.0

    from scheduler.pod_locator import PodLocationMap

    loc = PodLocationMap()
    loc.load_from_k8s_api(namespace=NS, agent_port=50100)

    subprocess.run(["pkill", "-f", "python.*-m orchestrator.main"], capture_output=True)
    time.sleep(1)

    from node_agent.proto.node_agent_pb2_grpc import add_NodeAgentServiceServicer_to_server
    from node_agent.server import NodeAgentServicer

    pid_map = {nid: loc.pid(nid) for nid in loc.node_ids}
    agent_server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    add_NodeAgentServiceServicer_to_server(NodeAgentServicer(pid_map=pid_map), agent_server)
    agent_server.add_insecure_port("0.0.0.0:50100")
    agent_server.start()

    ome_pod = kubectl(
        "get",
        "pods",
        "-l",
        "app=nodalarc-ome",
        "--no-headers",
        "-o",
        "custom-columns=NAME:.metadata.name",
    )
    ome_ip = kubectl("get", "pod", ome_pod, "-o", "jsonpath={.status.podIP}")

    to_ctx = zmq.Context()
    to_pub = to_ctx.socket(zmq.PUB)
    from nodalarc.zmq_channels import to_events_bind

    to_pub.bind(to_events_bind())

    from scheduler.agent_pool import AgentPool
    from scheduler.dispatcher import Dispatcher

    pool = AgentPool()
    dispatcher = Dispatcher(
        ome_endpoint=f"tcp://{ome_ip}:5560",
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        override_set=set(),
        override_lock=threading.Lock(),
        compression_factor=session.time.compression,
        latency_update_interval_s=10,
    )

    async def run():
        task = asyncio.create_task(dispatcher.run(external_to_pub=to_pub))
        # Just wait for reconciliation (runs before event loop)
        await asyncio.sleep(8)
        dispatcher.stop()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            pass

    print("=== Phase 5B: Reconciliation Test ===")
    print(
        f"Checkpoint exists: {kubectl('get', 'configmap', 'nodalarc-scheduler-checkpoint', '-o', 'jsonpath={.data.sim_time}')}"
    )
    asyncio.run(run())
    pool.close()
    agent_server.stop(grace=0)
    to_pub.close()
    to_ctx.term()


if __name__ == "__main__":
    main()
