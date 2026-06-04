# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Kopf handlers for ConstellationSpec CRD lifecycle.

True desired-state reconciler: _reconcile_session() computes expected state
from the CRD spec (not cached status.podCount) and converges the cluster
toward it. Handles creation, updates, scale-up, scale-down, and crash
recovery through the same state machine.

All handlers (on_create, on_resume, on_update) are non-blocking — they
validate, set initial status, and call the reconciler once. The kopf timer
re-enters every 10 seconds to drive progress through the 5-condition state
machine (old pods cleared → pods created → routing ready → wired → Ready).
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
from functools import lru_cache

import kopf
import kubernetes
from nodalarc.models.resolved_session import SourceContext
from nodalarc.nats_channels import sanitize_session_id
from nodalarc.resolve_session import resolve_session_with_assets
from nodalarc.session_identity import derive_session_run_id

from nodalarc_operator.session_deployer import (
    RetryableSessionDependency,
    check_all_pods_running,
    check_old_pods_terminated,
    check_pods_ready,
    check_wiring_complete,
    compute_expected_pod_count,
    compute_platform_hash,
    compute_runtime_hash,
    count_stale_session_pods,
    current_session_pod_node_ids,
    ensure_session_configmaps,
    ensure_session_pod_identity,
    ensure_session_pods,
    restart_platform_pods,
    set_nodalpath_mode,
    teardown_session,
    write_pod_ips_configmap,
    write_wiring_manifest,
)

log = logging.getLogger(__name__)


@kopf.on.startup()
async def on_startup(**_):
    """Connect the logging library to NATS for OpsEvent publishing and debug control."""
    import nats
    from nodal.logging import connect as _connect_logging
    from nodalarc.nats_channels import NATS_CONNECT_OPTIONS, nats_url

    try:
        nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
        await _connect_logging(nc)
        log.info("Operator NATS logging connected")
    except Exception as exc:
        log.error("Operator NATS logging connection failed: %s", exc)


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


def _with_observed_generation(meta: dict, status: dict) -> dict:
    """Attach the CR generation this status was computed from."""
    merged = dict(status)
    merged["observedGeneration"] = meta.get("generation", 0)
    return merged


def _status_observed_current_generation(meta: dict, status: dict) -> bool:
    """Return true when status was computed from this CR generation."""
    try:
        generation = int(meta.get("generation", 0))
        observed_generation = int(status.get("observedGeneration", 0))
    except TypeError, ValueError:
        return False
    return generation > 0 and observed_generation == generation


def _build_owner_ref(name: str, meta: dict) -> dict:
    """Build ownerReference dict for garbage collection."""
    return {
        "apiVersion": "nodalarc.io/v1alpha1",
        "kind": "ConstellationSpec",
        "name": name,
        "uid": meta["uid"],
        "blockOwnerDeletion": True,
    }


@lru_cache(maxsize=4)
def _compute_expected_node_ids_cached(_spec_hash: str, session_yaml: str) -> frozenset[str]:
    """Compute expected pod names from resolver output. Memoized by spec hash.

    Cached to avoid re-resolving the same session on every 10-second reconciler
    tick during scale-down. Resolution errors are fatal to reconciliation; an
    empty expected set would silently delete or ignore runtime state.
    """
    import yaml as _yaml

    resolution = resolve_session_with_assets(
        _yaml.safe_load(session_yaml),
        source_context=SourceContext(origin="operator.expected_node_ids"),
    )
    return frozenset(node_id.lower() for node_id in resolution.resolved.node_ids())


def _compute_expected_node_ids(spec: dict) -> frozenset[str]:
    """Compute expected node_ids with memoization keyed on spec hash."""
    session_yaml = spec.get("sessionYaml", "")
    if not session_yaml:
        return frozenset()
    return _compute_expected_node_ids_cached(compute_platform_hash(spec), session_yaml)


