"""Step 4C: Verify LatencyUpdate events flow on port 5561.

Runs Scheduler + Node Agent for ~75s. Expects LatencyUpdate events
to appear after FullStateSnapshot provides positions + active links.
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
                        "latency_ms": d.get("latency_ms", 0),
                        "range_km": d.get("range_km", 0),
                    }
                )
            except zmq.Again:
                pass
        sock.close()
        ctx.term()

    sub_t = threading.Thread(target=subscriber, daemon=True)
    sub_t.start()

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
    print(f"OME: {ome_endpoint}")

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
        latency_update_interval_s=1,  # Fire latency check every batch
    )

    # Force latency updates by clearing last_latencies after link-up.
    # In production, per-timestep OME Snapshot events drive position drift.
    # Between OME windows, FullStateSnapshots repeat the same trajectory,
    # so latencies don't change. This patch simulates first-update trigger.
    original_dispatch_ups = dispatcher._dispatch_ups

    async def patched_dispatch_ups(*a, **kw):
        result = await original_dispatch_ups(*a, **kw)
        dispatcher._last_latencies.clear()
        return result

    dispatcher._dispatch_ups = patched_dispatch_ups

    async def run():
        task = asyncio.create_task(dispatcher.run())
        print("Running Scheduler for up to 50s...")
        for i in range(50):
            await asyncio.sleep(1)
            lats = [e for e in captured if e["topic"] == "LatencyUpdate"]
            if len(lats) >= 5:
                print(f"  Got {len(lats)} LatencyUpdates at {i + 1}s")
                break
            if i % 15 == 14:
                ups = [e for e in captured if e["topic"] == "LinkUp"]
                print(f"  {i + 1}s: {len(ups)} LinkUp, {len(lats)} LatencyUpdate")
        dispatcher.stop()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(run())
    sub_stop.set()
    sub_t.join(timeout=2)
    pool.close()
    agent_server.stop(grace=1)

    ups = [e for e in captured if e["topic"] == "LinkUp"]
    downs = [e for e in captured if e["topic"] == "LinkDown"]
    lats = [e for e in captured if e["topic"] == "LatencyUpdate"]
    print(f"\nCaptured: {len(ups)} LinkUp, {len(downs)} LinkDown, {len(lats)} LatencyUpdate")
    print("\nLatencyUpdate events:")
    for e in lats[:10]:
        print(
            f"  {e['node_a']:15s} <-> {e['node_b']:15s}: {e['latency_ms']:.2f}ms (range {e['range_km']:.1f}km)"
        )
    if len(lats) > 10:
        print(f"  ... and {len(lats) - 10} more")
    if lats:
        print("\nPASS: LatencyUpdate events flowing on port 5561")
    else:
        print("\nFAIL: no LatencyUpdate events")
        sys.exit(1)


if __name__ == "__main__":
    main()
