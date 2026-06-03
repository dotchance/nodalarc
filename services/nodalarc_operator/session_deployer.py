# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Session deployer — renders configs, creates pods and ConfigMaps.

Replicates na_deploy.py Steps 3-5 using the K8s Python client.
Called by kopf handlers in handlers.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import kubernetes
import yaml
from jinja2 import Environment, FileSystemLoader
from nodalarc.constellation_loader import satellite_node_id
from nodalarc.ground_terminals import station_ground_terminal_capacity
from nodalarc.models.addressing import neighbors_by_node
from nodalarc.models.resolved_session import SourceContext
from nodalarc.models.session import PlacementConfig, SessionConfig
from nodalarc.nats_channels import sanitize_session_id
from nodalarc.resolve_session import resolve_session_with_assets
from nodalarc.session_identity import require_session_run_id
from nodalarc.stack_resolver import resolve_stack
from nodalarc.template_vars import build_template_vars

log = logging.getLogger(__name__)

SESSION_POD_SELECTOR = "nodalarc.io/node-id"
POD_SESSION_RUN_LABEL = "nodalarc.io/session-run-id"
POD_OWNER_UID_LABEL = "nodalarc.io/owner-uid"


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


def _metadata(obj: Any) -> Any:
    return getattr(obj, "metadata", None)


def _labels(obj: Any) -> dict[str, str]:
    metadata = _metadata(obj)
    return dict(getattr(metadata, "labels", None) or {})


def _pod_node_id(pod: Any) -> str:
    labels = _labels(pod)
    node_id = str(labels.get("nodalarc.io/node-id") or "")
    if node_id:
        return node_id.lower()
    metadata = _metadata(pod)
    return str(getattr(metadata, "name", "") or "").lower()


def _pod_deleting(pod: Any) -> bool:
    metadata = _metadata(pod)
    return bool(getattr(metadata, "deletion_timestamp", None))


def _owner_ref_field(ref: Any, field: str) -> str:
    if isinstance(ref, dict):
        return str(ref.get(field) or "")
    return str(getattr(ref, field, "") or "")


def _pod_owned_by(pod: Any, owner_ref: dict | None) -> bool:
    if owner_ref is None:
        return False
    expected_uid = str(owner_ref.get("uid") or "")
    expected_name = str(owner_ref.get("name") or "")
    if not expected_uid or not expected_name:
        return False
    metadata = _metadata(pod)
    for ref in getattr(metadata, "owner_references", None) or []:
        if (
            _owner_ref_field(ref, "uid") == expected_uid
            and _owner_ref_field(ref, "name") == expected_name
        ):
            return True
    return False


def _pod_current_for_runtime(pod: Any, session_id: str, owner_ref: dict | None) -> bool:
    labels = _labels(pod)
    return (
        not _pod_deleting(pod)
        and labels.get(POD_SESSION_RUN_LABEL) == session_id
        and _pod_owned_by(pod, owner_ref)
    )


def _list_session_pods(v1: kubernetes.client.CoreV1Api, namespace: str) -> list[Any]:
    return list(v1.list_namespaced_pod(namespace, label_selector=SESSION_POD_SELECTOR).items)


def _patch_pod_runtime_identity(
    v1: kubernetes.client.CoreV1Api,
    pod: Any,
    namespace: str,
    session_id: str,
    owner_ref: dict,
) -> None:
    metadata = _metadata(pod)
    pod_name = str(getattr(metadata, "name", "") or "")
    if not pod_name:
        raise ValueError("Cannot patch unnamed session pod")
    body = {
        "metadata": {
            "labels": {
                POD_SESSION_RUN_LABEL: session_id,
                POD_OWNER_UID_LABEL: str(owner_ref.get("uid") or ""),
            }
        }
    }
    v1.patch_namespaced_pod(pod_name, namespace, body)


def ensure_session_pod_identity(
    namespace: str,
    expected_ids: set[str] | frozenset[str],
    session_id: str,
    owner_ref: dict,
) -> int:
    """Stamp same-CR session pods with the current runtime identity.

    A CR generation change may intentionally reuse running pods while refreshing
    ConfigMaps and rewiring. Pods from a different CR UID are never adopted.
    """
    v1 = _get_v1()
    expected = {node_id.lower() for node_id in expected_ids}
    patched = 0
    for pod in _list_session_pods(v1, namespace):
        if _pod_node_id(pod) not in expected:
            continue
        if _pod_deleting(pod) or not _pod_owned_by(pod, owner_ref):
            continue
        labels = _labels(pod)
        if labels.get(POD_SESSION_RUN_LABEL) != session_id or labels.get(
            POD_OWNER_UID_LABEL
        ) != str(owner_ref.get("uid") or ""):
            _patch_pod_runtime_identity(v1, pod, namespace, session_id, owner_ref)
            patched += 1
    if patched:
        log.info("Stamped %d session pods with runtime identity %s", patched, session_id)
    return patched


def current_session_pod_node_ids(
    namespace: str,
    session_id: str,
    owner_ref: dict,
) -> set[str]:
    """Return node IDs for pods owned by this CR and stamped with this run ID."""
    v1 = _get_v1()
    return {
        _pod_node_id(pod)
        for pod in _list_session_pods(v1, namespace)
        if _pod_current_for_runtime(pod, session_id, owner_ref)
    }


def count_stale_session_pods(
    namespace: str,
    expected_ids: set[str] | frozenset[str],
    session_id: str,
    owner_ref: dict,
) -> int:
    """Count expected-name pods that cannot belong to the active runtime."""
    v1 = _get_v1()
    expected = {node_id.lower() for node_id in expected_ids}
    stale = 0
    for pod in _list_session_pods(v1, namespace):
        if _pod_node_id(pod) not in expected:
            continue
        if not _pod_current_for_runtime(pod, session_id, owner_ref):
            stale += 1
    return stale


def discover_available_nodes() -> list[str]:
    """Discover K3s nodes available for session pods.

    Returns node names that have the nodalarc.io/node-agent=true label
    and do not have the nodalarc.io/not-ready taint.
    """
    v1 = _get_v1()
    nodes = v1.list_node(label_selector="nodalarc.io/node-agent=true")
    available = []
    for node in nodes.items:
        taints = node.spec.taints or []
        blocked = any(t.key == "nodalarc.io/not-ready" and t.effect == "NoSchedule" for t in taints)
        if not blocked:
            available.append(node.metadata.name)
    return sorted(available)


def _deterministic_node(nid: str, available_nodes: list[str]) -> str:
    """Rendezvous (HRW) hash — minimal migration on node-set changes.

    For each candidate node, compute weight = SHA256(nid:node).
    Assign to the highest-weight node. Adding the Nth node migrates
    only ~1/N pods. Removing a node migrates only its pods.
    """
    best_node = available_nodes[0]
    best_weight = -1
    for node in available_nodes:
        w = int(hashlib.sha256(f"{nid}:{node}".encode()).hexdigest()[:8], 16)
        if w > best_weight:
            best_weight = w
            best_node = node
    return best_node


