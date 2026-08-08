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
import os

import kopf
import kubernetes
from nodalarc.content_identity import SHA256_DIGEST_PATTERN
from nodalarc.nats_channels import sanitize_session_id
from nodalarc.runtime_config import RuntimeDeploymentContext
from nodalarc.session_identity import derive_session_run_id
from nodalarc.workloads.refs import (
    BUILTIN_FRR_SELECTION_IDENTITY,
    ImplementationBindingRef,
    SelectionPairError,
)

from nodalarc_operator.runtime_session import OperatorSessionConfig, resolve_operator_session
from nodalarc_operator.session_deployer import (
    RetryableSessionDependency,
    build_runtime_deployment_context,
    build_runtime_session_config_data,
    check_all_pods_provisioned,
    check_all_pods_running,
    check_old_pods_terminated,
    check_platform_runtime_ready,
    check_pods_ready,
    check_wiring_complete,
    compute_expected_pod_count,
    compute_platform_hash,
    compute_runtime_hash,
    count_stale_session_pods,
    current_session_pod_node_ids,
    delete_owned_session_pods,
    ensure_session_configmaps,
    ensure_session_pod_identity,
    ensure_session_pods,
    prepare_session_workloads,
    restart_platform_pods,
    set_nodalpath_mode,
    teardown_session,
    write_pod_ips_configmap,
    write_wiring_manifest,
)
from nodalarc_operator.workloads.selection import WorkloadSelectionError

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
        # loop-blocking-ok: one-shot per process — the client is cached.
        kubernetes.config.load_incluster_config()
        _custom_api = kubernetes.client.CustomObjectsApi()
    return _custom_api


def _update_status(name: str, namespace: str, status: dict) -> None:
    """Update the ConstellationSpec CR status subresource."""
    # loop-blocking-ok: small status PATCH at reconcile-event cadence; the
    # operator loop serves no feed consumers, so API-server tail latency
    # degrades only reconcile responsiveness, never a user-facing stream.
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


def _compute_expected_node_ids(active_session: OperatorSessionConfig) -> frozenset[str]:
    """Return expected pod names from the reconciliation's verified resolution."""
    return frozenset(node_id.lower() for node_id in active_session.resolution.resolved.node_ids())


def _resolve_active_session(
    spec: dict,
    namespace: str,
    session_run_id: str,
) -> OperatorSessionConfig:
    from nodalarc_operator.session_deployer import _get_v1

    return resolve_operator_session(
        spec,
        core_v1=_get_v1(),
        namespace=namespace,
        source_origin="operator.reconcile",
        run_id=session_run_id,
    )


_EXPECTED_SELECTION_SCHEMA = {
    "implementationBindingRef": ImplementationBindingRef.json_schema_pattern(),
    "implementationPackageDigest": SHA256_DIGEST_PATTERN,
}
_selection_schema_verified = False


def _require_selection_schema_served() -> None:
    """Refuse to reconcile against a served CRD schema without the pair.

    An older served schema prunes both selection fields; a pruned pair is
    indistinguishable from a built-in-default CR and would silently become FRR. Each
    field must be present with the exact string type and pattern of the
    typed authority — a present-but-different field validates different
    input than the loader accepts. The check runs before the pair is
    parsed, is retryable (installing the corrected CRD recovers without
    touching the CR), and caches only success, once per operator process.
    """
    global _selection_schema_verified
    if _selection_schema_verified:
        return
    crd = kubernetes.client.ApiextensionsV1Api().read_custom_resource_definition(
        "constellationspecs.nodalarc.io"
    )
    for version in crd.spec.versions:
        if not version.served:
            continue
        root = getattr(version.schema, "open_apiv3_schema", None)
        root_properties = getattr(root, "properties", None)
        spec_schema = None if root_properties is None else root_properties.get("spec")
        properties = getattr(spec_schema, "properties", None)
        if properties is None:
            raise RuntimeError(
                f"served CRD schema (version {version.name}) exposes no spec "
                "properties; workload selection fields cannot be verified"
            )
        problems = []
        for field_name, expected_pattern in _EXPECTED_SELECTION_SCHEMA.items():
            prop = properties.get(field_name)
            if prop is None:
                problems.append(f"{field_name} is absent")
                continue
            prop_type = getattr(prop, "type", None)
            prop_pattern = getattr(prop, "pattern", None)
            if prop_type != "string" or prop_pattern != expected_pattern:
                problems.append(
                    f"{field_name} has type={prop_type!r} pattern={prop_pattern!r}; "
                    f"expected type='string' pattern={expected_pattern!r}"
                )
        if problems:
            raise RuntimeError(
                f"served CRD schema (version {version.name}) does not carry the "
                f"workload selection pair: {'; '.join(problems)}. Upgrade the "
                "installed CRD; reconciliation resumes once it is served."
            )
    _selection_schema_verified = True


