# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session deployer — renders configs, creates pods and ConfigMaps.

Replicates na_deploy.py Steps 3-5 using the K8s Python client.
Called by kopf handlers in handlers.py.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import kubernetes
from nodalarc.catalog_upload import CatalogUploadSelection
from nodalarc.content_identity import canonical_json_bytes
from nodalarc.cr_runtime_config import ConstellationSpecSpec, load_cr_runtime_config
from nodalarc.models.resolved_session import ResolvedSession
from nodalarc.nats_channels import sanitize_session_id, session_purge_filters
from nodalarc.platform_config import (
    compute_pod_placement,
    get_platform_config,
)
from nodalarc.runtime_config import (
    RUNTIME_DEPLOYMENT_CONTEXT_FILENAME,
    SESSION_YAML_FILENAME,
    ResolvedRuntimeConfig,
    RuntimeConfigProof,
    RuntimeDeploymentContext,
)
from nodalarc.runtime_service_config import (
    CATALOG_UPLOAD_SELECTION_FILENAME,
    SESSION_RUN_ID_FILENAME,
)
from nodalarc.session_identity import require_resolved_session_run_id
from nodalarc.session_nodes import available_session_nodes
from nodalarc.session_validator import validate_session_readiness
from nodalarc.substrate.manifest_contract import WIRING_MANIFEST_CONFIGMAP
from nodalarc.substrate.routing_requirements import routing_kernel_requirements
from nodalarc.substrate.wiring_status import WIRING_STATUS_CONFIGMAP

from nodalarc_operator.session_pods import (
    PodClass,
    SessionPodIdentity,
    canonical_node_id,
    delete_conflicting_pod,
)
from nodalarc_operator.workloads.materializer import (
    build_session_pod,
)
from nodalarc_operator.workloads.preparation import (
    WorkloadPreparationError,
    prepare_session_workloads,
)

log = logging.getLogger(__name__)


class RetryableSessionDependency(RuntimeError):
    """Raised when Kubernetes has not finished deleting a prior runtime object."""


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        log.error("FATAL: Required environment variable %s is not set", name)
        raise RuntimeError(f"Required environment variable {name} is not set")
    return val


# Module-level K8s API clients — initialized once on first use, reused for
# all calls. Eliminates per-function load_incluster_config() + client
# instantiation that leaks TCP sockets from urllib3 connection pools.
_v1: kubernetes.client.CoreV1Api | None = None
_apps_v1: kubernetes.client.AppsV1Api | None = None


def _get_v1() -> kubernetes.client.CoreV1Api:
    global _v1
    if _v1 is None:
        kubernetes.config.load_incluster_config()
        _v1 = kubernetes.client.CoreV1Api()
    return _v1


def _get_apps_v1() -> kubernetes.client.AppsV1Api:
    global _apps_v1
    if _apps_v1 is None:
        kubernetes.config.load_incluster_config()
        _apps_v1 = kubernetes.client.AppsV1Api()
    return _apps_v1


def _operator_session_config(
    spec: Mapping[str, Any],
    active_session: ResolvedRuntimeConfig | None,
    *,
    namespace: str,
    origin: str,
    run_id: str | None = None,
) -> ResolvedRuntimeConfig:
    """Return a supplied verified result or load one through the sole boundary."""
    parsed = ConstellationSpecSpec.from_cr(spec)
    if active_session is None:
        return load_cr_runtime_config(
            spec,
            core_v1=_get_v1(),
            namespace=namespace,
            source_origin=origin,
            run_id=run_id,
        )
    if not isinstance(active_session, ResolvedRuntimeConfig):
        raise TypeError("active_session must be an ResolvedRuntimeConfig")
    if parsed.session_yaml.encode("utf-8") != active_session.root_yaml:
        raise ValueError("active_session root YAML does not match spec.sessionYaml")
    if parsed.catalog_upload != active_session.selection:
        raise ValueError("active_session upload selection does not match spec.catalogUpload")
    if run_id is not None and active_session.proof.run_id != run_id:
        raise ValueError("active_session runtime identity does not match session_run_id")
    return active_session


def _proof_hash_fields(proof: RuntimeConfigProof) -> dict[str, str | int]:
    return {
        "upload_id": proof.upload_id,
        "document_digest": proof.document_digest,
        "closure_digest": proof.closure_digest,
        "resolved_semantic_digest": proof.resolved_semantic_digest,
        "file_count": proof.file_count,
        "total_bytes": proof.total_bytes,
        "resolved_node_count": proof.resolved_node_count,
    }


def _ensure_immutable_configmap(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    config_map: kubernetes.client.V1ConfigMap,
) -> None:
    """Create-or-verify semantics for immutable workload artifact objects.

    An immutable object is never replaced. On 409 the existing object is
    read back and must match on the complete owner-reference projection,
    immutability, and exact contents — anything else (an old CR's object,
    mutated bytes) is a deterministic workload error for this selection.
    """
    name = config_map.metadata.name
    try:
        v1.create_namespaced_config_map(namespace, config_map)
        return
    except kubernetes.client.rest.ApiException as error:
        if error.status != 409:
            raise
    existing = v1.read_namespaced_config_map(name, namespace)
    expected_ref = config_map.metadata.owner_references[0]
    expected_projection = [
        {
            "api_version": str(expected_ref.get("apiVersion") or ""),
            "kind": str(expected_ref.get("kind") or ""),
            "name": str(expected_ref.get("name") or ""),
            "uid": str(expected_ref.get("uid") or ""),
            "block_owner_deletion": bool(expected_ref.get("blockOwnerDeletion") or False),
        }
    ]
    existing_projection = [
        {
            "api_version": str(getattr(ref, "api_version", "") or ""),
            "kind": str(getattr(ref, "kind", "") or ""),
            "name": str(getattr(ref, "name", "") or ""),
            "uid": str(getattr(ref, "uid", "") or ""),
            "block_owner_deletion": bool(getattr(ref, "block_owner_deletion", False) or False),
        }
        for ref in existing.metadata.owner_references or []
    ]
    if existing_projection != expected_projection:
        raise WorkloadPreparationError(
            f"workload artifact ConfigMap {name!r} is owned by "
            f"{existing_projection!r}, not the current CR {expected_projection!r}"
        )
    if existing.immutable is not True:
        raise WorkloadPreparationError(
            f"workload artifact ConfigMap {name!r} exists but is not immutable"
        )
    if (existing.binary_data or {}) != (config_map.binary_data or {}):
        raise WorkloadPreparationError(
            f"workload artifact ConfigMap {name!r} exists with different contents"
        )
    if (existing.data or {}) != (config_map.data or {}):
        raise WorkloadPreparationError(
            f"workload artifact ConfigMap {name!r} exists with unexpected plain data"
        )


def _cluster_pod_cidr(v1: kubernetes.client.CoreV1Api) -> str | None:
    """The minimal IPv4 CIDR covering every node's pod CIDR.

    The Node Agent installs a management route to this block via the CNI
    gateway so a session pod on any node can answer the browser terminal and
    control-plane traffic while the routing engine owns the default route.
    Returns None when no node advertises a pod CIDR (nothing to install).
    """
    import ipaddress

    networks: list[ipaddress.IPv4Network] = []
    for node in v1.list_node().items:
        cidr = getattr(node.spec, "pod_cidr", None)
        if not cidr:
            continue
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if isinstance(network, ipaddress.IPv4Network):
            networks.append(network)
    if not networks:
        return None
    low = min(int(n.network_address) for n in networks)
    high = max(int(n.broadcast_address) for n in networks)
    prefix = 32
    while prefix >= 0:
        mask = ((1 << 32) - 1) ^ ((1 << (32 - prefix)) - 1) if prefix else 0
        if (low & mask) == (high & mask):
            return str(ipaddress.ip_network((low & mask, prefix)))
        prefix -= 1
    return "0.0.0.0/0"


