# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Node Agent entry point — async NATS-native actor for netlink operations.

Runs as a DaemonSet on each K3s node. Connects to NATS IMMEDIATELY on
startup, then runs wiring in a thread pool executor. Progress publishes
to NATS in real-time (<10ms to VS-API) instead of through K8s ConfigMap
polling (2-3.5s latency).

Architecture:
  1. Connect to NATS (first act of life)
  2. Run wiring watcher in ThreadPoolExecutor (synchronous kernel work)
     - progress_fn publishes to NATS via loop.call_soon_threadsafe
  3. After first wiring pass: subscribe to request/reply subject
  4. Serve until SIGTERM/SIGINT

One event loop. One NATS connection. No daemon threads. No second loops.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import socket
from pathlib import Path

import nats
from nodalarc.nats_channels import NATS_CONNECT_OPTIONS, nats_url, wiring_progress_subject

from node_agent.reconcile import (
    clean_nodalarc_kernel_state,
    get_actual_nodalarc_interfaces,
    wiring_status_is_current,
)
from node_agent.server import dispatch
from node_agent.wiring import execute_wiring, write_wiring_status

log = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [node-agent] %(name)s — %(message)s"


async def main() -> None:
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

    # -----------------------------------------------------------------------
    # Connect to NATS FIRST — the Node Agent is a NATS-native actor.
    # This connection is used for wiring progress, request/reply, and
    # substrate monitoring. One connection for the lifetime of the process.
    # -----------------------------------------------------------------------
    nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    hostname = socket.gethostname()
    progress_subject = wiring_progress_subject(hostname)
    loop = asyncio.get_running_loop()
    log.info("NATS connected to %s as %s", nats_url(), hostname)

    # Synchronous progress publisher for the wiring thread.
    # The wiring thread is synchronous Python (kernel netlink work).
    # loop.call_soon_threadsafe schedules the async publish on the
    # main event loop without blocking or requiring a second loop.
    def _publish_progress(msg: str) -> None:
        payload = json.dumps({"node": hostname, "message": msg}).encode()
        loop.call_soon_threadsafe(
            lambda p=payload: asyncio.ensure_future(nc.publish(progress_subject, p))
        )

    # -----------------------------------------------------------------------
    # Shared state between wiring and request/reply server
    # -----------------------------------------------------------------------
    shared_pid_map: dict[str, int] = {}
    first_wiring_done = asyncio.Event()

    # If --pid-map provided, skip wiring discovery
    if args.pid_map:
        shared_pid_map.update(json.loads(Path(args.pid_map).read_text()))
        log.info("Loaded pid_map from %s (%d entries)", args.pid_map, len(shared_pid_map))
        first_wiring_done.set()

    # -----------------------------------------------------------------------
    # Wiring watcher — runs in thread pool executor (synchronous code).
    # Watches nodalarc-topology-wiring ConfigMap, executes wiring on change.
    # -----------------------------------------------------------------------
    def _wiring_watcher() -> None:
        import time

        try:
            import kubernetes.client
            import kubernetes.config

            kubernetes.config.load_incluster_config()
        except Exception:
            log.info("Not running in K8s — wiring watcher disabled")
            loop.call_soon_threadsafe(first_wiring_done.set)
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

                # New manifest detected — immediately clear stale pid_map.
                # Any BatchLinkUp arriving from this point forward will be
                # cleanly deferred until wiring completes and PIDs refresh.
                shared_pid_map.clear()

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
                    _refresh_pids(shared_pid_map)
                    loop.call_soon_threadsafe(first_wiring_done.set)
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

                wired = execute_wiring(manifest, namespace=ns, progress_fn=_publish_progress)
                log.info("Wiring complete: %d nodes wired", len(wired))

                # Refresh pid_map BEFORE writing wiring status. Once the status
                # is written, the Operator advances to Ready and the Scheduler
                # dispatches immediately. The pid_map must be current before
                # any BatchLinkUp arrives, or the handler rejects with
                # "PID not found."
                _refresh_pids(shared_pid_map)
                write_wiring_status(wired, namespace=ns)
                loop.call_soon_threadsafe(first_wiring_done.set)

            except Exception as exc:
                if hasattr(exc, "status") and exc.status == 404:
                    if last_resource_version:
                        log.info("Wiring manifest removed — cleaning kernel state")
                        actual = get_actual_nodalarc_interfaces()
                        if actual:
                            clean_nodalarc_kernel_state()
                        last_resource_version = ""
                else:
                    log.warning("Wiring watcher error: %s", exc)
            time.sleep(5)

    # Start wiring watcher in thread pool
    wiring_task = loop.run_in_executor(None, _wiring_watcher)

    # Wait for first wiring pass to complete before accepting requests
    log.info("Waiting for wiring to complete before accepting NATS requests...")
    await first_wiring_done.wait()
    log.info("Wiring ready — pid_map has %d entries", len(shared_pid_map))

    # -----------------------------------------------------------------------
    # NATS request/reply server — subscribes AFTER wiring (pid_map gate)
    # -----------------------------------------------------------------------
    agent_subject = f"nodalarc.agent.{hostname}"

    async def _handle_request(msg):
        try:
            response_bytes = await loop.run_in_executor(None, dispatch, msg.data, shared_pid_map)
            await msg.respond(response_bytes)
        except Exception as exc:
            log.error("Handler error: %s", exc, exc_info=True)
            await msg.respond(b"")

    sub = await nc.subscribe(agent_subject, cb=_handle_request)
    log.info("NodeAgent NATS listening on subject %s", agent_subject)

    # Start substrate latency monitor
    from node_agent import substrate_monitor

    substrate_monitor.init(nc, hostname, loop)
    monitor_task = asyncio.create_task(substrate_monitor.monitor_loop(nc, hostname))

    # -----------------------------------------------------------------------
    # Serve until signal
    # -----------------------------------------------------------------------
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("Shutting down...")

    monitor_task.cancel()
    # wiring_task is a long-lived executor task — it dies with the process
    await sub.unsubscribe()
    await nc.close()
    log.info("Node Agent stopped")


def _refresh_pids(shared_pid_map: dict[str, int]) -> None:
    """Refresh the shared pid_map from local pod discovery."""
    try:
        from node_agent.pid_discovery import discover_local_pod_pids

        new_pids = discover_local_pod_pids()
        shared_pid_map.clear()
        shared_pid_map.update(new_pids)
        log.info("PID map refreshed: %d pods", len(shared_pid_map))
    except Exception as exc:
        log.warning("PID refresh failed: %s", exc)


if __name__ == "__main__":
    asyncio.run(main())
