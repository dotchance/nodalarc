"""Node Agent entry point — ZMQ ROUTER server for netlink operations.

Runs as a DaemonSet on each K3s node. Listens on the configured
port (default 50100) and executes privileged namespace operations
on behalf of the Scheduler via ZMQ ROUTER/DEALER transport.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
from pathlib import Path

from node_agent.server import NodeAgentServer

log = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [node-agent] %(name)s — %(message)s"


def main() -> None:
    logging.basicConfig(format=_LOG_FORMAT, level=logging.INFO)

    parser = argparse.ArgumentParser(description="Nodal Arc Node Agent")
    parser.add_argument("--port", type=int, default=50100, help="ZMQ ROUTER listen port")
    parser.add_argument(
        "--platform-config",
        default="configs/platform.yaml",
        help="Path to platform configuration YAML",
    )
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

    # Load pid_map for GetTopology and PID resolution
    pid_map: dict[str, int] = {}
    if args.pid_map:
        pid_map = json.loads(Path(args.pid_map).read_text())
        log.info("Loaded pid_map from %s (%d entries)", args.pid_map, len(pid_map))
    else:
        try:
            from node_agent.pid_discovery import discover_local_pod_pids

            pid_map = discover_local_pod_pids()
        except Exception as exc:
            log.warning("PID discovery failed: %s — will retry lazily on first request", exc)

    server = NodeAgentServer(port=port, pid_map=pid_map)

    def _shutdown(signum, frame):
        log.info("Shutting down (signal %d)...", signum)
        server.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server.run()
    log.info("Node Agent stopped")


if __name__ == "__main__":
    main()