def _selection_identity_for(active_session: OperatorSessionConfig) -> str:
    selection_ref = active_session.workload_selection
    return selection_ref.identity() if selection_ref else BUILTIN_FRR_SELECTION_IDENTITY


def _cr_generation_is_current(name: str, namespace: str, meta: dict) -> bool:
    """Re-read the CR and confirm this pass's generation is still live.

    Guards workload zeroing: a pass that observed generation N must not
    delete pods a newer generation is creating under the same CR UID.
    """
    cr = _get_custom_api().get_namespaced_custom_object(
        group="nodalarc.io",
        version="v1alpha1",
        namespace=namespace,
        plural="constellationspecs",
        name=name,
    )
    live = int((cr.get("metadata") or {}).get("generation", 0) or 0)
    return live == int(meta.get("generation", 0) or 0)


async def _converge_selection_failure(
    loop,
    name: str,
    namespace: str,
    meta: dict,
    owner_ref: dict,
    status_fields: dict,
    error_msg: str,
) -> None:
    """Drive this CR's workloads to zero, then publish Error.

    Deletion requests are UID-preconditioned and issued only while this
    pass's generation is still live. The phase stays Creating while owned
    pods remain — including terminating ones — and becomes Error only once
    zero is observed, so a terminal phase never coexists with running
    workloads.
    """
    if not await loop.run_in_executor(None, _cr_generation_is_current, name, namespace, meta):
        log.info("Reconcile: skipping workload zeroing; CR generation advanced")
        return
    remaining, requested = await loop.run_in_executor(
        None, delete_owned_session_pods, namespace, owner_ref
    )
    if remaining:
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "message": (
                        f"Workload selection failed: {error_msg[:350]}; "
                        f"removing {remaining} session pod(s)"
                    ),
                    **status_fields,
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
                "phase": "Error",
                "message": f"Workload selection failed: {error_msg[:400]}",
                **status_fields,
            },
        ),
    )


def _runtime_proof_status_fields(
    active_session: OperatorSessionConfig,
    deployment_context: RuntimeDeploymentContext,
) -> dict[str, str]:
    proof = active_session.proof
    return {
        "documentDigest": proof.document_digest,
        "closureDigest": proof.closure_digest,
        "resolvedSemanticDigest": proof.resolved_semantic_digest,
        "runtimeRelease": deployment_context.release,
        "runtimeBuild": deployment_context.build,
    }


def _runtime_deployment_context(
    active_session: OperatorSessionConfig,
    meta: dict,
    session_run_id: str,
) -> RuntimeDeploymentContext:
    return build_runtime_deployment_context(
        active_session.proof,
        cr_uid=str(meta.get("uid") or ""),
        cr_generation=int(meta.get("generation", 0) or 0),
        session_run_id=session_run_id,
        release=os.environ.get("NODALARC_RELEASE", ""),
        build=os.environ.get("NODAL_BUILD", ""),
    )


def _runtime_session_config_matches(
    namespace: str,
    active_session: OperatorSessionConfig,
    deployment_context: RuntimeDeploymentContext,
) -> bool:
    """Return true only for the exact mounted inputs of this CR generation."""
    from nodalarc_operator.session_deployer import _get_v1

    expected = build_runtime_session_config_data(
        active_session.resolution.resolved,
        active_session.root_yaml,
        active_session.catalog_upload,
        deployment_context,
    )
    try:
        config_map = _get_v1().read_namespaced_config_map("nodalarc-session", namespace)
    except kubernetes.client.rest.ApiException as exc:
        if exc.status == 404:
            return False
        raise
    if dict(getattr(config_map, "data", None) or {}) != expected:
        return False
    metadata = getattr(config_map, "metadata", None)
    owner_uids = {
        str(getattr(reference, "uid", "") or "")
        for reference in (getattr(metadata, "owner_references", None) or [])
    }
    return deployment_context.cr_uid in owner_uids


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