def discover_available_nodes() -> list[str]:
    """The Kubernetes nodes that accept session pods."""
    return available_session_nodes(_get_v1())


def _node_internal_ips(
    v1: kubernetes.client.CoreV1Api,
    required_nodes: set[str],
) -> dict[str, str]:
    """Return InternalIP for every required Kubernetes node."""
    ips: dict[str, str] = {}
    for node in v1.list_node().items:
        name = node.metadata.name
        if name not in required_nodes:
            continue
        for addr in node.status.addresses or []:
            if addr.type == "InternalIP":
                ips[name] = addr.address
                break
    missing = sorted(required_nodes - set(ips))
    if missing:
        raise ValueError("missing InternalIP for Kubernetes nodes: " + ", ".join(missing))
    return ips


def _required_substrate_pairs(
    *,
    nodes: dict[str, dict[str, Any]],
    isl_pairs: set[tuple[str, str]],
    pod_placement: dict[str, str],
    node_ips: dict[str, str],
    ground_candidate_satellites_by_gs: Mapping[str, tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    """Collapse possible cross-node links into required directional node pairs."""
    from nodalarc.substrate.measurement_contract import RequiredSubstratePair

    reasons_by_direction: dict[tuple[str, str], set[str]] = {}

    def _add_reason(node_a: str, node_b: str, reason: str) -> None:
        k8s_a = pod_placement[node_a]
        k8s_b = pod_placement[node_b]
        if k8s_a == k8s_b:
            return
        reasons_by_direction.setdefault((k8s_a, k8s_b), set()).add(reason)
        reasons_by_direction.setdefault((k8s_b, k8s_a), set()).add(reason)

    for node_a, node_b in isl_pairs:
        _add_reason(node_a, node_b, "isl")

    all_ground_ids = {
        node_id for node_id, spec in nodes.items() if spec["node_type"] == "ground_station"
    }
    all_satellite_ids = {
        node_id for node_id, spec in nodes.items() if spec["node_type"] == "satellite"
    }
    if ground_candidate_satellites_by_gs is not None:
        unknown_ground = sorted(set(ground_candidate_satellites_by_gs) - all_ground_ids)
        if unknown_ground:
            raise ValueError(
                "substrate candidate map names unknown ground station(s): "
                + ", ".join(unknown_ground)
            )
        ground_ids = sorted(ground_candidate_satellites_by_gs)
    else:
        ground_ids = sorted(all_ground_ids)

    for gs_id in ground_ids:
        candidate_sat_ids = (
            ground_candidate_satellites_by_gs.get(gs_id, ())
            if ground_candidate_satellites_by_gs is not None
            else tuple(sorted(all_satellite_ids))
        )
        if not candidate_sat_ids:
            raise ValueError(f"ground station {gs_id!r} has no substrate candidate satellites")
        unknown_satellites = sorted(set(candidate_sat_ids) - all_satellite_ids)
        if unknown_satellites:
            raise ValueError(
                f"ground station {gs_id!r} declares unknown substrate candidate satellite(s): "
                + ", ".join(unknown_satellites)
            )
        for sat_id in sorted(candidate_sat_ids):
            _add_reason(gs_id, sat_id, "ground")

    pairs = [
        RequiredSubstratePair.build(
            source_node=source,
            source_ip=node_ips[source],
            target_node=target,
            target_ip=node_ips[target],
            reasons=sorted(reasons),
        ).model_dump(mode="json")
        for (source, target), reasons in reasons_by_direction.items()
    ]
    return sorted(pairs, key=lambda pair: pair["directional_key"])


def _platform_placement_policy() -> Any:
    cfg = get_platform_config()
    return {
        "policy": cfg.default_session_pod_placement_policy,
        "planes_per_group": cfg.default_session_pod_planes_per_group,
    }


def _pod_inventory(resolved: ResolvedSession) -> dict[str, dict]:
    """Every resolved node's pod-inventory facts: kind, grid position, site.

    Placement and pod creation read these; they are the same for every
    workload a node runs.
    """
    inventory: dict[str, dict] = {}
    for node in resolved.nodes:
        if node.forwarding not in (None, "routed", "host"):
            raise ValueError(
                f"node {node.node_id!r} has forwarding class {node.forwarding!r}, "
                "which the deployer does not support"
            )
        facts: dict = {
            "node_type": "satellite" if node.kind == "satellite" else "ground_station",
        }
        if node.kind == "ground_station":
            facts["gs_name"] = node.local_node_id
        if node.plane is not None and node.slot is not None:
            facts.update({"plane": node.plane, "slot": node.slot})
        inventory[node.node_id] = facts
    return inventory


def _fixed_link_interfaces_by_node(resolved: ResolvedSession) -> dict[str, list[dict[str, str]]]:
    by_node: dict[str, list[dict[str, str]]] = {}
    for candidate in resolved.link_candidates:
        if candidate.kind == "access":
            continue
        interface_a, interface_b = candidate.fixed_interfaces
        by_node.setdefault(candidate.node_a, []).append(
            {
                "name": interface_a,
                "peer_node": candidate.node_b,
                "peer_iface": interface_b,
            }
        )
        by_node.setdefault(candidate.node_b, []).append(
            {
                "name": interface_b,
                "peer_node": candidate.node_a,
                "peer_iface": interface_a,
            }
        )
    return {
        node_id: sorted(
            items, key=lambda item: (item["name"], item["peer_node"], item["peer_iface"])
        )
        for node_id, items in by_node.items()
    }


def _publish_validation_ops_events(results: list, namespace: str, session_id: str) -> None:
    """Publish validation results as OpsEvents via the logging system."""
    for r in results:
        level = logging.ERROR if r.level == "error" else logging.WARNING
        details = {"remediation": r.remediation} if r.remediation else None
        log.log(
            level,
            "Validation: [%s] %s",
            r.code,
            r.message,
            extra={"code": r.code, "details": details},
        )


def ensure_session_configmaps(
    spec: dict,
    name: str,
    namespace: str,
    owner_ref: dict,
    progress_fn: Any | None = None,
    session_run_id: str | None = None,
    active_session: ResolvedRuntimeConfig | None = None,
    deployment_context: RuntimeDeploymentContext | None = None,
    prepared_workloads=None,
) -> dict:
    """Create/update all ConfigMaps and SSH keys for a session.

    Runs the deploy pipeline up to pod creation: resolve the session,
    validate readiness, prepare workloads (adapter-rendered configuration
    included), create ConfigMaps, generate the SSH keypair, compute pod
    placement.

    Idempotent — ConfigMaps use create-or-update, SSH key uses create-or-replace.
    Safe to call repeatedly; only writes what's missing or changed.

    Args:
        spec: The CR's .spec dict.
        name: CR metadata.name.
        namespace: K8s namespace.
        owner_ref: ownerReferences entry for garbage collection.
        progress_fn: Optional callback(message: str) for status updates.

    Returns:
        Context dict with keys: session_id, session_run_id, resolved_session,
        pod_inventory, pod_placement, available_nodes. Passed to
        ensure_session_pods().
    """

    def _progress(msg: str) -> None:
        log.debug(msg)
        if progress_fn:
            progress_fn(msg)

    v1 = _get_v1()

    # Discover available K8s nodes for pod placement.
    _progress("Discovering available K8s nodes")
    available_nodes = discover_available_nodes()
    if not available_nodes:
        import kopf

        raise kopf.PermanentError(
            "No K8s nodes with label nodalarc.io/node-agent=true found. "
            "Label at least one node: kubectl label node <name> nodalarc.io/node-agent=true"
        )

    # --- Step 1: Resolve segment session YAML from the CRD spec ---
    _progress("Resolving segment session configuration")
    operator_session = _operator_session_config(
        spec,
        active_session,
        namespace=namespace,
        origin="operator.deploy",
        run_id=session_run_id,
    )
    resolution = operator_session.resolution
    if not session_run_id:
        raise ValueError("session_run_id is required to create runtime session ConfigMaps")
    if not isinstance(deployment_context, RuntimeDeploymentContext):
        raise TypeError("deployment_context is required to create runtime session ConfigMaps")
    if deployment_context.cr_uid != str(owner_ref.get("uid") or ""):
        raise ValueError("deployment_context CR UID does not match the session owner")
    operator_session.proof.bind_deployment_identity(
        deployment_context,
        pod_uid="operator-context-validation",
    )
    session_id = sanitize_session_id(session_run_id)
    resolved_session = resolution.resolved
    if require_resolved_session_run_id(resolved_session) != session_id:
        raise ValueError("resolved runtime session id does not match operator session_run_id")

    # --- Step 2: Use catalog-resolved runtime truth ---
    _progress("Using resolved catalog runtime definitions")
    satellite_count = sum(1 for node in resolved_session.nodes if node.kind == "satellite")
    ground_count = sum(1 for node in resolved_session.nodes if node.kind == "ground_station")
    _progress(
        f"Expanded {satellite_count} satellites and {ground_count} ground nodes "
        f"across {len(resolved_session.routing_domains)} routing domain(s)"
    )
    if satellite_count <= 0:
        raise ValueError("No satellites in resolved session")

    # --- Step 3: Pre-deployment readiness validation ---
    # A session that resolves but is unsuitable for deployment (zero-candidate
    # link rules, disconnected routing members, SR index gaps, MBB capacity
    # shortfalls) must fail here, before any ConfigMap or pod exists.
    _progress("Validating session readiness")
    validation_results = validate_session_readiness(
        resolved_session,
        available_node_count=len(available_nodes),
    )
    val_errors = [r for r in validation_results if r.level == "error"]
    if validation_results:
        _publish_validation_ops_events(validation_results, namespace, session_id=session_run_id)
    if val_errors:
        import kopf

        error_msg = "; ".join(f"[{r.code}] {r.message}" for r in val_errors)
        raise kopf.PermanentError(f"Session validation failed: {error_msg}")

    # --- Step 4: Pod inventory per node ---
    pod_inventory = _pod_inventory(resolved_session)

    # --- Step 5: Workload preparation (write-free) ---
    # Admit, render, and compose EVERY node's effective profile before the
    # first Kubernetes write below. Any preparation failure is terminal.
    # The reconciler runs the same preparation before any pod mutation and
    # hands the result in; a direct caller computes it here.
    _progress("Preparing session workloads")
    if prepared_workloads is None:
        prepared_workloads = prepare_session_workloads(
            operator_session.resolution,
            namespace=namespace,
            owner_ref=owner_ref,
        )

    # --- Step 6: immutable workload artifact ConfigMaps ---
    # Rendered configuration travels as plan artifacts inside these objects.
    _progress(f"Creating {len(prepared_workloads.composed)} workload artifact ConfigMaps")
    artifact_count = 0
    for composed in prepared_workloads.composed.values():
        if composed.artifact_config_map is not None:
            _ensure_immutable_configmap(v1, namespace, composed.artifact_config_map)
            artifact_count += 1
    log.info("Ensured %d workload artifact ConfigMaps", artifact_count)

    # --- Step 7: Create session-level ConfigMaps ---
    _progress("Creating session-level ConfigMaps")
    _create_session_configmaps(
        v1,
        resolved_session,
        operator_session.root_yaml.decode("utf-8"),
        operator_session.selection,
        deployment_context,
        namespace,
        owner_ref,
    )

    # --- Step 7b: Ensure SSH keypair for terminal access ---
    _progress("Ensuring SSH keypair for terminal access")
    _create_terminal_ssh_keys(v1, namespace, owner_ref)

    # --- Step 8: Compute pod placement ---
    placement = _platform_placement_policy()
    _progress(f"Computing pod placement ({placement['policy']} policy)")
    pod_placement = compute_pod_placement(placement, pod_inventory, available_nodes)
    node_counts: dict[str, int] = {}
    for target in pod_placement.values():
        node_counts[target] = node_counts.get(target, 0) + 1
    log.info(
        "Placement policy=%s, %d pods across %d nodes: %s",
        placement["policy"],
        len(pod_placement),
        len(node_counts),
        ", ".join(f"{n}={c}" for n, c in sorted(node_counts.items())),
    )

    return {
        "session_id": session_id,
        "session_run_id": session_run_id,
        "operator_session": operator_session,
        "deployment_context": deployment_context,
        "resolved_session": resolved_session,
        "pod_inventory": pod_inventory,
        "pod_placement": pod_placement,
        "available_nodes": available_nodes,
        "prepared_workloads": prepared_workloads,
    }


def ensure_session_pods(
    context: dict,
    namespace: str,
    owner_ref: dict,
    pod_identity: SessionPodIdentity,
    progress_fn: Any | None = None,
) -> int:
    """Create ONLY missing session pods from a prepared context.

    Takes the context dict from ensure_session_configmaps(). Checks which
    pods already exist and creates only the missing ones. Returns the total
    expected pod count (not just created count).

    Idempotent — a 409 on create is resolved through the shared session-pod
    classification: a current pod is kept, an ineligible owned pod is deleted
    by its observed UID, a foreign or terminating pod is refused.

    Args:
        context: Dict from ensure_session_configmaps().
        namespace: K8s namespace.
        owner_ref: ownerReferences entry for garbage collection.
        pod_identity: The desired session-pod identity for this reconciliation.
        progress_fn: Optional callback(message: str) for status updates.

    Returns:
        Total expected pod count.
    """

    def _progress(msg: str) -> None:
        log.debug(msg)
        if progress_fn:
            progress_fn(msg)

    pod_inventory = context["pod_inventory"]
    pod_placement = context["pod_placement"]
    session_id = context["session_id"]
    prepared_workloads = context["prepared_workloads"]
    if (
        pod_identity.selection_identity != prepared_workloads.identity
        or pod_identity.run_label != session_id
        or pod_identity.expected_node_ids
        != {canonical_node_id(node_id) for node_id in pod_inventory}
    ):
        raise ValueError("session pod identity does not describe the prepared deployment context")

    v1 = _get_v1()
    total_pods = len(pod_inventory)
    _progress(f"Creating {total_pods} session pods")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    pod_specs: list[dict] = []
    for node_id, facts in pod_inventory.items():
        pod_specs.append(
            {
                "pod_name": node_id.lower(),
                "node_id": node_id,
                "node_type": facts["node_type"],
                "plane": facts.get("plane"),
                "slot": facts.get("slot"),
                "gs_name": facts.get("gs_name"),
                "target_node": pod_placement.get(node_id),
            }
        )

    import threading

    created_pods = 0
    errors = []
    api_failures: list[kubernetes.client.rest.ApiException] = []
    _pod_creation_done = threading.Event()

    # Heartbeat thread: if no pod completes for 10 seconds, update the
    # progress message so the UI knows the system is still working.
    def _heartbeat():
        last_count = 0
        while not _pod_creation_done.wait(timeout=10):
            if created_pods == last_count and created_pods < total_pods:
                _progress(
                    f"Creating session pods: {created_pods}/{total_pods} "
                    f"(K8s scheduling {total_pods - created_pods} remaining — please wait)"
                )
            last_count = created_pods

    heartbeat = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat.start()
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {}
            for ps in pod_specs:
                fut = pool.submit(
                    _create_workload_pod,
                    v1=v1,
                    pod_name=ps["pod_name"],
                    namespace=namespace,
                    node_id=ps["node_id"],
                    node_type=ps["node_type"],
                    plane=ps["plane"],
                    slot=ps["slot"],
                    gs_name=ps["gs_name"],
                    composed=prepared_workloads.composed[ps["node_id"]],
                    target_node=ps["target_node"],
                    owner_ref=owner_ref,
                    pod_identity=pod_identity,
                )
                futures[fut] = ps["node_id"]

            for fut in as_completed(futures):
                node_id = futures[fut]
                try:
                    fut.result()
                    created_pods += 1
                    _progress(f"Creating session pods: {created_pods}/{total_pods}")
                except kubernetes.client.rest.ApiException as exc:
                    api_failures.append(exc)
                    errors.append(f"{node_id}: {exc}")
                    log.error("Pod creation failed for %s: %s", node_id, exc)
                except Exception as exc:
                    errors.append(f"{node_id}: {exc}")
                    log.error("Pod creation failed for %s: %s", node_id, exc)
    finally:
        # Every exit stops progress reporting before the caller publishes a
        # phase: no heartbeat may overwrite it afterwards.
        _pod_creation_done.set()
        heartbeat.join()

    if errors:
        log.error("Pod creation: %d failures out of %d", len(errors), total_pods)
        displayed = "; ".join(errors[:10])
        if len(errors) > 10:
            displayed += f"; ... and {len(errors) - 10} more"
        if len(api_failures) == len(errors):
            # Every failure was a Kubernetes API failure: transient. The
            # typed exception reaches the reconciler, which stays Creating
            # and creates the missing pods on the next tick.
            raise api_failures[0]
        raise RuntimeError(f"Pod creation failed: {displayed}")
    log.info("Created %d session pods (total expected: %d)", created_pods, total_pods)

    return total_pods


def _site_lans_for_manifest(
    resolved: ResolvedSession,
    pod_placement: dict[str, str],
    node_ips: dict[str, str],
) -> dict[str, dict]:
    """Ethernet segments for the wiring manifest: site LANs and carried buses.

    The resolver's segment table is the single source: every segment's
    members carry their in-pod interface name, allocated addresses, and the
    operator-assigned Kubernetes node with its host IP so the Node Agent can
    build per-host bridges and head-end-replicated VXLAN without any
    placement or addressing re-derivation of its own.
    """
    from nodalarc.vxlan import compute_site_vni

    node_by_id = {node.node_id: node for node in resolved.nodes}
    segments: dict[str, dict] = {}
    vni_owner: dict[int, str] = {}
    for segment in resolved.ethernet_segments:
        segment_key = f"{segment.scope_id}-{segment.segment_id}"
        members: list[dict] = []
        for member in segment.members:
            node = node_by_id.get(member.node_id)
            if node is None or node.interfaces is None:
                raise ValueError(
                    f"segment {segment_key!r} member {member.node_id!r} has no resolved interfaces"
                )
            address_set = node.interfaces.ethernet.get(member.interface)
            if address_set is None:
                raise ValueError(
                    f"segment {segment_key!r} member {member.node_id!r} has no "
                    f"resolved address on {member.interface!r}"
                )
            k3s_node = pod_placement.get(member.node_id)
            if not k3s_node:
                raise ValueError(
                    f"segment member {member.node_id!r} has no discovered pod "
                    "placement; segment wiring cannot be derived"
                )
            host_ip = node_ips.get(k3s_node)
            if not host_ip:
                raise ValueError(
                    f"Kubernetes node {k3s_node!r} (hosting {member.node_id!r}) has "
                    "no InternalIP; segment wiring cannot be derived"
                )
            gateway = None
            attachment = node.host_attachment
            if attachment is not None and attachment.interface == member.interface:
                gateway = attachment.gateway_ipv4
            members.append(
                {
                    "node_id": member.node_id,
                    "interface": member.interface,
                    "addresses": [
                        address
                        for address in (address_set.ipv4, address_set.ipv6)
                        if address is not None
                    ],
                    **({"gateway": gateway} if gateway is not None else {}),
                    "k3s_node": k3s_node,
                    "host_ip": host_ip,
                }
            )
        vni = compute_site_vni(segment_key)
        if vni in vni_owner:
            raise ValueError(
                f"segment VNI collision: {segment_key!r} and {vni_owner[vni]!r} both "
                f"hash to VNI {vni}; rename one"
            )
        vni_owner[vni] = segment_key
        segments[segment_key] = {
            "vni": vni,
            "members": sorted(members, key=lambda m: (m["node_id"], m["interface"])),
        }
    return segments


def write_wiring_manifest(
    spec: dict,
    namespace: str,
    owner_ref: dict | None,
    session_run_id: str,
    active_session: ResolvedRuntimeConfig | None,
    platform_hash: str | None,
    pod_placement: Mapping[str, str],
) -> int:
    """Generate and write the topology wiring manifest ConfigMap.

    Called once every current session pod has a provisioned network. The Node
    Agent watches this ConfigMap and executes all data plane wiring operations.
    ``pod_placement`` maps each canonical node id to its Kubernetes node, from
    the reconciliation's session-pod observation.

    Returns the number of ISL links in the manifest.
    """

    from nodalarc.nats_channels import sanitize_session_id
    from nodalarc.substrate.manifest_contract import (
        REQUIRED_WIRING_PHASES,
        WIRING_MANIFEST_PAYLOAD_KEY,
        derive_wiring_generation,
        encode_wiring_manifest_payload,
    )

    if not session_run_id:
        raise ValueError("session_run_id is required to write topology wiring manifest")
    operator_session = _operator_session_config(
        spec,
        active_session,
        namespace=namespace,
        origin="operator.wiring_manifest",
        run_id=session_run_id,
    )
    resolution = operator_session.resolution
    resolved_session = resolution.resolved

    v1 = _get_v1()

    # Delete stale wiring-status before writing new manifest.
    # Without this, the Node Agent sees old wiring-status as "current" and
    # hits Case B (no-op) instead of Case A (wire from scratch).
    try:
        v1.delete_namespaced_config_map(WIRING_STATUS_CONFIGMAP, namespace)
        log.debug("Deleted stale nodalarc-wiring-status")
    except kubernetes.client.rest.ApiException as e:
        if e.status != 404:
            raise
    # Platform-level sysctls merged with the kernel requirements of each
    # routed node's routing domain.
    base_sysctls = {
        "net.ipv6.conf.all.forwarding": "1",
        "net.ipv4.conf.all.rp_filter": "0",
        "net.ipv4.conf.default.rp_filter": "0",
        "net.ipv6.conf.all.dad_transmits": "0",
        "net.ipv6.conf.default.dad_transmits": "0",
        # Unprivileged ICMP echo for every gid: workload containers run with
        # all capabilities dropped and NoNewPrivs, where a file-capability
        # ping cannot even exec. Datagram ICMP sockets need no capability.
        "net.ipv4.ping_group_range": "0 2147483647",
    }
    fixed_interfaces = _fixed_link_interfaces_by_node(resolved_session)
    ground_indices = resolved_session.ground_index_by_node_id()

    # Build per-node wiring spec
    nodes: dict[str, Any] = {}

    ground_bridges: dict[str, dict] = {}
    for node in resolved_session.nodes:
        if node.forwarding == "host":
            # A processing node: terr0 attachment plus a default gateway,
            # applied by the Node Agent at wiring. No routing stack, no RF
            # interfaces, no ground bridge.
            if node.host_attachment is None:
                raise ValueError(
                    f"host node {node.node_id!r} has no derived attachment; not wireable"
                )
            nodes[node.node_id] = {
                "node_type": "host",
                "sysctls": dict(base_sysctls),
                "isl_interfaces": [],
                "gnd_interfaces": [],
                "mpls_enable": False,
                "segment_routing": False,
                "mtu": 9000,
                "remove_default_route": True,
            }
            continue
        requirements = routing_kernel_requirements(
            resolved_session.routing_domain_for(node.node_id)
        )
        node_sysctls = {**base_sysctls, **requirements.sysctls}
        if node.kind == "satellite":
            # plane/slot are optional grid coordinates — individually
            # placed satellites (GEO longitude slots, state vectors) have
            # neither. The Node Agent never reads them; included only
            # when present so consumers stay on their absent-key paths.
            nodes[node.node_id] = {
                "node_type": "satellite",
                **(
                    {"plane": node.plane, "slot": node.slot}
                    if node.plane is not None and node.slot is not None
                    else {}
                ),
                "sysctls": dict(node_sysctls),
                "isl_interfaces": fixed_interfaces.get(node.node_id, []),
                "gnd_interfaces": [
                    {"name": iface.name}
                    for iface in node.wan_interfaces
                    if iface.name.startswith("gnd")
                ],
                "mpls_enable": requirements.mpls_enable,
                "segment_routing": requirements.segment_routing,
                "mtu": 9000,
                "remove_default_route": True,
            }
            continue
        if node.kind == "ground_station":
            nodes[node.node_id] = {
                "node_type": "ground_station",
                "gs_name": node.local_node_id,
                "gs_index": ground_indices[node.node_id],
                "sysctls": dict(node_sysctls),
                "isl_interfaces": [],
                "gnd_interfaces": [{"name": iface.name} for iface in node.wan_interfaces],
                "mpls_enable": requirements.mpls_enable,
                "segment_routing": requirements.segment_routing,
                "mtu": 9000,
                "remove_default_route": True,
            }
            ground_bridges[node.node_id] = {}
            continue
        raise ValueError(f"unsupported node kind in wiring manifest: {node.kind!r}")

    # Count unique ISL links
    isl_pairs: set[tuple[str, str]] = set()
    for candidate in resolved_session.link_candidates:
        if candidate.kind != "access":
            isl_pairs.add((candidate.node_a, candidate.node_b))

    missing_placement = sorted(
        node_id for node_id in nodes if canonical_node_id(node_id) not in pod_placement
    )
    if missing_placement:
        raise ValueError(
            "missing session pod placement for manifest nodes: " + ", ".join(missing_placement[:20])
        )
    for manifest_node_id in nodes:
        nodes[manifest_node_id]["host"] = pod_placement[canonical_node_id(manifest_node_id)]
    k8s_nodes = {nodes[manifest_node_id]["host"] for manifest_node_id in nodes}
    node_ips = _node_internal_ips(v1, k8s_nodes)
    site_lans = _site_lans_for_manifest(resolved_session, pod_placement, node_ips)
    required_substrate_pairs = _required_substrate_pairs(
        nodes=nodes,
        isl_pairs=isl_pairs,
        pod_placement=pod_placement,
        node_ips=node_ips,
        ground_candidate_satellites_by_gs=resolved_session.ground_candidate_satellites_by_gs(),
    )
    try:
        manifest_session_id = sanitize_session_id(session_run_id)
    except Exception as exc:
        log.error(
            "FATAL: Cannot derive runtime session_id from session_run_id=%r: %s",
            session_run_id,
            exc,
        )
        raise

    manifest_owner_uid = str((owner_ref or {}).get("uid") or "")
    if not session_run_id or not manifest_owner_uid:
        raise ValueError(
            "wiring manifest requires the deployment run identity: "
            f"session_run_id={session_run_id!r}, owner_uid={manifest_owner_uid!r}"
        )
    manifest = {
        "session_id": manifest_session_id,
        "session_run_id": session_run_id,
        "owner_uid": manifest_owner_uid,
        "wiring_generation": "",
        "required_phases": list(REQUIRED_WIRING_PHASES),
        "nodes": nodes,
        "ground_bridges": ground_bridges,
        "required_substrate_pairs": required_substrate_pairs,
        "site_lans": site_lans,
        "isl_link_count": len(isl_pairs),
        "cluster_pod_cidr": _cluster_pod_cidr(v1),
    }
    manifest["wiring_generation"] = derive_wiring_generation(manifest)

    payload = encode_wiring_manifest_payload(manifest)

    _create_or_update_configmap(
        v1,
        WIRING_MANIFEST_CONFIGMAP,
        namespace,
        {
            WIRING_MANIFEST_PAYLOAD_KEY: payload,
            "session_id": manifest_session_id,
            "platform_hash": platform_hash
            or compute_platform_hash(spec, active_session=operator_session, namespace=namespace),
            "wiring_generation": manifest["wiring_generation"],
            "node_count": str(len(nodes)),
        },
        owner_ref,
    )
    log.info(
        "Wrote topology wiring manifest: %d nodes, %d ISL links, %d substrate pairs "
        "(%d bytes encoded)",
        len(nodes),
        len(isl_pairs),
        len(required_substrate_pairs),
        len(payload),
    )
    return len(isl_pairs)


def set_nodalpath_mode(namespace: str, protocol: str) -> None:
    """Patch the NodalPath Deployment to use --mode live for NodalPath sessions,
    --mode console for all others. Called before restarting the NodalPath pod.
    """
    mode = "live" if protocol == "nodalpath" else "console"
    apps_v1 = _get_apps_v1()
    try:
        deployments = apps_v1.list_namespaced_deployment(
            namespace, label_selector="app=nodalarc-nodalpath"
        )
        if not deployments.items:
            log.debug("NodalPath deployment not found — skipping mode patch")
            return
        deployment = deployments.items[0]
        deploy_name = deployment.metadata.name
    except kubernetes.client.rest.ApiException:
        log.debug("NodalPath deployment not found — skipping mode patch")
        return

    for container in deployment.spec.template.spec.containers:
        if container.name == "nodalpath":
            args = list(container.args or [])
            for i, arg in enumerate(args):
                if arg == "--mode" and i + 1 < len(args):
                    if args[i + 1] != mode:
                        args[i + 1] = mode
                        container.args = args
                        apps_v1.patch_namespaced_deployment(deploy_name, namespace, deployment)
                        log.info("NodalPath mode set to %s", mode)
                    else:
                        log.debug("NodalPath mode already %s", mode)
                    return
    log.warning("NodalPath container --mode arg not found in deployment spec")


def restart_platform_pods(namespace: str, config_hash: str = "") -> None:
    """Trigger rolling restart of session-scoped platform pods.

    Patches each Deployment's pod template with a config-hash annotation,
    which triggers a rolling update. Only session-scoped services are
    restarted — those that initialize session state at startup and don't
    yet have a hot-reload path for new session parameters.

    VS-API is NOT restarted. It is platform infrastructure that
    orchestrates session switches from the browser wizard. Restarting it
    mid-switch kills the orchestrator, drops the WebSocket connections to
    every connected browser, and leaves the frontend with no completion
    signal. VS-API already has a hot-reload path: _run_switch() tears
    down the old SessionContext and creates a new one with fresh NATS
    subscriptions. No pod restart needed.

    Architecture direction: eventually ALL platform services adopt the
    hot-reload pattern (receive new config via NATS, reinitialize internal
    state, continue serving) and this function becomes unnecessary. The
    methods, procedures, and logic are the code — session parameters are
    just variables. See PRD §3.3 "Platform Service Lifecycle."
    """
    apps_v1 = _get_apps_v1()

    annotation_value = config_hash or datetime.now(UTC).isoformat()

    failures: list[str] = []
    for label in [
        "app=nodalarc-ome",
        "app=nodalarc-scheduler",
        "app=nodalarc-nodalpath",
    ]:
        deployments = apps_v1.list_namespaced_deployment(namespace, label_selector=label)
        for deploy in deployments.items:
            body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "nodalarc.io/config-hash": annotation_value,
                            }
                        }
                    }
                }
            }
            try:
                apps_v1.patch_namespaced_deployment(deploy.metadata.name, namespace, body)
                log.info("Rolling restart triggered for %s", deploy.metadata.name)
            except kubernetes.client.rest.ApiException as exc:
                failures.append(f"{deploy.metadata.name}: {exc}")
    if failures:
        raise RuntimeError("Failed to restart platform deployment(s): " + "; ".join(failures))


