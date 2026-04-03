"""Node Agent entry point — NATS request/reply server for netlink operations.

Runs as a DaemonSet on each K3s node. Subscribes to NATS subject
nodalarc.agent.{hostname} and executes privileged namespace operations
on behalf of the Scheduler.

Startup ordering (enforced, not hoped for):
  1. Wiring watcher thread starts, waits for topology manifest
  2. Wiring completes — PIDs discovered, veth pairs created, status written
  3. Wiring thread signals ready and shares pid_map
  4. NATS server subscribes and begins accepting requests
  5. Requests use the wiring thread's pid_map — no rediscovery

This is the same principle as the Scheduler wiring gate.
The NATS server must NOT accept requests until wiring is complete.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
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
        help="Path to pid_map.json (from na-deploy). If not provided, discovers PIDs during wiring.",
    )
    args = parser.parse_args()

    # Init platform config (required for NATS URL)
    try:
        from nodalarc.platform import init_platform_config

        init_platform_config(Path(args.platform_config))
    except Exception:
        pass

    # Shared state between wiring thread and NATS server
    wiring_ready = threading.Event()
    shared_pid_map: dict[str, int] = {}

    # If --pid-map provided, skip wiring discovery — use the file directly
    if args.pid_map:
        shared_pid_map.update(json.loads(Path(args.pid_map).read_text()))
        log.info("Loaded pid_map from %s (%d entries)", args.pid_map, len(shared_pid_map))
        wiring_ready.set()

    # Wiring watcher thread — discovers PIDs, creates interfaces, signals ready
    def _wiring_watcher():
        import time

        try:
            import kubernetes.client
            import kubernetes.config

            kubernetes.config.load_incluster_config()
        except Exception:
            log.info("Not running in K8s — wiring watcher disabled")
            wiring_ready.set()  # Don't block NATS server in non-K8s env
            return

        try:
            from nodalarc.platform import get_platform_config

            ns = get_platform_config().kubernetes_namespace
        except RuntimeError:
            ns = "nodalarc"
        v1 = kubernetes.client.CoreV1Api()
        last_resource_version = ""

        while True:
            try:
                cm = v1.read_namespaced_config_map("nodalarc-topology-wiring", ns)
                rv = cm.metadata.resource_version or ""

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
                    # Even on no-op, refresh PIDs and signal ready
                    _refresh_pids(shared_pid_map)
                    wiring_ready.set()
                    time.sleep(5)
                    continue

                # Case A or C
                actual = get_actual_nodalarc_interfaces()
                if not actual:
                    log.info("No kernel state — wiring from scratch (%d nodes)", len(nodes))
                else:
                    log.warning(
                        "Kernel state diverged (%d interfaces) — cleaning and re-wiring",
                        len(actual),
                    )
                    cleaned = clean_nodalarc_kernel_state()
                    log.info("Cleaned %d stale kernel interfaces", cleaned)

                wired = execute_wiring(manifest, namespace=ns)
                write_wiring_status(wired, namespace=ns)
                log.info("Wiring complete: %d nodes wired", len(wired))

                # Share PIDs from wiring discovery with the NATS server
                _refresh_pids(shared_pid_map)
                wiring_ready.set()

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

    # Wait for wiring to complete before starting NATS server
    log.info("Waiting for wiring to complete before accepting NATS requests...")
    wiring_ready.wait()
    log.info("Wiring ready — pid_map has %d entries", len(shared_pid_map))

    server = NodeAgentServer(pid_map=shared_pid_map)

    def _shutdown(signum, frame):
        log.info("Shutting down (signal %d)...", signum)
        server.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server.run()
    log.info("Node Agent stopped")


def _refresh_pids(shared_pid_map: dict[str, int]) -> None:
    """Refresh the shared pid_map from local pod discovery.

    Called by the wiring thread after wiring completes. The NATS server
    uses this map directly — no separate discovery needed.
    """
    try:
        from node_agent.pid_discovery import discover_local_pod_pids

        new_pids = discover_local_pod_pids()
        shared_pid_map.clear()
        shared_pid_map.update(new_pids)
        log.info("PID map refreshed: %d pods", len(shared_pid_map))
    except Exception as exc:
        log.warning("PID refresh failed: %s", exc)


if __name__ == "__main__":
    main()