def _delete_obsolete_pods(expected_ids: set[str], namespace: str) -> int:
    """Delete session pods whose names are not in expected_ids.

    Takes pre-computed expected_ids (from _compute_expected_node_ids)
    to avoid re-expanding the constellation on every reconciler tick.
    """
    from nodalarc_operator.session_deployer import _get_v1

    v1 = _get_v1()
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    deleted = 0
    for pod in pods.items:
        pod_name = pod.metadata.name
        if pod_name not in expected_ids:
            try:
                v1.delete_namespaced_pod(pod_name, namespace)
                deleted += 1
                log.info("Deleted obsolete pod %s", pod_name)
            except Exception as exc:
                log.error("Failed to delete obsolete pod %s: %s", pod_name, exc)
    return deleted


def _parse_session_yaml(spec: dict) -> dict | None:
    """Parse the sessionYaml from the CRD spec once. Returns parsed dict or None."""
    import yaml

    session_yaml = spec.get("sessionYaml", "")
    if not session_yaml:
        return None
    try:
        return yaml.safe_load(session_yaml)
    except Exception as exc:
        log.error("Failed to parse sessionYaml: %s", exc)
        return None


def _session_name_from_spec(spec: dict) -> str:
    import yaml as _yaml

    session_yaml = spec.get("sessionYaml", "")
    if not session_yaml:
        raise ValueError("spec.sessionYaml is required")
    raw = _yaml.safe_load(session_yaml) or {}
    session_meta = raw.get("session") or {}
    session_name = str(session_meta.get("name") or "")
    if not session_name:
        raise ValueError("session.name is required")
    if session_meta.get("run_id"):
        raise ValueError("session.run_id is operator-managed and must not be set in session YAML")
    return session_name


def _runtime_identity(spec: dict, meta: dict) -> tuple[str, str]:
    """Return (display session name, runtime session_run_id)."""
    session_name = _session_name_from_spec(spec)
    generation = int(meta.get("generation", 0) or 0)
    owner_uid = str(meta.get("uid") or "")
    run_id = derive_session_run_id(
        session_name=session_name,
        owner_uid=owner_uid,
        generation=generation,
    )
    return session_name, run_id


def _status_identity_fields(spec: dict, meta: dict) -> dict:
    session_name, session_run_id = _runtime_identity(spec, meta)
    return {
        "sessionName": session_name,
        "sessionRunId": session_run_id,
    }


def _teardown_session_id(spec: dict | None, meta: dict | None, status: dict | None) -> str | None:
    """Return the best available runtime identity for delete cleanup.

    Delete retries must not depend solely on nodalarc-session still existing.
    The CR status is the first choice because it records the runtime identity
    actually deployed. Deriving from spec/meta is a second choice for partially
    reconciled CRs. If both are unavailable, teardown_session can still derive
    from the ConfigMap while it exists.
    """
    status_run_id = str((status or {}).get("sessionRunId") or "")
    if status_run_id:
        return status_run_id
    try:
        return _runtime_identity(dict(spec or {}), dict(meta or {}))[1]
    except Exception:
        return None


def _extract_protocol(parsed: dict | None) -> str:
    """Extract routing protocol from parsed session YAML."""
    if parsed is None:
        return "isis"
    return parsed.get("routing", {}).get("protocol", "isis") or "isis"


def _wiring_manifest_matches_spec(
    spec: dict,
    namespace: str,
    expected_count: int,
    session_run_id: str,
) -> bool:
    """Return True only when the live wiring manifest matches desired session identity."""
    from nodalarc_operator.session_deployer import _get_v1

    v1 = _get_v1()
    try:
        cm = v1.read_namespaced_config_map("nodalarc-topology-wiring", namespace)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return False
        raise
    data = cm.data or {}

    desired_session_id = sanitize_session_id(session_run_id)
    desired_hash = compute_platform_hash(spec)
    if data.get("session_id") != desired_session_id:
        log.info(
            "Reconcile: wiring manifest session mismatch (%r != %r), rewriting",
            data.get("session_id"),
            desired_session_id,
        )
        return False
    if data.get("platform_hash") != desired_hash:
        log.info("Reconcile: wiring manifest platform hash mismatch, rewriting")
        return False
    if data.get("node_count") != str(expected_count):
        log.info(
            "Reconcile: wiring manifest node count mismatch (%r != %s), rewriting",
            data.get("node_count"),
            expected_count,
        )
        return False

    encoded = data.get("manifest.json.gz.b64")
    if not encoded:
        log.info("Reconcile: wiring manifest payload missing, rewriting")
        return False
    try:
        manifest = json.loads(gzip.decompress(base64.b64decode(encoded)))
    except Exception as exc:
        log.warning("Reconcile: wiring manifest payload invalid (%s), rewriting", exc)
        return False

    manifest_nodes = manifest.get("nodes")
    if not isinstance(manifest_nodes, dict) or len(manifest_nodes) != expected_count:
        log.info("Reconcile: wiring manifest node payload mismatch, rewriting")
        return False
    if manifest.get("session_id") != desired_session_id:
        log.info("Reconcile: wiring manifest payload session mismatch, rewriting")
        return False
    if manifest.get("wiring_generation") != data.get("wiring_generation"):
        log.info("Reconcile: wiring manifest generation metadata mismatch, rewriting")
        return False
    return True


