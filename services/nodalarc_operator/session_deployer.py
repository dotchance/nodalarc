"""Session deployer — renders configs, creates pods and ConfigMaps.

Replicates na_deploy.py Steps 3-5 using the K8s Python client.
Called by kopf handlers in handlers.py.
"""

from __future__ import annotations

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
from nodalarc.platform import get_platform_config
from nodalarc.stack_resolver import resolve_stack
from nodalarc.template_vars import build_template_vars

log = logging.getLogger(__name__)


def discover_available_nodes() -> list[str]:
    """Discover K3s nodes available for session pods.

    Returns node names that have the nodalarc.io/node-agent=true label
    and do not have the nodalarc.io/not-ready taint.
    """
    v1 = kubernetes.client.CoreV1Api()
    nodes = v1.list_node(label_selector="nodalarc.io/node-agent=true")
    available = []
    for node in nodes.items:
        taints = node.spec.taints or []
        blocked = any(t.key == "nodalarc.io/not-ready" and t.effect == "NoSchedule" for t in taints)
        if not blocked:
            available.append(node.metadata.name)
    return sorted(available)


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
        # All pods on the first available node (backward compatible)
        target = available_nodes[0]
        return {nid: target for nid in node_vars}

    if placement.policy == "planePerNode":
        # One plane per node, round-robin across available nodes.
        # Ground stations go on the first node (control plane).
        result: dict[str, str] = {}
        for nid, vars in node_vars.items():
            if vars.get("node_type") == "ground_station":
                result[nid] = available_nodes[0]
            else:
                plane = vars.get("plane", 0)
                result[nid] = available_nodes[plane % len(available_nodes)]
        return result

    if placement.policy == "planeGroupPerNode":
        # Group adjacent planes, assign groups round-robin.
        ppg = placement.planes_per_group or max(1, len(available_nodes))
        result = {}
        for nid, vars in node_vars.items():
            if vars.get("node_type") == "ground_station":
                result[nid] = available_nodes[0]
            else:
                plane = vars.get("plane", 0)
                group = plane // ppg
                result[nid] = available_nodes[group % len(available_nodes)]
        return result

    raise ValueError(f"Unknown placement policy: {placement.policy}")


