"""Node Agent entry point — gRPC server for netlink operations.

Runs as a DaemonSet on each K3s node. Listens on the configured
gRPC port (default 50100) and executes privileged namespace operations
on behalf of the Scheduler.
"""

from __future__ import annotations

import argparse
import logging
import signal
from concurrent import futures
from pathlib import Path

import grpc

from node_agent.proto.node_agent_pb2_grpc import add_NodeAgentServiceServicer_to_server
from node_agent.server import NodeAgentServicer

log = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [node-agent] %(name)s — %(message)s"


def main() -> None:
    logging.basicConfig(format=_LOG_FORMAT, level=logging.INFO)

    parser = argparse.ArgumentParser(description="Nodal Arc Node Agent")
    parser.add_argument("--port", type=int, default=50100, help="gRPC listen port")
    parser.add_argument(
        "--platform-config",
        default="configs/platform.yaml",
        help="Path to platform configuration YAML",
    )
    parser.add_argument("--workers", type=int, default=4, help="gRPC thread pool workers")
    parser.add_argument(
        "--pid-map",
        help="Path to pid_map.json (from na-deploy). If not provided, discovers PIDs from K8s API.",
    )
    args = parser.parse_args()

    # Init platform config if available (non-fatal if missing)
    try:
        from nodalarc.platform import init_platform_config

        init_platform_config(Path(args.platform_config))
        from nodalarc.zmq_channels import node_agent_grpc_port

        port = node_agent_grpc_port()
    except Exception:
        port = args.port

    # Load pid_map for GetTopology
    import json

    pid_map: dict[str, int] = {}
    if args.pid_map:
        pid_map = json.loads(Path(args.pid_map).read_text())
        log.info("Loaded pid_map from %s (%d entries)", args.pid_map, len(pid_map))
    else:
        try:
            from node_agent.pid_discovery import discover_local_pod_pids

            pid_map = discover_local_pod_pids()
        except Exception as exc:
            log.warning("PID discovery failed: %s — GetTopology will return empty", exc)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=args.workers))
    servicer = NodeAgentServicer(pid_map=pid_map)
    add_NodeAgentServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    log.info("NodeAgentService listening on port %d (workers=%d)", port, args.workers)

    # Graceful shutdown on SIGTERM/SIGINT
    stop_event = False

    def _shutdown(signum, frame):
        nonlocal stop_event
        if not stop_event:
            stop_event = True
            log.info("Shutting down (signal %d)...", signum)
            server.stop(grace=5)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server.wait_for_termination()
    log.info("Node Agent stopped")


if __name__ == "__main__":
    main()