def _pod_runtime_proof(
    v1: kubernetes.client.CoreV1Api,
    *,
    namespace: str,
    pod_name: str,
) -> RuntimeConfigProof:
    response = v1.connect_get_namespaced_pod_proxy_with_path(
        f"{pod_name}:8081",
        namespace,
        "readyz",
        _preload_content=False,
        _request_timeout=5,
    )
    payload = getattr(response, "data", response)
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("runtime readiness response is not an object")
    if payload.get("status") != "ready":
        raise ValueError("runtime service did not report ready")
    return RuntimeConfigProof.model_validate(payload.get("proof"), strict=True)


def check_platform_runtime_ready(
    namespace: str,
    runtime_hash: str,
    expected_content_proof: RuntimeConfigProof,
    deployment_context: RuntimeDeploymentContext,
) -> tuple[bool, str]:
    """Accept only current OME/Scheduler pods proving this CR generation."""
    if not runtime_hash:
        raise ValueError("runtime_hash is required")
    if expected_content_proof.deployment_identity_bound:
        raise ValueError("expected_content_proof must be pre-deployment evidence")
    if not isinstance(deployment_context, RuntimeDeploymentContext):
        raise TypeError("deployment_context must be a RuntimeDeploymentContext")
    apps_v1 = _get_apps_v1()
    v1 = _get_v1()
    for label, service, origin in (
        ("app=nodalarc-ome", "OME", "ome"),
        ("app=nodalarc-scheduler", "Scheduler", "scheduler"),
    ):
        deployments = apps_v1.list_namespaced_deployment(namespace, label_selector=label)
        items = list(getattr(deployments, "items", None) or [])
        if len(items) != 1:
            return False, f"Waiting for exactly one {service} Deployment"
        deployment = items[0]
        metadata = deployment.metadata
        spec = deployment.spec
        status = deployment.status
        annotations = dict(getattr(spec.template.metadata, "annotations", None) or {})
        if annotations.get("nodalarc.io/config-hash") != runtime_hash:
            return False, f"Waiting for {service} runtime template update"
        generation = int(getattr(metadata, "generation", 0) or 0)
        observed_generation = int(getattr(status, "observed_generation", 0) or 0)
        if generation <= 0 or observed_generation < generation:
            return False, f"Waiting for {service} rollout observation"
        desired = int(getattr(spec, "replicas", 1) or 1)
        replicas = int(getattr(status, "replicas", 0) or 0)
        updated = int(getattr(status, "updated_replicas", 0) or 0)
        ready = int(getattr(status, "ready_replicas", 0) or 0)
        available = int(getattr(status, "available_replicas", 0) or 0)
        unavailable = int(getattr(status, "unavailable_replicas", 0) or 0)
        terminating = int(getattr(status, "terminating_replicas", 0) or 0)
        if (
            replicas != desired
            or updated != desired
            or ready != desired
            or available != desired
            or unavailable != 0
            or terminating != 0
        ):
            return False, f"Waiting for {service} proof-gated readiness ({ready}/{desired})"
        pods = v1.list_namespaced_pod(namespace, label_selector=label)
        current_pods = []
        for pod in list(getattr(pods, "items", None) or []):
            pod_metadata = getattr(pod, "metadata", None)
            pod_annotations = dict(getattr(pod_metadata, "annotations", None) or {})
            if getattr(pod_metadata, "deletion_timestamp", None) is not None:
                return False, f"Waiting for retired {service} runtime pod deletion"
            if pod_annotations.get("nodalarc.io/config-hash") != runtime_hash:
                return False, f"Waiting for retired {service} runtime pod replacement"
            current_pods.append(pod)
        if len(current_pods) != desired:
            return (
                False,
                f"Waiting for {service} current runtime pods ({len(current_pods)}/{desired})",
            )
        for pod in current_pods:
            pod_metadata = getattr(pod, "metadata", None)
            pod_name = str(getattr(pod_metadata, "name", "") or "")
            pod_uid = str(getattr(pod_metadata, "uid", "") or "")
            if not pod_name or not pod_uid:
                return False, f"Waiting for {service} pod identity"
            expected = RuntimeConfigProof.model_validate(
                {
                    **expected_content_proof.model_dump(mode="json"),
                    "source_origin": origin,
                },
                strict=True,
            ).bind_deployment_identity(deployment_context, pod_uid=pod_uid)
            try:
                observed = _pod_runtime_proof(
                    v1,
                    namespace=namespace,
                    pod_name=pod_name,
                )
            except Exception as exc:
                return False, f"Waiting for {service} runtime proof ({type(exc).__name__})"
            if observed != expected:
                return False, f"Waiting for {service} runtime proof to match current deployment"
    return True, "OME and Scheduler runtime configuration verified"


