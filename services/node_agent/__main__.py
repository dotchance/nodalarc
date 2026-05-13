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
import ipaddress
import json
import logging
import os
import signal
import socket
from pathlib import Path

import nats
from nodalarc.nats_channels import (
    NATS_CONNECT_OPTIONS,
    nats_url,
    node_agent_subject,
    wiring_progress_subject,
)
from nodalarc.substrate.manifest_contract import WiringManifest

from node_agent import ops_events
from node_agent.command_contract import RuntimeFence
from node_agent.reconcile import (
    clean_nodalarc_kernel_state,
    get_actual_nodalarc_interfaces,
    wiring_status_is_current,
)
from node_agent.server import dispatch
from node_agent.wiring import execute_wiring, write_wiring_status

log = logging.getLogger(__name__)


def _running_in_k8s() -> bool:
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST") or os.environ.get("NODE_NAME"))


def _require_host_ip_for_vxlan_capable_startup() -> None:
    """Validate the downward-API host IP before accepting any work."""
    if not _running_in_k8s():
        return
    host_ip = os.environ.get("HOST_IP", "").strip()
    if not host_ip:
        ops_events.spool_failure(
            code="STARTUP_HOST_IP_MISSING",
            message="HOST_IP env var is required for VXLAN-capable Node Agent startup",
            details={"node_name": os.environ.get("NODE_NAME", "")},
            session_id="",
        )
        raise RuntimeError("HOST_IP env var is required for VXLAN-capable Node Agent startup")
    try:
        ipaddress.ip_address(host_ip)
    except ValueError as exc:
        ops_events.spool_failure(
            code="STARTUP_HOST_IP_INVALID",
            message=f"HOST_IP env var is not a valid IP address: {host_ip!r}",
            details={"node_name": os.environ.get("NODE_NAME", ""), "host_ip": host_ip},
            session_id="",
        )
        raise RuntimeError(f"HOST_IP env var is not a valid IP address: {host_ip!r}") from exc


def _explicit_fence_from_env() -> RuntimeFence | None:
    session_id = os.environ.get("NODE_AGENT_SESSION_ID", "").strip()
    wiring_generation = os.environ.get("NODE_AGENT_WIRING_GENERATION", "").strip()
    if not session_id and not wiring_generation:
        return None
    if not session_id or not wiring_generation:
        raise RuntimeError(
            "NODE_AGENT_SESSION_ID and NODE_AGENT_WIRING_GENERATION must be provided together"
        )
    if not wiring_generation.startswith("sha256:") or len(wiring_generation) != len("sha256:") + 64:
        raise RuntimeError("NODE_AGENT_WIRING_GENERATION must be sha256:<64 hex chars>")
    from nodalarc.nats_channels import sanitize_session_id

    return RuntimeFence(
        session_id=sanitize_session_id(session_id),
        wiring_generation=wiring_generation,
    )


def _require_ready_fence(fence: RuntimeFence) -> None:
    if fence.session_id and fence.wiring_generation:
        return
    ops_events.publish(
        level="critical",
        code="STARTUP_WIRING_IDENTITY_MISSING",
        message="Node Agent has no session_id/wiring_generation; refusing NATS command subscription",
        session_id="",
        details={
            "session_id_present": bool(fence.session_id),
            "wiring_generation_present": bool(fence.wiring_generation),
        },
    )
    raise RuntimeError("Node Agent wiring identity unavailable; refusing NATS command subscription")


