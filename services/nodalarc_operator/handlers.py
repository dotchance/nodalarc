# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Kopf handlers for ConstellationSpec CRD lifecycle."""

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


@kopf.on.create("constellationspecs", group="nodalarc.io")
async def on_create(spec, name, namespace, meta, **_):
    """Handle ConstellationSpec CR creation — deploy a session."""
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
        },
    )

    # Build ownerReference for garbage collection
    owner_ref = {
        "apiVersion": "nodalarc.io/v1alpha1",
        "kind": "ConstellationSpec",
        "name": name,
        "uid": meta["uid"],
        "blockOwnerDeletion": True,
    }

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

    # Wait for pods to reach Running
    if result.get("phase") == "Creating":
        pod_count = result.get("podCount", 0)
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

        # Signal FRR config-ready in each pod
        _update_status(
            name,
            namespace,
            {
                "phase": "Creating",
                "readyPods": pod_count,
                "podCount": pod_count,
                "message": f"Signaling FRR config ready in {pod_count} pods",
            },
        )
        await loop.run_in_executor(None, signal_frr_config_ready, namespace, _deploy_progress)

        # Write pod-IPs ConfigMap (needs running pods)
        _update_status(
            name,
            namespace,
            {
                "phase": "Creating",
                "readyPods": pod_count,
                "podCount": pod_count,
                "message": "Writing pod IP addresses",
            },
        )
        await loop.run_in_executor(None, write_pod_ips_configmap, namespace)

        # Write topology wiring manifest for Node Agent (7b)
        _update_status(
            name,
            namespace,
            {
                "phase": "Creating",
                "readyPods": pod_count,
                "podCount": pod_count,
                "message": "Writing topology wiring manifest",
            },
        )
        await loop.run_in_executor(None, write_wiring_manifest, dict(spec), namespace, owner_ref)

        # Set Wiring phase — Node Agent will read manifest and wire data plane
        _update_status(
            name,
            namespace,
            {
                "phase": "Wiring",
                "readyPods": pod_count,
                "podCount": pod_count,
                "message": f"All {pod_count} pods running. Node Agent wiring data plane.",
            },
        )
        log.info(f"Session deployed: {pod_count} pods running, waiting for wiring")

        # Wait for Node Agent to write wiring-complete status
        for _i in range(300):  # 5 minutes max (large constellations can take several minutes)
            try:
                complete, wired_count, progress_msg = await loop.run_in_executor(
                    None, check_wiring_complete, namespace, pod_count
                )
                if wired_count == 0 and progress_msg is None:
                    # ConfigMap not yet written (404) — Node Agent hasn't started
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
                        "readyPods": pod_count,
                        "podCount": pod_count,
                        "wiredPods": wired_count,
                        "message": display_msg,
                    },
                )
                if complete:
                    _update_status(
                        name,
                        namespace,
                        {
                            "phase": "Ready",
                            "readyPods": pod_count,
                            "podCount": pod_count,
                            "wiredPods": wired_count,
                            "message": f"Session ready: {pod_count} pods, {wired_count} wired.",
                        },
                    )
                    log.info(f"Session ready: {wired_count}/{pod_count} nodes wired")
                    return
            except kubernetes.client.rest.ApiException as e:
                log.warning(f"Wiring status check error: {e}")
            await asyncio.sleep(1)

        # Timeout — stay in Wiring phase
        log.warning("Wiring not complete after 120s, staying in Wiring phase")


@kopf.on.delete("constellationspecs", group="nodalarc.io")
async def on_delete(name, namespace, **_):
    """Handle ConstellationSpec CR deletion — tear down session."""
    log.info(f"ConstellationSpec '{name}' deleted, tearing down session")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, teardown_session, namespace)
    # Reset NodalPath to console mode after session teardown
    await loop.run_in_executor(None, set_nodalpath_mode, namespace, "console")
    log.info("Session teardown complete")


