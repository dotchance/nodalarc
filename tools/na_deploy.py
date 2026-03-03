"""na-deploy — 11-step startup sequence from PRD 13.23.

Each step is fail-hard: any failure aborts the entire deployment.

Usage: python -m tools.na_deploy --session configs/sessions/sample-session.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from pydantic import TypeAdapter

from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.routing_stack import RoutingStackConfig
from nodalarc.models.session import SessionConfig
from nodalarc.template_vars import build_template_vars
from ome.constellation_loader import expand_constellation, load_constellation, load_ground_stations
from nodalarc.zmq_channels import VS_API_HTTP_PORT
from ome.main import run as ome_run

log = logging.getLogger(__name__)
adapter = TypeAdapter(ConstellationConfig)


def _fail(msg: str) -> None:
    log.error(msg)
    sys.exit(1)


def deploy(session_path: str, dwell: float = 0.05, skip_vsapi: bool = False) -> None:
    """Execute the 11-step startup sequence."""
    # === Step 1: Load and validate ===
    log.info("Step 1: Load and validate session config")
    raw = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(raw)
    constellation_data = yaml.safe_load(Path(session.constellation).read_text())
    constellation = adapter.validate_python(constellation_data)
    gs_data = yaml.safe_load(Path(session.ground_stations).read_text())
    gs_file = GroundStationFile.model_validate(gs_data)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz")
    session_id = f"{session.session.name}-{ts}".lower()
    data_dir = Path(session.session.data_dir) / session_id
    data_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Deploying {session_id} to {data_dir}")

    addressing = AddressingScheme(session.addressing)
    satellites = expand_constellation(constellation)

    # Load routing stack
    stack_dir = Path(session.routing.stack)
    stack_yaml = yaml.safe_load((stack_dir / "stack.yaml").read_text())
    stack_config = RoutingStackConfig.model_validate(stack_yaml["stack"])

    # === Step 2: Pre-compute timeline ===
    log.info("Step 2: Pre-compute timeline")
    timeline_path = ome_run(session_path, str(data_dir))
    log.info(f"Timeline: {timeline_path}")

    # === Step 3: Build template variables ===
    log.info("Step 3: Build template variables")
    config_overrides = dict(stack_config.template_variables)
    config_overrides.update(session.routing.config_overrides)

    node_vars: dict[str, dict] = {}
    for sat in satellites:
        node_id = addressing.sat_id(sat.plane, sat.slot)
        vars = build_template_vars(
            session=session, constellation=constellation,
            ground_stations=gs_file, addressing=addressing,
            node_type="satellite", plane=sat.plane, slot=sat.slot,
            config_overrides=config_overrides,
        )
        node_vars[node_id] = vars

    for i, station in enumerate(gs_file.stations):
        node_id = addressing.gs_id(station.name)
        vars = build_template_vars(
            session=session, constellation=constellation,
            ground_stations=gs_file, addressing=addressing,
            node_type="ground_station", gs_name=station.name, gs_index=i,
            config_overrides=config_overrides,
        )
        node_vars[node_id] = vars

    # === Step 4: Render routing configurations ===
    log.info("Step 4: Render routing configurations")
    env = Environment(
        loader=FileSystemLoader(str(stack_dir)),
        keep_trailing_newline=True,
    )
    configs_dir = data_dir / "configs"
    for node_id, vars in node_vars.items():
        node_dir = configs_dir / node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        for tpl_config in stack_config.config_templates:
            tpl = env.get_template(tpl_config.src)
            rendered = tpl.render(**vars)
            dest_name = Path(tpl_config.dst).name
            (node_dir / dest_name).write_text(rendered)
        log.info(f"  Rendered configs for {node_id}")

    # === Step 5: Deploy K3s pods ===
    log.info("Step 5: Deploy K3s pods")
    helm_values = {
        "satellites": [
            {"nodeId": nid, "plane": vars["plane"], "slot": vars["slot"]}
            for nid, vars in node_vars.items() if vars["node_type"] == "satellite"
        ],
        "groundStations": [
            {"nodeId": nid, "gsName": vars["gs_name"]}
            for nid, vars in node_vars.items() if vars["node_type"] == "ground_station"
        ],
        "mode": "de" if session.time.mode == "discrete-event" else "rt",
        "sessionConfig": session_path,
        "timelineFile": str(timeline_path),
    }
    values_file = data_dir / "helm-values.yaml"
    values_file.write_text(yaml.dump(helm_values))

    result = subprocess.run(
        [
            "helm", "install", session_id, "deploy/helm",
            "-n", "nodalarc", "--create-namespace",
            "-f", str(values_file),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        _fail(f"Helm install failed: {result.stderr}")
    log.info("Helm install complete")

    # Wait for pods
    log.info("Waiting for pods to be Running...")
    for _ in range(120):  # 2 minute timeout
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", "nodalarc",
             "-l", "nodalarc.io/node-id",
             "-o", "jsonpath={.items[*].status.phase}"],
            capture_output=True, text=True,
        )
        phases = result.stdout.strip().split()
        if phases and all(p == "Running" for p in phases):
            break
        time.sleep(1)
    else:
        _fail("Timeout waiting for pods to be Running")
    log.info(f"All {len(phases)} pods Running")

    # === Step 6: Copy configs into pods, signal readiness ===
    log.info("Step 6: Copy configs into pods")
    for node_id in node_vars:
        pod_name = node_id.lower()
        node_dir = configs_dir / node_id
        # Copy rendered configs
        result = subprocess.run(
            ["kubectl", "cp", str(node_dir) + "/.", f"nodalarc/{pod_name}:/etc/frr/",
             "-c", "frr"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _fail(f"Config copy failed for {node_id}: {result.stderr}")
        # Touch sentinel so entrypoint starts FRR daemons
        result = subprocess.run(
            ["kubectl", "exec", "-n", "nodalarc", pod_name, "-c", "frr",
             "--", "touch", "/etc/frr/.config-ready"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _fail(f"Config ready signal failed for {node_id}: {result.stderr}")
    log.info("Configs copied and daemons signaled for all pods")

    # Wait for FRR daemons to start (entrypoint waits for sentinel, then launches)
    log.info("Waiting 5s for FRR daemons to start...")
    time.sleep(5)

    # === Step 7: Wire data plane ===
    log.info("Step 7: Wire data plane")
    from orchestrator.link_manager import (
        configure_interface,
        create_dummy_interface,
        create_veth_pair,
        discover_pod_pids,
        enable_mpls_input,
    )

    # Retry PID discovery — containers may still be initializing
    for attempt in range(5):
        pid_map = discover_pod_pids(namespace="nodalarc")
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
    for node_id, pid in pid_map.items():
        for sysctl_key, value in [
            ("net.ipv6.conf.all.forwarding", "1"),
            ("net.mpls.platform_labels", "100000"),
        ]:
            result = subprocess.run(
                ["nsenter", "--target", str(pid), "--net", "--",
                 "sysctl", "-w", f"{sysctl_key}={value}"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                _fail(f"Failed to set {sysctl_key}={value} in ns({pid}): {result.stderr}")
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
                pid_a, pid_b, peer_iface, remote_iface,
                node_id_a=node_id, node_id_b=na.peer_node_id,
            )
            created_links.add(pair)

    log.info(f"Created {len(created_links)} veth pairs (all admin down)")

    # Enable MPLS input on ISL interfaces (no shelling out — PRD 13.6)
    for node_id, assignments in by_node.items():
        pid = pid_map[node_id]
        for na in assignments:
            enable_mpls_input(pid, na.interface)
    log.info("Enabled MPLS input on all ISL interfaces")

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

    # Save pid_map for orchestrator
    pid_map_file = data_dir / "pid_map.json"
    pid_map_file.write_text(json.dumps(pid_map))
    log.info(f"PID map saved to {pid_map_file}")

    # === Step 8: Start MI ===
    log.info("Step 8: Start MI service")
    mi_db = str(data_dir / "session.db")
    mi_proc = subprocess.Popen(
        [sys.executable, "-m", "measurement.mi_main",
         "--session", session_path,
         "--db", mi_db],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info(f"MI service PID: {mi_proc.pid}")

    # === Step 9: Configure probe flows ===
    log.info("Step 9: Configure probe flows (skipped in Phase 1B)")

    # === Step 10: Start VS-API ===
    if not skip_vsapi:
        log.info("Step 10: Start VS-API")
        vsapi_proc = subprocess.Popen(
            [sys.executable, "-m", "vs_api.main",
             "--session", session_path,
             "--db", mi_db,
             "--port", str(VS_API_HTTP_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info(f"VS-API PID: {vsapi_proc.pid}")
    else:
        log.info("Step 10: Skipping VS-API (--skip-vsapi)")
        vsapi_proc = None

    # === Step 11: Begin event dispatch ===
    log.info("Step 11: Begin event dispatch")
    mode_flag = "de" if session.time.mode == "discrete-event" else "rt"
    to_proc = subprocess.Popen(
        [
            sys.executable, "-m", "orchestrator.main",
            "--session", session_path,
            "--timeline", str(timeline_path),
            "--mode", mode_flag,
            "--pid-map", str(pid_map_file),
            "--dwell", str(dwell),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info(f"Orchestrator PID: {to_proc.pid}")

    # === Complete — save session state and print summary ===
    vsapi_pid = vsapi_proc.pid if vsapi_proc else 0
    session_state = {
        "session_id": session_id,
        "data_dir": str(data_dir),
        "timeline": str(timeline_path),
        "mi_pid": mi_proc.pid,
        "vsapi_pid": vsapi_pid,
        "orchestrator_pid": to_proc.pid,
        "session_config": session_path,
        "db_path": mi_db,
    }
    state_file = data_dir / "session-state.json"
    state_file.write_text(json.dumps(session_state, indent=2))

    log.info(f"Session: {session_id}")
    log.info(f"Data directory: {data_dir}")
    log.info(f"Timeline: {timeline_path}")
    log.info(f"MI service PID: {mi_proc.pid}")
    log.info(f"VS-API PID: {vsapi_pid}")
    log.info(f"Orchestrator PID: {to_proc.pid}")
    log.info(f"Session state: {state_file}")


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc deployment tool")
    parser.add_argument("--session", required=True, help="Path to session YAML")
    parser.add_argument("--dwell", type=float, default=0.05, help="DE mode dwell between event batches (seconds)")
    parser.add_argument("--skip-vsapi", action="store_true", help="Skip VS-API start (step 10)")
    args = parser.parse_args()
    deploy(args.session, dwell=args.dwell, skip_vsapi=args.skip_vsapi)


if __name__ == "__main__":
    main()
