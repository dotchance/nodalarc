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
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import kopf
import kubernetes
from nodalarc.cr_runtime_config import (
    CR_API_VERSION,
    CR_GROUP,
    CR_KIND,
    CR_NAME,
    CR_PLURAL,
    CR_VERSION,
    ConstellationSpecSpec,
    ConstellationSpecStatus,
    load_cr_runtime_config,
)
from nodalarc.nats_channels import sanitize_session_id
from nodalarc.runtime_config import ResolvedRuntimeConfig, RuntimeDeploymentContext
from nodalarc.session_identity import derive_session_run_id
from nodalarc.substrate.manifest_contract import (
    WIRING_MANIFEST_CONFIGMAP,
    WiringManifestPayloadError,
    decode_wiring_manifest_payload,
)

from nodalarc_operator import session_deployer as _deployer
from nodalarc_operator.session_deployer import (
    RetryableSessionDependency,
    build_runtime_session_config_data,
    check_platform_runtime_ready,
    check_wiring_complete,
    compute_platform_hash,
    compute_runtime_hash,
    ensure_session_configmaps,
    ensure_session_pods,
    prepare_session_workloads,
    restart_platform_pods,
    teardown_session,
    write_wiring_manifest,
)
from nodalarc_operator.session_pods import (
    DeletionOutcome,
    OwnerIdentity,
    SessionPodIdentity,
    SessionPodStateError,
    delete_all_owned_pods,
    delete_ineligible_pods,
    observe_session_pods,
    owned_session_run_ids,
)
from nodalarc_operator.workloads.preparation import PreparedWorkloads, WorkloadPreparationError

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


def _update_status(name: str, namespace: str, status: ConstellationSpecStatus) -> None:
    """Patch the ConstellationSpec status subresource with the fields one status sets."""
    # loop-blocking-ok: small status PATCH at reconcile-event cadence; the
    # operator loop serves no feed consumers, so API-server tail latency
    # degrades only reconcile responsiveness, never a user-facing stream.
    _get_custom_api().patch_namespaced_custom_object_status(
        group=CR_GROUP,
        version=CR_VERSION,
        namespace=namespace,
        plural=CR_PLURAL,
        name=name,
        body={"status": status.to_patch()},
    )


def _with_observed_generation(meta: dict, status: Mapping[str, Any]) -> ConstellationSpecStatus:
    """The status patch with the CR generation it was computed from attached."""
    return ConstellationSpecStatus.from_cr(
        {**status, "observedGeneration": meta.get("generation", 0)}
    )


def _build_owner_ref(name: str, meta: dict) -> dict:
    """Build ownerReference dict for garbage collection."""
    return {
        "apiVersion": CR_API_VERSION,
        "kind": CR_KIND,
        "name": name,
        "uid": meta["uid"],
        "blockOwnerDeletion": True,
    }


def _with_core_v1(function, *args):
    """Run a session-pod operation with the Operator's CoreV1 client.

    Called inside the executor thread: building the client can load the
    in-cluster configuration, which must never run on the event loop.
    """
    return function(_deployer._get_v1(), *args)


def _session_pod_identity(
    owner_ref: dict,
    session_run_id: str,
    prepared_workloads: PreparedWorkloads,
    active_session: ResolvedRuntimeConfig,
) -> SessionPodIdentity:
    """The desired session-pod identity from the reconciliation's verified inputs."""
    return SessionPodIdentity.for_session(
        owner_ref=owner_ref,
        session_run_id=session_run_id,
        selection_identity=prepared_workloads.identity,
        node_ids=active_session.resolution.resolved.node_ids(),
    )


@dataclass(frozen=True, slots=True)
class _RuntimeVerification:
    """The runtime identity one reconciliation publishes and verifies against."""

    platform_hash: str
    deployment_context: RuntimeDeploymentContext
    runtime_hash: str
    proof_fields: dict