def _session_name_from_spec(spec: dict) -> str:
    from nodalarc.configuration_yaml import load_configuration_yaml
    from nodalarc.models.segment_session import SegmentSessionConfig

    session_yaml = spec.get("sessionYaml", "")
    if not session_yaml:
        raise ValueError("spec.sessionYaml is required")
    document = SegmentSessionConfig.model_validate(load_configuration_yaml(session_yaml))
    return document.session.name


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


def _wiring_manifest_matches_spec(
    namespace: str,
    expected_count: int,
    session_run_id: str,
    desired_platform_hash: str,
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
    if data.get("session_id") != desired_session_id:
        log.info(
            "Reconcile: wiring manifest session mismatch (%r != %r), rewriting",
            data.get("session_id"),
            desired_session_id,
        )
        return False
    if data.get("platform_hash") != desired_platform_hash:
        log.info(
            "Reconcile: wiring manifest platform hash mismatch for session %s "
            "(stored=%.12s desired=%.12s), rewriting",
            desired_session_id,
            data.get("platform_hash") or "",
            desired_platform_hash,
        )
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
        log.info(
            "Reconcile: wiring manifest payload missing for session %s, rewriting",
            desired_session_id,
        )
        return False
    try:
        manifest = json.loads(gzip.decompress(base64.b64decode(encoded)))
    except Exception as exc:
        log.warning("Reconcile: wiring manifest payload invalid (%s), rewriting", exc)
        return False

    manifest_nodes = manifest.get("nodes")
    if not isinstance(manifest_nodes, dict) or len(manifest_nodes) != expected_count:
        log.info(
            "Reconcile: wiring manifest node payload mismatch for session %s "
            "(have %s nodes, expected %s), rewriting",
            desired_session_id,
            len(manifest_nodes)
            if isinstance(manifest_nodes, dict)
            else type(manifest_nodes).__name__,
            expected_count,
        )
        return False
    if manifest.get("session_id") != desired_session_id:
        log.info(
            "Reconcile: wiring manifest payload session mismatch (%r != %r), rewriting",
            manifest.get("session_id"),
            desired_session_id,
        )
        return False
    if manifest.get("wiring_generation") != data.get("wiring_generation"):
        log.info(
            "Reconcile: wiring manifest generation mismatch for session %s "
            "(payload=%.20s metadata=%.20s), rewriting",
            desired_session_id,
            manifest.get("wiring_generation") or "",
            data.get("wiring_generation") or "",
        )
        return False
    return True


async def _reconcile_session(
    spec,
    name,
    namespace,
    meta,
    status,
    active_session: OperatorSessionConfig | None = None,
):
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

    # The pair's absence is only trustworthy when the served schema carries
    # both fields with the exact types and patterns of the typed authority;
    # verify BEFORE the pair is parsed. A failure here is retryable:
    # installing the corrected CRD recovers without any mutation of the CR
    # or its workloads.
    try:
        await loop.run_in_executor(None, _require_selection_schema_served)
    except Exception as exc:
        error_msg = str(exc)
        log.warning("Reconcile: served CRD schema not verified: %s", error_msg)
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Pending",
                    "message": f"Waiting for served CRD schema: {error_msg[:400]}",
                },
            ),
        )
        return

    try:
        session_name, session_run_id = await loop.run_in_executor(
            None, _runtime_identity, spec_dict, meta
        )
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

    try:
        if active_session is None:
            active_session = await loop.run_in_executor(
                None,
                _resolve_active_session,
                spec_dict,
                namespace,
                session_run_id,
            )
        elif active_session.proof.run_id != session_run_id:
            raise ValueError("verified Operator session has the wrong runtime identity")
        platform_hash = await asyncio.to_thread(
            compute_platform_hash,
            spec_dict,
            active_session=active_session,
            namespace=namespace,
        )
        deployment_context = _runtime_deployment_context(
            active_session,
            meta,
            session_run_id,
        )
        runtime_hash = compute_runtime_hash(
            platform_hash,
            session_run_id,
            active_session.proof,
            deployment_context,
        )
        proof_fields = _runtime_proof_status_fields(active_session, deployment_context)
        status_fields = {**identity_fields, **proof_fields}
    except SelectionPairError as exc:
        # Deterministic selection failure: the CR's desired workloads cannot
        # exist. Leaving previous workloads running under a terminal phase
        # would misrepresent session state — converge this CR's workloads
        # to zero, then publish Error once zero is observed.
        error_msg = str(exc)
        log.error("Reconcile: invalid workload selection pair: %s", error_msg, exc_info=True)
        await _converge_selection_failure(
            loop, name, namespace, meta, owner_ref, identity_fields, error_msg
        )
        return
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
                    "message": f"Invalid session configuration: {error_msg[:500]}",
                    **identity_fields,
                },
            ),
        )
        return

    # The COMPLETE write-free preparation — render, load, digest-verify,
    # resolve, compile, compose — runs BEFORE any pod is deleted or reused
    # for this generation. A typed failure converges this CR's workloads to
    # zero, then publishes Error once zero is observed.
    try:
        prepared_workloads = await asyncio.to_thread(
            prepare_session_workloads,
            active_session,
            namespace=namespace,
            owner_ref=owner_ref,
        )
    except WorkloadSelectionError as exc:
        error_msg = str(exc)
        log.error("Reconcile: terminal workload selection failure: %s", error_msg, exc_info=True)
        await _converge_selection_failure(
            loop, name, namespace, meta, owner_ref, status_fields, error_msg
        )
        return

    # Compute desired state from spec — this is what makes it a REAL reconciler.
    # No K8s calls, no template rendering — just parse YAML and count nodes.
    # If the session config is invalid, compute_expected_pod_count raises.
    # Set CR phase to Error so VS-API can relay the message to the browser.
    try:
        expected_count = await asyncio.to_thread(
            compute_expected_pod_count,
            spec_dict,
            active_session=active_session,
            namespace=namespace,
        )
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
                    **status_fields,
                },
            ),
        )
        return

    expected_ids = _compute_expected_node_ids(active_session)
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
                    **status_fields,
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
                    **status_fields,
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
        _selection_identity_for(active_session),
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
                        **status_fields,
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
                    **status_fields,
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
                    **status_fields,
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
                        **status_fields,
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
                active_session,
                deployment_context,
                prepared_workloads,
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
                        **status_fields,
                    },
                ),
            )
            return
        except WorkloadSelectionError as exc:
            error_msg = str(exc)
            log.error(
                "Reconcile: terminal workload selection failure during deploy: %s",
                error_msg,
                exc_info=True,
            )
            await _converge_selection_failure(
                loop, name, namespace, meta, owner_ref, status_fields, error_msg
            )
            return
        except kubernetes.client.rest.ApiException as exc:
            # Transient Kubernetes failure — remain Creating; the timer
            # re-enters and the ensure pipeline is idempotent.
            log.warning("Reconcile: transient Kubernetes API failure during deploy: %s", exc)
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Creating",
                        "message": f"Transient Kubernetes API failure, retrying: {str(exc)[:300]}",
                        "podCount": expected_count,
                        **status_fields,
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
                        **status_fields,
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
                    "message": f"Pods created, waiting for pod networks ({expected_count} expected)",
                    **status_fields,
                },
            ),
        )
        return  # Timer will re-enter to check network provisioning

    all_provisioned, provisioned, ready = await loop.run_in_executor(
        None,
        check_all_pods_provisioned,
        namespace,
        expected_count,
        session_run_id,
        owner_ref,
        expected_ids,
    )
    if not all_provisioned:
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "readyPods": ready,
                    "podCount": expected_count,
                    "message": (
                        f"Pods: {provisioned} networked, "
                        f"{expected_count - provisioned} awaiting network"
                    ),
                    **status_fields,
                },
            ),
        )
        log.debug(
            "Reconcile: %d/%d pod networks provisioned, waiting for all",
            provisioned,
            expected_count,
        )
        return

    # All pod networks provisioned — proceed through remaining conditions.
    #
    # NOTE: Wiring publication deliberately gates on provisioned pod
    # networks (scheduled + pod IP assigned), NOT on Running and NOT on
    # the readiness probe. The sandbox network namespace exists from pod
    # provisioning onward, which is all the Node Agent needs; a workload
    # held behind a pre-start wiring gate can never reach Running before
    # wiring, so gating on Running would deadlock. The readiness probe
    # (vtysh + config version diff) remains K8s health monitoring only:
    # at 591 pods, FRR startup takes 30-60s under CPU contention, and
    # FRR forms adjacencies when the carrier arrives on wired interfaces
    # regardless of when it started. All-Running is enforced later,
    # after wiring completes, before the session is declared Ready.

    # --- Condition 4: Wiring manifest written + wiring complete ---
    runtime_config_current = await loop.run_in_executor(
        None,
        _runtime_session_config_matches,
        namespace,
        active_session,
        deployment_context,
    )
    manifest_current = await loop.run_in_executor(
        None,
        _wiring_manifest_matches_spec,
        namespace,
        expected_count,
        session_run_id,
        platform_hash,
    )
    if not manifest_current or not runtime_config_current:
        refresh_message = (
            "Writing pod IP addresses and wiring manifest"
            if not manifest_current
            else "Refreshing mounted runtime deployment identity"
        )
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "readyPods": ready,
                    "podCount": expected_count,
                    "message": refresh_message,
                    **status_fields,
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
                active_session,
                deployment_context,
            )
            if not manifest_current:
                await loop.run_in_executor(
                    None,
                    write_pod_ips_configmap,
                    namespace,
                    session_run_id,
                    owner_ref,
                    expected_ids,
                )
                await loop.run_in_executor(
                    None,
                    write_wiring_manifest,
                    spec_dict,
                    namespace,
                    owner_ref,
                    session_run_id,
                    active_session,
                    platform_hash,
                )

                await loop.run_in_executor(None, set_nodalpath_mode, namespace, "console")
            # OME/Scheduler restart deliberately does NOT happen here: the
            # platform services are restarted only after wiring completes
            # and every session workload container is Running, so they never
            # start consuming a session whose workloads have not begun.
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
                        **status_fields,
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
                        **status_fields,
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
                    "message": (
                        f"All {expected_count} pod networks provisioned. "
                        "Node Agent wiring data plane."
                        if not manifest_current
                        else "Runtime configuration refreshed; waiting for verified services."
                    ),
                    **status_fields,
                },
            ),
        )
        log.info("Reconcile: runtime inputs refreshed, advanced to Wiring")
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
                    **status_fields,
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
                    **status_fields,
                },
            ),
        )
        log.debug("Reconcile: wiring in progress (%d/%d)", wired_count, expected_count)
        return

    # Wiring is complete — every session container must be Running before
    # the session may be declared Ready. Under the earlier provisioned-gate
    # this is no longer implied, and Ready must never mask starting pods.
    all_running, _running_total, ready = await loop.run_in_executor(
        None,
        check_all_pods_running,
        namespace,
        expected_count,
        session_run_id,
        owner_ref,
        expected_ids,
    )
    if not all_running:
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
                    "message": f"Wired; pods running: {ready}/{expected_count}",
                    **status_fields,
                },
            ),
        )
        log.debug("Reconcile: wired, %d/%d pods running", ready, expected_count)
        return

    # Wired and all workloads Running — now (and only now) roll the
    # session-scoped platform services onto the new runtime inputs. The
    # config-hash annotation makes this a no-op on every later pass with
    # the same runtime hash.
    try:
        await loop.run_in_executor(None, restart_platform_pods, namespace, runtime_hash)
    except kubernetes.client.rest.ApiException as exc:
        log.warning("Reconcile: platform restart error: %s", exc)
        return

    try:
        platform_ready, platform_detail = await loop.run_in_executor(
            None,
            check_platform_runtime_ready,
            namespace,
            runtime_hash,
            active_session.proof,
            deployment_context,
        )
    except kubernetes.client.rest.ApiException as exc:
        log.warning("Reconcile: platform readiness check error: %s", exc)
        return
    if not platform_ready:
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
                    "platformHash": platform_hash,
                    "runtimeHash": runtime_hash,
                    "message": platform_detail,
                    **status_fields,
                },
            ),
        )
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
                "platformHash": platform_hash,
                "runtimeHash": runtime_hash,
                "message": f"Session ready: {expected_count} pods, {wired_count} wired.",
                **status_fields,
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

    await _reconcile_session(spec, name, namespace, meta, status)