def measure_substrate_latency(available_nodes: list[str]) -> dict[str, str]:
    """Measure baseline network latency between all node pairs.

    Uses ICMP ping between node InternalIPs. 20 samples, takes median.
    Stores results as a flat dict suitable for a ConfigMap:
    {"nodal-nodal03": "0.23", "nodal03-nodal": "0.24"}

    For single-node deployments, returns empty dict (no cross-node latency).
    """
    import subprocess

    if len(available_nodes) < 2:
        return {}

    # Discover node IPs
    v1 = kubernetes.client.CoreV1Api()
    node_ips: dict[str, str] = {}
    for node in v1.list_node().items:
        name = node.metadata.name
        if name in available_nodes:
            for addr in node.status.addresses or []:
                if addr.type == "InternalIP":
                    node_ips[name] = addr.address
                    break

    results: dict[str, str] = {}
    for i, node_a in enumerate(available_nodes):
        for node_b in available_nodes[i + 1 :]:
            ip_b = node_ips.get(node_b)
            if not ip_b:
                continue
            # Measure A→B latency from the operator pod (runs on control plane)
            try:
                out = subprocess.run(
                    ["ping", "-c", "20", "-q", "-W", "1", ip_b],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                # Parse avg from "rtt min/avg/max/mdev = 0.1/0.2/0.3/0.05 ms"
                for line in out.stdout.splitlines():
                    if "avg" in line and "/" in line:
                        parts = line.split("=")[1].strip().split("/")
                        avg_ms = float(parts[1])
                        results[f"{node_a}-{node_b}"] = f"{avg_ms:.3f}"
                        results[f"{node_b}-{node_a}"] = f"{avg_ms:.3f}"
                        break
            except Exception as exc:
                log.warning(
                    "Substrate latency measurement %s→%s failed: %s",
                    node_a,
                    node_b,
                    exc,
                )

    if results:
        log.info(
            "Substrate latency measured: %s",
            ", ".join(f"{k}={v}ms" for k, v in sorted(results.items())),
        )
    return results


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


def deploy_session(
    spec: dict,
    name: str,
    namespace: str,
    owner_ref: dict,
) -> dict:
    """Deploy a full session from a ConstellationSpec CR spec.

    Args:
        spec: The CR's .spec dict.
        name: CR metadata.name (used for session_id).
        namespace: K8s namespace.
        owner_ref: ownerReferences entry for garbage collection.

    Returns:
        Status dict with phase, podCount, readyPods, sessionId, message.
    """
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

    session_id = f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    # --- Step 1: Parse session YAML from the CRD spec ---
    session_yaml = spec.get("sessionYaml")
    if not session_yaml:
        return {"phase": "Error", "message": "spec.sessionYaml is required"}
    session = SessionConfig.model_validate(yaml.safe_load(session_yaml))

    # --- Step 2: Load constellation and ground stations ---
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
    if not satellites:
        return {"phase": "Error", "message": "No satellites in constellation"}

    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation, addressing)

    # --- Step 3: Resolve routing stack ---
    resolved = resolve_stack(
        session.routing.protocol,
        session.routing.extensions,
    )
    config_overrides = dict(resolved.template_variables)
    config_overrides.update(session.routing.config_overrides)

    # --- Step 4: Build template vars per node ---
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

    # --- Step 5: Render FRR configs ---
    template_dir = str(Path("configs/templates/frr").resolve())
    # nosec B701 — these are FRR router config templates, not HTML; autoescape would break config syntax
    env = Environment(loader=FileSystemLoader(template_dir), keep_trailing_newline=True)

    rendered_configs: dict[str, dict[str, str]] = {}
    for node_id, vars in node_vars.items():
        configs: dict[str, str] = {}
        for tpl_file in resolved.template_files:
            tpl = env.get_template(tpl_file.src)
            rendered = tpl.render(**vars)
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

        rendered_configs[node_id] = configs

    # --- Step 6: Create per-node FRR config ConfigMaps ---
    for node_id, configs in rendered_configs.items():
        cm_name = f"frr-config-{node_id.lower()}"
        _create_or_update_configmap(v1, cm_name, namespace, configs, owner_ref)
    log.info(f"Created {len(rendered_configs)} FRR config ConfigMaps")

    # --- Step 7: Create session-level ConfigMaps ---
    _create_session_configmaps(
        v1,
        session,
        session_yaml,
        constellation_source if isinstance(constellation_source, str) else None,
        gs_source if isinstance(gs_source, str) else None,
        namespace,
        owner_ref,
    )

    # --- Step 8: Compute pod placement ---
    available_nodes = discover_available_nodes()
    if not available_nodes:
        # Fallback: use the env var if no labeled nodes found (backward compat)
        fallback = os.environ.get("SESSION_NODE_NAME", "nodal")
        available_nodes = [fallback]
        log.warning(
            "No nodes with nodalarc.io/node-agent=true label — "
            "falling back to SESSION_NODE_NAME=%s",
            fallback,
        )

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

    # --- Step 8b: Measure and store substrate latency (multi-node only) ---
    if len(available_nodes) > 1:
        substrate = measure_substrate_latency(available_nodes)
        if substrate:
            _create_or_update_configmap(
                v1,
                "nodalarc-substrate-latency",
                namespace,
                substrate,
                owner_ref,
            )
            log.info("Stored substrate latency ConfigMap with %d entries", len(substrate))

    # --- Step 9: Create session pods ---
    sidecar_config = _build_sidecar_config(resolved)
    env_list = resolved.env

    created_pods = 0
    for node_id, vars in node_vars.items():
        node_type = vars["node_type"]
        pod_name = node_id.lower()
        cm_name = f"frr-config-{pod_name}"

        sidecar_env = _build_sidecar_env(node_id, vars, env_list) if sidecar_config else None

        _create_session_pod(
            v1=v1,
            pod_name=pod_name,
            namespace=namespace,
            node_id=node_id,
            node_type=node_type,
            plane=vars.get("plane"),
            slot=vars.get("slot"),
            gs_name=vars.get("gs_name"),
            config_cm_name=cm_name,
            sidecar_config=sidecar_config,
            sidecar_env=sidecar_env,
            probe_enabled=session.mi.enabled if session.mi else False,
            target_node=pod_placement.get(node_id),
            owner_ref=owner_ref,
        )
        created_pods += 1

    log.info(f"Created {created_pods} session pods")

    return {
        "phase": "Creating",
        "sessionId": session_id,
        "podCount": created_pods,
        "readyPods": 0,
        "wiredPods": 0,
        "message": f"Created {created_pods} pods, waiting for Running",
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
    import json as _json

    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

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
            "gnd_interfaces": [{"name": "gnd0"}],
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
        import ipaddress as _ipaddress

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
            "gnd_interfaces": [{"name": "gnd0"}],
            "terrestrial": {"addresses": addrs},
            "mpls_enable": True,
            "segment_routing": segment_routing,
            "mtu": 9000,
            "remove_default_route": True,
        }

        # All satellites are potential GS peers
        ground_bridges[gs_id] = {
            "satellites": [addressing.sat_id(s.plane, s.slot) for s in satellites],
        }

    # Count unique ISL links
    isl_pairs: set[tuple[str, str]] = set()
    for node_id, assignments in by_node.items():
        for na in assignments:
            isl_pairs.add((min(node_id, na.peer_node_id), max(node_id, na.peer_node_id)))

    manifest = {
        "session_id": spec.get("_session_id", "operator-session"),
        "generation": int(datetime.now(UTC).timestamp()),
        "nodes": nodes,
        "ground_bridges": ground_bridges,
        "isl_link_count": len(isl_pairs),
    }

    _create_or_update_configmap(
        v1,
        "nodalarc-topology-wiring",
        namespace,
        {"manifest.json": _json.dumps(manifest)},
        owner_ref,
    )
    log.info(
        f"Wrote topology wiring manifest: {len(nodes)} nodes, "
        f"{len(isl_pairs)} ISL links, {len(ground_bridges)} ground bridges"
    )
    return len(isl_pairs)