def teardown_session(namespace: str, session_ids: Sequence[str]) -> None:
    """Purge every deployed run id, then clean up the session ConfigMaps.

    Pods are garbage-collected through their owner references. ``session_ids``
    are the run ids the caller proved from the owned resources
    (``owned_session_run_ids``); an empty sequence means nothing was deployed
    and only the ConfigMap sweep runs.
    """
    v1 = _get_v1()
    log.info("Teardown run ids: %s", ", ".join(session_ids) or "(none)")

    # Purge retained runtime state before deleting the ConfigMaps that can be
    # used to rediscover the session identity on retry. If NATS is unavailable,
    # the delete finalizer must be retryable with the same run ids.
    for session_id in session_ids:
        purge_session_runtime_state(namespace, session_id)

    # Delete session-level ConfigMaps
    for cm_name in [
        "nodalarc-session",
        "nodalarc-constellation",
        "nodalarc-ground-stations",
        "nodalarc-pod-ips",
        WIRING_MANIFEST_CONFIGMAP,
        WIRING_STATUS_CONFIGMAP,
    ]:
        _delete_configmap_or_absent(v1, cm_name, namespace)


def _delete_configmap_or_absent(v1: kubernetes.client.CoreV1Api, name: str, namespace: str) -> None:
    """Delete one ConfigMap; absence is fine, any other API failure propagates.

    Teardown runs under the delete finalizer: a failed delete that is only
    logged would let the finalizer release with cleanup incomplete.
    """
    try:
        v1.delete_namespaced_config_map(name, namespace)
        log.debug("Deleted ConfigMap %s", name)
    except kubernetes.client.rest.ApiException as exc:
        if exc.status == 404:
            return
        log.error("Failed to delete ConfigMap %s during teardown: %s", name, exc)
        raise


