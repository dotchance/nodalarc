"""Session deployer — renders configs, creates pods and ConfigMaps.

Replicates na_deploy.py Steps 3-5 using the K8s Python client.
Called by kopf handlers in handlers.py.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import kubernetes
import yaml
from jinja2 import Environment, FileSystemLoader
from nodalarc.models.addressing import AddressingScheme, assign_isl_neighbors, neighbors_by_node
from nodalarc.models.session import SessionConfig
from nodalarc.platform import get_platform_config
from nodalarc.stack_resolver import resolve_stack
from nodalarc.template_vars import build_template_vars

from ome.constellation_loader import expand_constellation, load_constellation, load_ground_stations

log = logging.getLogger(__name__)

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

    # --- Step 1: Parse spec into SessionConfig ---
    session_yaml = _spec_to_session_yaml(spec)
    session = SessionConfig.model_validate(yaml.safe_load(session_yaml))

    # --- Step 2: Load constellation and ground stations ---
    constellation = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)
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
    _create_session_configmaps(v1, session, session_yaml, namespace, owner_ref)

    # --- Step 8: Create session pods ---
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

    # Re-parse spec to get constellation/addressing/neighbors
    session_yaml = _spec_to_session_yaml(spec)
    session = SessionConfig.model_validate(yaml.safe_load(session_yaml))
    constellation = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)
    satellites = expand_constellation(constellation)
    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    resolved = resolve_stack(session.routing.protocol, session.routing.extensions)
    segment_routing = resolved.segment_routing
    ttl_propagation = resolved.ttl_propagation or "pipe"

    mpls_labels = str(get_platform_config().mpls_kernel_max_platform_labels)

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
            "sysctls": {
                "net.ipv6.conf.all.forwarding": "1",
                "net.mpls.platform_labels": mpls_labels,
                "net.ipv4.conf.all.rp_filter": "0",
                "net.ipv4.conf.default.rp_filter": "0",
            },
            "isl_interfaces": isl_interfaces,
            "gnd_interfaces": [{"name": "gnd0"}],
            "mpls_enable": True,
            "segment_routing": segment_routing,
            "ttl_propagation": ttl_propagation,
            "mtu": 9000,
            "remove_default_route": True,
        }
        if segment_routing:
            ttl_val = "0" if ttl_propagation == "pipe" else "1"
            nodes[node_id]["sysctls"]["net.mpls.ip_ttl_propagate"] = ttl_val

    # Ground stations
    ground_bridges: dict[str, dict] = {}
    for i, station in enumerate(gs_file.stations):
        gs_id = addressing.gs_id(station.name)

        # Terrestrial prefix addresses
        addrs = []
        if station.terrestrial_prefixes:
            for tp in station.terrestrial_prefixes:
                addrs.append(tp.prefix)
        elif gs_file.default_terrestrial_prefixes:
            tpl = gs_file.default_terrestrial_prefixes
            addrs.append(tpl.ipv4_template.format(gs_index=i))
            addrs.append(tpl.ipv6_template.format(gs_index=i))

        nodes[gs_id] = {
            "node_type": "ground_station",
            "gs_name": station.name,
            "gs_index": i,
            "sysctls": {
                "net.ipv6.conf.all.forwarding": "1",
                "net.mpls.platform_labels": mpls_labels,
                "net.ipv4.conf.all.rp_filter": "0",
                "net.ipv4.conf.default.rp_filter": "0",
            },
            "isl_interfaces": [],
            "gnd_interfaces": [{"name": "gnd0"}],
            "terrestrial": {"addresses": addrs},
            "mpls_enable": True,
            "segment_routing": segment_routing,
            "ttl_propagation": ttl_propagation,
            "mtu": 9000,
            "remove_default_route": True,
        }
        if segment_routing:
            ttl_val = "0" if ttl_propagation == "pipe" else "1"
            nodes[gs_id]["sysctls"]["net.mpls.ip_ttl_propagate"] = ttl_val

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


def restart_platform_pods(namespace: str) -> None:
    """Restart OME, Scheduler, and VS-API pods to pick up new session ConfigMaps.

    Deletes pods — the Deployments recreate them automatically.
    """
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()

    for label in ["app=nodalarc-ome", "app=nodalarc-scheduler", "app=nodalarc-vs-api"]:
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


def write_pod_ips_configmap(namespace: str) -> None:
    """Write nodalarc-pod-ips ConfigMap from running session pods."""
    kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace, label_selector="nodalarc.io/node-id")
    data = {}
    for pod in pods.items:
        node_id = pod.metadata.labels.get("nodalarc.io/node-id", "")
        if node_id and pod.status and pod.status.pod_ip:
            data[node_id] = pod.status.pod_ip
    _create_or_update_configmap(v1, "nodalarc-pod-ips", namespace, data, owner_ref=None)
    log.info(f"Wrote nodalarc-pod-ips with {len(data)} entries")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_area_assignment(routing: dict) -> dict:
    """Build area_assignment config from routing spec.

    OSPF uses dotted IP area format (0.0.0.0 for backbone).
    IS-IS uses NET area format (49.0001).
    """
    strategy = routing.get("areaStrategy", "flat")
    protocol = routing.get("protocol", "isis")

    gs_area_id = "0.0.0.0" if protocol == "ospf" else "49.0001"

    result: dict[str, Any] = {
        "strategy": strategy,
        "gs_area_id": gs_area_id,
    }
    if strategy == "stripe":
        result["planes_per_stripe"] = routing.get("planesPerStripe", 1)
    return result


def _spec_to_session_yaml(spec: dict) -> str:
    """Convert ConstellationSpec CR spec to a SessionConfig-compatible YAML string."""
    routing = spec.get("routing", {})
    time_cfg = spec.get("time", {})

    session_dict: dict[str, Any] = {
        "session": {"name": "operator-session"},
        "constellation": f"configs/constellations/{spec['constellation']}.yaml",
        "ground_stations": f"configs/ground-stations/sets/{spec['groundStations']}.yaml",
        "routing": {
            "protocol": routing.get("protocol", "isis"),
            "extensions": routing.get("extensions", []),
            "config_overrides": routing.get("configOverrides", {}),
            "area_assignment": _build_area_assignment(routing),
        },
        "time": {
            "compression": time_cfg.get("compression", 1),
            "step_seconds": time_cfg.get("stepSeconds", 1),
        },
        "addressing": {},
    }
    if routing.get("stack"):
        session_dict["routing"]["stack"] = routing["stack"]
    if time_cfg.get("startTime"):
        session_dict["time"]["start_time"] = time_cfg["startTime"]
    if spec.get("satelliteType"):
        session_dict["satellite_type"] = spec["satelliteType"]
    if spec.get("mi", {}).get("enabled"):
        session_dict["mi"] = {"enabled": True}

    return yaml.dump(session_dict, default_flow_style=False)


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

    # Constellation YAML
    constellation_path = Path(session.constellation)
    if constellation_path.exists():
        _create_or_update_configmap(
            v1,
            "nodalarc-constellation",
            namespace,
            {"constellation.yaml": constellation_path.read_text()},
            owner_ref,
        )

    # Ground stations
    if isinstance(session.ground_stations, str):
        gs_path = Path(session.ground_stations)
        if gs_path.exists():
            _create_or_update_configmap(
                v1,
                "nodalarc-ground-stations",
                namespace,
                {"ground-stations.yaml": gs_path.read_text()},
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
        image=cfg.frr_image if hasattr(cfg, "frr_image") else "nodalarc/frr:latest",
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
