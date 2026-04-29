# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Session deployer — renders configs, creates pods and ConfigMaps.

Replicates na_deploy.py Steps 3-5 using the K8s Python client.
Called by kopf handlers in handlers.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import kubernetes
import yaml
from jinja2 import Environment, FileSystemLoader
from nodalarc.constellation_loader import (
    expand_constellation,
    load_constellation,
    load_ground_stations,
)
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors, neighbors_by_node
from nodalarc.models.session import PlacementConfig, SessionConfig
from nodalarc.platform_config import get_platform_config
from nodalarc.stack_resolver import resolve_stack
from nodalarc.template_vars import build_template_vars

log = logging.getLogger(__name__)


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
        return {nid: target for nid in node_vars}

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
        ppg = placement.planes_per_group or max(1, len(available_nodes))
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


# NOTE: Substrate latency measurement moved to Node Agent substrate_monitor.
# Each Node Agent measures latency to its active VXLAN peers (peer-only,
# continuous) and publishes to NATS. The Scheduler consumes live measurements.
# See services/node_agent/substrate_monitor.py.


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
) -> dict:
    """Create/update all ConfigMaps and SSH keys for a session.

    Runs steps 1-10 of the deploy pipeline: parse session, load constellation,
    resolve stack, validate, render FRR configs, create ConfigMaps, generate
    SSH keypair, compute pod placement.

    Idempotent — ConfigMaps use create-or-update, SSH key uses create-or-replace.
    Safe to call repeatedly; only writes what's missing or changed.

    Args:
        spec: The CR's .spec dict.
        name: CR metadata.name (used for session_id).
        namespace: K8s namespace.
        owner_ref: ownerReferences entry for garbage collection.
        progress_fn: Optional callback(message: str) for status updates.

    Returns:
        Context dict with keys: session_id, session, constellation,
        satellites, gs_file, resolved_stack, node_vars, pod_placement,
        available_nodes. Passed to ensure_session_pods().
    """

    def _progress(msg: str) -> None:
        log.info(msg)
        if progress_fn:
            progress_fn(msg)

    v1 = _get_v1()

    session_id = f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    # Discover available K8s nodes for pod placement.
    _progress("Discovering available K8s nodes")
    available_nodes = discover_available_nodes()
    if not available_nodes:
        import kopf

        raise kopf.PermanentError(
            "No K8s nodes with label nodalarc.io/node-agent=true found. "
            "Label at least one node: kubectl label node <name> nodalarc.io/node-agent=true"
        )

    # --- Step 1: Parse session YAML from the CRD spec ---
    _progress("Parsing session configuration")
    session_yaml = spec.get("sessionYaml")
    if not session_yaml:
        raise ValueError("spec.sessionYaml is required")
    session = SessionConfig.model_validate(yaml.safe_load(session_yaml))

    # --- Step 2: Load constellation and ground stations ---
    _progress("Loading constellation and ground station definitions")
    # Handle inline constellation dicts: write to ephemeral file, then load
    constellation_source = session.constellation
    if isinstance(constellation_source, dict):
        eph_dir = Path("configs/constellations/_ephemeral")
        eph_dir.mkdir(parents=True, exist_ok=True)
        eph_path = eph_dir / f"{session_id}.yaml"
        eph_path.write_text(yaml.dump(constellation_source, default_flow_style=False))
        log.info(f"Wrote ephemeral constellation: {eph_path}")
        constellation_source = str(eph_path)
    elif session.satellite_type:
        # Satellite type override on a file-path constellation
        from nodalarc.session_generator import merge_constellation_with_satellite_type

        merged = merge_constellation_with_satellite_type(
            constellation_source, session.satellite_type
        )
        eph_dir = Path("configs/constellations/_ephemeral")
        eph_dir.mkdir(parents=True, exist_ok=True)
        eph_path = eph_dir / f"{session_id}.yaml"
        eph_path.write_text(yaml.dump(merged, default_flow_style=False))
        log.info(f"Wrote ephemeral constellation (satellite_type override): {eph_path}")
        constellation_source = str(eph_path)

    constellation = load_constellation(constellation_source)

    # Handle inline ground station dicts
    gs_source = session.ground_stations
    if isinstance(gs_source, dict):
        eph_dir = Path("configs/ground-stations/_ephemeral")
        eph_dir.mkdir(parents=True, exist_ok=True)
        eph_path = eph_dir / f"{session_id}.yaml"
        eph_path.write_text(yaml.dump(gs_source, default_flow_style=False))
        log.info(f"Wrote ephemeral ground stations: {eph_path}")
        gs_source = str(eph_path)

    gs_file = load_ground_stations(gs_source)
    satellites = expand_constellation(constellation)
    num_planes = max((s.plane for s in satellites), default=0) + 1
    _progress(
        f"Expanded {len(satellites)} satellites across {num_planes} planes, {len(gs_file.stations)} ground stations"
    )
    if not satellites:
        raise ValueError("No satellites in constellation")

    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation, addressing)

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
        _publish_validation_ops_events(
            validation_results, namespace, session_id=session.session.name
        )
    if val_errors:
        import kopf

        error_msg = "; ".join(f"[{r.code}] {r.message}" for r in val_errors)
        raise kopf.PermanentError(f"Session validation failed: {error_msg}")

    # --- Step 4: Build template vars per node ---
    total_nodes = len(satellites) + len(gs_file.stations)
    _progress(f"Building template variables for {total_nodes} nodes")
    node_vars: dict[str, dict] = {}
    for sat in satellites:
        node_id = addressing.sat_id(sat.plane, sat.slot)
        node_vars[node_id] = build_template_vars(
            session=session,
            constellation=constellation,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=sat.plane,
            slot=sat.slot,
            config_overrides=config_overrides,
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
    log.info(f"Created {len(rendered_configs)} FRR config ConfigMaps")

    # --- Step 7: Create session-level ConfigMaps ---
    _progress("Creating session-level ConfigMaps")
    _create_session_configmaps(
        v1,
        session,
        session_yaml,
        constellation_source if isinstance(constellation_source, str) else None,
        gs_source if isinstance(gs_source, str) else None,
        namespace,
        owner_ref,
    )

    # --- Step 7b: Generate SSH keypair for terminal access ---
    _progress("Generating SSH keypair for terminal access")
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
        log.info(msg)
        if progress_fn:
            progress_fn(msg)

    v1 = _get_v1()
    node_vars = context["node_vars"]
    pod_placement = context["pod_placement"]
    session = context["session"]
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
                log.warning(f"Pod creation failed for {node_id}: {exc}")

    _pod_creation_done.set()

    if errors:
        log.warning(f"Pod creation: {len(errors)} failures out of {total_pods}")
    log.info(f"Created {created_pods} session pods (total expected: {total_pods})")

    return total_pods


def deploy_session(
    spec: dict,
    name: str,
    namespace: str,
    owner_ref: dict,
    progress_fn: Any | None = None,
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
    context = ensure_session_configmaps(spec, name, namespace, owner_ref, progress_fn)
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
) -> int:
    """Generate and write the topology wiring manifest ConfigMap.

    Called after pods are Running. The Node Agent watches this ConfigMap
    and executes all data plane wiring operations.

    Returns the number of ISL links in the manifest.
    """
    import ipaddress as _ipaddress
    import json as _json

    from nodalarc.nats_channels import sanitize_session_id

    v1 = _get_v1()

    # Delete stale wiring-status before writing new manifest.
    # Without this, the Node Agent sees old wiring-status as "current" and
    # hits Case B (no-op) instead of Case A (wire from scratch).
    try:
        v1.delete_namespaced_config_map("nodalarc-wiring-status", namespace)
        log.info("Deleted stale nodalarc-wiring-status")
    except kubernetes.client.rest.ApiException as e:
        if e.status != 404:
            raise

    # Parse session from CRD spec
    session_yaml = spec.get("sessionYaml", "")
    session = SessionConfig.model_validate(yaml.safe_load(session_yaml))
    constellation = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)
    satellites = expand_constellation(constellation)
    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    resolved = resolve_stack(session.routing.protocol, session.routing.extensions)
    segment_routing = resolved.segment_routing

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
        node_id = addressing.sat_id(sat.plane, sat.slot)
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
            "mpls_enable": True,
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
                for t in range(
                    sum(
                        t.tracking_capacity
                        for t in (station.terminals or gs_file.default_terminals)
                    )
                    or 1
                )
            ],
            "terrestrial": {"addresses": addrs},
            "mpls_enable": True,
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

    try:
        manifest_session_id = sanitize_session_id(session.session.name)
    except Exception as exc:
        log.error(
            "FATAL: Cannot derive session_id from session.name=%r: %s", session.session.name, exc
        )
        raise

    manifest = {
        "session_id": manifest_session_id,
        "generation": int(datetime.now(UTC).timestamp()),
        "nodes": nodes,
        "ground_bridges": ground_bridges,
        "isl_link_count": len(isl_pairs),
    }

    import base64 as _base64
    import gzip as _gzip

    raw_json = _json.dumps(manifest).encode()
    compressed = _base64.b64encode(_gzip.compress(raw_json)).decode()

    _create_or_update_configmap(
        v1,
        "nodalarc-topology-wiring",
        namespace,
        {"manifest.json.gz.b64": compressed},
        owner_ref,
    )
    log.info(
        f"Wrote topology wiring manifest: {len(nodes)} nodes, "
        f"{len(isl_pairs)} ISL links "
        f"({len(raw_json)} bytes raw, {len(compressed)} bytes compressed)"
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
            log.info("NodalPath deployment not found — skipping mode patch")
            return
        deployment = deployments.items[0]
        deploy_name = deployment.metadata.name
    except kubernetes.client.rest.ApiException:
        log.info("NodalPath deployment not found — skipping mode patch")
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
                        log.info(f"NodalPath mode set to {mode}")
                    else:
                        log.info(f"NodalPath mode already {mode}")
                    return
    log.warning("NodalPath container --mode arg not found in deployment spec")


def restart_platform_pods(namespace: str, config_hash: str = "") -> None:
    """Trigger rolling restart of platform pods via annotation change.

    Patches each Deployment's pod template with a config-hash annotation,
    which triggers a rolling update. The VS-API is NOT restarted — it
    handles session re-subscription internally via SessionContext lifecycle.
    Killing the VS-API pod destroys WebSocket connections unnecessarily.
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
                log.info(f"Rolling restart triggered for {deploy.metadata.name}")
            except kubernetes.client.rest.ApiException as exc:
                log.warning(f"Failed to patch deployment {deploy.metadata.name}: {exc}")


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
                session_name = raw.get("session", {}).get("name", "")
                if not session_name:
                    log.error(
                        "FATAL: nodalarc-session ConfigMap has no session.name — cannot purge JetStream subjects"
                    )
                    raise ValueError("session.name missing from nodalarc-session ConfigMap")
                session_id = sanitize_session_id(session_name)
            else:
                log.error(
                    "FATAL: nodalarc-session ConfigMap has no session.yaml data — cannot determine session_id for teardown"
                )
                raise ValueError("nodalarc-session ConfigMap missing session.yaml")
        except (ValueError, kubernetes.client.rest.ApiException) as exc:
            log.error("FATAL: Cannot derive session_id for teardown: %s", exc)
            raise
    log.info("Teardown session_id: %s", session_id)

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
            log.info(f"Deleted ConfigMap {cm_name}")
        except kubernetes.client.rest.ApiException as e:
            if e.status != 404:
                log.warning(f"Failed to delete ConfigMap {cm_name}: {e}")

    # Delete per-node FRR config ConfigMaps
    from contextlib import suppress

    cms = v1.list_namespaced_config_map(namespace, label_selector="nodalarc.io/config-type=frr")
    for cm in cms.items:
        with suppress(kubernetes.client.rest.ApiException):
            v1.delete_namespaced_config_map(cm.metadata.name, namespace)
    log.info(f"Cleaned up {len(cms.items)} FRR config ConfigMaps")

    # Clean up ephemeral constellation and ground station files
    import glob

    for pattern in [
        "configs/constellations/_ephemeral/*",
        "configs/ground-stations/_ephemeral/*",
    ]:
        for f in glob.glob(pattern):
            Path(f).unlink(missing_ok=True)
    log.info("Cleaned up ephemeral config files")

    # Purge session-scoped JetStream subjects to prevent cross-session
    # checkpoint contamination on session name reuse. Without this, a new
    # session with the same name (e.g., "current-session") would read the
    # stale SchedulingCheckpoint from the previous instance.
    _purge_session_jetstream_subjects(namespace, session_id)


def _purge_session_jetstream_subjects(namespace: str, session_id: str) -> None:
    """Purge retained JetStream messages for the torn-down session.

    Best-effort — failure doesn't block teardown. Uses session-scoped
    subject filter so concurrent sessions are not affected. The session_id
    must be passed by the caller BEFORE ConfigMaps are deleted.
    """
    try:
        import asyncio

        import nats
        from nodalarc.nats_channels import (
            NATS_CONNECT_OPTIONS,
            STREAM_SESSION_EVENTS,
            nats_url,
        )

        async def _purge():
            nc = await nats.connect(nats_url(), **NATS_CONNECT_OPTIONS)
            try:
                js = nc.jetstream()
                # Purge ONLY this session's subjects — not the entire stream.
                # Without the subject filter, concurrent sessions would lose
                # their checkpoints, ephemeris, and playback state.
                subject_filter = f"nodalarc.session.{session_id}.>"
                await js.purge_stream(STREAM_SESSION_EVENTS, subject=subject_filter)
                log.info("Purged session subjects: %s", subject_filter)
            finally:
                await nc.close()

        asyncio.run(_purge())
    except Exception as exc:
        log.warning("Failed to purge JetStream session subjects: %s", exc)


def check_pods_ready(namespace: str) -> tuple[int, int]:
    """Count total and ready session pods. Returns (total, ready).

    Matches pods with nodalarc.io/node-id label (present on both
    Operator-created and na_deploy-created session pods).
    """
    v1 = _get_v1()
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    total = len(pods.items)
    ready = sum(1 for p in pods.items if p.status and p.status.phase == "Running")
    return total, ready


def check_old_pods_terminated(namespace: str) -> bool:
    """Return True if zero session pods exist in the namespace.

    Pure query — no side effects. Used before deploying a new session
    to ensure the previous session's pods have fully terminated.
    """
    total, _ = check_pods_ready(namespace)
    return total == 0


def check_all_pods_running(namespace: str, expected_count: int) -> tuple[bool, int, int]:
    """Check whether all expected session pods are Running.

    Returns (all_ready, total, ready) where all_ready is True
    if ready >= expected_count.

    Pure query — no side effects.
    """
    total, ready = check_pods_ready(namespace)
    return ready >= expected_count, total, ready


def check_wiring_complete(namespace: str, expected_count: int) -> tuple[bool, int, str | None]:
    """Check whether Node Agent wiring is complete.

    Reads the nodalarc-wiring-status ConfigMap and counts wired nodes
    (excludes the _progress key used for display messages).

    Returns (complete, wired_count, progress_msg) where:
      - complete: True if wired_count >= expected_count
      - wired_count: number of wired node entries
      - progress_msg: the _progress value from the ConfigMap, or None

    Returns (False, 0, None) if the ConfigMap does not exist (404).
    Raises on other API errors.

    Pure query — no side effects.
    """
    v1 = _get_v1()
    try:
        cm = v1.read_namespaced_config_map("nodalarc-wiring-status", namespace)
        data = dict(cm.data) if cm.data else {}
        progress_msg = data.pop("_progress", None)
        wired_count = len(data)
        return wired_count >= expected_count, wired_count, progress_msg
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return False, 0, None
        raise


def compute_platform_hash(spec: dict) -> str:
    """Hash platform-impacting fields of a ConstellationSpec for change detection.

    Hashes only fields that affect the platform pods or data plane:
    constellation, ground_stations, routing, time. Changes to display-only
    fields (name, labels, etc.) do not produce a different hash.

    Returns a hex digest string (SHA-256).
    """
    platform_fields = {}
    for key in ("constellation", "ground_stations", "routing", "time"):
        if key in spec:
            platform_fields[key] = spec[key]
    # Canonical YAML serialization for deterministic hashing
    canonical = yaml.dump(platform_fields, default_flow_style=False, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_expected_pod_count(spec: dict) -> int:
    """Compute how many session pods SHOULD exist from the CRD spec.

    Pure computation — parses sessionYaml, expands constellation, counts
    satellites + ground stations. No K8s API calls, no template rendering,
    no ConfigMap creation. Fast enough for every reconciler invocation.

    Returns 0 if sessionYaml is missing or unparseable (caller handles this).
    """
    session_yaml = spec.get("sessionYaml")
    if not session_yaml:
        return 0
    try:
        session = SessionConfig.model_validate(yaml.safe_load(session_yaml))
        constellation = load_constellation(session.constellation)
        gs_file = load_ground_stations(session.ground_stations)
        satellites = expand_constellation(constellation)
        return len(satellites) + len(gs_file.stations)
    except Exception as exc:
        log.warning("compute_expected_pod_count failed: %s", exc)
        return 0


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


def write_pod_ips_configmap(namespace: str) -> None:
    """Write nodalarc-pod-ips ConfigMap from running session pods.

    Stores the IP map as a single 'pod-ips.json' key so it can be
    volume-mounted directly as a JSON file by the NodalPath Deployment.
    """
    v1 = _get_v1()
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    ip_map = {}
    for pod in pods.items:
        node_id = pod.metadata.labels.get("nodalarc.io/node-id", "")
        if node_id and pod.status and pod.status.pod_ip:
            ip_map[node_id] = pod.status.pod_ip
    data = {"pod-ips.json": json.dumps(ip_map)}
    _create_or_update_configmap(v1, "nodalarc-pod-ips", namespace, data, owner_ref=None)
    log.info(f"Wrote nodalarc-pod-ips with {len(ip_map)} entries")


# ---------------------------------------------------------------------------
# SSH terminal access
# ---------------------------------------------------------------------------

TERMINAL_SSH_SECRET_NAME = "nodalarc-terminal-keys"


def _create_terminal_ssh_keys(
    v1: kubernetes.client.CoreV1Api,
    namespace: str,
    owner_ref: dict | None,
) -> None:
    """Generate an ED25519 SSH keypair and store in a K8s Secret.

    The public key is mounted into session pods for SSH authorized_keys.
    The private key is read by the VS-API to SSH into pods for terminal proxy.
    Owner reference ties the Secret lifecycle to the ConstellationSpec CR —
    teardown deletes the Secret automatically.
    """
    import subprocess
    import tempfile

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
            name=TERMINAL_SSH_SECRET_NAME,
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
        log.info("Terminal SSH keypair created (Secret: %s)", TERMINAL_SSH_SECRET_NAME)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:  # Already exists — replace
            v1.replace_namespaced_secret(TERMINAL_SSH_SECRET_NAME, namespace, body)
            log.info("Terminal SSH keypair updated (Secret: %s)", TERMINAL_SSH_SECRET_NAME)
        else:
            raise


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
    constellation_path: str | None,
    gs_path: str | None,
    namespace: str,
    owner_ref: dict,
) -> None:
    """Create session-level ConfigMaps (session, constellation, ground-stations)."""
    # Session YAML — rewrite paths for container mounts
    raw = yaml.safe_load(session_yaml)
    raw["constellation"] = "/etc/nodalarc/constellation.yaml"
    if isinstance(raw.get("ground_stations"), str):
        raw["ground_stations"] = "/etc/nodalarc/ground-stations.yaml"
    _create_or_update_configmap(
        v1,
        "nodalarc-session",
        namespace,
        {"session.yaml": yaml.dump(raw, default_flow_style=False)},
        owner_ref,
    )

    # Constellation YAML ConfigMap
    if constellation_path:
        const_p = Path(constellation_path)
        if const_p.exists():
            _create_or_update_configmap(
                v1,
                "nodalarc-constellation",
                namespace,
                {"constellation.yaml": const_p.read_text()},
                owner_ref,
            )

    # Ground stations ConfigMap
    if gs_path:
        gs_p = Path(gs_path)
        if gs_p.exists():
            _create_or_update_configmap(
                v1,
                "nodalarc-ground-stations",
                namespace,
                {"ground-stations.yaml": gs_p.read_text()},
                owner_ref,
            )

    log.info("Created session-level ConfigMaps")


def _build_sidecar_config(resolved) -> dict | None:
    """Build sidecar container config from resolved stack."""
    if resolved.image and not resolved.image.startswith("nodalarc/frr"):
        return {
            "image": resolved.image,
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
    target_node: str | None = None,
) -> None:
    """Create a single session pod (satellite or ground station)."""
    cfg = get_platform_config()

    labels: dict[str, str] = {
        "nodalarc.io/session": "true",
        "nodalarc.io/node-id": node_id,
        "nodalarc.io/role": node_type.replace("_", "-"),
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
                secret_name=TERMINAL_SSH_SECRET_NAME,
                items=[kubernetes.client.V1KeyToPath(key="id_ed25519.pub", path="authorized_keys")],
                optional=True,  # Don't fail pod start if terminal keys not yet created
            ),
        ),
    ]

    # Sidecar container (e.g., nodalpath-fwd)
    if sidecar_config:
        sidecar_container = kubernetes.client.V1Container(
            name=sidecar_config["image"].replace(":", "-").replace("/", "-").lower(),
            image=sidecar_config["image"],
            image_pull_policy="IfNotPresent",
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
            image="nodalarc/probe:1",
            image_pull_policy="IfNotPresent",
            security_context=kubernetes.client.V1SecurityContext(
                capabilities=kubernetes.client.V1Capabilities(add=["NET_RAW"])
            ),
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
            log.info(f"Pod {pod_name} already exists, skipping")
        else:
            raise
