# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
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
from nodalarc.substrate.wiring_status import rewiring_status

from node_agent import ops_events
from node_agent.command_contract import RuntimeFence
from node_agent.reconcile import (
    clean_nodalarc_kernel_state,
    get_actual_nodalarc_interfaces,
    wiring_status_is_current,
)
from node_agent.server import DispatchGate, dispatch
from node_agent.wiring import (
    discover_expected_handles,
    execute_wiring,
    expected_local_nodes,
    write_wiring_status,
)

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
    from node_agent.pid_discovery import NamespaceHandle, netns_identity

    shared_handles: dict[str, NamespaceHandle] = {}
    dispatch_gate = DispatchGate()
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
        for explicit_node_id, explicit_pid in json.loads(Path(args.pid_map).read_text()).items():
            explicit_netns = netns_identity(int(explicit_pid))
            if explicit_netns is None:
                raise RuntimeError(
                    f"--pid-map entry {explicit_node_id}={explicit_pid} has no live "
                    "network namespace"
                )
            shared_handles[explicit_node_id] = NamespaceHandle(
                node_id=explicit_node_id,
                # Explicitly unmanaged: this path bypasses pod discovery, so
                # there is no pod or sandbox identity to bind. The netns
                # identity is real and still verified per request.
                pod_uid="explicit",
                sandbox_id="explicit",
                sandbox_attempt=0,
                pid=int(explicit_pid),
                netns_id=explicit_netns,
            )
        current_fence = explicit_fence
        from node_agent import substrate_monitor as _substrate_monitor

        _substrate_monitor.set_identity(
            current_fence.session_id,
            current_fence.wiring_generation,
        )
        log.info("Loaded pid_map from %s (%d entries)", args.pid_map, len(shared_handles))
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
                    # Steady state: verify the shared handles still name the
                    # namespaces they were created for. A sandbox recreation
                    # invalidates a handle even though the pod persists; the
                    # kernel wiring died with the old namespace, so force a
                    # rewire of the current manifest.
                    stale = [
                        node_id
                        for node_id, handle in shared_handles.items()
                        if not _verify_handle(handle)
                    ]
                    if stale:
                        log.warning(
                            "Namespace handles invalidated for %s — rewiring current manifest",
                            ", ".join(sorted(stale)),
                        )
                        last_resource_version = ""
                        continue
                    time.sleep(5)
                    continue

                # New manifest detected. Handles are NOT withdrawn here:
                # the transition is owned solely by perform_rewire (or the
                # no-local/Case B terminal states below), and the fence flip
                # below already rejects any request from the previous
                # generation.
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

                # Case B: wiring-status exists and covers all manifest nodes.
                # Host firewall state drifts independently of interface state
                # (a Docker (re)start re-imposes FORWARD DROP), so verification
                # also re-pins site-LAN transit rules; a pin failure means the
                # host may police LAN transit, which is NOT a verified state —
                # fall through to the rewire path so the failure lands in
                # wiring status instead of a clean no-op.
                transit_verified = True
                if manifest_model.site_lans:
                    from node_agent.site_lan import ensure_site_lan_transit

                    try:
                        ensure_site_lan_transit()
                    except Exception:
                        log.exception(
                            "Site LAN transit rules could not be pinned during "
                            "verification — treating wiring as diverged"
                        )
                        transit_verified = False
                # One validated handle set drives everything below. The
                # expected-local set comes from the manifest, and discovery
                # must COMPLETE before any conclusion is drawn or any kernel
                # state is touched: an incomplete result (including any
                # transient Kubernetes or CRI failure) leaves the existing
                # data plane untouched and retries.
                expected_local = expected_local_nodes(manifest_model)
                if not expected_local:
                    log.info("Manifest places no pods on this node — nothing to wire")
                    if not replace_handles_when_idle(dispatch_gate, shared_handles, {}):
                        time.sleep(5)
                        continue
                    loop.call_soon_threadsafe(first_wiring_done.set)
                    last_resource_version = rv
                    time.sleep(5)
                    continue

                handles = discover_expected_handles(manifest_model, ns, expected_local)
                if handles is None:
                    log.warning(
                        "Wiring pending: incomplete handle discovery — existing "
                        "kernel state left untouched; retrying the current manifest"
                    )
                    time.sleep(5)
                    continue

                # Case B: every local row names the exact live incarnation
                # and every manifest node is ready — a true no-op.
                if transit_verified and wiring_status_is_current(v1, ns, manifest_model, handles):
                    log.info(
                        "Wiring verified — status matches manifest (%d nodes), no-op",
                        len(nodes),
                    )
                    if not replace_handles_when_idle(dispatch_gate, shared_handles, handles):
                        time.sleep(5)
                        continue
                    loop.call_soon_threadsafe(first_wiring_done.set)
                    last_resource_version = rv
                    time.sleep(5)
                    continue

                rewired = perform_rewire(
                    manifest_model,
                    ns,
                    handles,
                    expected_local,
                    shared_handles,
                    dispatch_gate,
                    progress_fn=_publish_progress,
                )
                if rewired is None:
                    # Drain timed out: nothing was mutated and dispatch was
                    # restored. Retry the same manifest.
                    time.sleep(5)
                    continue
                loop.call_soon_threadsafe(first_wiring_done.set)
                last_resource_version = rv

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

    # Start wiring watcher in thread pool.
    loop.run_in_executor(None, _wiring_watcher)

    # Wait for first wiring pass to complete before accepting requests
    log.debug("Waiting for wiring to complete before accepting NATS requests...")
    await first_wiring_done.wait()
    log.debug("Wiring ready — %d namespace handles", len(shared_handles))
    _require_ready_fence(current_fence)

    # -----------------------------------------------------------------------
    # NATS request/reply server — subscribes AFTER wiring (pid_map gate)
    # -----------------------------------------------------------------------
    agent_subject = node_agent_subject(hostname)

    async def _handle_request(msg):
        try:
            response_bytes = await loop.run_in_executor(
                None, dispatch, msg.data, shared_handles, current_fence, dispatch_gate
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

    # Start substrate status refresh monitor.
    from node_agent import substrate_monitor

    stop = asyncio.Event()
    substrate_monitor.init(hostname)
    monitor_task = asyncio.create_task(substrate_monitor.monitor_loop(hostname))

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


def perform_rewire(
    manifest_model: WiringManifest,
    namespace: str,
    handles: dict,
    expected_local: set[str],
    shared_handles: dict,
    dispatch_gate,
    progress_fn=None,
) -> dict:
    """The one rewire transition, in its only legal order.

    stop/drain dispatch -> publish non-ready -> withdraw handles -> rebuild
    -> install handles -> publish ready -> resume dispatch. A dispatched
    batch can therefore never run against half-torn kernel state, and ready
    proof is never visible while the host is being rebuilt.

    A drain timeout mutates NOTHING: dispatch is restored and None is
    returned so the caller retries later — an in-flight mutation must never
    overlap a rebuild. Failure paths (failed wiring, failed ready-status
    write) leave dispatch CLOSED and handles withdrawn and raise; dispatch
    resumes only after ready status is successfully published.
    """
    if not dispatch_gate.drain():
        log.warning(
            "Dispatch drain timed out — an operation is still running; "
            "mutating nothing and retrying later"
        )
        dispatch_gate.resume()
        return None
    try:
        write_wiring_status(
            {
                node_id: rewiring_status(
                    node_id,
                    manifest_model,
                    pod_uid=handles[node_id].pod_uid,
                    sandbox_id=handles[node_id].sandbox_id,
                    netns_id=handles[node_id].netns_id,
                )
                for node_id in expected_local
            },
            manifest_model,
            namespace=namespace,
        )
        shared_handles.clear()

        actual = get_actual_nodalarc_interfaces()
        if not actual:
            log.info("No kernel state — wiring from scratch (%d nodes)", len(manifest_model.nodes))
        else:
            log.warning(
                "Kernel state diverged (%d interfaces) — cleaning and re-wiring",
                len(actual),
            )
            cleaned = clean_nodalarc_kernel_state()
            log.info("Cleaned %d stale kernel interfaces", cleaned)

        statuses = execute_wiring(
            manifest_model, namespace=namespace, handles=handles, progress_fn=progress_fn
        )

        ready_count = sum(1 for s in statuses.values() if s.status == "ready")
        failed_count = len(statuses) - ready_count
        log.info("Wiring complete: %d ready, %d failed", ready_count, failed_count)

        if failed_count:
            write_wiring_status(statuses, manifest_model, namespace=namespace)
            raise RuntimeError(
                f"wiring failed for {failed_count} local node(s); not accepting requests"
            )

        shared_handles.update(handles)
        try:
            write_wiring_status(statuses, manifest_model, namespace=namespace)
        except Exception:
            shared_handles.clear()
            raise
    except BaseException:
        # A destructive rewire failure leaves dispatch CLOSED and handles
        # withdrawn: the host may hold partial kernel state, and no runtime
        # mutation may run until a later pass publishes honest ready proof.
        log.error("Rewire failed — dispatch stays closed until a later pass succeeds")
        raise
    dispatch_gate.resume()
    return statuses


def replace_handles_when_idle(dispatch_gate, shared_handles: dict, new_handles: dict) -> bool:
    """Swap the shared handle map only when dispatch is fully drained.

    The terminal states (no local pods; wiring already current) replace the
    handle map without a rebuild, and the same rule applies as everywhere
    else: a request in flight must never observe a half-swapped map. On a
    drain timeout nothing is touched, dispatch is restored, and False is
    returned — the caller must not advance and must retry.
    """
    if not dispatch_gate.drain():
        log.warning("Dispatch drain timed out — handle swap deferred; retrying")
        dispatch_gate.resume()
        return False
    shared_handles.clear()
    shared_handles.update(new_handles)
    dispatch_gate.resume()
    return True


def _verify_handle(handle) -> bool:
    """Whether a shared handle still names the namespace it was created for."""
    from node_agent.pid_discovery import verify_handle

    return verify_handle(handle)


if __name__ == "__main__":
    asyncio.run(main())
