"""na-deploy — 11-step startup sequence from PRD 13.23.

Each step is fail-hard: any failure aborts the entire deployment.

Usage: python -m tools.na_deploy --session configs/sessions/starlink-early-44-isis-flat.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.routing_stack import RoutingStackConfig
from nodalarc.models.session import SessionConfig
from nodalarc.template_vars import build_template_vars
from nodalarc.zmq_channels import vs_api_http_port

from ome.constellation_loader import expand_constellation, load_constellation, load_ground_stations

log = logging.getLogger(__name__)


def _fail(msg: str) -> None:
    log.error(msg)
    sys.exit(1)


def _teardown_previous() -> None:
    """Kill stale backend processes and remove any existing Helm release + pods."""
    # Kill known backend modules from any previous session
    for module in (
        "ome.main",
        "orchestrator.main",
        "scheduler",
        "node_agent",
        "vs_api.main",
        "measurement.mi_main",
        "nodalpath",
    ):
        result = subprocess.run(
            ["pgrep", "-f", f"python.*-m {module}"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.strip().splitlines():
            pid = line.strip()
            if pid:
                log.info(f"Killing stale {module} (PID {pid})")
                subprocess.run(["kill", pid], capture_output=True)
    # Kill stale Vite dev server
    subprocess.run(["pkill", "-f", "node_modules/.bin/vite"], capture_output=True)
    # Brief pause to let processes exit
    time.sleep(1)

    # Clean up ground bridge infrastructure from host namespace
    try:
        from orchestrator.link_manager import teardown_all_ground_infra

        teardown_all_ground_infra()
    except Exception as exc:
        log.warning(f"Ground bridge cleanup: {exc}")

    # Uninstall any Helm releases in the namespace
    from nodalarc.platform import get_platform_config

    ns = get_platform_config().kubernetes_namespace
    result = subprocess.run(
        ["helm", "list", "-n", ns, "--short"],
        capture_output=True,
        text=True,
    )
    for release in result.stdout.strip().splitlines():
        release = release.strip()
        if not release:
            continue
        log.info(f"Uninstalling Helm release: {release}")
        subprocess.run(
            ["helm", "uninstall", release, "-n", ns],
            capture_output=True,
            text=True,
        )

    # Wait for all pods to terminate (up to 2 minutes)
    for tick in range(120):
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", ns, "--no-headers"],
            capture_output=True,
            text=True,
        )
        pod_lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        if not pod_lines:
            break
        if tick % 10 == 0:
            log.info(f"Waiting for {len(pod_lines)} pods to terminate...")
        time.sleep(1)
    else:
        log.warning("Some pods did not terminate in 2 minutes, proceeding anyway")

    log.info("Previous session cleaned up")


def _apply_configmap(name: str, ns: str, data: dict[str, str]) -> None:
    """Create or update a ConfigMap via dry-run + apply."""
    args = ["kubectl", "create", "configmap", name, "-n", ns, "--dry-run=client", "-o", "yaml"]
    for key, value in data.items():
        args.append(f"--from-literal={key}={value}")
    dry_run = subprocess.run(args, capture_output=True, text=True)
    if dry_run.returncode != 0:
        log.warning(f"Failed to generate ConfigMap {name}: {dry_run.stderr}")
        return
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"], input=dry_run.stdout, text=True, capture_output=True
    )
    if result.returncode != 0:
        log.warning(f"Failed to apply ConfigMap {name}: {result.stderr}")
    else:
        log.info(f"ConfigMap {name} applied ({len(data)} keys)")


def deploy(
    session_path: str,
    skip_vsapi: bool = False,
    skip_teardown: bool = False,
    host_nodalpath: bool = False,
    host_ome: bool = False,
) -> None:
    """Execute the 11-step startup sequence."""
    # === Step 0: Teardown previous session ===
    if not skip_teardown:
        log.info("Step 0: Teardown previous session")
        _teardown_previous()

    # === Step 1: Load and validate ===
    log.info("Step 1: Load and validate session config")
    raw = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(raw)
    constellation = load_constellation(session.constellation)
    gs_file = load_ground_stations(session.ground_stations)

    ts = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
    session_id = f"{session.session.name}-{ts}".lower()
    data_dir = Path(session.session.data_dir) / session_id
    data_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Deploying {session_id} to {data_dir}")

    addressing = AddressingScheme(session.addressing)
    satellites = expand_constellation(constellation)

    # Load routing stack — legacy (routing.stack) or resolved (routing.protocol)
    resolved = None
    if session.routing.stack is not None:
        # Legacy path: load from stack directory
        stack_dir = Path(session.routing.stack)
        stack_yaml = yaml.safe_load((stack_dir / "stack.yaml").read_text())
        stack_config = RoutingStackConfig.model_validate(stack_yaml["stack"])
    else:
        # Resolved path: derive from protocol + extensions
        from nodalarc.stack_resolver import resolve_stack

        resolved = resolve_stack(session.routing.protocol, session.routing.extensions)
        stack_config = None
        stack_dir = None

    # Determine if NodalPath should run in live mode (push MPLS forwarding almanacs).
    # Only "nodalpath" protocol uses the NEBULA model — centralized path computation
    # with gRPC push to nodalpath-fwd sidecars. All other protocols (OSPF, IS-IS,
    # static-SR) use FRR's distributed routing — NodalPath runs in console-only mode.
    _is_nodalpath = (resolved is not None and session.routing.protocol == "nodalpath") or (
        stack_dir is not None and stack_dir.name == "nodalpath-fwd"
    )

    # === Step 2: Start OME (continuous mode) ===
    # By default OME runs as a K8s Deployment with ZMQ streaming (no files).
    # Use --host-ome to run as a host subprocess with file-based timeline.
    ome_proc = None
    timeline_path = None
    if not host_ome:
        log.info("Step 2: OME running as K8s Deployment (ZMQ streaming on port 5560)")
        # No sentinel polling — orchestrator subscribes directly to OME ZMQ.
        # OME publishes FullStateSnapshot every 30s for subscriber catchup.
    else:
        log.info("Step 2: Start OME (continuous mode, host subprocess)")
        ome_log = open(data_dir / "ome.log", "w")
        ome_proc = subprocess.Popen(
            [sys.executable, "-m", "ome.main", "--continuous", session_path, "-o", str(data_dir)],
            stdout=ome_log,
            stderr=ome_log,
        )
        log.info(f"OME PID: {ome_proc.pid}")
        # Wait for sentinel (host OME writes file + sentinel)
        sentinel = data_dir / f"{session.session.name}-timeline.ready"
        for _ in range(300):
            if sentinel.exists():
                timeline_path = Path(sentinel.read_text().strip())
                break
            time.sleep(1)
        else:
            _fail("OME did not produce first window in 5 minutes")
        log.info(f"Timeline: {timeline_path}")

    # === Step 3: Build template variables ===
    log.info("Step 3: Build template variables")
    if resolved is not None:
        config_overrides = dict(resolved.template_variables)
    else:
        config_overrides = dict(stack_config.template_variables)
    config_overrides.update(session.routing.config_overrides)

    node_vars: dict[str, dict] = {}
    for sat in satellites:
        node_id = addressing.sat_id(sat.plane, sat.slot)
        vars = build_template_vars(
            session=session,
            constellation=constellation,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=sat.plane,
            slot=sat.slot,
            config_overrides=config_overrides,
        )
        node_vars[node_id] = vars

    for i, station in enumerate(gs_file.stations):
        node_id = addressing.gs_id(station.name)
        vars = build_template_vars(
            session=session,
            constellation=constellation,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="ground_station",
            gs_name=station.name,
            gs_index=i,
            config_overrides=config_overrides,
        )
        node_vars[node_id] = vars

    # === Step 4: Render routing configurations ===
    log.info("Step 4: Render routing configurations")
    if resolved is not None:
        template_dir = str(Path("configs/templates/frr").resolve())
        template_list = resolved.template_files
        daemon_list = resolved.daemons
    else:
        template_dir = str(stack_dir)
        template_list = [(t.src, t.dst) for t in stack_config.config_templates]
        daemon_list = stack_config.daemons or []
    env = Environment(
        loader=FileSystemLoader(template_dir),
        keep_trailing_newline=True,
    )
    configs_dir = data_dir / "configs"
    for node_id, vars in node_vars.items():
        node_dir = configs_dir / node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        for tpl_src, tpl_dst in template_list:
            tpl = env.get_template(tpl_src)
            rendered = tpl.render(**vars)
            dest_name = Path(tpl_dst).name
            (node_dir / dest_name).write_text(rendered)
        # Generate daemons file
        if daemon_list:
            all_frr_daemons = [
                "mgmtd",  # FRR 10.x config management daemon (must be first)
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
            daemons_content = (
                "\n".join(f"{d}={'yes' if d in daemon_list else 'no'}" for d in all_frr_daemons)
                + "\n"
            )
            (node_dir / "daemons").write_text(daemons_content)
        log.info(f"  Rendered configs for {node_id}")

    # === Step 5: Deploy K3s pods ===
    log.info("Step 5: Deploy K3s pods")
    # Build sidecar config from stack if the stack uses a non-FRR image
    sidecar_config: dict | None = None
    _image = resolved.image if resolved else stack_config.image
    _env_list = (
        resolved.env if resolved else [{"name": e.name, "value": e.value} for e in stack_config.env]
    )
    _capabilities = (
        resolved.security_context_capabilities
        if resolved
        else (stack_config.security_context.capabilities if stack_config.security_context else [])
    )
    if _image and not _image.startswith("nodalarc/frr"):
        sidecar_config = {
            "image": _image,
            "capabilities": _capabilities or ["NET_ADMIN", "NET_RAW", "SYS_ADMIN"],
        }

    def _build_env(nid: str) -> list[dict]:
        vars_for_node = node_vars.get(nid, {})
        result = []
        for e in _env_list:
            val = e["value"]
            # Substitute any {{ var_name }} from the node's template vars
            val = val.replace("{{ node_id }}", nid)
            for k, v in vars_for_node.items():
                val = val.replace("{{ " + k + " }}", str(v))
            result.append({"name": e["name"], "value": val})
        return result

    helm_values = {
        "satellites": [
            {
                "nodeId": nid,
                "plane": vars["plane"],
                "slot": vars["slot"],
                **({"env": _build_env(nid)} if sidecar_config and _env_list else {}),
            }
            for nid, vars in node_vars.items()
            if vars["node_type"] == "satellite"
        ],
        "groundStations": [
            {
                "nodeId": nid,
                "gsName": vars["gs_name"],
                **({"env": _build_env(nid)} if sidecar_config and _env_list else {}),
            }
            for nid, vars in node_vars.items()
            if vars["node_type"] == "ground_station"
        ],
        "mode": "rt",
        "sessionConfig": session_path,
        "timelineFile": str(timeline_path),
    }
    if sidecar_config:
        helm_values["sidecar"] = sidecar_config
    if not host_nodalpath:
        # Containerized NodalPath (default) — detect host IP for ZMQ headless Service
        host_ip_result = subprocess.run(
            [
                "kubectl",
                "get",
                "node",
                "-o",
                "jsonpath={.items[0].status.addresses[?(@.type=='InternalIP')].address}",
            ],
            capture_output=True,
            text=True,
        )
        host_ip = host_ip_result.stdout.strip() or "127.0.0.1"
        log.info(f"Host IP for ZMQ services: {host_ip}")
        helm_values["nodalpath"] = {
            "enabled": True,
            "mode": "live" if _is_nodalpath else "console",
            "transport": "grpc" if _is_nodalpath else "grpc",
        }
        helm_values["hostZmq"] = {"enabled": True, "hostIP": host_ip}
    if not host_ome:
        helm_values["ome"] = {"enabled": True}
    # Only deploy probe sidecars when MI is enabled
    helm_values["probeEnabled"] = session.mi.enabled if session.mi else False
    values_file = data_dir / "helm-values.yaml"
    values_file.write_text(yaml.dump(helm_values))

    from nodalarc.platform import get_platform_config

    ns = get_platform_config().kubernetes_namespace

    # Write ConfigMaps BEFORE helm install — containerized OME and NodalPath
    # need these at pod startup. Pod-IPs ConfigMap is written later (needs running pods).
    log.info("Writing ConfigMaps for containerized components...")

    # Session YAML — rewrite file paths for container mount locations
    container_session = dict(raw)
    container_session["constellation"] = "/etc/nodalarc/constellation.yaml"
    if isinstance(session.ground_stations, str):
        container_session["ground_stations"] = "/etc/nodalarc/ground-stations.yaml"
    _apply_configmap(
        "nodalarc-session",
        ns,
        {
            "session.yaml": yaml.dump(container_session, default_flow_style=False),
        },
    )

    # Constellation YAML
    _apply_configmap(
        "nodalarc-constellation",
        ns,
        {
            "constellation.yaml": Path(session.constellation).read_text(),
        },
    )

    # Ground stations — file content (skip for inline lists — pod mounts are optional)
    if isinstance(session.ground_stations, str):
        gs_path = Path(session.ground_stations)
        if gs_path.exists():
            _apply_configmap(
                "nodalarc-ground-stations",
                ns,
                {
                    "ground-stations.yaml": gs_path.read_text(),
                },
            )

    # Platform config — container version with per-service ZMQ connect hosts.
    # OME has its own K8s Service (nodalarc-ome). Scheduler owns TO events
    # (port 5561) via nodalarc-scheduler Service.
    host_platform = yaml.safe_load(Path("configs/platform.yaml").read_text())
    container_platform = dict(host_platform.get("platform", host_platform))
    container_platform["zmq_connect_hosts"] = {
        "ome": "nodalarc-ome",
        "orchestrator": "nodalarc-scheduler-control",  # REQ/REP (scenario, playback)
        "scheduler-events": "nodalarc-scheduler-events",  # PUB/SUB (headless)
        "vs-api": "nodalarc-scheduler-control",
    }
    _apply_configmap(
        "nodalarc-platform-config",
        ns,
        {
            "platform.yaml": yaml.dump({"platform": container_platform}, default_flow_style=False),
        },
    )

    # NodalPath config
    nodalpath_config_path = Path("configs/nodalpath.yaml")
    if nodalpath_config_path.exists():
        _apply_configmap(
            "nodalarc-nodalpath-config",
            ns,
            {
                "nodalpath.yaml": nodalpath_config_path.read_text(),
            },
        )

    result = subprocess.run(
        [
            "helm",
            "upgrade",
            "--install",
            session_id,
            "deploy/helm",
            "-n",
            ns,
            "--create-namespace",
            "-f",
            str(values_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _fail(f"Helm install failed: {result.stderr}")
    log.info("Helm install complete")

    # For ZMQ OME: no sentinel polling needed. The orchestrator subscribes
    # directly to the OME ZMQ Service and catches up via FullStateSnapshot.

    # === Step 5+6: Wait for pods and deliver configs progressively ===
    # Deliver configs to pods as they become Running, rather than waiting
    # for all pods first. This prevents early pods from timing out on the
    # config sentinel while late pods are still starting.
    log.info("Waiting for pods and delivering configs progressively...")
    expected_pods = {nid.lower() for nid in node_vars}
    configured_pods: set[str] = set()
    for _tick in range(600):  # 10 minute timeout
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                ns,
                "-l",
                "nodalarc.io/node-id",
                "-o",
                'jsonpath={range .items[*]}{.metadata.name} {.status.phase}{"\\n"}{end}',
            ],
            capture_output=True,
            text=True,
        )
        running_pods: set[str] = set()
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[1] == "Running":
                running_pods.add(parts[0])

        # Deliver configs to newly-Running pods
        newly_ready = running_pods - configured_pods
        for pod_name in sorted(newly_ready):
            # Find the node_id (original case) for this pod
            node_id = None
            for nid in node_vars:
                if nid.lower() == pod_name:
                    node_id = nid
                    break
            if node_id is None:
                continue
            node_dir = configs_dir / node_id
            cp_result = subprocess.run(
                ["kubectl", "cp", str(node_dir) + "/.", f"{ns}/{pod_name}:/etc/frr/", "-c", "frr"],
                capture_output=True,
                text=True,
            )
            if cp_result.returncode != 0:
                log.warning(f"Config copy failed for {node_id}, will retry: {cp_result.stderr}")
                continue
            touch_result = subprocess.run(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    ns,
                    pod_name,
                    "-c",
                    "frr",
                    "--",
                    "touch",
                    "/etc/frr/.config-ready",
                ],
                capture_output=True,
                text=True,
            )
            if touch_result.returncode != 0:
                log.warning(
                    f"Config signal failed for {node_id}, will retry: {touch_result.stderr}"
                )
                continue
            configured_pods.add(pod_name)
            if len(configured_pods) % 20 == 0 or len(configured_pods) == len(expected_pods):
                log.info(f"  Configured {len(configured_pods)}/{len(expected_pods)} pods")

        if configured_pods >= expected_pods:
            break
        time.sleep(1)
    else:
        missing = expected_pods - configured_pods
        _fail(f"Timeout: {len(missing)} pods never became ready: {sorted(missing)[:10]}...")
    log.info(f"All {len(configured_pods)} pods configured")

    # Write pod IP ConfigMap (needs running pods — other ConfigMaps created before helm install)
    pod_ip_data = {}
    for pod_name in sorted(configured_pods):
        nid = next((n for n in node_vars if n.lower() == pod_name), pod_name)
        ip_result = subprocess.run(
            ["kubectl", "get", "pod", pod_name, "-n", ns, "-o", "jsonpath={.status.podIP}"],
            capture_output=True,
            text=True,
        )
        if ip_result.returncode == 0 and ip_result.stdout.strip():
            pod_ip_data[nid] = ip_result.stdout.strip()
    _apply_configmap("nodalarc-pod-ips", ns, {"pod-ips.json": json.dumps(pod_ip_data)})

    # Wait for FRR daemons to start (entrypoint waits for sentinel, then launches)
    log.info("Waiting 5s for FRR daemons to start...")
    time.sleep(5)

    # === Step 7: Wire data plane ===
    log.info("Step 7: Wire data plane")
    from orchestrator.link_manager import (
        configure_interface,
        create_dummy_interface,
        create_ground_bridge,
        create_satellite_ground_veth,
        create_veth_pair,
        discover_pod_pids,
        enable_mpls_input,
        set_interface_up,
    )

    # Retry PID discovery — containers may still be initializing
    for attempt in range(5):
        pid_map = discover_pod_pids(namespace=ns)
        if all(pid > 0 for pid in pid_map.values()):
            break
        log.info(f"Some PIDs are 0, retrying in 3s (attempt {attempt + 1}/5)...")
        time.sleep(3)
    if any(pid == 0 for pid in pid_map.values()):
        zero_nodes = [n for n, p in pid_map.items() if p == 0]
        _fail(f"Could not discover PIDs for: {zero_nodes}")
    log.info(f"Discovered PIDs for {len(pid_map)} pods")

    # Configure kernel networking in each pod namespace.
    # K3s mounts /proc/sys read-only inside containers, so we use nsenter
    # to enter the network namespace and write sysctls from the host.
    mpls_labels = str(get_platform_config().mpls_kernel_max_platform_labels)
    for _node_id, pid in pid_map.items():
        for sysctl_key, value in [
            ("net.ipv6.conf.all.forwarding", "1"),
            ("net.mpls.platform_labels", mpls_labels),
            # Disable rp_filter on gnd0 — ground links use tc mirred redirect
            # between /32-addressed veths; rp_filter (even loose mode) drops
            # multicast from unroutable source IPs, preventing OSPF adjacency.
            ("net.ipv4.conf.all.rp_filter", "0"),
            ("net.ipv4.conf.default.rp_filter", "0"),
        ]:
            result = subprocess.run(
                [
                    "nsenter",
                    "--target",
                    str(pid),
                    "--net",
                    "--",
                    "sysctl",
                    "-w",
                    f"{sysctl_key}={value}",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                _fail(f"Failed to set {sysctl_key}={value} in ns({pid}): {result.stderr}")
    # Set ip_ttl_propagate for SR stacks (controls traceroute visibility)
    _segment_routing = resolved.segment_routing if resolved else stack_config.segment_routing
    _ttl_propagation = resolved.ttl_propagation if resolved else stack_config.ttl_propagation
    if _segment_routing:
        ttl_val = "0" if _ttl_propagation == "pipe" else "1"
        for _node_id, pid in pid_map.items():
            result = subprocess.run(
                [
                    "nsenter",
                    "--target",
                    str(pid),
                    "--net",
                    "--",
                    "sysctl",
                    "-w",
                    f"net.mpls.ip_ttl_propagate={ttl_val}",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log.warning(
                    f"Failed to set ip_ttl_propagate={ttl_val} in ns({pid}): {result.stderr}"
                )
        log.info(f"Set net.mpls.ip_ttl_propagate={ttl_val} for SR stack")

    log.info("Configured IPv6 forwarding and MPLS in all pod namespaces")

    # Compute ISL neighbor assignments to know which veths to create
    from nodalarc.models.addressing import assign_isl_neighbors, neighbors_by_node

    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    # Count total unique ISL links
    total_pairs: set[tuple[str, str]] = set()
    for node_id, assignments in by_node.items():
        for na in assignments:
            total_pairs.add((min(node_id, na.peer_node_id), max(node_id, na.peer_node_id)))
    log.info(f"{len(total_pairs)} veth pairs to create")

    # Create veth pair for each unique ISL link (deduplicate A→B and B→A)
    created_links: set[tuple[str, str]] = set()
    for node_id, assignments in by_node.items():
        pid_a = pid_map.get(node_id)
        if pid_a is None:
            _fail(f"No PID for node {node_id}")
        for na in assignments:
            pair = (min(node_id, na.peer_node_id), max(node_id, na.peer_node_id))
            if pair in created_links:
                continue
            pid_b = pid_map.get(na.peer_node_id)
            if pid_b is None:
                _fail(f"No PID for peer node {na.peer_node_id}")
            # Find the peer's interface name for this link
            peer_iface = na.interface  # This node's interface
            peer_assignments = by_node.get(na.peer_node_id, [])
            remote_iface = ""
            for pa in peer_assignments:
                if pa.peer_node_id == node_id:
                    remote_iface = pa.interface
                    break
            if not remote_iface:
                log.warning(f"No reciprocal assignment for {node_id} <-> {na.peer_node_id}")
                continue

            create_veth_pair(
                pid_a,
                pid_b,
                peer_iface,
                remote_iface,
                node_id_a=node_id,
                node_id_b=na.peer_node_id,
            )
            created_links.add(pair)

    log.info(f"Created {len(created_links)} veth pairs (all admin down)")

    # Enable MPLS input on ISL interfaces (no shelling out — PRD 13.6)
    for node_id, assignments in by_node.items():
        pid = pid_map[node_id]
        for na in assignments:
            enable_mpls_input(pid, na.interface)
    log.info("Enabled MPLS input on all ISL interfaces")

    # Create GS-side veths for each ground station
    bridge_map: dict[str, str] = {}  # gs_id → gs_port_name
    for _i, station in enumerate(gs_file.stations):
        gs_id = addressing.gs_id(station.name)
        gs_pid = pid_map.get(gs_id)
        if gs_pid is None:
            _fail(f"No PID for ground station {gs_id}")
        gs_port = create_ground_bridge(gs_id, gs_pid)
        bridge_map[gs_id] = gs_port
        # Configure GS gnd0: deterministic MAC, disable IPv6 autoconfig, MPLS
        configure_interface(gs_pid, "gnd0", gs_id)
        enable_mpls_input(gs_pid, "gnd0")
        # Bring GS gnd0 UP (permanently — never touched during handoffs)
        set_interface_up(gs_pid, "gnd0")

    log.info(f"Created {len(bridge_map)} GS ground link veths")

    # Create satellite ground veths (all start admin DOWN)
    sat_gnd_map: dict[str, str] = {}  # sat_id → host_side_veth_name
    for sat in satellites:
        sat_id = addressing.sat_id(sat.plane, sat.slot)
        sat_pid = pid_map.get(sat_id)
        if sat_pid is None:
            _fail(f"No PID for satellite {sat_id}")
        host_name, _ = create_satellite_ground_veth(sat_id, sat_pid)
        sat_gnd_map[sat_id] = host_name
        # Configure satellite gnd0: deterministic MAC, disable IPv6 autoconfig, MPLS
        configure_interface(sat_pid, "gnd0", sat_id)
        enable_mpls_input(sat_pid, "gnd0")

    log.info(f"Created {len(sat_gnd_map)} satellite ground veths (all DOWN)")

    # Save ground link maps for orchestrator/debugging
    bridge_map_file = data_dir / "bridge_map.json"
    bridge_map_file.write_text(json.dumps(bridge_map))
    sat_gnd_map_file = data_dir / "sat_gnd_map.json"
    sat_gnd_map_file.write_text(json.dumps(sat_gnd_map))
    log.info(f"Saved bridge_map and sat_gnd_map to {data_dir}")

    # Create dummy terr0 interfaces for ground stations
    for i, station in enumerate(gs_file.stations):
        gs_id = addressing.gs_id(station.name)
        gs_pid = pid_map.get(gs_id)
        if gs_pid is None:
            _fail(f"No PID for ground station {gs_id}")
        addrs = []
        if station.terrestrial_prefixes:
            for tp in station.terrestrial_prefixes:
                addrs.append(tp.prefix)
        else:
            tpl = gs_file.default_terrestrial_prefixes
            if tpl:
                addrs.append(tpl.ipv4_template.format(gs_index=i))
                addrs.append(tpl.ipv6_template.format(gs_index=i))
        create_dummy_interface(gs_pid, "terr0", addrs)

    log.info(f"Created terr0 dummy interfaces for {len(gs_file.stations)} ground stations")

    # Remove default route from each pod so traffic goes through FRR, not K3s overlay.
    # eth0 stays (needed for kubectl exec) but must not participate in forwarding.
    removed_default = 0
    for node_id, pid in pid_map.items():
        result = subprocess.run(
            ["nsenter", "--target", str(pid), "--net", "--", "ip", "route", "del", "default"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            removed_default += 1
        else:
            log.warning(f"Failed to remove default route in {node_id}: {result.stderr.strip()}")
    log.info(f"Removed default route from {removed_default}/{len(pid_map)} pods")

    # Save pid_map for orchestrator
    pid_map_file = data_dir / "pid_map.json"
    pid_map_file.write_text(json.dumps(pid_map))
    log.info(f"PID map saved to {pid_map_file}")

    # === Step 8: Start MI ===
    mi_db = str(data_dir / "session.db")
    mi_proc = None
    mi_enabled = session.mi.enabled if session.mi else False
    if mi_enabled:
        log.info("Step 8: Start MI service (adapter=%s)", session.mi.adapter)
        mi_log = open(data_dir / "mi.log", "w")
        mi_proc = subprocess.Popen(
            [sys.executable, "-m", "measurement.mi_main", "--session", session_path, "--db", mi_db],
            stdout=mi_log,
            stderr=mi_log,
        )
        log.info(f"MI service PID: {mi_proc.pid}")
    else:
        log.info("Step 8: MI disabled, skipping")

    # === Step 9: Configure probe flows ===
    log.info("Step 9: Configure probe flows (skipped in Phase 1B)")

    # === Start deploy daemon (needed by VS-API introspect) ===
    log.info("Starting deploy daemon...")
    daemon_proc = subprocess.Popen(
        [sys.executable, "-m", "tools.deploy_daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info(f"Deploy daemon PID: {daemon_proc.pid}")
    # Wait for socket to appear
    daemon_sock_path = get_platform_config().deploy_daemon_unix_socket_path
    for _wait in range(20):
        if Path(daemon_sock_path).exists():
            break
        time.sleep(0.5)
    else:
        log.warning("Deploy daemon socket did not appear in 10s")

    # === Step 10: Start VS-API ===
    api_key = os.environ.get("NODAL_API_KEY", "") or secrets.token_urlsafe(32)
    if not skip_vsapi:
        log.info("Step 10: Start VS-API")
        vsapi_env = {**os.environ, "NODAL_API_KEY": api_key}
        vsapi_log = open(data_dir / "vsapi.log", "w")
        vsapi_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "vs_api.main",
                "--session",
                session_path,
                "--db",
                mi_db,
                "--port",
                str(vs_api_http_port()),
            ],
            stdout=vsapi_log,
            stderr=vsapi_log,
            env=vsapi_env,
        )
        log.info(f"VS-API PID: {vsapi_proc.pid}")
        # Wait for VS-API to be healthy before starting orchestrator
        import urllib.request

        for _attempt in range(30):
            try:
                req = urllib.request.Request(
                    f"http://localhost:{vs_api_http_port()}/api/v1/state",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                urllib.request.urlopen(req, timeout=1)
                log.info("VS-API healthy")
                break
            except Exception:
                time.sleep(0.5)
        else:
            log.warning("VS-API did not become healthy in 15s, proceeding anyway")
    else:
        log.info("Step 10: Skipping VS-API (--skip-vsapi)")
        vsapi_proc = None

    # === Step 10b: Start Vite dev server (skip during session switches) ===
    if not skip_vsapi:
        log.info("Step 10b: Start Vite dev server")
        # Kill any existing Vite on port 3000
        subprocess.run(["pkill", "-f", "node_modules/.bin/vite"], capture_output=True)
        time.sleep(1)
        # Ensure inotify instance limit is high enough for Vite's file watchers
        subprocess.run(
            ["sysctl", "-w", "fs.inotify.max_user_instances=512"],
            capture_output=True,
        )
        vite_env = {**os.environ, "VITE_API_KEY": api_key}
        vite_log = open(data_dir / "vite.log", "w")
        vite_proc = subprocess.Popen(
            ["bash", "-c", "ulimit -n 65536; exec npx vite --host 0.0.0.0 --port 3000"],
            cwd=str(Path("visualization").resolve()),
            stdout=vite_log,
            stderr=vite_log,
            env=vite_env,
        )
        log.info(f"Vite dev server PID: {vite_proc.pid}")
        # Wait for port 3000 to be listening (up to 15s)
        import socket

        for _vite_wait in range(30):
            try:
                with socket.create_connection(("127.0.0.1", 3000), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            log.warning("Vite dev server did not start listening on port 3000 in 15s")
    else:
        log.info("Step 10b: Skipping Vite (session switch — VF fetches new key at runtime)")
        vite_proc = None

    # === Step 11: Scheduler + Node Agent (K8s pods) ===
    # Both are deployed by the Helm chart as K8s Deployment/DaemonSet.
    # Wait for them to be ready.
    log.info("Step 11: Waiting for Scheduler + Node Agent pods")
    for label, name in [
        ("app=nodalarc-scheduler", "Scheduler"),
        ("app=nodalarc-node-agent", "Node Agent"),
    ]:
        for _wait in range(60):
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", ns, "-l", label, "--no-headers"],
                capture_output=True,
                text=True,
            )
            if "Running" in result.stdout:
                log.info(f"{name} pod Running")
                break
            time.sleep(2)
        else:
            log.warning(f"{name} pod did not reach Running in 120s")

    # === Step 11b: Start NodalPath ===
    # Always start NodalPath console. For nodalpath-fwd sessions, run in live
    # mode (ZMQ + push). For all other sessions, run in console-only mode
    # so the operator UI is always accessible on port 3100.
    # By default NodalPath runs as a K8s Deployment (containerized).
    # Use --host-nodalpath to run as a host subprocess instead.
    nodalpath_proc = None
    if not host_nodalpath:
        log.info("Step 11b: NodalPath running as K8s Deployment (skipping host subprocess)")
    else:
        np_log = open(data_dir / "nodalpath.log", "w")
        if _is_nodalpath:
            log.info("Step 11b: Start NodalPath (live mode)")
            nodalpath_proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "nodalpath",
                    "--session",
                    session_path,
                    "--mode",
                    "live",
                    "--transport",
                    "grpc",
                    "--namespace",
                    ns,
                ],
                stdout=np_log,
                stderr=np_log,
            )
        else:
            log.info("Step 11b: Start NodalPath (console + live path trace)")
            nodalpath_proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "nodalpath",
                    "--mode",
                    "console",
                    "--session",
                    session_path,
                    "--namespace",
                    ns,
                ],
                stdout=np_log,
                stderr=np_log,
            )
        log.info(f"NodalPath PID: {nodalpath_proc.pid}")

    # === Complete — save session state and print summary ===
    vsapi_pid = vsapi_proc.pid if vsapi_proc else 0
    session_state = {
        "session_id": session_id,
        "data_dir": str(data_dir),
        "timeline": str(timeline_path) if timeline_path else "",
        "ome_pid": ome_proc.pid if ome_proc else 0,
        "mi_pid": mi_proc.pid if mi_proc else 0,
        "vsapi_pid": vsapi_pid,
        "scheduler": "k8s-deployment",
        "node_agent": "k8s-daemonset",
        "daemon_pid": daemon_proc.pid,
        "vite_pid": vite_proc.pid if vite_proc else 0,
        "nodalpath_pid": nodalpath_proc.pid if nodalpath_proc else 0,
        "session_config": session_path,
        "db_path": mi_db,
        "api_key": api_key,
    }
    state_file = data_dir / "session-state.json"
    state_file.write_text(json.dumps(session_state, indent=2))

    log.info(f"Session: {session_id}")
    log.info(f"Data directory: {data_dir}")
    log.info(f"Timeline: {timeline_path}")
    if ome_proc:
        log.info(f"OME PID: {ome_proc.pid}")
    if mi_proc:
        log.info(f"MI service PID: {mi_proc.pid}")
    log.info(f"VS-API PID: {vsapi_pid}")
    log.info("Node Agent: K8s DaemonSet")
    log.info("Scheduler: K8s Deployment")
    log.info(f"Session state: {state_file}")

    if host_nodalpath:
        from nodalarc.zmq_channels import nodalpath_console_port

        log.info(f"NodalPath console: http://0.0.0.0:{nodalpath_console_port()}")
    else:
        log.info("NodalPath console: http://0.0.0.0:31100 (K8s NodePort)")


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc deployment tool")
    parser.add_argument("--session", required=True, help="Path to session YAML")

    parser.add_argument("--skip-vsapi", action="store_true", help="Skip VS-API start (step 10)")
    parser.add_argument(
        "--skip-teardown",
        action="store_true",
        help="Skip Step 0 teardown (caller already cleaned up)",
    )
    parser.add_argument(
        "--host-nodalpath",
        action="store_true",
        help="Run NodalPath as host subprocess instead of K8s Deployment",
    )
    parser.add_argument(
        "--host-ome",
        action="store_true",
        help="Run OME as host subprocess instead of K8s Deployment",
    )
    parser.add_argument(
        "--platform-config", default="configs/platform.yaml", help="Path to platform config YAML"
    )
    args = parser.parse_args()

    from nodalarc.platform import init_platform_config

    init_platform_config(Path(args.platform_config))

    deploy(
        args.session,
        skip_vsapi=args.skip_vsapi,
        skip_teardown=args.skip_teardown,
        host_nodalpath=args.host_nodalpath,
        host_ome=args.host_ome,
    )


if __name__ == "__main__":
    main()
