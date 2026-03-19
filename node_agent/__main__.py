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
    args = parser.parse_args()

    # Init platform config if available (non-fatal if missing)
    try:
        from nodalarc.platform import init_platform_config

        init_platform_config(Path(args.platform_config))
        from nodalarc.zmq_channels import node_agent_grpc_port

        port = node_agent_grpc_port()
    except Exception:
        port = args.port

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=args.workers))
    servicer = NodeAgentServicer()
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