def _is_missing_stream_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "stream not found" in text or "stream not found" in repr(exc).lower()


def purge_session_runtime_state(namespace: str, session_id: str) -> None:
    """Purge retained JetStream messages for a fresh session lineage.

    This is required cleanup. If NATS cannot confirm the purge, the operator
    must fail the session transition instead of letting OME or Scheduler start
    against retained state from an earlier run.
    """
    try:
        import asyncio

        import nats
        from nodalarc.nats_channels import (
            NATS_CONNECT_OPTIONS,
            nats_url,
        )

        async def _purge():
            nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
            try:
                js = nc.jetstream()
                failures: list[str] = []
                for stream, subject_filter in session_purge_filters(session_id):
                    try:
                        await js.purge_stream(stream, subject=subject_filter)
                        log.debug("Purged %s in %s: %s", stream, namespace, subject_filter)
                    except Exception as exc:
                        if _is_missing_stream_error(exc):
                            log.debug(
                                "Skipping absent JetStream stream %s while purging %s",
                                stream,
                                subject_filter,
                            )
                            continue
                        failures.append(f"{stream} {subject_filter}: {exc}")
                if failures:
                    raise RuntimeError("; ".join(failures))
            finally:
                await nc.close()

        asyncio.run(_purge())
    except Exception as exc:
        log.error("FATAL: Failed to purge JetStream session subjects for %s: %s", session_id, exc)
        raise