async def _reconcile_session(spec, name, namespace, meta, status):
    """Converge cluster state toward desired session state.

    True desired-state reconciler: computes expected pod count from the CRD
    spec (not from cached status.podCount). Can create missing pods when
    the cluster has diverged from the spec.

    Called by on_create (after initial deploy), on_resume, on_update, and
    the wiring_check timer. Idempotent — safe to call at any point in
    the lifecycle.

    Checks 5 conditions in order. For each condition that isn't met,
    performs the convergence action and returns (one step per invocation).
    The kopf timer re-enters periodically to drive progress.
    """
    loop = asyncio.get_running_loop()
    phase = status.get("phase", "")
    owner_ref = _build_owner_ref(name, meta)
    spec_dict = dict(spec)
    parsed_yaml = _parse_session_yaml(spec_dict)
    try:
        session_name, session_run_id = _runtime_identity(spec_dict, meta)
        identity_fields = {
            "sessionName": session_name,
            "sessionRunId": session_run_id,
        }
    except Exception as exc:
        error_msg = str(exc)
        log.error("Reconcile: invalid session identity: %s", error_msg, exc_info=True)
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Error",
                    "message": f"Invalid session identity: {error_msg}",
                },
            ),
        )
        return

    # Compute desired state from spec — this is what makes it a REAL reconciler.
    # No K8s calls, no template rendering — just parse YAML and count nodes.
    # If the session config is invalid, compute_expected_pod_count raises.
    # Set CR phase to Error so VS-API can relay the message to the browser.
    try:
        expected_count = await loop.run_in_executor(None, compute_expected_pod_count, spec_dict)
    except Exception as exc:
        error_msg = str(exc)
        log.error("Reconcile: invalid session config: %s", error_msg, exc_info=True)
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Error",
                    "message": f"Invalid session configuration: {error_msg}",
                    **identity_fields,
                },
            ),
        )
        return

    expected_ids = await loop.run_in_executor(None, _compute_expected_node_ids, spec_dict)
    if len(expected_ids) != expected_count:
        message = (
            "Expected node identity set does not match expected pod count "
            f"({len(expected_ids)} IDs for {expected_count} pods)"
        )
        log.error("Reconcile: %s", message)
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Error",
                    "message": message,
                    **identity_fields,
                },
            ),
        )
        return

    deleted_obsolete = await loop.run_in_executor(
        None, _delete_obsolete_pods, expected_ids, namespace
    )
    if deleted_obsolete:
        log.info(
            "Reconcile: deleted %d obsolete pods before readiness evaluation",
            deleted_obsolete,
        )
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "message": f"Pruning {deleted_obsolete} pod(s) from a previous session",
                    "podCount": expected_count,
                    **identity_fields,
                },
            ),
        )
        return

    await loop.run_in_executor(
        None,
        ensure_session_pod_identity,
        namespace,
        expected_ids,
        session_run_id,
        owner_ref,
    )

    # --- Condition 1: Old pods terminated ---
    # A same-count old CR must not be allowed to satisfy the new generation.
    stale_count = await loop.run_in_executor(
        None,
        count_stale_session_pods,
        namespace,
        expected_ids,
        session_run_id,
        owner_ref,
    )
    if stale_count:
        cleared = await loop.run_in_executor(
            None,
            check_old_pods_terminated,
            namespace,
            session_run_id,
            owner_ref,
            expected_ids,
        )
        if not cleared:
            log.debug("Reconcile: waiting for %d stale session pods to terminate", stale_count)
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Pending",
                        "message": f"Waiting for {stale_count} old session pods to terminate",
                        **identity_fields,
                    },
                ),
            )
            return

    # --- Condition 2: Session deployed (correct number of pods) ---
    current_ids = await loop.run_in_executor(
        None,
        current_session_pod_node_ids,
        namespace,
        session_run_id,
        owner_ref,
    )
    missing_ids = expected_ids - current_ids
    obsolete_ids = current_ids - expected_ids
    total, ready = await loop.run_in_executor(
        None,
        check_pods_ready,
        namespace,
        session_run_id,
        owner_ref,
        expected_ids,
    )

    if obsolete_ids:
        # Scale-down: compute expected_ids once, delete pods not in the set.
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "message": f"Scaling down: {len(current_ids)} pods exist, {expected_count} expected",
                    "podCount": expected_count,
                    **identity_fields,
                },
            ),
        )
        deleted = await loop.run_in_executor(None, _delete_obsolete_pods, expected_ids, namespace)
        log.info(
            "Reconcile: deleted %d obsolete pods (%d → %d)",
            deleted,
            len(current_ids),
            expected_count,
        )
        return  # Timer re-enters to verify

    if missing_ids:
        # Pods missing — run the full ensure pipeline to converge
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "message": f"Deploying: {total}/{expected_count} pods exist",
                    "podCount": expected_count,
                    **identity_fields,
                },
            ),
        )

        def _progress(msg):
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Creating",
                        "message": msg,
                        **identity_fields,
                    },
                ),
            )

        try:
            context = await loop.run_in_executor(
                None,
                ensure_session_configmaps,
                spec_dict,
                name,
                namespace,
                owner_ref,
                _progress,
                session_run_id,
            )
            await loop.run_in_executor(
                None, ensure_session_pods, context, namespace, owner_ref, _progress
            )
        except RetryableSessionDependency as exc:
            log.info("Reconcile: waiting on runtime dependency: %s", exc)
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Pending",
                        "message": str(exc),
                        **identity_fields,
                    },
                ),
            )
            return
        except Exception as exc:
            log.error("Reconcile: ensure pipeline failed: %s", exc, exc_info=True)
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Error",
                        "message": f"Reconcile deploy failed: {str(exc)[:500]}",
                        **identity_fields,
                    },
                ),
            )
            return

        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "podCount": expected_count,
                    "message": f"Pods created, waiting for Running ({expected_count} expected)",
                    **identity_fields,
                },
            ),
        )
        return  # Timer will re-enter to check Running status

    all_ready, total, ready = await loop.run_in_executor(
        None,
        check_all_pods_running,
        namespace,
        expected_count,
        session_run_id,
        owner_ref,
        expected_ids,
    )
    if not all_ready:
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "readyPods": ready,
                    "podCount": expected_count,
                    "message": f"Pods: {ready} running, {expected_count - ready} starting",
                    **identity_fields,
                },
            ),
        )
        log.debug("Reconcile: %d/%d pods running, waiting for all", ready, expected_count)
        return

    # All pods running — proceed through remaining conditions.
    #
    # NOTE: Condition 3 (readiness probe check) is deliberately SKIPPED
    # as a deployment gate. The readiness probe (vtysh + config version
    # diff) is for K8s health monitoring, NOT for gating the wiring phase.
    # At 591 pods, FRR startup takes 30-60s under CPU contention, causing
    # readiness probe timeouts. The wiring phase doesn't need FRR to be
    # responsive — it creates kernel interfaces. FRR loads its config
    # independently and forms adjacencies when the carrier arrives on
    # wired interfaces. Gating on Ready here blocked deployment for the
    # entire session. See plan: "wiring gates on Running, not Ready."

    # --- Condition 4: Wiring manifest written + wiring complete ---
    manifest_current = await loop.run_in_executor(
        None, _wiring_manifest_matches_spec, spec_dict, namespace, expected_count, session_run_id
    )
    if not manifest_current:
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "readyPods": ready,
                    "podCount": expected_count,
                    "message": "Writing pod IP addresses and wiring manifest",
                    **identity_fields,
                },
            ),
        )
        try:
            # Refresh runtime ConfigMaps before publishing a new manifest. When a
            # CR generation changes but the pod count stays the same, this is the
            # only path that can update /etc/nodalarc/session.yaml with the new
            # operator-managed session.run_id. The manifest and the platform pods
            # must agree on the same runtime identity.
            await loop.run_in_executor(
                None,
                ensure_session_configmaps,
                spec_dict,
                name,
                namespace,
                owner_ref,
                None,
                session_run_id,
            )
            await loop.run_in_executor(
                None,
                write_pod_ips_configmap,
                namespace,
                session_run_id,
                owner_ref,
                expected_ids,
            )
            await loop.run_in_executor(
                None, write_wiring_manifest, spec_dict, namespace, owner_ref, session_run_id
            )

            protocol = _extract_protocol(parsed_yaml)
            await loop.run_in_executor(None, set_nodalpath_mode, namespace, protocol)
            platform_hash = compute_platform_hash(spec_dict)
            runtime_hash = compute_runtime_hash(platform_hash, session_run_id)
            await loop.run_in_executor(None, restart_platform_pods, namespace, runtime_hash)
        except RetryableSessionDependency as exc:
            log.info("Reconcile: waiting on runtime dependency during refresh: %s", exc)
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Pending",
                        "readyPods": ready,
                        "podCount": expected_count,
                        "message": str(exc),
                        **identity_fields,
                    },
                ),
            )
            return
        except Exception as exc:
            log.error("Reconcile: runtime refresh failed: %s", exc, exc_info=True)
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Error",
                        "readyPods": ready,
                        "podCount": expected_count,
                        "message": f"Runtime refresh failed: {str(exc)[:500]}",
                        **identity_fields,
                    },
                ),
            )
            return

        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Wiring",
                    "readyPods": ready,
                    "podCount": expected_count,
                    "platformHash": platform_hash,
                    "runtimeHash": runtime_hash,
                    "message": f"All {expected_count} pods running. Node Agent wiring data plane.",
                    **identity_fields,
                },
            ),
        )
        log.info("Reconcile: wiring manifest written, advanced to Wiring")
        return

    # Manifest exists — check wiring completion
    try:
        complete, wired_count, progress_msg = await loop.run_in_executor(
            None, check_wiring_complete, namespace, expected_count
        )
    except kubernetes.client.rest.ApiException as e:
        log.warning("Reconcile: wiring status check error: %s", e)
        return
    except ValueError as e:
        log.error("Reconcile: wiring status invalid: %s", e)
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Error",
                    "readyPods": ready,
                    "podCount": expected_count,
                    "wiredPods": 0,
                    "message": f"Wiring status invalid: {e}",
                    **identity_fields,
                },
            ),
        )
        return

    if not complete:
        if wired_count == 0 and progress_msg is None:
            display_msg = "Waiting for Node Agent to begin wiring"
        else:
            display_msg = (
                progress_msg or f"Data plane wiring: {wired_count}/{expected_count} nodes wired"
            )
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Wiring",
                    "readyPods": ready,
                    "podCount": expected_count,
                    "wiredPods": wired_count,
                    "message": display_msg,
                    **identity_fields,
                },
            ),
        )
        log.debug("Reconcile: wiring in progress (%d/%d)", wired_count, expected_count)
        return

    # --- Condition 5: Ready ---
    if phase != "Ready":
        log.info(
            "Session ready [pods=%d, wired=%d]",
            expected_count,
            wired_count,
        )
    platform_hash = compute_platform_hash(spec_dict)
    runtime_hash = compute_runtime_hash(platform_hash, session_run_id)
    _update_status(
        name,
        namespace,
        _with_observed_generation(
            meta,
            {
                "phase": "Ready",
                "readyPods": ready,
                "podCount": expected_count,
                "wiredPods": wired_count,
                "platformHash": platform_hash,
                "runtimeHash": runtime_hash,
                "message": f"Session ready: {expected_count} pods, {wired_count} wired.",
                **identity_fields,
            },
        ),
    )