def set_nodalpath_mode(namespace: str, protocol: str) -> None:
    """Patch the NodalPath Deployment to use --mode live for NodalPath sessions,
    --mode console for all others. Called before restarting the NodalPath pod.
    """
    mode = "live" if protocol == "nodalpath" else "console"
    kubernetes.config.load_incluster_config()
    apps_v1 = kubernetes.client.AppsV1Api()
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


def restart_platform_pods(namespace: str) -> None:
    """Restart OME, Scheduler, VS-API, and NodalPath pods to pick up new session ConfigMaps.

    Deletes pods — the Deployments recreate them automatically.
    """
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

    for label in [
        "app=nodalarc-ome",
        "app=nodalarc-scheduler",
        "app=nodalarc-vs-api",
        "app=nodalarc-nodalpath",
    ]:
        pods = v1.list_namespaced_pod(namespace, label_selector=label)
        for pod in pods.items:
            try:
                v1.delete_namespaced_pod(pod.metadata.name, namespace)
                log.info(f"Restarted {pod.metadata.name}")
            except kubernetes.client.rest.ApiException:
                pass


def teardown_session(namespace: str) -> None:
    """Clean up session ConfigMaps (pods are garbage-collected via ownerReferences)."""
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

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


def check_pods_ready(namespace: str) -> tuple[int, int]:
    """Count total and ready session pods. Returns (total, ready).

    Matches pods with nodalarc.io/node-id label (present on both
    Operator-created and na_deploy-created session pods).
    """
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    total = len(pods.items)
    ready = sum(1 for p in pods.items if p.status and p.status.phase == "Running")
    return total, ready


def signal_frr_config_ready(namespace: str) -> int:
    """Copy FRR configs from ConfigMap staging dir and touch the config-ready sentinel.

    Replicates na_deploy.py's two-step config delivery:
      1. cp /etc/frr-config/* /etc/frr/  (ConfigMap mount → FRR config dir)
      2. touch /etc/frr/.config-ready     (sentinel that unblocks the entrypoint)

    The FRR entrypoint waits for the sentinel before starting daemons.

    Returns the number of pods signaled.
    """
    from kubernetes.stream import stream

    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    signaled = 0
    for pod in pods.items:
        pod_name = pod.metadata.name
        try:
            # Step 1: Copy configs from ConfigMap staging dir to /etc/frr/
            stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                container="frr",
                command=["sh", "-c", "cp /etc/frr-config/* /etc/frr/"],
                stderr=True,
                stdout=True,
                stdin=False,
                tty=False,
            )
            # Step 2: Touch sentinel to unblock entrypoint
            stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                container="frr",
                command=["touch", "/etc/frr/.config-ready"],
                stderr=True,
                stdout=True,
                stdin=False,
                tty=False,
            )
            signaled += 1
        except Exception as exc:
            log.warning(f"Failed to signal config-ready in {pod_name}: {exc}")
    log.info(f"Signaled FRR config-ready in {signaled}/{len(pods.items)} pods")
    return signaled


def write_pod_ips_configmap(namespace: str) -> None:
    """Write nodalarc-pod-ips ConfigMap from running session pods.

    Stores the IP map as a single 'pod-ips.json' key so it can be
    volume-mounted directly as a JSON file by the NodalPath Deployment.
    """
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
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

    # FRR container
    frr_container = kubernetes.client.V1Container(
        name="frr",
        image=os.environ.get("FRR_IMAGE", "nodalarc/frr:latest"),
        image_pull_policy="IfNotPresent",
        security_context=kubernetes.client.V1SecurityContext(
            capabilities=kubernetes.client.V1Capabilities(add=["NET_ADMIN", "NET_RAW", "SYS_ADMIN"])
        ),
        resources=kubernetes.client.V1ResourceRequirements(limits={"memory": "60Mi", "cpu": "50m"}),
        volume_mounts=[
            kubernetes.client.V1VolumeMount(
                name="frr-config",
                mount_path="/etc/frr-config",
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
                limits={"memory": "60Mi", "cpu": "50m"}
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
        ),
    )

    try:
        v1.create_namespaced_pod(namespace, pod)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 409:  # Already exists
            log.info(f"Pod {pod_name} already exists, skipping")
        else:
            raise