def _verify_runtime(
    spec_dict: dict,
    meta: dict,
    namespace: str,
    session_run_id: str,
    active_session: ResolvedRuntimeConfig,
) -> _RuntimeVerification:
    """Hashes and proof fields shared by reconciliation and the Ready check."""
    platform_hash = compute_platform_hash(
        spec_dict,
        active_session=active_session,
        namespace=namespace,
    )
    deployment_context = _runtime_deployment_context(active_session, meta, session_run_id)
    runtime_hash = compute_runtime_hash(
        platform_hash,
        session_run_id,
        active_session.proof,
        deployment_context,
    )
    return _RuntimeVerification(
        platform_hash=platform_hash,
        deployment_context=deployment_context,
        runtime_hash=runtime_hash,
        proof_fields=_runtime_proof_status(active_session, deployment_context).to_patch(),
    )


def _resolve_active_session(
    spec: dict,
    namespace: str,
    session_run_id: str,
) -> ResolvedRuntimeConfig:
    from nodalarc_operator.session_deployer import _get_v1

    return load_cr_runtime_config(
        spec,
        core_v1=_get_v1(),
        namespace=namespace,
        source_origin="operator.reconcile",
        run_id=session_run_id,
    )


def _cr_generation_is_current(name: str, namespace: str, meta: dict) -> bool:
    """Re-read the CR and confirm this pass's generation is still live.

    Guards workload zeroing: a pass that observed generation N must not
    delete pods a newer generation is creating under the same CR UID.
    """
    cr = _get_custom_api().get_namespaced_custom_object(
        group=CR_GROUP,
        version=CR_VERSION,
        namespace=namespace,
        plural=CR_PLURAL,
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
    remaining, deletions = await loop.run_in_executor(
        None,
        _with_core_v1,
        delete_all_owned_pods,
        namespace,
        OwnerIdentity.from_owner_ref(owner_ref),
    )
    if deletions:
        log.info(
            "Requested deletion of %d owned session pods after terminal selection failure "
            "(%d still present): %s",
            len(deletions),
            remaining,
            ", ".join(f"{d.pod_name}={d.outcome}" for d in deletions),
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


def _runtime_proof_status(
    active_session: ResolvedRuntimeConfig,
    deployment_context: RuntimeDeploymentContext,
) -> ConstellationSpecStatus:
    """The status fields that publish the deployed proof and release identity."""
    proof = active_session.proof
    return ConstellationSpecStatus.from_cr(
        {
            "documentDigest": proof.document_digest,
            "closureDigest": proof.closure_digest,
            "resolvedSemanticDigest": proof.resolved_semantic_digest,
            "runtimeRelease": deployment_context.release,
            "runtimeBuild": deployment_context.build,
        }
    )


def _runtime_deployment_context(
    active_session: ResolvedRuntimeConfig,
    meta: dict,
    session_run_id: str,
) -> RuntimeDeploymentContext:
    return RuntimeDeploymentContext.from_proof(
        active_session.proof,
        cr_uid=str(meta.get("uid") or ""),
        cr_generation=int(meta.get("generation", 0) or 0),
        session_run_id=session_run_id,
        release=os.environ.get("NODALARC_RELEASE", ""),
        build=os.environ.get("NODAL_BUILD", ""),
    )


def _runtime_session_config_matches(
    namespace: str,
    active_session: ResolvedRuntimeConfig,
    deployment_context: RuntimeDeploymentContext,
) -> bool:
    """Return true only for the exact mounted inputs of this CR generation."""
    from nodalarc_operator.session_deployer import _get_v1

    expected = build_runtime_session_config_data(
        active_session.resolution.resolved,
        active_session.root_yaml.decode("utf-8"),
        active_session.selection,
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


def _session_name_from_spec(spec: dict) -> str:
    from nodalarc.configuration_yaml import load_configuration_yaml
    from nodalarc.models.segment_session import SegmentSessionConfig

    session_yaml = ConstellationSpecSpec.from_cr(spec).session_yaml
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
        cm = v1.read_namespaced_config_map(WIRING_MANIFEST_CONFIGMAP, namespace)
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

    try:
        manifest = decode_wiring_manifest_payload(data)
    except WiringManifestPayloadError as exc:
        if exc.stage == "missing":
            log.info(
                "Reconcile: wiring manifest payload missing for session %s, rewriting",
                desired_session_id,
            )
        else:
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
    active_session: ResolvedRuntimeConfig | None = None,
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
    phase = ConstellationSpecStatus.from_cr(status).phase or ""
    owner_ref = _build_owner_ref(name, meta)
    spec_dict = dict(spec)

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
        verification = await asyncio.to_thread(
            _verify_runtime, spec_dict, meta, namespace, session_run_id, active_session
        )
        platform_hash = verification.platform_hash
        deployment_context = verification.deployment_context
        runtime_hash = verification.runtime_hash
        status_fields = {**identity_fields, **verification.proof_fields}
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
            active_session.resolution,
            namespace=namespace,
            owner_ref=owner_ref,
        )
    except WorkloadPreparationError as exc:
        error_msg = str(exc)
        log.error("Reconcile: terminal workload selection failure: %s", error_msg, exc_info=True)
        await _converge_selection_failure(
            loop, name, namespace, meta, owner_ref, status_fields, error_msg
        )
        return

    # Desired session-pod identity: the resolved node set, this run and the
    # prepared workload selection. An empty or colliding node set is refused.
    try:
        pod_identity = _session_pod_identity(
            owner_ref, session_run_id, prepared_workloads, active_session
        )
    except SessionPodStateError as exc:
        error_msg = str(exc)
        log.error("Reconcile: invalid session configuration: %s", error_msg)
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
    expected_count = pod_identity.expected_count

    # One observation of the session pods drives every decision in this pass.
    try:
        view = await loop.run_in_executor(
            None, _with_core_v1, observe_session_pods, namespace, pod_identity
        )
    except SessionPodStateError as exc:
        error_msg = str(exc)
        log.error("Reconcile: session pod observation refused: %s", error_msg)
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Error",
                    "message": f"Session pod state refused: {error_msg}",
                    **status_fields,
                },
            ),
        )
        return

    # --- Condition 1: no session pod of another owner ---
    # A foreign session pod is never counted, adopted or deleted.
    if view.foreign:
        log.warning(
            "Reconcile: %d session pod(s) not owned by ConstellationSpec %s: %s",
            len(view.foreign),
            pod_identity.owner.describe(),
            view.describe_foreign(),
        )
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Pending",
                    "message": (
                        f"{len(view.foreign)} session pod(s) not owned by ConstellationSpec "
                        f"{pod_identity.owner.describe()}: {view.describe_foreign()}"
                    )[:500],
                    **status_fields,
                },
            ),
        )
        return

    # --- Condition 2: no owned pod of another run, owner or workload, or of an
    # unexpected node ---
    if view.deletable:
        deletions = await loop.run_in_executor(
            None, _with_core_v1, delete_ineligible_pods, namespace, view
        )
        conflicts = [d for d in deletions if d.outcome is DeletionOutcome.CONFLICT]
        message = (
            f"Replacing {len(view.deletable)} session pod(s) whose run, owner or workload "
            "differs or whose node is not expected"
        )
        if conflicts:
            message += (
                f"; deletion of {conflicts[0].pod_name} conflicted "
                f"({conflicts[0].reason or 'HTTP 409'}), reobserving"
            )
        log.info("Reconcile: %s", message)
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "message": message,
                    "podCount": expected_count,
                    **status_fields,
                },
            ),
        )
        return

    # --- Condition 3: owned pods already deleting have gone ---
    if view.terminating:
        log.debug("Reconcile: waiting for %d session pods to terminate", len(view.terminating))
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Pending",
                    "message": f"Waiting for {len(view.terminating)} old session pods to terminate",
                    **status_fields,
                },
            ),
        )
        return

    # --- Condition 4: every expected node has a current pod ---
    ready = view.running_count
    if view.missing_node_ids:
        # Pods missing — run the full ensure pipeline to converge
        _update_status(
            name,
            namespace,
            _with_observed_generation(
                meta,
                {
                    "phase": "Creating",
                    "message": f"Deploying: {len(view.current)}/{expected_count} pods exist",
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
                None, ensure_session_pods, context, namespace, owner_ref, pod_identity, _progress
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
        except WorkloadPreparationError as exc:
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

    # --- Condition 5: every current pod has a provisioned network ---
    provisioned = view.provisioned_count
    if provisioned < expected_count:
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
                    write_wiring_manifest,
                    spec_dict,
                    namespace,
                    owner_ref,
                    session_run_id,
                    active_session,
                    platform_hash,
                    view.placement(),
                )

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
    if ready < expected_count:
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


@kopf.on.create(CR_PLURAL, group=CR_GROUP)
async def on_create(spec, name, namespace, meta, **_):
    """Handle ConstellationSpec CR creation.

    Non-blocking: validates the CRD, sets initial status, and calls the
    reconciler once. The kopf timer re-enters every 10 seconds to drive
    progress through ConfigMap creation, pod creation, readiness, wiring,
    and Ready. No blocking waits — the Operator stays responsive.
    """
    log.info("ConstellationSpec '%s' created in %s", name, namespace)

    if name != CR_NAME:
        _update_status(
            name,
            namespace,
            ConstellationSpecStatus.from_cr(
                {
                    "phase": "Error",
                    "message": f"Only {CR_NAME!r} is allowed as CR name, got {name!r}",
                }
            ),
        )
        raise kopf.PermanentError(f"Invalid CR name: {name}")

    _update_status(name, namespace, _with_observed_generation(meta, {"phase": "Pending"}))

    # The reconciler handles everything. First invocation kicks off
    # the state machine; the timer drives subsequent ticks.
    await _reconcile_session(spec, name, namespace, meta, {"phase": "Pending"})


@kopf.on.update(CR_PLURAL, group=CR_GROUP)
async def on_update(spec, name, namespace, meta, status, **_):
    """Handle CRD spec changes — session switch or config update.

    Uses semantic hashing to determine what changed:
    - Platform-impacting fields (constellation, routing, time, GS):
      restart platform pods via forced rolling update, then reconcile.
    - Non-impacting fields (metadata, placement): reconcile without
      restarting platform pods.
    """
    observed = ConstellationSpecStatus.from_cr(status)
    phase = observed.phase or ""
    if phase == "Error" and observed.observes_generation(meta.get("generation")):
        log.debug("on_update: session in Error state, skipping")
        return

    await _reconcile_session(spec, name, namespace, meta, status)


@kopf.on.delete(CR_PLURAL, group=CR_GROUP)
async def on_delete(name, namespace, spec=None, meta=None, status=None, **_):
    """Handle ConstellationSpec CR deletion: tear down what this CR deployed.

    The deployed identity is proven from the resources this CR owns (session
    pods and the two run-id-bearing ConfigMaps), never read from the CR status
    or derived from the desired generation. A session object with another
    owner, or an owned record without its run id, raises, so kopf retries and
    the finalizer stays; absence of every such object means nothing was
    deployed and only the ConfigMap sweep runs.
    """
    log.info("ConstellationSpec '%s' deleted, tearing down session", name)
    loop = asyncio.get_running_loop()
    owner_ref = _build_owner_ref(name, dict(meta or {}))
    run_ids = await loop.run_in_executor(
        None,
        _with_core_v1,
        owned_session_run_ids,
        namespace,
        OwnerIdentity.from_owner_ref(owner_ref),
    )
    if run_ids:
        log.info("Owned session resources name run ids %s; purging each", ", ".join(run_ids))
    else:
        log.info("No owned session resources; sweeping session ConfigMaps only")
    await loop.run_in_executor(None, teardown_session, namespace, run_ids)
    log.info("Session teardown complete")


@kopf.on.resume(CR_PLURAL, group=CR_GROUP)
async def on_resume(spec, name, namespace, meta, status, **_):
    """Handle Operator restart — reconcile existing session state."""
    observed = ConstellationSpecStatus.from_cr(status)
    phase = observed.phase or ""
    log.info("Resuming ConstellationSpec '%s', current phase: %s", name, phase)

    if phase == "Error" and observed.observes_generation(meta.get("generation")):
        log.info("Operator resume: session in Error state: %s", observed.message or "")
        return

    await _reconcile_session(spec, name, namespace, meta, status)


@kopf.timer(CR_PLURAL, group=CR_GROUP, interval=10.0, idle=10)
async def wiring_check(spec, name, namespace, meta, status, **_):
    """Periodically advance session state via the reconciler.

    Active during Pending, Creating and Wiring phases. Drives progress for:
    - Pending: old runtime objects still terminating
    - Creating: pods still starting after operator resume
    - Wiring: Node Agent wiring data plane → Ready
    - Ready: repair missing runtime identity fields after operator/CRD upgrades
    """
    phase = ConstellationSpecStatus.from_cr(status).phase or ""
    if phase == "Ready":
        try:
            identity_fields = await asyncio.to_thread(_status_identity_fields, dict(spec), meta)
        except Exception:
            await _reconcile_session(spec, name, namespace, meta, status)
            return
        session_run_id = identity_fields["sessionRunId"]
        try:
            active_session = await asyncio.to_thread(
                _resolve_active_session,
                dict(spec),
                namespace,
                session_run_id,
            )
            verification = await asyncio.to_thread(
                _verify_runtime, dict(spec), meta, namespace, session_run_id, active_session
            )
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
        # missing, replaced, foreign or non-running pod, or wiring proof that
        # is no longer current, must take the session back through normal
        # reconciliation instead of remaining advertised as Ready. Pod
        # membership is judged by the same identity and classification the
        # reconciler uses.
        owner_ref = _build_owner_ref(name, meta)
        try:
            prepared_workloads = await asyncio.to_thread(
                prepare_session_workloads,
                active_session.resolution,
                namespace=namespace,
                owner_ref=owner_ref,
            )
            pod_identity = _session_pod_identity(
                owner_ref, session_run_id, prepared_workloads, active_session
            )
        except (WorkloadPreparationError, SessionPodStateError) as exc:
            log.warning("Ready session no longer prepares (%s) — reconciling", exc)
            await _reconcile_session(spec, name, namespace, meta, status, active_session)
            return
        expected_count = pod_identity.expected_count
        try:
            view = await asyncio.to_thread(
                _with_core_v1, observe_session_pods, namespace, pod_identity
            )
        except kubernetes.client.rest.ApiException as exc:
            log.warning("Ready session pod membership check failed: %s", exc)
            return
        except SessionPodStateError as exc:
            log.warning("Ready session pod state refused (%s) — reconciling", exc)
            await _reconcile_session(spec, name, namespace, meta, status, active_session)
            return
        pods_current = view.complete and view.running_count == expected_count
        if not pods_current:
            # Known-invalid membership is acted on before any further read:
            # a failed wiring-proof query must not leave Ready standing.
            log.warning(
                "Ready session pods not current (foreign=%d, replace=%d, terminating=%d, "
                "missing=%d, running=%d/%d) — reconciling",
                len(view.foreign),
                len(view.deletable),
                len(view.terminating),
                len(view.missing_node_ids),
                view.running_count,
                expected_count,
            )
            await _reconcile_session(spec, name, namespace, meta, status, active_session)
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
        if not wiring_ok:
            log.warning("Ready session wiring proof not current — reconciling")
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
                verification.runtime_hash,
                active_session.proof,
                verification.deployment_context,
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
        intended = ConstellationSpecStatus.from_cr(
            {
                **identity_fields,
                **verification.proof_fields,
                "platformHash": verification.platform_hash,
                "runtimeHash": verification.runtime_hash,
            }
        )
        if not ConstellationSpecStatus.from_cr(status).carries(intended):
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
