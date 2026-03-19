"""Step 4B: Scheduler dispatches link events on port 5561 from FullStateSnapshot.

Connects to the existing OME pod (ZMQ on pod IP). The OME publishes
FullStateSnapshots every 30s. The Scheduler sees active links in the
snapshot and dispatches BatchLinkUp to the Node Agent. The test
subscriber captures LinkUp events on port 5561.

Run with:
  sudo -E KUBECONFIG=/etc/rancher/k3s/k3s.yaml \
    PYTHONPATH=/home/chance/nodal:/home/chance/nodal/lib \
    .venv/bin/python tests/integration/test_scheduler_4b.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading
import time
from concurrent import futures
from pathlib import Path

import grpc
import yaml
import zmq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s — %(message)s")
log = logging.getLogger("test_4b")

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

    session_path = "configs/sessions/_test-iridium-small-36-isis-sr.yaml"
    data = yaml.safe_load(Path(session_path).read_text())
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
    print(f"Interface map: {len(interface_map)} link pairs")

    # Pod locator
    from scheduler.pod_locator import PodLocationMap

    loc = PodLocationMap()
    loc.load_from_k8s_api(namespace=NS, agent_port=50100)
    print(f"Pods: {len(loc.node_ids)}, Agents: {loc.all_agent_addrs()}")

    # Kill existing orchestrator
    subprocess.run(["pkill", "-f", "python.*-m orchestrator.main"], capture_output=True)
    time.sleep(1)

    # Start Node Agent
    from node_agent.proto.node_agent_pb2_grpc import add_NodeAgentServiceServicer_to_server
    from node_agent.server import NodeAgentServicer

    pid_map = {nid: loc.pid(nid) for nid in loc.node_ids}
    agent_server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    add_NodeAgentServiceServicer_to_server(NodeAgentServicer(pid_map=pid_map), agent_server)
    agent_server.add_insecure_port("0.0.0.0:50100")
    agent_server.start()
    print("Node Agent on 0.0.0.0:50100")

    # ZMQ subscriber on 5561
    from nodalarc.zmq_channels import decode_message

    captured: list[dict] = []
    sub_stop = threading.Event()

    def subscriber():
        ctx = zmq.Context()
        sock = ctx.socket(zmq.SUB)
        sock.connect("tcp://127.0.0.1:5561")
        sock.subscribe(b"")
        sock.setsockopt(zmq.RCVTIMEO, 1000)
        while not sub_stop.is_set():
            try:
                raw = sock.recv()
                topic, payload = decode_message(raw)
                d = json.loads(payload)
                captured.append(
                    {
                        "topic": topic.decode(),
                        "node_a": d.get("node_a", ""),
                        "node_b": d.get("node_b", ""),
                        "sim": d.get("sim_time", "")[:19],
                    }
                )
            except zmq.Again:
                pass
        sock.close()
        ctx.term()

    sub_t = threading.Thread(target=subscriber, daemon=True)
    sub_t.start()
    print("ZMQ subscriber on tcp://127.0.0.1:5561")

    # Get OME pod IP
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
    ome_endpoint = f"tcp://{ome_ip}:5560"
    print(f"OME: {ome_pod} at {ome_endpoint}")

    # Scheduler
    from scheduler.agent_pool import AgentPool
    from scheduler.dispatcher import Dispatcher

    pool = AgentPool()
    override_set: set[tuple[str, str]] = set()
    override_lock = threading.Lock()

    dispatcher = Dispatcher(
        ome_endpoint=ome_endpoint,
        interface_map=interface_map,
        bandwidth_map=bandwidth_map,
        pod_locator=loc,
        agent_pool=pool,
        override_set=override_set,
        override_lock=override_lock,
        compression_factor=session.time.compression,
        latency_update_interval_s=session.time.latency_update_interval_seconds,
    )

    async def run_test():
        task = asyncio.create_task(dispatcher.run())
        # Wait for FullStateSnapshot (published every 30s) + dispatch time
        print("\nWaiting up to 45s for FullStateSnapshot + dispatch...")
        for i in range(45):
            await asyncio.sleep(1)
            if captured:
                print(f"  Events detected at {i + 1}s!")
                # Wait a few more seconds for remaining events
                await asyncio.sleep(5)
                break
        dispatcher.stop()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(run_test())
    sub_stop.set()
    sub_t.join(timeout=2)
    pool.close()
    agent_server.stop(grace=1)

    # Results
    ups = [e for e in captured if e["topic"] == "LinkUp"]
    downs = [e for e in captured if e["topic"] == "LinkDown"]
    lats = [e for e in captured if e["topic"] == "LatencyUpdate"]
    print("\n=== Results ===")
    print(
        f"Captured {len(captured)} events: {len(ups)} LinkUp, {len(downs)} LinkDown, {len(lats)} LatencyUpdate"
    )
    for e in captured[:20]:
        print(f"  {e['topic']:20s}  {e['node_a']:15s}  {e['node_b']:15s}  sim={e['sim']}")
    if len(captured) > 20:
        print(f"  ... and {len(captured) - 20} more")

    if ups or downs:
        print("\nPASS: LinkUp/LinkDown events flowing on port 5561")
    else:
        print("\nFAIL: No LinkUp/LinkDown events captured")
        sys.exit(1)


if __name__ == "__main__":
    main()
