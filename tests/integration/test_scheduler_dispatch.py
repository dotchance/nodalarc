"""Step 4B verification: Scheduler dispatches to Node Agent, events appear on port 5561.

Runs:
  1. In-process Node Agent gRPC server (privileged, uses real netlink)
  2. Scheduler async dispatch loop connected to live OME ZMQ
  3. ZMQ SUB on port 5561 capturing LinkUp/LinkDown/LatencyUpdate events

Expects: within 60 seconds of OME streaming, at least one LinkUp or LinkDown
event appears on port 5561.

Run with:
  sudo -E KUBECONFIG=/etc/rancher/k3s/k3s.yaml \
    PYTHONPATH=/home/chance/nodal:/home/chance/nodal/lib \
    .venv/bin/python tests/integration/test_scheduler_dispatch.py
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger("test_4b")

KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"
NAMESPACE = "nodalarc"
SESSION_PATH = None  # Discovered below


def find_session_path() -> str:
    """Find the active session YAML."""
    # Check session-state.json for active session
    import glob

    for state_file in glob.glob("/home/chance/nodal/data/*/session-state.json"):
        try:
            state = json.loads(Path(state_file).read_text())
            sp = state.get("session_config", "")
            if sp and Path(sp).exists():
                return sp
        except Exception:
            pass
    # Fallback: look in configs/sessions
    for sp in sorted(Path("configs/sessions").glob("*.yaml")):
        return str(sp)
    raise FileNotFoundError("No session YAML found")


def find_ome_endpoint() -> str:
    """Get OME ZMQ endpoint."""
    try:
        out = subprocess.run(
            [
                "kubectl",
                f"--kubeconfig={KUBECONFIG}",
                "-n",
                NAMESPACE,
                "get",
                "svc",
                "nodalarc-ome",
                "-o",
                "jsonpath={.spec.clusterIP}",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if out:
            return f"tcp://{out}:5560"
    except Exception:
        pass
    return "tcp://127.0.0.1:5560"


def main():
    print("=== Step 4B: Scheduler Dispatch Integration Test ===\n")

    # --- Setup ---
    session_path = find_session_path()
    ome_endpoint = find_ome_endpoint()
    print(f"Session: {session_path}")
    print(f"OME endpoint: {ome_endpoint}")

    from nodalarc.platform import init_platform_config

    init_platform_config(Path("configs/platform.yaml"))

    # Load session
    data = yaml.safe_load(Path(session_path).read_text())
    from nodalarc.models.session import SessionConfig

    session = SessionConfig.model_validate(data)

    from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors, neighbors_by_node

    addressing = AddressingScheme(session.addressing)

    # Build interface map
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
    print(f"Interface map: {len(interface_map)} link pairs")

    # Pod location
    from scheduler.pod_locator import PodLocationMap

    loc = PodLocationMap()
    loc.load_from_k8s_api(namespace=NAMESPACE, agent_port=50100)
    print(f"Pods: {len(loc.node_ids)}")
    print(f"Agent: {loc.all_agent_addrs()}")

    # --- Start Node Agent in-process ---
    print("\nStarting Node Agent gRPC server...")
    from node_agent.proto.node_agent_pb2_grpc import add_NodeAgentServiceServicer_to_server
    from node_agent.server import NodeAgentServicer

    # Build pid_map for GetTopology
    pid_map = {nid: loc.pid(nid) for nid in loc.node_ids}

    agent_server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    servicer = NodeAgentServicer(pid_map=pid_map)
    add_NodeAgentServiceServicer_to_server(servicer, agent_server)
    agent_server.add_insecure_port("0.0.0.0:50100")
    agent_server.start()
    print("Node Agent listening on 0.0.0.0:50100")

    # --- Start ZMQ subscriber on port 5561 (background) ---
    captured_events: list[dict] = []
    sub_stop = threading.Event()

    def zmq_subscriber():
        ctx = zmq.Context()
        sock = ctx.socket(zmq.SUB)
        sock.connect("tcp://127.0.0.1:5561")
        sock.setsockopt(zmq.SUBSCRIBE, b"")
        sock.setsockopt(zmq.RCVTIMEO, 1000)
        while not sub_stop.is_set():
            try:
                raw = sock.recv()
                from nodalarc.zmq_channels import decode_message

                topic, payload = decode_message(raw)
                data = json.loads(payload)
                captured_events.append(
                    {
                        "topic": topic.decode(),
                        "sim_time": data.get("sim_time", ""),
                        "node_a": data.get("node_a", ""),
                        "node_b": data.get("node_b", ""),
                    }
                )
            except zmq.Again:
                continue
            except Exception as exc:
                log.debug("Subscriber error: %s", exc)
        sock.close()
        ctx.term()

    sub_thread = threading.Thread(target=zmq_subscriber, daemon=True)
    sub_thread.start()
    print("ZMQ subscriber listening on tcp://127.0.0.1:5561")

    # --- Kill existing orchestrator ---
    subprocess.run(["pkill", "-f", "python.*-m orchestrator.main"], capture_output=True)
    time.sleep(1)
    print("Existing orchestrator stopped")

    # --- Start Scheduler dispatch loop ---
    print("\nStarting Scheduler dispatcher...")
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

    # Run dispatcher in background task
    async def run_for_duration(seconds: int):
        task = asyncio.create_task(dispatcher.run())
        await asyncio.sleep(seconds)
        dispatcher.stop()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            pass

    print("Running for 45 seconds...")
    try:
        asyncio.run(run_for_duration(45))
    except KeyboardInterrupt:
        pass

    # --- Collect results ---
    sub_stop.set()
    sub_thread.join(timeout=3)
    pool.close()
    agent_server.stop(grace=1)

    print("\n=== Results ===")
    print(f"Captured {len(captured_events)} events on port 5561:")
    link_ups = [e for e in captured_events if e["topic"] == "LinkUp"]
    link_downs = [e for e in captured_events if e["topic"] == "LinkDown"]
    latency_updates = [e for e in captured_events if e["topic"] == "LatencyUpdate"]
    print(f"  LinkUp: {len(link_ups)}")
    print(f"  LinkDown: {len(link_downs)}")
    print(f"  LatencyUpdate: {len(latency_updates)}")

    # Show first few events
    for e in captured_events[:10]:
        print(f"  {e['topic']:20s}  {e['node_a']:15s}  {e['node_b']:15s}  sim={e['sim_time'][:19]}")
    if len(captured_events) > 10:
        print(f"  ... and {len(captured_events) - 10} more")

    if link_ups or link_downs:
        print("\nPASS: LinkUp/LinkDown events flowing on port 5561")
    else:
        print("\nFAIL: No LinkUp/LinkDown events captured")
        sys.exit(1)


if __name__ == "__main__":
    main()
