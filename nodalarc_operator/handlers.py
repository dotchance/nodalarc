"""Kopf handlers for ConstellationSpec CRD lifecycle."""

from __future__ import annotations

import asyncio
import logging

import kopf
import kubernetes

from nodalarc_operator.session_deployer import (
    check_pods_ready,
    deploy_session,
    teardown_session,
    write_pod_ips_configmap,
    write_wiring_manifest,
)

log = logging.getLogger(__name__)


def _update_status(name: str, namespace: str, status: dict) -> None:
    """Update the ConstellationSpec CR status subresource directly."""
    kubernetes.config.load_incluster_config()
    api = kubernetes.client.CustomObjectsApi()
    api.patch_namespaced_custom_object_status(
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
    for _ in range(60):
        total, _ = await loop.run_in_executor(None, check_pods_ready, namespace)
        if total == 0:
            break
        log.info(f"Waiting for {total} old session pods to terminate...")
        await asyncio.sleep(2)

    # Deploy session (blocking — run in executor to not block kopf)
    try:
        result = await loop.run_in_executor(
            None, deploy_session, dict(spec), name, namespace, owner_ref
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

    # Wait for pods to reach Running
    if result.get("phase") == "Creating":
        pod_count = result.get("podCount", 0)
        for i in range(300):  # 5 minutes max
            total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
            if i % 10 == 0:
                _update_status(
                    name,
                    namespace,
                    {
                        "phase": "Creating",
                        "readyPods": ready,
                        "podCount": pod_count,
                        "message": f"{ready}/{pod_count} pods running",
                    },
                )
            if ready >= pod_count:
                break
            await asyncio.sleep(1)

        # Write pod-IPs ConfigMap (needs running pods)
        await loop.run_in_executor(None, write_pod_ips_configmap, namespace)

        # Write topology wiring manifest for Node Agent (7b)
        await loop.run_in_executor(None, write_wiring_manifest, dict(spec), namespace, owner_ref)

        # Set Wiring phase — Node Agent will read manifest and wire data plane
        _update_status(
            name,
            namespace,
            {
                "phase": "Wiring",
                "readyPods": pod_count,
                "podCount": pod_count,
                "message": f"All {pod_count} pods running. Awaiting data plane wiring.",
            },
        )
        log.info(f"Session deployed: {pod_count} pods running, waiting for wiring")

        # Wait for Node Agent to write wiring-complete status
        kubernetes.config.load_incluster_config()
        v1 = kubernetes.client.CoreV1Api()
        for _i in range(120):  # 2 minutes max
            try:
                cm = await loop.run_in_executor(
                    None,
                    v1.read_namespaced_config_map,
                    "nodalarc-wiring-status",
                    namespace,
                )
                wired_count = len(cm.data) if cm.data else 0
                if wired_count >= pod_count:
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
                if e.status != 404:
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
    log.info("Session teardown complete")


@kopf.on.resume("constellationspecs", group="nodalarc.io")
async def on_resume(spec, name, namespace, meta, status, **_):
    """Handle Operator restart — reconcile existing session state."""
    phase = status.get("phase", "")
    log.info(f"Resuming ConstellationSpec '{name}', current phase: {phase}")

    if phase in ("Ready", "Wiring"):
        loop = asyncio.get_running_loop()
        total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
        _update_status(
            name,
            namespace,
            {
                "phase": phase,
                "readyPods": ready,
                "podCount": total,
                "message": f"{ready}/{total} pods running after Operator restart",
            },
        )
        log.info(f"Operator resume: {ready}/{total} pods running, phase={phase}")
    elif phase == "Error":
        log.info(f"Operator resume: session in Error state: {status.get('message', '')}")
    elif not phase:
        log.info("Operator resume: no phase set, session may need re-deployment")