def check_wiring_complete(namespace: str, expected_count: int) -> tuple[bool, int, str | None]:
    """Check whether Node Agent wiring is complete.

    Reads the topology manifest and the nodalarc-wiring-status ConfigMap,
    then counts only typed node status entries that are ready for the active
    session and wiring generation. Metadata keys such as _session_id and
    _wiring_generation are not node status entries.

    Returns (complete, wired_count, progress_msg) where:
      - complete: True if wired_count == expected_count
      - wired_count: number of current-generation ready node entries
      - progress_msg: a global progress message, or None

    Returns (False, 0, None) if the ConfigMap does not exist (404).
    Raises on malformed, dirty, failed, or impossible status.

    Pure query — no side effects.
    """
    from nodalarc.substrate.manifest_contract import (
        WiringManifestPayloadError,
        decode_wiring_manifest,
    )
    from nodalarc.substrate.wiring_status import failed_status_summary, parse_status_configmap

    v1 = _get_v1()
    manifest_cm = v1.read_namespaced_config_map(WIRING_MANIFEST_CONFIGMAP, namespace)
    try:
        manifest = decode_wiring_manifest(manifest_cm.data)
    except WiringManifestPayloadError as exc:
        if exc.stage == "missing":
            raise ValueError("topology wiring manifest payload is missing") from exc
        raise ValueError(f"topology wiring manifest payload is invalid: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"topology wiring manifest payload is invalid: {exc}") from exc

    if len(manifest.nodes) != expected_count:
        raise ValueError(
            f"topology wiring manifest has {len(manifest.nodes)} nodes, expected {expected_count}"
        )

    try:
        cm = v1.read_namespaced_config_map(WIRING_STATUS_CONFIGMAP, namespace)
        data = dict(cm.data) if cm.data else {}
        status_session_id, status_generation, statuses = parse_status_configmap(data)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return False, 0, None
        raise

    if not statuses:
        return False, 0, None

    if status_session_id != manifest.session_id or status_generation != manifest.wiring_generation:
        return (
            False,
            0,
            "Wiring status belongs to an old session or generation; waiting for current Node Agent status",
        )

    manifest_node_ids = set(manifest.nodes)
    status_node_ids = set(statuses)
    unknown = status_node_ids - manifest_node_ids
    if unknown:
        raise ValueError(
            "wiring status contains unknown node entries: " + ", ".join(sorted(unknown)[:10])
        )

    failed = [
        node_id
        for node_id, status in statuses.items()
        if status.status in {"failed", "dirty_kernel"} or status.dirty_kernel
    ]
    if failed:
        raise ValueError(failed_status_summary(statuses, node_ids=manifest_node_ids))

    mismatched = [node_id for node_id, status in statuses.items() if status.node_id != node_id]
    if mismatched:
        raise ValueError(
            "wiring status node_id/key mismatch for: " + ", ".join(sorted(mismatched)[:10])
        )

    ready_count = sum(
        1
        for node_id in manifest.nodes
        if (status := statuses.get(node_id)) is not None and status.ready_for(manifest)
    )
    return ready_count == expected_count, ready_count, None


def _canonical_hash_value(value: Any) -> Any:
    """Convert resolved runtime objects into deterministic JSON primitives."""
    if hasattr(value, "model_dump"):
        return _canonical_hash_value(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_hash_value(getattr(value, field.name)) for field in fields(value)
        }
    if hasattr(value, "_asdict"):
        return {str(k): _canonical_hash_value(v) for k, v in value._asdict().items()}
    if isinstance(value, Mapping):
        return {str(k): _canonical_hash_value(v) for k, v in sorted(value.items())}
    if isinstance(value, set | frozenset):
        items = [_canonical_hash_value(v) for v in value]
        return sorted(
            items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
        )
    if isinstance(value, tuple | list):
        return [_canonical_hash_value(v) for v in value]
    return value


def compute_platform_hash(
    spec: dict,
    *,
    active_session: ResolvedRuntimeConfig | None = None,
    namespace: str = "nodalarc",
) -> str:
    """Hash resolved runtime truth for service restart detection.

    OME, Scheduler, and NodalPath currently load session truth at startup. Any
    user-authored YAML field or referenced catalog asset that can affect runtime
    computation must therefore change this hash and trigger a platform-pod
    restart. Hashing the raw segment YAML is insufficient because a session can
    referenced orbit, node, terminal, constellation, site, or site-set files whose
    contents can change while the reference string stays fixed.

    The only excluded fields are operator-owned runtime lineage/context
    (``session.run_id`` and ``source_context``). Everything else comes from the
    resolver-owned runtime model and resolved assets.

    restart_platform_pods uses this hash as a Deployment annotation. A changed
    hash triggers a rolling restart so OME/Scheduler pick up the new session
    configuration and publish to the correct NATS subjects.

    Returns a hex digest string (SHA-256).
    """
    operator_session = _operator_session_config(
        spec,
        active_session,
        namespace=namespace,
        origin="operator.platform_hash",
    )
    canonical_obj = {
        # The resolved model carries every node's effective profile, so a
        # changed workload statement invalidates cached-session reuse exactly
        # like changed session truth.
        "resolved": operator_session.resolution.resolved.model_dump(
            mode="json",
            exclude={"source_context": True},
        ),
        "runtime_config": _proof_hash_fields(operator_session.proof),
    }
    canonical = json.dumps(canonical_obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_runtime_hash(
    platform_hash: str,
    session_run_id: str,
    proof: RuntimeConfigProof | None = None,
    deployment_context: RuntimeDeploymentContext | None = None,
) -> str:
    """Hash platform config plus immutable runtime lineage for pod restarts."""
    if not platform_hash:
        raise ValueError("platform_hash is required")
    if not session_run_id:
        raise ValueError("session_run_id is required")
    payload: dict[str, Any] = {
        "platform_hash": platform_hash,
        "session_run_id": session_run_id,
    }
    if proof is not None:
        payload["runtime_config"] = _proof_hash_fields(proof)
    if deployment_context is not None:
        if deployment_context.session_run_id != session_run_id:
            raise ValueError("deployment_context has the wrong session run ID")
        payload["deployment_context"] = deployment_context.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def write_pod_ips_configmap(namespace: str, pod_ips: Mapping[str, str]) -> None:
    """Write nodalarc-pod-ips ConfigMap from the current session pods' addresses.

    ``pod_ips`` comes from the reconciliation's session-pod observation. Stores
    the map as a single 'pod-ips.json' key so it can be volume-mounted directly
    as a JSON file by the NodalPath Deployment.
    """
    v1 = _get_v1()
    ip_map = dict(sorted(pod_ips.items()))
    data = {"pod-ips.json": json.dumps(ip_map)}
    _create_or_update_configmap(v1, "nodalarc-pod-ips", namespace, data, owner_ref=None)
    log.info("Wrote nodalarc-pod-ips with %d entries", len(ip_map))


# ---------------------------------------------------------------------------
# SSH terminal access
# ---------------------------------------------------------------------------

TERMINAL_SSH_KEY_RESOURCE_NAME = "nodalarc-terminal-keys"


def _create_terminal_ssh_keys(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    owner_ref: dict | None,
) -> None:
    """Ensure an ED25519 SSH keypair exists in a K8s Secret.

    The public key is mounted into session pods for SSH authorized_keys.
    The private key is read by the VS-API to SSH into pods for terminal proxy.
    Owner reference ties the Secret lifecycle to the ConstellationSpec CR —
    teardown deletes the Secret automatically.

    This function is intentionally create-if-missing. Reconciliation may refresh
    ConfigMaps for an already-running session, and that must not rotate terminal
    credentials underneath existing pods.
    """
    import subprocess
    import tempfile

    try:
        existing = v1.read_namespaced_secret(TERMINAL_SSH_KEY_RESOURCE_NAME, namespace)
        if _terminal_secret_reusable(existing, owner_ref):
            log.debug(
                "Terminal SSH keypair already exists (Secret: %s)", TERMINAL_SSH_KEY_RESOURCE_NAME
            )
            return
        raise RetryableSessionDependency(
            f"Existing {TERMINAL_SSH_KEY_RESOURCE_NAME} Secret is not owned by the current "
            "ConstellationSpec or is already deleting; waiting for Kubernetes garbage collection"
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status != 404:
            raise

    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = f"{tmpdir}/id_ed25519"
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-q"],
            check=True,
        )
        private_key = Path(key_path).read_text()
        public_key = Path(f"{key_path}.pub").read_text().strip()

    body = kubernetes.client.V1Secret(
        metadata=kubernetes.client.V1ObjectMeta(
            name=TERMINAL_SSH_KEY_RESOURCE_NAME,
            namespace=namespace,
            labels={"nodalarc.io/managed-by": "operator"},
            owner_references=[owner_ref] if owner_ref else None,
        ),
        string_data={
            "id_ed25519": private_key,
            "id_ed25519.pub": public_key,
        },
    )
    try:
        v1.create_namespaced_secret(namespace, body)
        log.info("Terminal SSH keypair created (Secret: %s)", TERMINAL_SSH_KEY_RESOURCE_NAME)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:
            log.debug(
                "Terminal SSH keypair already exists after create race (Secret: %s)",
                TERMINAL_SSH_KEY_RESOURCE_NAME,
            )
        else:
            raise


def _terminal_secret_reusable(secret: Any, owner_ref: dict | None) -> bool:
    """Return True when an existing terminal Secret belongs to this CR.

    Reusing a still-owned Secret avoids rotating SSH keys during an ordinary
    reconcile. Reusing a Secret owned by a deleted/replaced CR is not safe: the
    Kubernetes garbage collector can remove it after new pods have already
    mounted the key.
    """
    metadata = getattr(secret, "metadata", None)
    if metadata is None:
        return False
    if getattr(metadata, "deletion_timestamp", None):
        return False
    if owner_ref is None:
        return True

    expected_uid = str(owner_ref.get("uid") or "")
    expected_name = str(owner_ref.get("name") or "")
    if not expected_uid or not expected_name:
        return False

    for ref in getattr(metadata, "owner_references", None) or []:
        ref_uid = str(getattr(ref, "uid", "") or "")
        ref_name = str(getattr(ref, "name", "") or "")
        if ref_uid == expected_uid and ref_name == expected_name:
            return True
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# _spec_to_session_yaml and _build_area_assignment removed.
# The CRD carries spec.sessionFile — the Operator reads it directly.


def _create_or_update_configmap(
    v1: kubernetes.client.CoreV1Api,
    name: str,
    namespace: str,
    data: dict[str, str],
    owner_ref: dict | None,
) -> None:
    """Create or update a ConfigMap."""
    labels = {"nodalarc.io/managed-by": "operator"}

    body = kubernetes.client.V1ConfigMap(
        metadata=kubernetes.client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels=labels,
            owner_references=[owner_ref] if owner_ref else None,
        ),
        data=data,
    )
    try:
        v1.create_namespaced_config_map(namespace, body)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:  # Already exists — update
            v1.replace_namespaced_config_map(name, namespace, body)
        else:
            raise


def _create_session_configmaps(
    v1: kubernetes.client.CoreV1Api,
    resolved: ResolvedSession,
    session_yaml: str,
    catalog_upload: CatalogUploadSelection,
    deployment_context: RuntimeDeploymentContext,
    namespace: str,
    owner_ref: dict,
) -> None:
    """Create session-level ConfigMaps with segment session YAML."""
    data = build_runtime_session_config_data(
        resolved,
        session_yaml,
        catalog_upload,
        deployment_context,
    )
    _create_or_update_configmap(
        v1,
        "nodalarc-session",
        namespace,
        data,
        owner_ref,
    )
    log.debug("Created session-level ConfigMap")


def build_runtime_session_config_data(
    resolved: ResolvedSession,
    session_yaml: str,
    catalog_upload: CatalogUploadSelection,
    deployment_context: RuntimeDeploymentContext,
) -> dict[str, str]:
    """Return the exact mounted runtime inputs for one deployment identity."""
    if not isinstance(catalog_upload, CatalogUploadSelection):
        raise TypeError("catalog_upload must be a CatalogUploadSelection")
    session_run_id = require_resolved_session_run_id(resolved)
    mismatches = deployment_context.mounted_input_mismatches(
        run_id=session_run_id,
        selection=catalog_upload,
        root_yaml=session_yaml.encode("utf-8"),
    )
    if mismatches:
        raise ValueError(
            "deployment_context differs from the runtime session inputs: " + ", ".join(mismatches)
        )
    return {
        SESSION_YAML_FILENAME: session_yaml,
        SESSION_RUN_ID_FILENAME: session_run_id,
        CATALOG_UPLOAD_SELECTION_FILENAME: canonical_json_bytes(
            catalog_upload.model_dump(mode="json")
        ).decode("utf-8"),
        RUNTIME_DEPLOYMENT_CONTEXT_FILENAME: canonical_json_bytes(
            deployment_context.model_dump(mode="json")
        ).decode("utf-8"),
    }


def _create_pod_with_conflict_check(
    v1: kubernetes.client.CoreV1Api,
    pod: kubernetes.client.V1Pod,
    namespace: str,
    pod_name: str,
    pod_identity: SessionPodIdentity,
) -> None:
    """The one create/409 path every session pod uses."""
    try:
        v1.create_namespaced_pod(namespace, pod)
    except kubernetes.client.rest.ApiException as e:
        if e.status != 409:
            raise
        existing = v1.read_namespaced_pod(pod_name, namespace)
        if pod_identity.classify(existing) is PodClass.CURRENT:
            log.info("Pod %s already exists with the desired workload and run", pod_name)
            return
        deletion = delete_conflicting_pod(v1, namespace, existing, pod_identity)
        log.info(
            "Existing pod %s blocks creation: its run, owner or workload differs from "
            "the desired session; deletion %s, reconciliation reobserves",
            pod_name,
            deletion.outcome,
        )


def _create_workload_pod(
    v1: kubernetes.client.CoreV1Api,
    pod_name: str,
    namespace: str,
    node_id: str,
    node_type: str,
    plane: int | None,
    slot: int | None,
    gs_name: str | None,
    composed: Any,
    owner_ref: dict,
    pod_identity: SessionPodIdentity,
    target_node: str | None = None,
) -> None:
    """Create one explicitly selected workload pod.

    The composition was produced before any write; this function only
    assembles through the shared materializer and creates through the same
    conflict path as the built-in producer.
    """
    extra_labels: dict[str, str] = {}
    if plane is not None:
        extra_labels["nodalarc.io/plane"] = str(plane)
    if slot is not None:
        extra_labels["nodalarc.io/slot"] = str(slot)
    if gs_name:
        extra_labels["nodalarc.io/gs-name"] = gs_name
    pod = build_session_pod(
        pod_name=pod_name,
        namespace=namespace,
        node_id=node_id,
        role=node_type.replace("_", "-"),
        session_id=pod_identity.run_label,
        owner_ref=owner_ref,
        composition=composed.composition,
        selection_identity=pod_identity.selection_identity,
        terminal_access=composed.terminal_access,
        target_node=target_node,
        extra_labels=extra_labels,
    )
    _create_pod_with_conflict_check(v1, pod, namespace, pod_name, pod_identity)
