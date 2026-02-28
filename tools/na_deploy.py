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
from ome.main import run as ome_run

log = logging.getLogger(__name__)
adapter = TypeAdapter(ConstellationConfig)


def _fail(msg: str) -> None:
    log.error(msg)
    sys.exit(1)


def deploy(session_path: str) -> None:
    """Execute the 11-step startup sequence."""
    # === Step 1: Load and validate ===
    log.info("Step 1: Load and validate session config")
    raw = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(raw)
    constellation_data = yaml.safe_load(Path(session.constellation).read_text())
    constellation = adapter.validate_python(constellation_data)
    gs_data = yaml.safe_load(Path(session.ground_stations).read_text())
    gs_file = GroundStationFile.model_validate(gs_data)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    session_id = f"{session.session.name}-{ts}"
    data_dir = Path(session.session.data_dir) / session_id
    data_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Session: {session_id}, data_dir: {data_dir}")

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
            "-n", "nodalarc",
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

    # === Step 6: Copy configs into pods ===
    log.info("Step 6: Copy configs into pods")
    for node_id in node_vars:
        node_dir = configs_dir / node_id
        result = subprocess.run(
            ["kubectl", "cp", str(node_dir), f"nodalarc/{node_id}:/etc/frr/",
             "-c", "frr"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _fail(f"Config copy failed for {node_id}: {result.stderr}")
    log.info("Configs copied to all pods")

    # === Step 7: Wire data plane ===
    log.info("Step 7: Wire data plane")
    log.info("  (Data plane wiring handled by orchestrator at startup)")

    # === Step 8: Start MI (convergence gate stub) ===
    log.info("Step 8: Start MI convergence gate stub")
    mi_proc = subprocess.Popen(
        [sys.executable, "-m", "measurement.stubs.convergence_stub"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info(f"Convergence gate stub PID: {mi_proc.pid}")

    # === Step 9: Configure probe flows ===
    log.info("Step 9: Configure probe flows (skipped in Phase 1B)")

    # === Step 10: Start VS-API ===
    log.info("Step 10: Start VS-API (skipped in Phase 1B)")

    # === Step 11: Begin event dispatch ===
    log.info("Step 11: Begin event dispatch")
    mode_flag = "de" if session.time.mode == "discrete-event" else "rt"
    to_proc = subprocess.Popen(
        [
            sys.executable, "-m", "orchestrator.main",
            "--session", session_path,
            "--timeline", str(timeline_path),
            "--mode", mode_flag,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info(f"Orchestrator PID: {to_proc.pid}")

    # === Complete ===
    print(f"\nSession: {session_id}")
    print(f"Data directory: {data_dir}")
    print(f"Timeline: {timeline_path}")
    print(f"Convergence stub PID: {mi_proc.pid}")
    print(f"Orchestrator PID: {to_proc.pid}")


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc deployment tool")
    parser.add_argument("--session", required=True, help="Path to session YAML")
    args = parser.parse_args()
    deploy(args.session)


if __name__ == "__main__":
    main()