def compute_pod_placement(
    placement: PlacementConfig,
    node_vars: dict[str, dict],
    available_nodes: list[str],
) -> dict[str, str]:
    """Compute target node for each pod based on placement policy.

    Args:
        placement: PlacementConfig from session YAML.
        node_vars: {node_id: {node_type, plane, ...}} from template_vars.
        available_nodes: sorted list of available K3s node names.

    Returns:
        {node_id: k3s_node_name} mapping.
    """
    if not available_nodes:
        raise ValueError("No available K3s nodes for pod placement")

    if placement.policy == "allOnOne":
        target = available_nodes[0]
        return dict.fromkeys(node_vars, target)

    if placement.policy == "planePerNode":
        result: dict[str, str] = {}
        for nid, vars in node_vars.items():
            if vars.get("node_type") == "ground_station":
                result[nid] = _deterministic_node(nid, available_nodes)
            else:
                plane = vars.get("plane", 0)
                result[nid] = available_nodes[plane % len(available_nodes)]
        return result

    if placement.policy == "planeGroupPerNode":
        ppg = placement.planes_per_group
        result = {}
        for nid, vars in node_vars.items():
            if vars.get("node_type") == "ground_station":
                result[nid] = _deterministic_node(nid, available_nodes)
            else:
                plane = vars.get("plane", 0)
                group = plane // ppg
                result[nid] = available_nodes[group % len(available_nodes)]
        return result

    raise ValueError(f"Unknown placement policy: {placement.policy}")


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


def _discover_session_pod_placement(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    expected_node_ids: set[str],
) -> dict[str, str]:
    """Read actual session pod placement from Running pod specs."""
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    placement: dict[str, str] = {}
    duplicates: list[str] = []
    for pod in pods.items:
        labels = pod.metadata.labels or {}
        node_id = labels.get("nodalarc.io/node-id", "")
        if node_id not in expected_node_ids:
            continue
        k8s_node = pod.spec.node_name or ""
        if not k8s_node:
            raise ValueError(f"session pod {pod.metadata.name} has no Kubernetes node assignment")
        if node_id in placement and placement[node_id] != k8s_node:
            duplicates.append(node_id)
        placement[node_id] = k8s_node
    if duplicates:
        raise ValueError("duplicate session pod placement for: " + ", ".join(sorted(duplicates)))
    missing = sorted(expected_node_ids - set(placement))
    if missing:
        raise ValueError(
            "missing session pod placement for manifest nodes: " + ", ".join(missing[:20])
        )
    return placement


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

    ground_ids = sorted(
        node_id for node_id, spec in nodes.items() if spec["node_type"] == "ground_station"
    )
    for gs_id in ground_ids:
        candidate_sat_ids = (
            ground_candidate_satellites_by_gs.get(gs_id, ())
            if ground_candidate_satellites_by_gs is not None
            else tuple(
                node_id for node_id, spec in nodes.items() if spec["node_type"] == "satellite"
            )
        )
        if not candidate_sat_ids:
            raise ValueError(f"ground station {gs_id!r} has no substrate candidate satellites")
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


def _delete_stale_substrate_status_configmaps(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
) -> None:
    """Remove old substrate status documents before publishing a new manifest."""
    from nodalarc.substrate.measurement_contract import (
        STATUS_CONFIGMAP_LABEL_KEY,
        STATUS_CONFIGMAP_LABEL_VALUE,
    )

    try:
        cms = v1.list_namespaced_config_map(
            namespace,
            label_selector=f"{STATUS_CONFIGMAP_LABEL_KEY}={STATUS_CONFIGMAP_LABEL_VALUE}",
        )
        for cm in getattr(cms, "items", []) or []:
            name = cm.metadata.name
            if name:
                v1.delete_namespaced_config_map(name, namespace)
                log.debug("Deleted stale substrate status ConfigMap %s", name)
    except kubernetes.client.rest.ApiException as exc:
        if exc.status != 404:
            raise