@kopf.on.resume("constellationspecs", group="nodalarc.io")
async def on_resume(spec, name, namespace, meta, status, **_):
    """Handle Operator restart — reconcile existing session state."""
    phase = status.get("phase", "")
    log.info(f"Resuming ConstellationSpec '{name}', current phase: {phase}")

    if phase == "Wiring":
        # Check if wiring completed while we were away
        loop = asyncio.get_running_loop()
        total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
        try:
            complete, wired_count, _ = await loop.run_in_executor(
                None, check_wiring_complete, namespace, total
            )
            if complete:
                _update_status(
                    name,
                    namespace,
                    {
                        "phase": "Ready",
                        "readyPods": ready,
                        "podCount": total,
                        "wiredPods": wired_count,
                        "message": f"Session ready: {total} pods, {wired_count} wired.",
                    },
                )
                log.info(f"Operator resume: advanced Wiring → Ready ({wired_count} wired)")
                return
        except kubernetes.client.rest.ApiException as e:
            log.warning("Wiring status check error on resume: %s", e)
        _update_status(
            name,
            namespace,
            {
                "phase": "Wiring",
                "readyPods": ready,
                "podCount": total,
                "message": f"{ready}/{total} pods running, wiring in progress",
            },
        )
        log.info(f"Operator resume: {ready}/{total} pods, still Wiring")
    elif phase == "Ready":
        loop = asyncio.get_running_loop()
        total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
        _update_status(
            name,
            namespace,
            {
                "phase": "Ready",
                "readyPods": ready,
                "podCount": total,
                "message": f"{ready}/{total} pods running",
            },
        )
        log.info(f"Operator resume: {ready}/{total} pods running, phase=Ready")
    elif phase in ("Creating", "Pending"):
        # Operator restarted during pod creation or template building.
        # Check how many session pods are already running — if all are
        # up, continue from where we left off (FRR signaling → wiring).
        loop = asyncio.get_running_loop()
        pod_count_from_status = status.get("podCount", 0)
        if pod_count_from_status > 0:
            all_ready, total, ready = await loop.run_in_executor(
                None, check_all_pods_running, namespace, pod_count_from_status
            )
            pod_count = pod_count_from_status
        else:
            total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
            pod_count = total
            all_ready = ready >= pod_count
        if all_ready and pod_count > 0:
            log.info(
                f"Operator resume: all {ready}/{pod_count} pods running in {phase} phase, "
                f"continuing to FRR signaling and wiring"
            )
            _update_status(
                name,
                namespace,
                {
                    "phase": "Creating",
                    "readyPods": ready,
                    "podCount": pod_count,
                    "message": f"Resuming: signaling FRR config in {pod_count} pods",
                },
            )
            await loop.run_in_executor(
                None, signal_frr_config_ready, namespace, lambda msg: log.info(f"Resume: {msg}")
            )
            await loop.run_in_executor(None, write_pod_ips_configmap, namespace)
            owner_ref = {
                "apiVersion": "nodalarc.io/v1alpha1",
                "kind": "ConstellationSpec",
                "name": name,
                "uid": meta["uid"],
                "blockOwnerDeletion": True,
            }
            await loop.run_in_executor(
                None, write_wiring_manifest, dict(spec), namespace, owner_ref
            )
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
            log.info(f"Operator resume: advanced Creating → Wiring ({pod_count} pods)")
        else:
            log.info(
                f"Operator resume: {ready}/{pod_count} pods in {phase} phase, "
                f"waiting for all pods to reach Running"
            )
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
    elif phase == "Error":
        log.info(f"Operator resume: session in Error state: {status.get('message', '')}")
    elif not phase:
        log.info("Operator resume: no phase set, session may need re-deployment")


@kopf.timer("constellationspecs", group="nodalarc.io", interval=10.0, idle=10)
async def wiring_check(name, namespace, status, **_):
    """Periodically check if wiring completed and advance Wiring → Ready."""
    phase = status.get("phase", "")
    if phase != "Wiring":
        return

    loop = asyncio.get_running_loop()
    total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
    if total == 0:
        return
    try:
        complete, wired_count, _ = await loop.run_in_executor(
            None, check_wiring_complete, namespace, total
        )
        if complete:
            _update_status(
                name,
                namespace,
                {
                    "phase": "Ready",
                    "readyPods": ready,
                    "podCount": total,
                    "wiredPods": wired_count,
                    "message": f"Session ready: {total} pods, {wired_count} wired.",
                },
            )
            log.info(f"Timer: advanced Wiring → Ready ({wired_count} wired)")
    except kubernetes.client.rest.ApiException as e:
        log.warning("Wiring status check error in timer: %s", e)