@kopf.on.delete("constellationspecs", group="nodalarc.io")
async def on_delete(name, namespace, spec=None, meta=None, status=None, **_):
    """Handle ConstellationSpec CR deletion — tear down session."""
    log.info("ConstellationSpec '%s' deleted, tearing down session", name)
    loop = asyncio.get_running_loop()
    session_id = await loop.run_in_executor(None, _teardown_session_id, spec, meta, status)
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
            identity_fields = await asyncio.to_thread(_status_identity_fields, dict(spec), meta)
        except Exception:
            await _reconcile_session(spec, name, namespace, meta, status)
            return
        try:
            active_session = await asyncio.to_thread(
                _resolve_active_session,
                dict(spec),
                namespace,
                identity_fields["sessionRunId"],
            )
            platform_hash = await asyncio.to_thread(
                compute_platform_hash,
                dict(spec),
                active_session=active_session,
                namespace=namespace,
            )
            deployment_context = _runtime_deployment_context(
                active_session,
                meta,
                identity_fields["sessionRunId"],
            )
            runtime_hash = compute_runtime_hash(
                platform_hash,
                identity_fields["sessionRunId"],
                active_session.proof,
                deployment_context,
            )
            proof_fields = _runtime_proof_status_fields(active_session, deployment_context)
        except Exception as exc:
            log.error("Ready session verification failed: %s", exc, exc_info=True)
            _update_status(
                name,
                namespace,
                _with_observed_generation(
                    meta,
                    {
                        "phase": "Error",
                        "message": f"Runtime configuration verification failed: {str(exc)[:500]}",
                        **identity_fields,
                    },
                ),
            )
            return
        # Ready is a claim about the session, not only the platform: a
        # missing, replaced, or non-running pod, or wiring proof that is no
        # longer current, must take the session back through normal
        # reconciliation instead of remaining advertised as Ready.
        try:
            expected_count = await asyncio.to_thread(
                compute_expected_pod_count, dict(spec), active_session=active_session
            )
            expected_ids = _compute_expected_node_ids(active_session)
            owner_ref = _build_owner_ref(name, meta)
            all_running, _total, _running = await asyncio.to_thread(
                check_all_pods_running,
                namespace,
                expected_count,
                identity_fields["sessionRunId"],
                owner_ref,
                expected_ids,
            )
        except kubernetes.client.rest.ApiException as exc:
            log.warning("Ready session pod membership check failed: %s", exc)
            return
        wiring_ok = False
        try:
            wiring_ok, _wired, _progress = await asyncio.to_thread(
                check_wiring_complete, namespace, expected_count
            )
        except kubernetes.client.rest.ApiException as exc:
            log.warning("Ready session wiring proof check failed: %s", exc)
            return
        except ValueError as exc:
            log.warning("Ready session wiring proof invalid: %s", exc)
        if not all_running or not wiring_ok:
            log.warning(
                "Ready session degraded (all_running=%s, wiring_current=%s) — reconciling",
                all_running,
                wiring_ok,
            )
            await _reconcile_session(
                spec,
                name,
                namespace,
                meta,
                status,
                active_session,
            )
            return

        try:
            platform_ready, _ = await asyncio.to_thread(
                check_platform_runtime_ready,
                namespace,
                runtime_hash,
                active_session.proof,
                deployment_context,
            )
        except kubernetes.client.rest.ApiException as exc:
            log.warning("Ready session platform proof check failed: %s", exc)
            return
        if not platform_ready:
            await _reconcile_session(
                spec,
                name,
                namespace,
                meta,
                status,
                active_session,
            )
            return
        if (
            status.get("sessionName") != identity_fields["sessionName"]
            or status.get("sessionRunId") != identity_fields["sessionRunId"]
            or any(status.get(field) != value for field, value in proof_fields.items())
            or status.get("platformHash") != platform_hash
            or status.get("runtimeHash") != runtime_hash
        ):
            await _reconcile_session(
                spec,
                name,
                namespace,
                meta,
                status,
                active_session,
            )
        return

    if phase not in ("Pending", "Creating", "Wiring"):
        return

    await _reconcile_session(spec, name, namespace, meta, status)