# All known FRR daemons — used to generate the daemons file
_ALL_FRR_DAEMONS = [
    "mgmtd",
    "zebra",
    "bgpd",
    "ospfd",
    "ospf6d",
    "ripd",
    "ripngd",
    "isisd",
    "pimd",
    "ldpd",
    "nhrpd",
    "eigrpd",
    "babeld",
    "sharpd",
    "pbrd",
    "bfdd",
    "fabricd",
    "vrrpd",
    "pathd",
    "staticd",
]


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
) -> dict:
    """Create/update all ConfigMaps and SSH keys for a session.

    Runs steps 1-10 of the deploy pipeline: parse session, load constellation,
    resolve stack, validate, render FRR configs, create ConfigMaps, generate
    SSH keypair, compute pod placement.

    Idempotent — ConfigMaps use create-or-update, SSH key uses create-or-replace.
    Safe to call repeatedly; only writes what's missing or changed.

    Args:
        spec: The CR's .spec dict.
        name: CR metadata.name.
        namespace: K8s namespace.
        owner_ref: ownerReferences entry for garbage collection.
        progress_fn: Optional callback(message: str) for status updates.

    Returns:
        Context dict with keys: session_id, session_run_id, session, constellation,
        satellites, gs_file, resolved_stack, node_vars, pod_placement,
        available_nodes. Passed to ensure_session_pods().
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
    session_yaml = spec.get("sessionYaml")
    if not session_yaml:
        raise ValueError("spec.sessionYaml is required")
    raw_session = yaml.safe_load(session_yaml)
    resolution = resolve_session_with_assets(
        raw_session,
        source_context=SourceContext(origin="operator.deploy"),
    )
    session = resolution.runtime_session
    if session.session.run_id:
        raise ValueError("session.run_id is operator-managed and must not be set in session YAML")
    if not session_run_id:
        raise ValueError("session_run_id is required to create runtime session ConfigMaps")
    session_id = sanitize_session_id(session_run_id)

    # --- Step 2: Use resolver-owned constellation and ground assets ---
    _progress("Using resolved constellation and ground station definitions")
    constellation = resolution.runtime_constellation
    gs_file = resolution.primary_ground_set.config
    satellites = list(resolution.satellites)
    num_planes = max((s.plane for s in satellites), default=0) + 1
    _progress(
        f"Expanded {len(satellites)} satellites across {num_planes} planes, {len(gs_file.stations)} ground stations"
    )
    if not satellites:
        raise ValueError("No satellites in constellation")

    addressing = resolution.addressing
    neighbors = resolution.neighbors

    # --- Step 3: Resolve routing stack ---
    _progress(f"Resolving routing stack: {session.routing.protocol}")
    resolved = resolve_stack(
        session.routing.protocol,
        session.routing.extensions,
    )
    config_overrides = dict(resolved.template_variables)
    config_overrides.update(session.routing.config_overrides)

    # --- Step 3b: Validate session readiness ---
    _progress("Validating session readiness")
    from nodalarc.session_validator import validate_session_readiness

    validation_results = validate_session_readiness(
        session,
        constellation,
        satellites,
        gs_file,
        resolved,
        available_node_count=len(available_nodes),
    )
    val_errors = [r for r in validation_results if r.level == "error"]
    val_warnings = [r for r in validation_results if r.level == "warning"]
    for w in val_warnings:
        log.warning("Session validation %s: %s", w.code, w.message)
    if validation_results:
        _publish_validation_ops_events(validation_results, namespace, session_id=session_run_id)
    if val_errors:
        import kopf

        error_msg = "; ".join(f"[{r.code}] {r.message}" for r in val_errors)
        raise kopf.PermanentError(f"Session validation failed: {error_msg}")

    # --- Step 4: Build template vars per node ---
    total_nodes = len(satellites) + len(gs_file.stations)
    _progress(f"Building template variables for {total_nodes} nodes")
    node_vars: dict[str, dict] = {}
    for sat in satellites:
        node_id = satellite_node_id(sat, addressing)
        node_vars[node_id] = build_template_vars(
            session=session,
            constellation=constellation,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=sat.plane,
            slot=sat.slot,
            sat_node_id=node_id,
            sat_ground_terminal_count=sat.ground_terminal_count,
            config_overrides=config_overrides,
            neighbors=neighbors,
        )
    for i, station in enumerate(gs_file.stations):
        node_id = addressing.gs_id(station.name)
        node_vars[node_id] = build_template_vars(
            session=session,
            constellation=constellation,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="ground_station",
            gs_name=station.name,
            gs_index=i,
            config_overrides=config_overrides,
            neighbors=neighbors,
        )

    # --- Step 5: Render FRR configs (parallelized) ---
    _progress(f"Rendering FRR configurations for {len(node_vars)} nodes")
    template_dir = str(Path("configs/templates/frr").resolve())
    # nosec B701 — these are FRR router config templates, not HTML; autoescape would break config syntax
    env = Environment(loader=FileSystemLoader(template_dir), keep_trailing_newline=True)

    rendered_configs: dict[str, dict[str, str]] = {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _render_one_node(node_id: str, tpl_vars: dict) -> tuple[str, dict[str, str]]:
        configs: dict[str, str] = {}
        for tpl_file in resolved.template_files:
            tpl = env.get_template(tpl_file.src)
            rendered = tpl.render(**tpl_vars)
            dest_name = Path(tpl_file.dst).name
            configs[dest_name] = rendered
        # Generate daemons file
        if resolved.daemons:
            # mgmtd is always required in FRR 10.x — it manages config loading
            enabled = set(resolved.daemons) | {"mgmtd"}
            configs["daemons"] = (
                "\n".join(f"{d}={'yes' if d in enabled else 'no'}" for d in _ALL_FRR_DAEMONS) + "\n"
            )
        # Create unified frr.conf combining all daemon configs.
        # FRR's config parser treats blank lines inside blocks as implicit "exit",
        # so we must strip consecutive blank lines from Jinja2 output.
        frr_conf_parts = []
        for name_key in ("zebra.conf", "isisd.conf", "ospfd.conf", "pathd.conf", "staticd.conf"):
            if name_key in configs:
                frr_conf_parts.append(f"! === {name_key} ===")
                frr_conf_parts.append(configs[name_key])
        if frr_conf_parts:
            raw = "\n".join(frr_conf_parts)
            # Remove blank lines — FRR's config parser interprets blank lines
            # inside interface/router blocks as implicit "exit" commands.
            # Jinja2 {% if %} blocks produce blank lines that break parsing.
            lines = raw.splitlines()
            cleaned_lines = [line for line in lines if line.strip() != ""]
            configs["frr.conf"] = "\n".join(cleaned_lines) + "\n"
        # Config version hash — NOS-agnostic readiness contract.
        # The entrypoint writes this to a sentinel file after loading config.
        # The readiness probe diffs the sentinel against the ConfigMap mount
        # to verify the running NOS has loaded the intended config version.
        if "frr.conf" in configs:
            config_hash = hashlib.sha256(configs["frr.conf"].encode()).hexdigest()[:16]
            configs["_config_version"] = config_hash
        return node_id, configs

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_render_one_node, nid, vars): nid for nid, vars in node_vars.items()}
        for fut in as_completed(futures):
            nid, configs = fut.result()
            rendered_configs[nid] = configs

    # --- Step 6: Create per-node FRR config ConfigMaps ---
    _progress(f"Creating {len(rendered_configs)} FRR config ConfigMaps")
    for node_id, configs in rendered_configs.items():
        cm_name = f"frr-config-{node_id.lower()}"
        _create_or_update_configmap(v1, cm_name, namespace, configs, owner_ref)
    log.info("Created %d FRR config ConfigMaps", len(rendered_configs))

    # --- Step 7: Create session-level ConfigMaps ---
    _progress("Creating session-level ConfigMaps")
    _create_session_configmaps(
        v1,
        session,
        session_yaml,
        namespace,
        owner_ref,
        session_run_id,
    )

    # --- Step 7b: Ensure SSH keypair for terminal access ---
    _progress("Ensuring SSH keypair for terminal access")
    _create_terminal_ssh_keys(v1, namespace, owner_ref)

    # --- Step 8: Compute pod placement ---
    _progress(f"Computing pod placement ({session.placement.policy} policy)")
    pod_placement = compute_pod_placement(session.placement, node_vars, available_nodes)
    node_counts: dict[str, int] = {}
    for target in pod_placement.values():
        node_counts[target] = node_counts.get(target, 0) + 1
    log.info(
        "Placement policy=%s, %d pods across %d nodes: %s",
        session.placement.policy,
        len(pod_placement),
        len(node_counts),
        ", ".join(f"{n}={c}" for n, c in sorted(node_counts.items())),
    )

    return {
        "session_id": session_id,
        "session_run_id": session_run_id,
        "session": session,
        "constellation": constellation,
        "satellites": satellites,
        "gs_file": gs_file,
        "resolved_stack": resolved,
        "node_vars": node_vars,
        "pod_placement": pod_placement,
        "available_nodes": available_nodes,
    }


def ensure_session_pods(
    context: dict,
    namespace: str,
    owner_ref: dict,
    progress_fn: Any | None = None,
) -> int:
    """Create ONLY missing session pods from a prepared context.

    Takes the context dict from ensure_session_configmaps(). Checks which
    pods already exist and creates only the missing ones. Returns the total
    expected pod count (not just created count).

    Idempotent — K8s returns 409 for existing pods, handled as success.

    Args:
        context: Dict from ensure_session_configmaps().
        namespace: K8s namespace.
        owner_ref: ownerReferences entry for garbage collection.
        progress_fn: Optional callback(message: str) for status updates.

    Returns:
        Total expected pod count.
    """

    def _progress(msg: str) -> None:
        log.debug(msg)
        if progress_fn:
            progress_fn(msg)

    v1 = _get_v1()
    node_vars = context["node_vars"]
    pod_placement = context["pod_placement"]
    session = context["session"]
    session_id = context["session_id"]
    resolved = context["resolved_stack"]

    total_pods = len(node_vars)
    _progress(f"Creating {total_pods} session pods")
    sidecar_config = _build_sidecar_config(resolved)
    env_list = resolved.env

    from concurrent.futures import ThreadPoolExecutor, as_completed

    pod_specs: list[dict] = []
    for node_id, vars in node_vars.items():
        pod_specs.append(
            {
                "pod_name": node_id.lower(),
                "node_id": node_id,
                "node_type": vars["node_type"],
                "plane": vars.get("plane"),
                "slot": vars.get("slot"),
                "gs_name": vars.get("gs_name"),
                "config_cm_name": f"frr-config-{node_id.lower()}",
                "sidecar_env": _build_sidecar_env(node_id, vars, env_list)
                if sidecar_config
                else None,
                "probe_enabled": session.mi.enabled if session.mi else False,
                "target_node": pod_placement.get(node_id),
            }
        )

    import threading

    created_pods = 0
    errors = []
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

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {}
        for ps in pod_specs:
            fut = pool.submit(
                _create_session_pod,
                v1=v1,
                pod_name=ps["pod_name"],
                namespace=namespace,
                node_id=ps["node_id"],
                node_type=ps["node_type"],
                plane=ps["plane"],
                slot=ps["slot"],
                gs_name=ps["gs_name"],
                config_cm_name=ps["config_cm_name"],
                sidecar_config=sidecar_config,
                sidecar_env=ps["sidecar_env"],
                probe_enabled=ps["probe_enabled"],
                target_node=ps["target_node"],
                owner_ref=owner_ref,
                session_id=session_id,
            )
            futures[fut] = ps["node_id"]

        for fut in as_completed(futures):
            node_id = futures[fut]
            try:
                fut.result()
                created_pods += 1
                _progress(f"Creating session pods: {created_pods}/{total_pods}")
            except Exception as exc:
                errors.append(f"{node_id}: {exc}")
                log.error("Pod creation failed for %s: %s", node_id, exc)

    _pod_creation_done.set()

    if errors:
        log.error("Pod creation: %d failures out of %d", len(errors), total_pods)
        displayed = "; ".join(errors[:10])
        if len(errors) > 10:
            displayed += f"; ... and {len(errors) - 10} more"
        raise RuntimeError(f"Pod creation failed: {displayed}")
    log.info("Created %d session pods (total expected: %d)", created_pods, total_pods)

    return total_pods


def deploy_session(
    spec: dict,
    name: str,
    namespace: str,
    owner_ref: dict,
    progress_fn: Any | None = None,
    session_run_id: str | None = None,
) -> dict:
    """Deploy a full session from a ConstellationSpec CR spec.

    Convenience wrapper that calls ensure_session_configmaps() followed by
    ensure_session_pods(). Preserves backward compatibility for on_create.

    Args:
        spec: The CR's .spec dict.
        name: CR metadata.name (used for session_id).
        namespace: K8s namespace.
        owner_ref: ownerReferences entry for garbage collection.
        progress_fn: Optional callback(message: str) for status updates.

    Returns:
        Status dict with phase, podCount, readyPods, sessionId, message.
    """
    context = ensure_session_configmaps(
        spec, name, namespace, owner_ref, progress_fn, session_run_id
    )
    total_pods = ensure_session_pods(context, namespace, owner_ref, progress_fn)

    return {
        "phase": "Creating",
        "sessionId": context["session_id"],
        "podCount": total_pods,
        "readyPods": 0,
        "wiredPods": 0,
        "message": f"Created {total_pods} pods, waiting for Running",
    }


def write_wiring_manifest(
    spec: dict,
    namespace: str,
    owner_ref: dict | None = None,
    session_run_id: str | None = None,
) -> int:
    """Generate and write the topology wiring manifest ConfigMap.

    Called after pods are Running. The Node Agent watches this ConfigMap
    and executes all data plane wiring operations.

    Returns the number of ISL links in the manifest.
    """
    import ipaddress as _ipaddress
    import json as _json

    from nodalarc.nats_channels import sanitize_session_id
    from nodalarc.substrate.manifest_contract import (
        REQUIRED_WIRING_PHASES,
        derive_wiring_generation,
    )

    # Resolve session from CRD spec
    session_yaml = spec.get("sessionYaml", "")
    resolution = resolve_session_with_assets(
        yaml.safe_load(session_yaml),
        source_context=SourceContext(origin="operator.wiring_manifest"),
    )
    session = resolution.runtime_session
    if session.session.run_id:
        raise ValueError("session.run_id is operator-managed and must not be set in session YAML")
    if not session_run_id:
        raise ValueError("session_run_id is required to write topology wiring manifest")

    v1 = _get_v1()

    # Delete stale wiring-status before writing new manifest.
    # Without this, the Node Agent sees old wiring-status as "current" and
    # hits Case B (no-op) instead of Case A (wire from scratch).
    try:
        v1.delete_namespaced_config_map("nodalarc-wiring-status", namespace)
        log.debug("Deleted stale nodalarc-wiring-status")
    except kubernetes.client.rest.ApiException as e:
        if e.status != 404:
            raise
    gs_file = resolution.primary_ground_set.config
    satellites = list(resolution.satellites)
    addressing = resolution.addressing
    neighbors = resolution.neighbors
    by_node = neighbors_by_node(neighbors)

    resolved = resolve_stack(session.routing.protocol, session.routing.extensions)
    segment_routing = resolved.segment_routing
    mpls_enable = any(name.startswith("net.mpls.") for name in resolved.sysctls)

    # Validate stack × constellation constraints before building manifest
    from nodalarc.stack_resolver import validate_constellation_constraints

    num_planes = max((s.plane for s in satellites), default=0) + 1
    max_slot = max((s.slot for s in satellites), default=0)
    validate_constellation_constraints(
        resolved,
        num_planes=num_planes,
        max_slots_per_plane=max_slot,
        num_ground_stations=len(gs_file.stations),
    )

    # Platform-level sysctls (protocol-agnostic) merged with stack-provided sysctls.
    # The deployer never interprets stack fields to derive sysctls.
    base_sysctls = {
        "net.ipv6.conf.all.forwarding": "1",
        "net.ipv4.conf.all.rp_filter": "0",
        "net.ipv4.conf.default.rp_filter": "0",
        "net.ipv6.conf.all.dad_transmits": "0",
        "net.ipv6.conf.default.dad_transmits": "0",
    }
    node_sysctls = {**base_sysctls, **resolved.sysctls}

    # Build per-node wiring spec
    nodes: dict[str, Any] = {}

    # Satellites
    for sat in satellites:
        node_id = satellite_node_id(sat, addressing)
        node_assignments = by_node.get(node_id, [])
        isl_interfaces = []
        for na in node_assignments:
            isl_interfaces.append(
                {
                    "name": na.interface,
                    "peer_node": na.peer_node_id,
                    "peer_iface": "",  # filled below
                }
            )
        # Resolve peer interfaces
        for iface in isl_interfaces:
            peer_assignments = by_node.get(iface["peer_node"], [])
            for pa in peer_assignments:
                if pa.peer_node_id == node_id:
                    iface["peer_iface"] = pa.interface
                    break

        nodes[node_id] = {
            "node_type": "satellite",
            "plane": sat.plane,
            "slot": sat.slot,
            "sysctls": dict(node_sysctls),
            "isl_interfaces": isl_interfaces,
            "gnd_interfaces": [{"name": f"gnd{g}"} for g in range(sat.ground_terminal_count)],
            "mpls_enable": mpls_enable,
            "segment_routing": segment_routing,
            "mtu": 9000,
            "remove_default_route": True,
        }

    # Ground stations
    ground_bridges: dict[str, dict] = {}
    for i, station in enumerate(gs_file.stations):
        gs_id = addressing.gs_id(station.name)

        # Terrestrial prefix addresses — use host addresses, skip default routes
        addrs = []
        raw_prefixes: list[str] = []
        if station.terrestrial_prefixes:
            raw_prefixes = [tp.prefix for tp in station.terrestrial_prefixes]
        elif gs_file.default_terrestrial_prefixes:
            tpl = gs_file.default_terrestrial_prefixes
            raw_prefixes = [
                tpl.ipv4_template.format(gs_index=i),
                tpl.ipv6_template.format(gs_index=i),
            ]
        for pfx in raw_prefixes:
            net = _ipaddress.ip_network(pfx, strict=False)
            if net.prefixlen == 0:
                continue  # default route — not an interface address
            host = net.network_address + 1
            addrs.append(f"{host}/{net.prefixlen}")

        nodes[gs_id] = {
            "node_type": "ground_station",
            "gs_name": station.name,
            "gs_index": i,
            "sysctls": dict(node_sysctls),
            "isl_interfaces": [],
            "gnd_interfaces": [
                {"name": f"term{t}"}
                for t in range(station_ground_terminal_capacity(gs_file, station))
            ],
            "terrestrial": {"addresses": addrs},
            "mpls_enable": mpls_enable,
            "segment_routing": segment_routing,
            "mtu": 9000,
            "remove_default_route": True,
        }

        ground_bridges[gs_id] = {}

    # Count unique ISL links
    isl_pairs: set[tuple[str, str]] = set()
    for node_id, assignments in by_node.items():
        for na in assignments:
            isl_pairs.add((min(node_id, na.peer_node_id), max(node_id, na.peer_node_id)))

    pod_placement = _discover_session_pod_placement(v1, namespace, set(nodes))
    k8s_nodes = set(pod_placement.values())
    node_ips = _node_internal_ips(v1, k8s_nodes)
    required_substrate_pairs = _required_substrate_pairs(
        nodes=nodes,
        isl_pairs=isl_pairs,
        pod_placement=pod_placement,
        node_ips=node_ips,
        ground_candidate_satellites_by_gs=resolution.ground_candidate_satellites_by_gs,
    )
    _delete_stale_substrate_status_configmaps(v1, namespace)

    try:
        manifest_session_id = sanitize_session_id(session_run_id)
    except Exception as exc:
        log.error(
            "FATAL: Cannot derive runtime session_id from session_run_id=%r: %s",
            session_run_id,
            exc,
        )
        raise

    manifest = {
        "session_id": manifest_session_id,
        "wiring_generation": "",
        "required_phases": list(REQUIRED_WIRING_PHASES),
        "nodes": nodes,
        "ground_bridges": ground_bridges,
        "required_substrate_pairs": required_substrate_pairs,
        "isl_link_count": len(isl_pairs),
    }
    manifest["wiring_generation"] = derive_wiring_generation(manifest)

    import base64 as _base64
    import gzip as _gzip

    raw_json = _json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    compressed = _base64.b64encode(_gzip.compress(raw_json)).decode()

    _create_or_update_configmap(
        v1,
        "nodalarc-topology-wiring",
        namespace,
        {
            "manifest.json.gz.b64": compressed,
            "session_id": manifest_session_id,
            "platform_hash": compute_platform_hash(spec),
            "wiring_generation": manifest["wiring_generation"],
            "node_count": str(len(nodes)),
        },
        owner_ref,
    )
    log.info(
        "Wrote topology wiring manifest: %d nodes, %d ISL links, %d substrate pairs "
        "(%d bytes raw, %d bytes compressed)",
        len(nodes),
        len(isl_pairs),
        len(required_substrate_pairs),
        len(raw_json),
        len(compressed),
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
                log.warning("Failed to patch deployment %s: %s", deploy.metadata.name, exc)


def teardown_session(namespace: str, session_id: str | None = None) -> None:
    """Clean up session ConfigMaps (pods are garbage-collected via ownerReferences).

    Args:
        namespace: K8s namespace.
        session_id: Session identifier for JetStream purge. If not provided,
            derived from the nodalarc-session ConfigMap (which must still exist).
            Callers that know the session_id should pass it explicitly.
    """
    v1 = _get_v1()

    # Derive session_id from ConfigMap if not provided by caller.
    if session_id is None:
        from nodalarc.nats_channels import sanitize_session_id

        try:
            cm = v1.read_namespaced_config_map("nodalarc-session", namespace)
            if cm.data and "session.yaml" in cm.data:
                raw = yaml.safe_load(cm.data["session.yaml"])
                session_run_id = raw.get("session", {}).get("run_id", "")
                if not session_run_id:
                    log.error(
                        "FATAL: nodalarc-session ConfigMap has no session.run_id — cannot purge JetStream subjects"
                    )
                    raise ValueError("session.run_id missing from nodalarc-session ConfigMap")
                session_id = sanitize_session_id(session_run_id)
            else:
                log.error(
                    "FATAL: nodalarc-session ConfigMap has no session.yaml data — cannot determine session_id for teardown"
                )
                raise ValueError("nodalarc-session ConfigMap missing session.yaml")
        except (ValueError, kubernetes.client.rest.ApiException) as exc:
            log.error("FATAL: Cannot derive session_id for teardown: %s", exc)
            raise
    log.info("Teardown session_id: %s", session_id)

    # Purge retained runtime state before deleting the ConfigMaps that can be
    # used to rediscover the session identity on retry. If NATS is unavailable,
    # the delete finalizer must be retryable with the same session_id.
    purge_session_runtime_state(namespace, session_id)

    # Delete session-level ConfigMaps
    for cm_name in [
        "nodalarc-session",
        "nodalarc-constellation",
        "nodalarc-ground-stations",
        "nodalarc-pod-ips",
        "nodalarc-topology-wiring",
        "nodalarc-wiring-status",
    ]:
        try:
            v1.delete_namespaced_config_map(cm_name, namespace)
            log.debug("Deleted ConfigMap %s", cm_name)
        except kubernetes.client.rest.ApiException as e:
            if e.status != 404:
                log.warning("Failed to delete ConfigMap %s: %s", cm_name, e)

    # Delete per-node FRR config ConfigMaps
    from contextlib import suppress

    cms = v1.list_namespaced_config_map(namespace, label_selector="nodalarc.io/config-type=frr")
    for cm in cms.items:
        with suppress(kubernetes.client.rest.ApiException):
            v1.delete_namespaced_config_map(cm.metadata.name, namespace)
    log.debug("Cleaned up %d FRR config ConfigMaps", len(cms.items))

    # Clean up ephemeral constellation and ground station files
    import glob

    for pattern in [
        "configs/constellations/_ephemeral/*",
        "configs/ground-stations/_ephemeral/*",
    ]:
        for f in glob.glob(pattern):
            Path(f).unlink(missing_ok=True)
    log.debug("Cleaned up ephemeral config files")


def session_runtime_purge_targets(session_id: str) -> tuple[tuple[str, str], ...]:
    """Return retained JetStream stream/subject filters for one session.

    Current subjects are session-scoped. Future tenant support must add the
    tenant segment in this one place before multiple tenants can share NATS.
    Until then, callers must never purge a stream without a session filter.
    """
    from nodalarc.nats_channels import (
        STREAM_DEBUG_EVENTS,
        STREAM_LINK_EVENTS,
        STREAM_MI_EVENTS,
        STREAM_OME_EVENTS,
        STREAM_OPS_EVENTS,
        STREAM_SESSION_EVENTS,
        sanitize_session_id,
    )

    sid = sanitize_session_id(session_id)
    return (
        (STREAM_OME_EVENTS, f"nodalarc.ome.{sid}.>"),
        (STREAM_LINK_EVENTS, f"nodalarc.links.{sid}.>"),
        (STREAM_SESSION_EVENTS, f"nodalarc.session.{sid}.>"),
        (STREAM_MI_EVENTS, f"nodalarc.mi.{sid}.>"),
        (STREAM_OPS_EVENTS, f"nodalarc.ops.{sid}.>"),
        (STREAM_DEBUG_EVENTS, f"nodalarc.debug.{sid}.>"),
    )


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
                for stream, subject_filter in session_runtime_purge_targets(session_id):
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


def check_pods_ready(
    namespace: str,
    session_id: str | None = None,
    owner_ref: dict | None = None,
    expected_ids: set[str] | frozenset[str] | None = None,
) -> tuple[int, int]:
    """Count total and running session pods. Returns (total, running).

    When session_id/owner_ref are supplied, only pods owned by the active CR and
    stamped with the active runtime identity are counted.
    """
    v1 = _get_v1()
    expected = {node_id.lower() for node_id in expected_ids} if expected_ids else None
    pods = _list_session_pods(v1, namespace)
    filtered = []
    for pod in pods:
        if expected is not None and _pod_node_id(pod) not in expected:
            continue
        if session_id is not None and not _pod_current_for_runtime(pod, session_id, owner_ref):
            continue
        filtered.append(pod)
    total = len(filtered)
    ready = sum(1 for p in filtered if p.status and p.status.phase == "Running")
    return total, ready


def check_old_pods_terminated(
    namespace: str,
    session_id: str | None = None,
    owner_ref: dict | None = None,
    expected_ids: set[str] | frozenset[str] | None = None,
) -> bool:
    """Return True when no stale expected-name session pods remain.

    Pure query — no side effects. Used before deploying a new session
    to ensure the previous session's pods have fully terminated.
    """
    if session_id is None or owner_ref is None or expected_ids is None:
        total, _ = check_pods_ready(namespace)
        return total == 0
    return count_stale_session_pods(namespace, expected_ids, session_id, owner_ref) == 0


def check_all_pods_running(
    namespace: str,
    expected_count: int,
    session_id: str | None = None,
    owner_ref: dict | None = None,
    expected_ids: set[str] | frozenset[str] | None = None,
) -> tuple[bool, int, int]:
    """Check whether all expected session pods are Running.

    Returns (all_ready, total, ready) where all_ready is True
    if ready >= expected_count.

    Pure query — no side effects.
    """
    total, ready = check_pods_ready(namespace, session_id, owner_ref, expected_ids)
    return ready >= expected_count, total, ready


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
    import base64
    import gzip

    from nodalarc.substrate.manifest_contract import WiringManifest
    from nodalarc.substrate.wiring_status import failed_status_summary, parse_status_configmap

    v1 = _get_v1()
    manifest_cm = v1.read_namespaced_config_map("nodalarc-topology-wiring", namespace)
    manifest_data = manifest_cm.data or {}
    encoded_manifest = manifest_data.get("manifest.json.gz.b64")
    if not encoded_manifest:
        raise ValueError("topology wiring manifest payload is missing")
    try:
        manifest_payload = json.loads(gzip.decompress(base64.b64decode(encoded_manifest)))
        manifest = WiringManifest.model_validate(manifest_payload)
    except Exception as exc:
        raise ValueError(f"topology wiring manifest payload is invalid: {exc}") from exc

    if len(manifest.nodes) != expected_count:
        raise ValueError(
            f"topology wiring manifest has {len(manifest.nodes)} nodes, expected {expected_count}"
        )

    try:
        cm = v1.read_namespaced_config_map("nodalarc-wiring-status", namespace)
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


def _canonical_satellite_for_hash(sat: Any) -> dict[str, Any]:
    return {
        "plane": sat.plane,
        "slot": sat.slot,
        "elements": _canonical_hash_value(sat.elements),
        "isl_terminal_count": sat.isl_terminal_count,
        "ground_terminal_count": sat.ground_terminal_count,
        "isl_terminals": _canonical_hash_value(sat.isl_terminals),
        "ground_terminals": _canonical_hash_value(sat.ground_terminals),
        "tle_line_1": sat.tle_line_1,
        "tle_line_2": sat.tle_line_2,
        "norad_id": sat.norad_id,
    }


def compute_platform_hash(spec: dict) -> str:
    """Hash resolved runtime truth for service restart detection.

    OME, Scheduler, and NodalPath currently load session truth at startup. Any
    user-authored YAML field or referenced catalog asset that can affect runtime
    computation must therefore change this hash and trigger a platform-pod
    restart. Hashing the raw segment YAML is insufficient because a session can
    reference constellation, TLE, satellite-type, and ground-station files whose
    contents can change while the reference string stays fixed.

    The only excluded fields are operator-owned runtime lineage/context
    (``session.run_id`` and ``source_context``). Everything else comes from the
    resolver-owned runtime model and resolved assets.

    restart_platform_pods uses this hash as a Deployment annotation. A changed
    hash triggers a rolling restart so OME/Scheduler pick up the new session
    configuration and publish to the correct NATS subjects.

    Returns a hex digest string (SHA-256).
    """
    session_yaml = spec.get("sessionYaml", "")
    if not session_yaml:
        return hashlib.sha256(b"").hexdigest()
    parsed = yaml.safe_load(session_yaml)
    if not isinstance(parsed, dict):
        raise ValueError("spec.sessionYaml must parse to a mapping for platform hashing")
    resolution = resolve_session_with_assets(
        parsed,
        source_context=SourceContext(origin="operator.platform_hash"),
    )
    canonical_obj = {
        "resolved": resolution.resolved.model_dump(
            mode="json",
            exclude={"session": {"run_id"}, "source_context": True},
        ),
        "runtime_constellation": _canonical_hash_value(resolution.runtime_constellation),
        "constellations": [
            _canonical_hash_value(asset.config) for asset in resolution.constellations
        ],
        "primary_ground_set": _canonical_hash_value(resolution.primary_ground_set.config),
        "satellites": [_canonical_satellite_for_hash(sat) for sat in resolution.satellites],
        "declared_candidates": _canonical_hash_value(resolution.declared_candidates),
        "neighbors": _canonical_hash_value(resolution.neighbors),
    }
    canonical = json.dumps(canonical_obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_runtime_hash(platform_hash: str, session_run_id: str) -> str:
    """Hash platform config plus immutable runtime lineage for pod restarts."""
    if not platform_hash:
        raise ValueError("platform_hash is required")
    if not session_run_id:
        raise ValueError("session_run_id is required")
    return hashlib.sha256(f"{platform_hash}:{session_run_id}".encode()).hexdigest()


def compute_expected_pod_count(spec: dict) -> int:
    """Compute how many session pods SHOULD exist from the CRD spec.

    Pure computation — parses sessionYaml, expands constellation, counts
    satellites + ground stations. No K8s API calls, no template rendering,
    no ConfigMap creation. Fast enough for every reconciler invocation.

    Raises on invalid config — caller sets CR phase to Error with the
    message so the user sees what went wrong in the browser.
    """
    session_yaml = spec.get("sessionYaml")
    if not session_yaml:
        raise ValueError("spec.sessionYaml is missing")
    resolution = resolve_session_with_assets(
        yaml.safe_load(session_yaml),
        source_context=SourceContext(origin="operator.expected_pod_count"),
    )
    count = len(resolution.resolved.nodes)
    if count == 0:
        raise ValueError(
            "Session expands to 0 nodes — check constellation and ground station configs"
        )
    return count


def check_pods_ready_condition(namespace: str) -> tuple[int, int]:
    """Count session pods with K8s Ready condition = True.

    Ready means the readiness probe passed: config version sentinel matches
    the ConfigMap mount (NOS loaded the intended config) AND the NOS is
    responsive (e.g., vtysh -c "show version" for FRR).

    Returns (total, ready_count).
    """
    v1 = _get_v1()
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    total = len(pods.items)
    ready = 0
    for pod in pods.items:
        if pod.status and pod.status.conditions:
            for cond in pod.status.conditions:
                if cond.type == "Ready" and cond.status == "True":
                    ready += 1
                    break
    return total, ready


def write_pod_ips_configmap(
    namespace: str,
    session_id: str | None = None,
    owner_ref: dict | None = None,
    expected_ids: set[str] | frozenset[str] | None = None,
) -> None:
    """Write nodalarc-pod-ips ConfigMap from running session pods.

    Stores the IP map as a single 'pod-ips.json' key so it can be
    volume-mounted directly as a JSON file by the NodalPath Deployment.
    """
    v1 = _get_v1()
    expected = {node_id.lower() for node_id in expected_ids} if expected_ids else None
    pods = _list_session_pods(v1, namespace)
    ip_map = {}
    for pod in pods:
        if expected is not None and _pod_node_id(pod) not in expected:
            continue
        if session_id is not None and not _pod_current_for_runtime(pod, session_id, owner_ref):
            continue
        node_id = pod.metadata.labels.get("nodalarc.io/node-id", "")
        if node_id and pod.status and pod.status.pod_ip:
            ip_map[node_id] = pod.status.pod_ip
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
    if name.startswith("frr-config-"):
        labels["nodalarc.io/config-type"] = "frr"

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
    session: SessionConfig,
    session_yaml: str,
    namespace: str,
    owner_ref: dict,
    session_run_id: str,
) -> None:
    """Create session-level ConfigMaps with segment session YAML."""
    raw = yaml.safe_load(session_yaml)
    raw.setdefault("session", {})
    raw["session"]["run_id"] = session_run_id
    runtime_resolution = resolve_session_with_assets(
        raw,
        source_context=SourceContext(origin="operator.session_configmap"),
    )
    runtime_run_id = require_session_run_id(runtime_resolution.runtime_session)
    _create_or_update_configmap(
        v1,
        "nodalarc-session",
        namespace,
        {
            "session.yaml": yaml.dump(raw, default_flow_style=False),
            "session_name": session.session.name,
            "session_run_id": runtime_run_id,
        },
        owner_ref,
    )
    log.debug("Created session-level ConfigMap")


def _build_sidecar_config(resolved) -> dict | None:
    """Build sidecar container config from resolved stack."""
    if resolved.image and resolved.image != "frr":
        if resolved.image != "nodalpath-fwd":
            raise RuntimeError(f"Unsupported sidecar image intent: {resolved.image}")
        return {
            "name": resolved.image,
            "image": _require_env("NODALPATH_FWD_IMAGE"),
            "capabilities": resolved.security_context_capabilities
            or ["NET_ADMIN", "NET_RAW", "SYS_ADMIN"],
        }
    return None


def _build_sidecar_env(node_id: str, vars: dict, env_list: list) -> list[dict] | None:
    """Build sidecar environment variables with template substitution."""
    if not env_list:
        return None
    result = []
    for e in env_list:
        val = e.get("value", "") if isinstance(e, dict) else e.value
        name = e.get("name", "") if isinstance(e, dict) else e.name
        val = val.replace("{{ node_id }}", node_id)
        for k, v in vars.items():
            val = val.replace("{{ " + k + " }}", str(v))
        result.append({"name": name, "value": val})
    return result


def _create_session_pod(
    v1: kubernetes.client.CoreV1Api,
    pod_name: str,
    namespace: str,
    node_id: str,
    node_type: str,
    plane: int | None,
    slot: int | None,
    gs_name: str | None,
    config_cm_name: str,
    sidecar_config: dict | None,
    sidecar_env: list[dict] | None,
    probe_enabled: bool,
    owner_ref: dict,
    session_id: str,
    target_node: str | None = None,
) -> None:
    """Create a single session pod (satellite or ground station)."""
    labels: dict[str, str] = {
        "nodalarc.io/session": "true",
        "nodalarc.io/node-id": node_id,
        "nodalarc.io/role": node_type.replace("_", "-"),
        POD_SESSION_RUN_LABEL: session_id,
        POD_OWNER_UID_LABEL: str(owner_ref.get("uid") or ""),
    }
    if plane is not None:
        labels["nodalarc.io/plane"] = str(plane)
    if slot is not None:
        labels["nodalarc.io/slot"] = str(slot)
    if gs_name:
        labels["nodalarc.io/gs-name"] = gs_name

    # FRR container — hardened security context:
    #   SYS_ADMIN: required by FRR's ospfd/mgmtd (privs_init requests it)
    #   read_only_root_filesystem: writable paths via tmpfs only
    frr_container = kubernetes.client.V1Container(
        name="frr",
        image=_require_env("FRR_IMAGE"),
        image_pull_policy=_require_env("IMAGE_PULL_POLICY"),
        security_context=kubernetes.client.V1SecurityContext(
            capabilities=kubernetes.client.V1Capabilities(
                add=["NET_ADMIN", "NET_RAW", "SYS_ADMIN"]
            ),
            read_only_root_filesystem=True,
        ),
        resources=kubernetes.client.V1ResourceRequirements(
            requests={"memory": "32Mi", "cpu": "10m"},
            limits={"memory": "128Mi", "cpu": "200m"},
        ),
        readiness_probe=kubernetes.client.V1Probe(
            _exec=kubernetes.client.V1ExecAction(
                command=[
                    "sh",
                    "-c",
                    "test -f /etc/frr/.config_version && "
                    "diff -q /etc/frr-config/_config_version /etc/frr/.config_version > /dev/null 2>&1 && "
                    "vtysh -c 'show version' > /dev/null 2>&1",
                ],
            ),
            initial_delay_seconds=5,
            period_seconds=5,
            failure_threshold=6,
            timeout_seconds=5,
        ),
        volume_mounts=[
            kubernetes.client.V1VolumeMount(
                name="frr-config",
                mount_path="/etc/frr-config",
            ),
            kubernetes.client.V1VolumeMount(name="frr-run", mount_path="/var/run/frr"),
            kubernetes.client.V1VolumeMount(name="frr-etc", mount_path="/etc/frr"),
            kubernetes.client.V1VolumeMount(name="tmp", mount_path="/tmp"),
            kubernetes.client.V1VolumeMount(name="ssh-config", mount_path="/etc/ssh"),
            kubernetes.client.V1VolumeMount(name="operator-home", mount_path="/home/operator"),
            kubernetes.client.V1VolumeMount(name="var-log", mount_path="/var/log"),
            kubernetes.client.V1VolumeMount(
                name="ssh-keys", mount_path="/etc/ssh-keys", read_only=True
            ),
        ],
    )

    containers = [frr_container]
    volumes = [
        kubernetes.client.V1Volume(
            name="frr-config",
            config_map=kubernetes.client.V1ConfigMapVolumeSource(
                name=config_cm_name,
            ),
        ),
        # Writable tmpfs for FRR runtime (read-only root filesystem)
        kubernetes.client.V1Volume(
            name="frr-run",
            empty_dir=kubernetes.client.V1EmptyDirVolumeSource(medium="Memory"),
        ),
        kubernetes.client.V1Volume(
            name="frr-etc",
            empty_dir=kubernetes.client.V1EmptyDirVolumeSource(),
        ),
        kubernetes.client.V1Volume(
            name="tmp",
            empty_dir=kubernetes.client.V1EmptyDirVolumeSource(medium="Memory"),
        ),
        kubernetes.client.V1Volume(
            name="var-log",
            empty_dir=kubernetes.client.V1EmptyDirVolumeSource(medium="Memory"),
        ),
        # Forward-compatible mounts for SSH terminal (Phase 1)
        kubernetes.client.V1Volume(
            name="ssh-config",
            empty_dir=kubernetes.client.V1EmptyDirVolumeSource(medium="Memory"),
        ),
        kubernetes.client.V1Volume(
            name="operator-home",
            empty_dir=kubernetes.client.V1EmptyDirVolumeSource(medium="Memory"),
        ),
        # SSH public key for terminal access (SSH authorized_keys)
        kubernetes.client.V1Volume(
            name="ssh-keys",
            secret=kubernetes.client.V1SecretVolumeSource(
                secret_name=TERMINAL_SSH_KEY_RESOURCE_NAME,
                items=[kubernetes.client.V1KeyToPath(key="id_ed25519.pub", path="authorized_keys")],
                optional=True,  # Don't fail pod start if terminal keys not yet created
            ),
        ),
    ]

    # Sidecar container (e.g., nodalpath-fwd)
    if sidecar_config:
        sidecar_container = kubernetes.client.V1Container(
            name=sidecar_config["name"],
            image=sidecar_config["image"],
            image_pull_policy=_require_env("IMAGE_PULL_POLICY"),
            security_context=kubernetes.client.V1SecurityContext(
                capabilities=kubernetes.client.V1Capabilities(
                    add=sidecar_config.get("capabilities", ["NET_ADMIN", "NET_RAW", "SYS_ADMIN"])
                )
            ),
            resources=kubernetes.client.V1ResourceRequirements(
                requests={"memory": "32Mi", "cpu": "10m"},
                limits={"memory": "128Mi", "cpu": "200m"},
            ),
        )
        if sidecar_env:
            sidecar_container.env = [
                kubernetes.client.V1EnvVar(name=e["name"], value=e["value"]) for e in sidecar_env
            ]
        containers.append(sidecar_container)

    # Probe sidecar for ground stations
    if node_type == "ground_station" and probe_enabled:
        probe_container = kubernetes.client.V1Container(
            name="probe",
            image=_require_env("PROBE_IMAGE"),
            image_pull_policy=_require_env("IMAGE_PULL_POLICY"),
            security_context=kubernetes.client.V1SecurityContext(
                capabilities=kubernetes.client.V1Capabilities(add=["NET_RAW"])
            ),
            env=[
                kubernetes.client.V1EnvVar(
                    name="NODALARC_PROBE_BIND_HOST",
                    value_from=kubernetes.client.V1EnvVarSource(
                        field_ref=kubernetes.client.V1ObjectFieldSelector(field_path="status.podIP")
                    ),
                )
            ],
            ports=[kubernetes.client.V1ContainerPort(container_port=9100, name="probe-api")],
            resources=kubernetes.client.V1ResourceRequirements(
                limits={"memory": "64Mi", "cpu": "100m"}
            ),
        )
        containers.append(probe_container)

    pod = kubernetes.client.V1Pod(
        metadata=kubernetes.client.V1ObjectMeta(
            name=pod_name,
            namespace=namespace,
            labels=labels,
            owner_references=[owner_ref],
        ),
        spec=kubernetes.client.V1PodSpec(
            node_name=target_node,
            containers=containers,
            volumes=volumes,
            restart_policy="Never",
            automount_service_account_token=False,
            # Fast DNS timeout: pod IPs have no PTR records in CoreDNS.
            # Without this, every reverse DNS lookup (traceroute hops, sshd
            # client lookup, any gethostbyaddr) waits 10+ seconds.
            dns_config=kubernetes.client.V1PodDNSConfig(
                options=[
                    kubernetes.client.V1PodDNSConfigOption(name="timeout", value="1"),
                    kubernetes.client.V1PodDNSConfigOption(name="attempts", value="1"),
                ],
            ),
        ),
    )

    try:
        v1.create_namespaced_pod(namespace, pod)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:  # Already exists
            existing = v1.read_namespaced_pod(pod_name, namespace)
            if not _pod_owned_by(existing, owner_ref):
                raise RuntimeError(
                    f"Pod {pod_name} already exists but is not owned by the current ConstellationSpec"
                ) from e
            if _pod_deleting(existing):
                raise RuntimeError(f"Pod {pod_name} already exists and is deleting") from e
            if not _pod_current_for_runtime(existing, session_id, owner_ref):
                _patch_pod_runtime_identity(v1, existing, namespace, session_id, owner_ref)
            log.debug("Pod %s already exists for current runtime, skipping", pod_name)
        else:
            raise
