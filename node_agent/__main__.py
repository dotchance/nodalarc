"""Node Agent entry point — NATS request/reply server for netlink operations.

Runs as a DaemonSet on each K3s node. Subscribes to NATS subject
nodalarc.agent.{hostname} and executes privileged namespace operations
on behalf of the Scheduler.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
from pathlib import Path

from node_agent.reconcile import (
    clean_nodalarc_kernel_state,
    get_actual_nodalarc_interfaces,
    wiring_status_is_current,
)
from node_agent.server import NodeAgentServer
from node_agent.wiring import execute_wiring, write_wiring_status

log = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [node-agent] %(name)s — %(message)s"


def main() -> None:
    logging.basicConfig(format=_LOG_FORMAT, level=logging.INFO)

    parser = argparse.ArgumentParser(description="Nodal Arc Node Agent")
    parser.add_argument(
        "--port", type=int, default=50100, help="Deprecated — NATS transport. Ignored."
    )
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

    # Init platform config (required for NATS URL)
    try:
        from nodalarc.platform import init_platform_config

        init_platform_config(Path(args.platform_config))
    except Exception:
        pass
    port = args.port  # legacy — kept for logging only

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

    # Start wiring watcher in background thread (7b: watches for topology manifest)
    import threading

    def _wiring_watcher():
        """Poll for nodalarc-topology-wiring ConfigMap and execute wiring."""
        import time

        try:
            import kubernetes.client
            import kubernetes.config

            kubernetes.config.load_incluster_config()
        except Exception:
            log.info("Not running in K8s — wiring watcher disabled")
            return

        try:
            from nodalarc.platform import get_platform_config

            ns = get_platform_config().kubernetes_namespace
        except RuntimeError:
            ns = "nodalarc"  # Default namespace if platform config not initialized
        v1 = kubernetes.client.CoreV1Api()
        last_resource_version = ""

        while True:
            try:
                cm = v1.read_namespaced_config_map("nodalarc-topology-wiring", ns)
                rv = cm.metadata.resource_version or ""

                # Only reconcile when ConfigMap actually changes
                if rv == last_resource_version:
                    time.sleep(5)
                    continue
                last_resource_version = rv

                manifest_json = cm.data.get("manifest.json", "{}")
                manifest = json.loads(manifest_json)
                nodes = manifest.get("nodes", {})

                if not nodes:
                    time.sleep(5)
                    continue

                # Case B: wiring-status exists and covers all manifest nodes
                if wiring_status_is_current(v1, ns, nodes):
                    log.info(
                        "Wiring verified — status matches manifest (%d nodes), no-op",
                        len(nodes),
                    )
                    time.sleep(5)
                    continue

                # Case A or C: check kernel state
                actual = get_actual_nodalarc_interfaces()

                if not actual:
                    # Case A: no kernel state — wire from scratch
                    log.info(
                        "No kernel state — wiring from scratch (%d nodes)",
                        len(nodes),
                    )
                else:
                    # Case C: kernel state exists but wiring-status absent/stale
                    log.warning(
                        "Kernel state diverged (%d interfaces, stale wiring-status)"
                        " — cleaning and re-wiring",
                        len(actual),
                    )
                    cleaned = clean_nodalarc_kernel_state()
                    log.info("Cleaned %d stale kernel interfaces", cleaned)

                wired = execute_wiring(manifest, namespace=ns)
                write_wiring_status(wired, namespace=ns)
                log.info("Wiring complete: %d nodes wired", len(wired))

            except kubernetes.client.rest.ApiException as e:
                if e.status == 404:
                    if last_resource_version:
                        log.info("Wiring manifest removed — cleaning kernel state")
                        actual = get_actual_nodalarc_interfaces()
                        if actual:
                            clean_nodalarc_kernel_state()
                        last_resource_version = ""
                else:
                    log.warning("Wiring watcher error: %s", e)
            except Exception as exc:
                log.warning("Wiring watcher error: %s", exc)
            time.sleep(5)

    wiring_thread = threading.Thread(target=_wiring_watcher, daemon=True)
    wiring_thread.start()

    def _shutdown(signum, frame):
        log.info("Shutting down (signal %d)...", signum)
        server.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server.run()
    log.info("Node Agent stopped")


if __name__ == "__main__":
    main()
