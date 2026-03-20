"""Kopf handlers for ConstellationSpec CRD lifecycle."""

from __future__ import annotations

import asyncio
import logging

import kopf

from nodalarc_operator.session_deployer import (
    check_pods_ready,
    deploy_session,
    teardown_session,
    write_pod_ips_configmap,
)

log = logging.getLogger(__name__)

_NAMESPACE = "nodalarc"


@kopf.on.create("constellationspecs", group="nodalarc.io")
async def on_create(spec, name, namespace, meta, patch, **_):
    """Handle ConstellationSpec CR creation — deploy a session."""
    log.info(f"ConstellationSpec '{name}' created in {namespace}")

    # Singleton constraint: only 'current-session' is allowed
    if name != "current-session":
        patch.status["phase"] = "Error"
        patch.status["message"] = f"Only 'current-session' is allowed as CR name, got '{name}'"
        raise kopf.PermanentError(f"Invalid CR name: {name}")

    patch.status["phase"] = "Pending"
    patch.status["observedGeneration"] = meta.get("generation", 0)

    # Build ownerReference for garbage collection
    owner_ref = {
        "apiVersion": "nodalarc.io/v1alpha1",
        "kind": "ConstellationSpec",
        "name": name,
        "uid": meta["uid"],
        "blockOwnerDeletion": True,
    }

    # Deploy session (blocking — run in executor to not block kopf)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, deploy_session, dict(spec), name, namespace, owner_ref
        )
    except Exception as exc:
        patch.status["phase"] = "Error"
        patch.status["message"] = str(exc)
        raise kopf.PermanentError(f"Deploy failed: {exc}") from exc

    patch.status.update(result)

    # Wait for pods to reach Running
    if result.get("phase") == "Creating":
        pod_count = result.get("podCount", 0)
        for _ in range(300):  # 5 minutes max
            total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
            patch.status["readyPods"] = ready
            if ready >= pod_count:
                break
            await asyncio.sleep(1)

        # Write pod-IPs ConfigMap (needs running pods)
        await loop.run_in_executor(None, write_pod_ips_configmap, namespace)

        # For 7a: skip wiring, go straight to Wiring phase
        # 7b will advance from Wiring to Ready after Node Agent completes
        patch.status["phase"] = "Wiring"
        patch.status["message"] = f"All {pod_count} pods running. Awaiting data plane wiring."
        log.info(f"Session deployed: {pod_count} pods running")


@kopf.on.delete("constellationspecs", group="nodalarc.io")
async def on_delete(name, namespace, **_):
    """Handle ConstellationSpec CR deletion — tear down session."""
    log.info(f"ConstellationSpec '{name}' deleted, tearing down session")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, teardown_session, namespace)
    log.info("Session teardown complete")


@kopf.on.resume("constellationspecs", group="nodalarc.io")
async def on_resume(spec, name, namespace, meta, status, patch, **_):
    """Handle Operator restart — reconcile existing session state."""
    log.info(
        f"Resuming ConstellationSpec '{name}', current phase: {status.get('phase', 'unknown')}"
    )

    phase = status.get("phase", "")
    if phase in ("Ready", "Wiring"):
        # Session is deployed, verify pods still running
        loop = asyncio.get_running_loop()
        total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)
        patch.status["readyPods"] = ready
        patch.status["podCount"] = total
        if ready < total:
            patch.status["message"] = f"{ready}/{total} pods running after Operator restart"
            log.warning(f"Operator resume: only {ready}/{total} pods running")
        else:
            log.info(f"Operator resume: all {total} pods running, phase={phase}")
    elif phase == "Error":
        log.info(f"Operator resume: session in Error state: {status.get('message', '')}")
    elif not phase:
        log.info("Operator resume: no phase set, session may need re-deployment")