@kopf.on.create("constellationspecs", group="nodalarc.io")
async def on_create(spec, name, namespace, meta, **_):
    """Handle ConstellationSpec CR creation.

    Non-blocking: validates the CRD, sets initial status, and calls the
    reconciler once. The kopf timer re-enters every 10 seconds to drive
    progress through ConfigMap creation, pod creation, readiness, wiring,
    and Ready. No blocking waits — the Operator stays responsive.
    """
    log.info("ConstellationSpec '%s' created in %s", name, namespace)

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

    # The reconciler handles everything. First invocation kicks off
    # the state machine; the timer drives subsequent ticks.
    await _reconcile_session(spec, name, namespace, meta, {"phase": "Pending"})


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
    if phase == "Error" and _status_observed_current_generation(meta, status):
        log.debug("on_update: session in Error state, skipping")
        return

    new_hash = compute_platform_hash(dict(spec))
    old_hash = status.get("platformHash", "")

    if old_hash and new_hash != old_hash:
        log.info(
            "Platform-impacting spec change detected (hash %s → %s), reconciling session resources",
            old_hash[:8],
            new_hash[:8],
        )
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "message": "Session config changed — reconciling session resources",
                },
            ),
        )
    elif not old_hash:
        _update_status(
            name,
            namespace,
            {
                "platformHash": new_hash,
            },
        )

    await _reconcile_session(spec, name, namespace, meta, status)