async def main() -> None:
    from nodal.logging import configure as _configure_logging

    _configure_logging("nodal.arc.node_agent", nats_level=logging.INFO)

    parser = argparse.ArgumentParser(description="Nodal Arc Node Agent")
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

    hostname = socket.gethostname()

    # Init platform config — required for NATS URL and namespace resolution.
    # If this fails, the Node Agent cannot function. Spool durable evidence
    # before re-raising so pre-NATS startup failures are not logs-only.
    from nodalarc.platform_config import init_platform_config

    try:
        init_platform_config(Path(args.platform_config))
    except Exception as exc:
        ops_events.spool_failure(
            code="STARTUP_CONFIG_FAILED",
            message=f"Node Agent platform config failed: {exc}",
            details={"platform_config": args.platform_config},
            session_id="",
        )
        raise

    _require_host_ip_for_vxlan_capable_startup()

    log.info(
        "Node Agent starting [build=%s, node=%s]",
        os.environ.get("NODAL_BUILD", "dev"),
        hostname,
    )

    # -----------------------------------------------------------------------
    # Connect to NATS FIRST — the Node Agent is a NATS-native actor.
    # This connection is used for wiring progress, request/reply, and
    # substrate monitoring. One connection for the lifetime of the process.
    # -----------------------------------------------------------------------
    try:
        nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
    except Exception as exc:
        ops_events.spool_failure(
            code="STARTUP_NATS_FAILED",
            message=f"Node Agent NATS connection failed: {exc}",
            details={"nats_url": nats_url()},
            session_id="",
        )
        raise
    from nodal.logging import connect as _connect_logging

    await _connect_logging(nc)
    progress_subject = wiring_progress_subject(hostname)
    loop = asyncio.get_running_loop()
    await ops_events.init(nc, hostname=hostname, loop=loop)
    log.debug("NATS connected to %s as %s", nats_url(), hostname)

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
    current_fence = RuntimeFence(session_id="", wiring_generation="")
    first_wiring_done = asyncio.Event()

    # If --pid-map provided, skip wiring discovery
    if args.pid_map:
        explicit_fence = _explicit_fence_from_env()
        if explicit_fence is None:
            ops_events.spool_failure(
                code="STARTUP_WIRING_IDENTITY_MISSING",
                message="--pid-map requires NODE_AGENT_SESSION_ID and NODE_AGENT_WIRING_GENERATION",
                details={"pid_map": args.pid_map},
                session_id="",
            )
            raise RuntimeError(
                "--pid-map requires NODE_AGENT_SESSION_ID and NODE_AGENT_WIRING_GENERATION"
            )
        shared_pid_map.update(json.loads(Path(args.pid_map).read_text()))
        current_fence = explicit_fence
        from node_agent import substrate_monitor as _substrate_monitor

        _substrate_monitor.set_identity(
            current_fence.session_id,
            current_fence.wiring_generation,
        )
        log.info("Loaded pid_map from %s (%d entries)", args.pid_map, len(shared_pid_map))
        first_wiring_done.set()

    # -----------------------------------------------------------------------
    # Wiring watcher — runs in thread pool executor (synchronous code).
    # Watches nodalarc-topology-wiring ConfigMap, executes wiring on change.
    # -----------------------------------------------------------------------
    def _wiring_watcher() -> None:
        nonlocal current_fence
        import time

        from node_agent import substrate_monitor as _substrate_monitor

        try:
            import kubernetes.client
            import kubernetes.config

            kubernetes.config.load_incluster_config()
        except Exception:
            log.info("Not running in K8s — wiring watcher disabled")
            loop.call_soon_threadsafe(first_wiring_done.set)
            return

        from nodalarc.platform_config import get_platform_config

        ns = get_platform_config().kubernetes_namespace
        v1 = kubernetes.client.CoreV1Api()
        last_resource_version = ""

        while True:
            try:
                cm = v1.read_namespaced_config_map("nodalarc-topology-wiring", ns)
                rv = cm.metadata.resource_version or ""

                if rv == last_resource_version:
                    time.sleep(5)
                    continue

                # New manifest detected — immediately clear stale pid_map.
                # Any BatchLinkUp arriving from this point forward will be
                # cleanly deferred until wiring completes and PIDs refresh.
                shared_pid_map.clear()

                compressed = cm.data.get("manifest.json.gz.b64")
                if compressed:
                    import base64
                    import gzip

                    manifest_json = gzip.decompress(base64.b64decode(compressed)).decode()
                else:
                    manifest_json = cm.data.get("manifest.json", "{}")
                manifest = json.loads(manifest_json)
                manifest_model = WiringManifest.model_validate(manifest)
                nodes = manifest_model.nodes

                # Extract session_id for NATS subject scoping
                manifest_session_id = manifest_model.session_id
                if not manifest_session_id:
                    log.error(
                        "FATAL: Wiring manifest has no session_id — cannot scope NATS subjects"
                    )
                    raise ValueError("Wiring manifest missing session_id")
                wiring_generation = manifest_model.wiring_generation
                if not wiring_generation:
                    log.error(
                        "FATAL: Wiring manifest has no wiring_generation — cannot fence commands"
                    )
                    raise ValueError("Wiring manifest missing wiring_generation")
                from nodalarc.nats_channels import sanitize_session_id

                monitor_session_id = sanitize_session_id(manifest_session_id)
                _substrate_monitor.set_identity(monitor_session_id, wiring_generation)
                current_fence = RuntimeFence(
                    session_id=monitor_session_id,
                    wiring_generation=wiring_generation,
                )
                log.info(
                    "Node Agent session_id=%s generation=%s (from wiring manifest)",
                    monitor_session_id,
                    wiring_generation,
                )

                if not nodes:
                    last_resource_version = rv
                    time.sleep(5)
                    continue

                _substrate_monitor.configure_required_measurements(
                    v1=v1,
                    namespace=ns,
                    hostname=hostname,
                    manifest=manifest_model,
                )

                # Case B: wiring-status exists and covers all manifest nodes
                if wiring_status_is_current(v1, ns, manifest_model):
                    log.info(
                        "Wiring verified — status matches manifest (%d nodes), no-op",
                        len(nodes),
                    )
                    _refresh_pids(shared_pid_map)
                    loop.call_soon_threadsafe(first_wiring_done.set)
                    last_resource_version = rv
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

                statuses = execute_wiring(
                    manifest_model, namespace=ns, progress_fn=_publish_progress
                )

                if not statuses:
                    # No local pods on this node — nothing to wire, nothing
                    # to report. Advance cursor and move on silently.
                    loop.call_soon_threadsafe(first_wiring_done.set)
                    last_resource_version = rv
                else:
                    ready_count = sum(1 for s in statuses.values() if s.status == "ready")
                    failed_count = len(statuses) - ready_count
                    log.info(
                        "Wiring complete: %d ready, %d failed",
                        ready_count,
                        failed_count,
                    )

                    # Refresh pid_map BEFORE writing wiring status. Once the
                    # status is written, the Operator advances to Ready and
                    # the Scheduler dispatches immediately.
                    _refresh_pids(shared_pid_map)
                    write_wiring_status(statuses, manifest_model, namespace=ns)
                    if failed_count:
                        raise RuntimeError(
                            f"wiring failed for {failed_count} local node(s); not accepting requests"
                        )
                    loop.call_soon_threadsafe(first_wiring_done.set)
                    last_resource_version = rv

                    # If some pods were skipped (no PID at wiring time),
                    # retry sysctls and finalization in the background.
                    all_local = {n for n in nodes if n in shared_pid_map or n in statuses}
                    missed = all_local - set(statuses.keys())
                    if missed:
                        log.warning(
                            "Wiring partial: %d nodes skipped, retrying in background: %s",
                            len(missed),
                            ", ".join(sorted(missed)),
                        )
                        _retry_missed_nodes(missed, manifest, ns, shared_pid_map)

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
    log.debug("Waiting for wiring to complete before accepting NATS requests...")
    await first_wiring_done.wait()
    log.debug("Wiring ready — pid_map has %d entries", len(shared_pid_map))
    _require_ready_fence(current_fence)

    # -----------------------------------------------------------------------
    # NATS request/reply server — subscribes AFTER wiring (pid_map gate)
    # -----------------------------------------------------------------------
    agent_subject = node_agent_subject(hostname)

    async def _handle_request(msg):
        try:
            response_bytes = await loop.run_in_executor(
                None, dispatch, msg.data, shared_pid_map, current_fence
            )
            await msg.respond(response_bytes)
        except Exception as exc:
            log.error("Handler error: %s", exc, exc_info=True)
            from nodalarc.proto import node_agent_pb2

            await msg.respond(
                node_agent_pb2.CommandFailureResponse(
                    success=False,
                    error_code=node_agent_pb2.NODE_AGENT_INTERNAL_ERROR,
                    error_message=f"handler error: {exc}",
                    dirty_kernel=True,
                ).SerializeToString()
            )

    sub = await nc.subscribe(agent_subject, cb=_handle_request)
    log.debug("NodeAgent NATS listening on subject %s", agent_subject)

    # Start substrate latency monitor
    from node_agent import substrate_monitor

    stop = asyncio.Event()
    substrate_monitor.init(nc, hostname, loop)
    monitor_task = asyncio.create_task(substrate_monitor.monitor_loop(nc, hostname))

    def _monitor_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.critical(
                "Substrate monitor stopped unexpectedly: %s",
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            stop.set()

    monitor_task.add_done_callback(_monitor_done)

    # -----------------------------------------------------------------------
    # Serve until signal
    # -----------------------------------------------------------------------
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


def _retry_missed_nodes(
    missed: set[str],
    manifest: dict,
    namespace: str,
    shared_pid_map: dict[str, int],
) -> None:
    """Retry sysctls + finalization for nodes that were skipped during wiring.

    Called from the wiring watcher thread when some pods didn't have PIDs
    at wiring time. Polls for PIDs every 5 seconds up to 60 seconds.
    Once a PID appears, applies sysctls, removes default route, and locks
    down cni0 — the same operations as wiring Phases 1, 7, and 8.

    Does NOT create veths or ISL interfaces — those are handled by the
    Scheduler via BatchLinkUp when the OME makes them visible.
    """
    import time

    from node_agent.namespace_ops import _write_sysctl_in_netns
    from node_agent.pid_discovery import discover_local_pod_pids
    from node_agent.wiring import finalize_pod_phases

    nodes = manifest.get("nodes", {})
    remaining = set(missed)

    for _attempt in range(12):
        if not remaining:
            break
        time.sleep(5)
        fresh_pids = discover_local_pod_pids(namespace)
        shared_pid_map.update(fresh_pids)

        resolved = []
        for node_id in list(remaining):
            pid = fresh_pids.get(node_id, 0)
            if pid == 0:
                continue
            node_spec = nodes.get(node_id, {})
            for key, value in node_spec.get("sysctls", {}).items():
                err = _write_sysctl_in_netns(pid, key, str(value))
                if err:
                    log.warning("Retry sysctl %s=%s failed for %s: %s", key, value, node_id, err)
            route_err, security_err = finalize_pod_phases(pid, node_id)
            if route_err or security_err:
                log.warning(
                    "Retry finalization failed for %s: route=%s security=%s",
                    node_id,
                    route_err or "ok",
                    security_err or "ok",
                )
                continue
            resolved.append(node_id)
            remaining.discard(node_id)

        if resolved:
            log.info(
                "Late-start nodes recovered [resolved=%s, remaining=%d]",
                ", ".join(sorted(resolved)),
                len(remaining),
            )

    if remaining:
        log.error(
            "FATAL: %d nodes never got PIDs after 60s — sysctls and finalization not applied: %s",
            len(remaining),
            ", ".join(sorted(remaining)),
        )


if __name__ == "__main__":
    asyncio.run(main())
