# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Kopf handlers for ConstellationSpec CRD lifecycle.

Reconciler pattern: _reconcile_session() is the single convergence function
called by on_create, on_resume, and the wiring_check timer. It checks 5
conditions in order and performs at most one convergence action per invocation.
The kopf timer re-invokes it periodically to drive progress.

on_create retains blocking waits for old-pod termination and initial pod
deployment because fresh creation needs synchronous sequencing. After pods
are created, it delegates to _reconcile_session for the remaining lifecycle.
"""

from __future__ import annotations

import asyncio
import logging

import kopf
import kubernetes

from nodalarc_operator.session_deployer import (
    check_all_pods_running,
    check_old_pods_terminated,
    check_pods_ready,
    check_wiring_complete,
    compute_platform_hash,
    deploy_session,
    restart_platform_pods,
    set_nodalpath_mode,
    signal_frr_config_ready,
    teardown_session,
    write_pod_ips_configmap,
    write_wiring_manifest,
)

log = logging.getLogger(__name__)


# Module-level K8s clients — initialized once on first use, reused for all calls.
# Eliminates per-call load_incluster_config() + client instantiation overhead.
_custom_api: kubernetes.client.CustomObjectsApi | None = None


def _get_custom_api() -> kubernetes.client.CustomObjectsApi:
    global _custom_api
    if _custom_api is None:
        kubernetes.config.load_incluster_config()
        _custom_api = kubernetes.client.CustomObjectsApi()
    return _custom_api


def _update_status(name: str, namespace: str, status: dict) -> None:
    """Update the ConstellationSpec CR status subresource."""
    _get_custom_api().patch_namespaced_custom_object_status(
        group="nodalarc.io",
        version="v1alpha1",
        namespace=namespace,
        plural="constellationspecs",
        name=name,
        body={"status": status},
    )


def _build_owner_ref(name: str, meta: dict) -> dict:
    """Build ownerReference dict for garbage collection."""
    return {
        "apiVersion": "nodalarc.io/v1alpha1",
        "kind": "ConstellationSpec",
        "name": name,
        "uid": meta["uid"],
        "blockOwnerDeletion": True,
    }


def _has_wiring_manifest(namespace: str) -> bool:
    """Check whether the topology wiring manifest ConfigMap exists."""
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    try:
        v1.read_namespaced_config_map("nodalarc-topology-wiring", namespace)
        return True
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return False
        raise


def _has_frr_config_ready(namespace: str) -> bool:
    """Check whether FRR config-ready sentinel exists in session pods.

    Samples up to 3 pods — if all sampled pods have the sentinel,
    we consider FRR signaling complete. This avoids exec-ing into
    every pod on every reconcile tick.
    """
    from kubernetes.stream import stream

    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    if not pods.items:
        return False

    # Sample up to 3 pods
    sample = pods.items[:3]
    for pod in sample:
        if not pod.status or pod.status.phase != "Running":
            return False
        try:
            result = stream(
                v1.connect_get_namespaced_pod_exec,
                pod.metadata.name,
                namespace,
                container="frr",
                command=["test", "-f", "/etc/frr/.config-ready"],
                stderr=True,
                stdout=True,
                stdin=False,
                tty=False,
            )
            # stream returns empty string on success, non-zero exit raises
        except Exception:
            return False
    return True


async def _reconcile_session(spec, name, namespace, meta, status):
    """Converge cluster state toward desired session state.

    Called by on_create (after initial deploy), on_resume, and the
    wiring_check timer. Idempotent — safe to call at any point in
    the lifecycle.

    Checks 5 conditions in order. For each condition that isn't met,
    performs the convergence action and returns (one step per invocation).
    The kopf timer re-enters periodically to drive progress.
    """
    loop = asyncio.get_running_loop()
    phase = status.get("phase", "")
    owner_ref = _build_owner_ref(name, meta)

    # --- Condition 1: Old pods terminated ---
    # Only relevant on fresh creation (phase Pending with no pods yet).
    # If phase is already Creating/Wiring/Ready, pods belong to this session.
    if phase in ("", "Pending"):
        total, _ = await loop.run_in_executor(None, check_pods_ready, namespace)
        if total > 0 and status.get("podCount", 0) == 0:
            # Pods exist but this session hasn't deployed yet — old session remnants
            cleared = await loop.run_in_executor(None, check_old_pods_terminated, namespace)
            if not cleared:
                log.info("Reconcile: waiting for old session pods to terminate")
                _update_status(
                    name,
                    namespace,
                    {
                        "phase": "Pending",
                        "message": "Waiting for old session pods to terminate",
                    },
                )
                return

    # --- Condition 2: Session deployed (pods created and running) ---
    pod_count = status.get("podCount", 0)
    if pod_count == 0:
        # No pods deployed yet — check if any session pods exist from a
        # previous operator run that didn't update status
        total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
        if total > 0:
            # Pods exist but status.podCount is 0 — adopt them
            pod_count = total
            log.info(f"Reconcile: adopting {total} existing session pods")
        else:
            # No pods at all — cannot deploy from reconciler (on_create handles this)
            log.info("Reconcile: no pods exist, waiting for initial deployment")
            return

    all_ready, total, ready = await loop.run_in_executor(
        None, check_all_pods_running, namespace, pod_count
    )
    if not all_ready:
        _update_status(
            name,
            namespace,
            {
                "phase": "Creating",
                "readyPods": ready,
                "podCount": pod_count,
                "message": f"Pods: {ready} running, {pod_count - ready} starting",
            },
        )
        log.info(f"Reconcile: {ready}/{pod_count} pods running, waiting for all")
        return

    # All pods running — proceed through remaining conditions

    # --- Condition 3: FRR config signaled ---
    frr_signaled = await loop.run_in_executor(None, _has_frr_config_ready, namespace)
    if not frr_signaled:
        _update_status(
            name,
            namespace,
            {
                "phase": "Creating",
                "readyPods": ready,
                "podCount": pod_count,
                "message": f"Signaling FRR config ready in {pod_count} pods",
            },
        )
        await loop.run_in_executor(
            None, signal_frr_config_ready, namespace, lambda msg: log.info(f"Reconcile: {msg}")
        )
        log.info(f"Reconcile: FRR config signaled in {pod_count} pods")
        # Fall through — FRR signaling is fast, continue to wiring

    # --- Condition 4: Wiring manifest written + wiring complete ---
    manifest_exists = await loop.run_in_executor(None, _has_wiring_manifest, namespace)
    if not manifest_exists:
        _update_status(
            name,
            namespace,
            {
                "phase": "Creating",
                "readyPods": ready,
                "podCount": pod_count,
                "message": "Writing pod IP addresses and wiring manifest",
            },
        )
        await loop.run_in_executor(None, write_pod_ips_configmap, namespace)
        await loop.run_in_executor(None, write_wiring_manifest, dict(spec), namespace, owner_ref)
        _update_status(
            name,
            namespace,
            {
                "phase": "Wiring",
                "readyPods": ready,
                "podCount": pod_count,
                "message": f"All {pod_count} pods running. Node Agent wiring data plane.",
            },
        )
        log.info("Reconcile: wiring manifest written, advanced to Wiring")
        return

    # Manifest exists — check wiring completion
    try:
        complete, wired_count, progress_msg = await loop.run_in_executor(
            None, check_wiring_complete, namespace, pod_count
        )
    except kubernetes.client.rest.ApiException as e:
        log.warning("Reconcile: wiring status check error: %s", e)
        return

    if not complete:
        if wired_count == 0 and progress_msg is None:
            display_msg = "Waiting for Node Agent to begin wiring"
        else:
            display_msg = (
                progress_msg or f"Data plane wiring: {wired_count}/{pod_count} nodes wired"
            )
        _update_status(
            name,
            namespace,
            {
                "phase": "Wiring",
                "readyPods": ready,
                "podCount": pod_count,
                "wiredPods": wired_count,
                "message": display_msg,
            },
        )
        log.info(f"Reconcile: wiring in progress ({wired_count}/{pod_count})")
        return

    # --- Condition 5: Ready ---
    _update_status(
        name,
        namespace,
        {
            "phase": "Ready",
            "readyPods": ready,
            "podCount": pod_count,
            "wiredPods": wired_count,
            "message": f"Session ready: {pod_count} pods, {wired_count} wired.",
        },
    )
    log.info(f"Reconcile: session ready ({wired_count}/{pod_count} wired)")


@kopf.on.create("constellationspecs", group="nodalarc.io")
async def on_create(spec, name, namespace, meta, **_):
    """Handle ConstellationSpec CR creation — deploy a session.

    Retains blocking waits for old-pod termination and initial pod
    deployment (fresh creation needs synchronous sequencing). After
    pods reach Running, delegates to _reconcile_session for FRR
    signaling, wiring, and Ready advancement.
    """
    log.info(f"ConstellationSpec '{name}' created in {namespace}")

    # Singleton constraint: only 'current-session' is allowed
    if name != "current-session":
        _update_status(
            name,
            namespace,
            {
                "phase": "Error",
                "message": f"Only 'current-session' is allowed as CR name, got '{name}'",
            },
        )
        raise kopf.PermanentError(f"Invalid CR name: {name}")

    _update_status(
        name,
        namespace,
        {
            "phase": "Pending",
            "observedGeneration": meta.get("generation", 0),
            "platformHash": compute_platform_hash(dict(spec)),
        },
    )

    owner_ref = _build_owner_ref(name, meta)

    # Wait for any old session pods to finish terminating
    loop = asyncio.get_running_loop()
    old_pods_clear = False
    for _ in range(60):
        if await loop.run_in_executor(None, check_old_pods_terminated, namespace):
            old_pods_clear = True
            break
        log.info("Waiting for old session pods to terminate...")
        await asyncio.sleep(2)
    if not old_pods_clear:
        log.error("Old session pods did not terminate within 120s — aborting deploy")
        _update_status(
            name,
            namespace,
            {
                "phase": "Error",
                "message": "Timeout waiting for old session pods to terminate",
            },
        )
        raise kopf.PermanentError("Old session pods did not terminate within 120s")

    # Deploy session (blocking — run in executor to not block kopf)
    def _deploy_progress(msg: str) -> None:
        _update_status(name, namespace, {"phase": "Pending", "message": msg})

    try:
        result = await loop.run_in_executor(
            None, deploy_session, dict(spec), name, namespace, owner_ref, _deploy_progress
        )
    except Exception as exc:
        _update_status(
            name,
            namespace,
            {
                "phase": "Error",
                "message": str(exc)[:500],
            },
        )
        raise kopf.PermanentError(f"Deploy failed: {exc}") from exc

    _update_status(name, namespace, result)

    # Set NodalPath mode based on session protocol before restarting
    protocol = spec.get("routing", {}).get("protocol", "isis")
    _update_status(name, namespace, {"phase": "Creating", "message": "Configuring NodalPath mode"})
    await loop.run_in_executor(None, set_nodalpath_mode, namespace, protocol)

    # Restart platform pods to pick up new session ConfigMaps
    _update_status(
        name,
        namespace,
        {"phase": "Creating", "message": "Restarting platform services (OME, Scheduler, VS-API)"},
    )
    await loop.run_in_executor(None, restart_platform_pods, namespace)
    log.info("Restarted platform pods for new session")

    # Wait for pods to reach Running (blocking — fresh creation needs this)
    pod_count = result.get("podCount", 0)
    if pod_count > 0:
        all_pods_ready = False
        for _i in range(600):  # 10 minutes max
            total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
            pending = pod_count - ready
            _update_status(
                name,
                namespace,
                {
                    "phase": "Creating",
                    "readyPods": ready,
                    "podCount": pod_count,
                    "message": f"Pods: {ready} running, {pending} starting",
                },
            )
            if ready >= pod_count:
                all_pods_ready = True
                break
            await asyncio.sleep(1)
        if not all_pods_ready:
            log.error(
                f"Session pods did not reach Running within 600s "
                f"({ready}/{pod_count} ready) — aborting"
            )
            _update_status(
                name,
                namespace,
                {
                    "phase": "Error",
                    "message": f"Timeout: {ready}/{pod_count} pods Running after 600s",
                },
            )
            raise kopf.PermanentError(f"Session pods did not reach Running: {ready}/{pod_count}")

    # Pods are running — delegate to reconciler for FRR signaling → wiring → Ready
    current_status = {
        "phase": "Creating",
        "podCount": pod_count,
        "readyPods": pod_count,
    }
    await _reconcile_session(spec, name, namespace, meta, current_status)


@kopf.on.update("constellationspecs", group="nodalarc.io")
async def on_update(spec, name, namespace, meta, status, **_):
    """Handle CRD spec changes — session switch or config update.

    Uses semantic hashing to determine what changed:
    - Platform-impacting fields (constellation, routing, time, GS):
      restart platform pods via forced rolling update, then reconcile.
    - Non-impacting fields (metadata, placement): reconcile without
      restarting platform pods.
    """
    phase = status.get("phase", "")
    if phase == "Error":
        log.info("on_update: session in Error state, skipping")
        return

    loop = asyncio.get_running_loop()
    new_hash = compute_platform_hash(dict(spec))
    old_hash = status.get("platformHash", "")

    if old_hash and new_hash != old_hash:
        log.info(
            "Platform-impacting spec change detected (hash %s → %s), restarting platform services",
            old_hash[:8],
            new_hash[:8],
        )
        _update_status(
            name,
            namespace,
            {
                "phase": "Creating",
                "message": "Session config changed — restarting platform services",
                "platformHash": new_hash,
            },
        )
        await loop.run_in_executor(None, restart_platform_pods, namespace)
    elif not old_hash:
        _update_status(name, namespace, {"platformHash": new_hash})

    await _reconcile_session(spec, name, namespace, meta, status)


@kopf.on.delete("constellationspecs", group="nodalarc.io")
async def on_delete(name, namespace, **_):
    """Handle ConstellationSpec CR deletion — tear down session."""
    log.info(f"ConstellationSpec '{name}' deleted, tearing down session")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, teardown_session, namespace)
    await loop.run_in_executor(None, set_nodalpath_mode, namespace, "console")
    log.info("Session teardown complete")


@kopf.on.resume("constellationspecs", group="nodalarc.io")
async def on_resume(spec, name, namespace, meta, status, **_):
    """Handle Operator restart — reconcile existing session state."""
    phase = status.get("phase", "")
    log.info(f"Resuming ConstellationSpec '{name}', current phase: {phase}")

    if phase == "Error":
        log.info(f"Operator resume: session in Error state: {status.get('message', '')}")
        return

    await _reconcile_session(spec, name, namespace, meta, status)


@kopf.timer("constellationspecs", group="nodalarc.io", interval=10.0, idle=10)
async def wiring_check(spec, name, namespace, meta, status, **_):
    """Periodically advance session state via the reconciler.

    Active during Creating and Wiring phases. Drives progress for:
    - Creating: pods still starting after operator resume
    - Wiring: Node Agent wiring data plane → Ready
    """
    phase = status.get("phase", "")
    if phase not in ("Creating", "Wiring"):
        return

    await _reconcile_session(spec, name, namespace, meta, status)
