# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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

from nodalarc_operator.session_deployer import (
    check_all_pods_running,
    check_old_pods_terminated,
    check_pods_ready,
    check_wiring_complete,
    compute_expected_pod_count,
    compute_platform_hash,
    ensure_session_configmaps,
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
def _compute_expected_node_ids_cached(spec_hash: str, session_yaml: str) -> frozenset[str]:
    """Compute expected pod names from session YAML. Memoized by spec hash.

    Cached to avoid re-expanding the constellation on every 10-second
    reconciler tick during scale-down (waiting for K8s to terminate pods).
    """
    try:
        import yaml as _yaml
        from nodalarc.constellation_loader import (
            expand_constellation,
            load_constellation,
            load_ground_stations,
        )
        from nodalarc.models.addressing import AddressingScheme
        from nodalarc.models.session import SessionConfig

        raw = _yaml.safe_load(session_yaml)
        session = SessionConfig.model_validate(raw)
        constellation = load_constellation(session.constellation)
        satellites = expand_constellation(constellation)
        gs_file = load_ground_stations(session.ground_stations)
        addressing = AddressingScheme(session.addressing)
        expected = set()
        for sat in satellites:
            expected.add(addressing.sat_id(sat.plane, sat.slot).lower())
        for station in gs_file.stations:
            expected.add(addressing.gs_id(station.name).lower())
        return frozenset(expected)
    except Exception as exc:
        log.error("Cannot compute expected node_ids: %s", exc, exc_info=True)
        return frozenset()


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


def _extract_protocol(parsed: dict | None) -> str:
    """Extract routing protocol from parsed session YAML."""
    if parsed is None:
        return "isis"
    return parsed.get("routing", {}).get("protocol", "isis") or "isis"


def _manifest_session_id(spec: dict) -> str:
    """Return the sanitized session_id for a CR spec."""
    import yaml as _yaml
    from nodalarc.models.session import SessionConfig
    from nodalarc.nats_channels import sanitize_session_id

    session_yaml = spec.get("sessionYaml", "")
    session = SessionConfig.model_validate(_yaml.safe_load(session_yaml))
    return sanitize_session_id(session.session.name)


def _wiring_manifest_matches_spec(spec: dict, namespace: str, expected_count: int) -> bool:
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

    desired_session_id = _manifest_session_id(spec)
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
                },
            ),
        )
        return

    # --- Condition 1: Old pods terminated ---
    # Only relevant on fresh creation (phase Pending with no pods yet).
    # If phase is already Creating/Wiring/Ready, pods belong to this session.
    if phase in ("", "Pending"):
        total, _ = await loop.run_in_executor(None, check_pods_ready, namespace)
        if total > 0 and total != expected_count:
            # Pods exist but count doesn't match expected — stale session remnants
            cleared = await loop.run_in_executor(None, check_old_pods_terminated, namespace)
            if not cleared:
                log.debug("Reconcile: waiting for old session pods to terminate")
                _update_status(
                    name,
                    namespace,
                    _with_observed_generation(
                        meta,
                        {
                            "phase": "Pending",
                            "message": "Waiting for old session pods to terminate",
                        },
                    ),
                )
                return

    # --- Condition 2: Session deployed (correct number of pods) ---
    total, ready = await loop.run_in_executor(None, check_pods_ready, namespace)

    if total > expected_count:
        # Scale-down: compute expected_ids once, delete pods not in the set.
        expected_ids = await loop.run_in_executor(None, _compute_expected_node_ids, spec_dict)
        if expected_ids:
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Creating",
                        "message": f"Scaling down: {total} pods exist, {expected_count} expected",
                        "podCount": expected_count,
                    },
                ),
            )
            deleted = await loop.run_in_executor(
                None, _delete_obsolete_pods, expected_ids, namespace
            )
            log.info(
                "Reconcile: deleted %d obsolete pods (%d → %d)", deleted, total, expected_count
            )
        return  # Timer re-enters to verify

    if total < expected_count:
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
                },
            ),
        )

        def _progress(msg):
            _update_status(
                name,
                namespace,
                _with_observed_generation(meta, {"phase": "Creating", "message": msg}),
            )

        try:
            context = await loop.run_in_executor(
                None, ensure_session_configmaps, spec_dict, name, namespace, owner_ref, _progress
            )
            await loop.run_in_executor(
                None, ensure_session_pods, context, namespace, owner_ref, _progress
            )
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
                },
            ),
        )
        return  # Timer will re-enter to check Running status

    all_ready, total, ready = await loop.run_in_executor(
        None, check_all_pods_running, namespace, expected_count
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
        None, _wiring_manifest_matches_spec, spec_dict, namespace, expected_count
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
                },
            ),
        )
        await loop.run_in_executor(None, write_pod_ips_configmap, namespace)
        await loop.run_in_executor(None, write_wiring_manifest, spec_dict, namespace, owner_ref)

        protocol = _extract_protocol(parsed_yaml)
        await loop.run_in_executor(None, set_nodalpath_mode, namespace, protocol)
        platform_hash = compute_platform_hash(spec_dict)
        await loop.run_in_executor(None, restart_platform_pods, namespace, platform_hash)

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
                    "message": f"All {expected_count} pods running. Node Agent wiring data plane.",
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
                "message": f"Session ready: {expected_count} pods, {wired_count} wired.",
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
    if phase == "Error":
        log.debug("on_update: session in Error state, skipping")
        return

    loop = asyncio.get_running_loop()
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
            {"platformHash": new_hash},
        )

    await _reconcile_session(spec, name, namespace, meta, status)


@kopf.on.delete("constellationspecs", group="nodalarc.io")
async def on_delete(name, namespace, **_):
    """Handle ConstellationSpec CR deletion — tear down session."""
    log.info("ConstellationSpec '%s' deleted, tearing down session", name)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, teardown_session, namespace, name)
    await loop.run_in_executor(None, set_nodalpath_mode, namespace, "console")
    log.info("Session teardown complete")


@kopf.on.resume("constellationspecs", group="nodalarc.io")
async def on_resume(spec, name, namespace, meta, status, **_):
    """Handle Operator restart — reconcile existing session state."""
    phase = status.get("phase", "")
    log.info("Resuming ConstellationSpec '%s', current phase: %s", name, phase)

    if phase == "Error":
        log.info("Operator resume: session in Error state: %s", status.get("message", ""))
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