@kopf.on.delete("constellationspecs", group="nodalarc.io")
async def on_delete(name, namespace, spec=None, meta=None, status=None, **_):
    """Handle ConstellationSpec CR deletion — tear down session."""
    log.info("ConstellationSpec '%s' deleted, tearing down session", name)
    loop = asyncio.get_running_loop()
    session_id = _teardown_session_id(spec, meta, status)
    await loop.run_in_executor(None, teardown_session, namespace, session_id)
    await loop.run_in_executor(None, set_nodalpath_mode, namespace, "console")
    log.info("Session teardown complete")


@kopf.on.resume("constellationspecs", group="nodalarc.io")
async def on_resume(spec, name, namespace, meta, status, **_):
    """Handle Operator restart — reconcile existing session state."""
    phase = status.get("phase", "")
    log.info("Resuming ConstellationSpec '%s', current phase: %s", name, phase)

    if phase == "Error" and _status_observed_current_generation(meta, status):
        log.info("Operator resume: session in Error state: %s", status.get("message", ""))
        return

    await _reconcile_session(spec, name, namespace, meta, status)


@kopf.timer("constellationspecs", group="nodalarc.io", interval=10.0, idle=10)
async def wiring_check(spec, name, namespace, meta, status, **_):
    """Periodically advance session state via the reconciler.

    Active during Pending, Creating and Wiring phases. Drives progress for:
    - Pending: old runtime objects still terminating
    - Creating: pods still starting after operator resume
    - Wiring: Node Agent wiring data plane → Ready
    - Ready: repair missing runtime identity fields after operator/CRD upgrades
    """
    phase = status.get("phase", "")
    if phase == "Ready":
        try:
            identity_fields = _status_identity_fields(dict(spec), meta)
            platform_hash = compute_platform_hash(dict(spec))
            runtime_hash = compute_runtime_hash(platform_hash, identity_fields["sessionRunId"])
        except Exception:
            await _reconcile_session(spec, name, namespace, meta, status)
            return
        if (
            status.get("sessionName") != identity_fields["sessionName"]
            or status.get("sessionRunId") != identity_fields["sessionRunId"]
            or status.get("platformHash") != platform_hash
            or status.get("runtimeHash") != runtime_hash
        ):
            await _reconcile_session(spec, name, namespace, meta, status)
        return

    if phase not in ("Pending", "Creating", "Wiring"):
        return

    await _reconcile_session(spec, name, namespace, meta, status)
