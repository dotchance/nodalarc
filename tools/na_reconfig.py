"""na-reconfig — push config changes to running pods and manage probe flows.

PRD 13.10: re-render templates and push to targeted nodes.
PRD line 822: flow management via --add-flow / --remove-flow.

Usage:
  python -m tools.na_reconfig --session <path> --target all
  python -m tools.na_reconfig --session <path> --target plane:3
  python -m tools.na_reconfig --session <path> --target node:space-sat-p03s07
  python -m tools.na_reconfig --session <path> --target area:1
  python -m tools.na_reconfig --session <path> --target type:satellite
  python -m tools.na_reconfig --session <path> --target type:ground_station
  python -m tools.na_reconfig --session <path> --target all --set metric_type=wide
  python -m tools.na_reconfig --session <path> --add-flow test1:ground-gs-hawthorne:ground-gs-frankfurt:udp:100:continuous
  python -m tools.na_reconfig --session <path> --remove-flow test1
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import subprocess
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import compute_area_assignments
from nodalarc.resolve_session import load_session_resolution_from_file
from nodalarc.stack_resolver import resolve_stack
from nodalarc.template_vars import _constellation_dims, build_template_vars

log = logging.getLogger(__name__)


def _parse_set_args(set_args: list[str] | None) -> dict:
    """Parse --set key=value arguments."""
    result = {}
    if not set_args:
        return result
    for item in set_args:
        key, _, value = item.partition("=")
        # Try numeric conversion
        try:
            value = int(value)
        except ValueError:
            with contextlib.suppress(ValueError):
                value = float(value)
        result[key.strip()] = value
    return result


def _match_target(
    target: str,
    node_id: str,
    node_type: str,
    plane: int | None,
    area_id: str,
) -> bool:
    """Check if a node matches the target selector."""
    if target == "all":
        return True
    kind, _, value = target.partition(":")
    if kind == "node":
        return node_id == value
    if kind == "plane":
        return plane is not None and plane == int(value)
    if kind == "area":
        return area_id.endswith(f".{int(value):04d}")
    if kind == "type":
        return node_type == value
    return False


def reconfig(
    session_path: str, target: str, set_args: list[str] | None = None, vars_file: str | None = None
) -> None:
    """Re-render and push configs to targeted nodes."""
    resolution = load_session_resolution_from_file(session_path, origin="na-reconfig")
    if len(resolution.constellations) != 1:
        raise RuntimeError(
            "na_reconfig does not support multi-constellation sessions yet; "
            "refusing partial config push"
        )
    session = resolution.runtime_session
    constellation = resolution.primary_constellation.config
    gs_file = resolution.primary_ground_set.config
    addressing = resolution.addressing
    satellites = list(resolution.primary_constellation.satellites)
    resolved_stack = resolve_stack(session.routing.protocol, session.routing.extensions)
    sid_by_node = resolution.resolved.sid_index_by_node_id()

    # Build config overrides from stack + session + --set + --vars-file
    config_overrides = dict(resolved_stack.template_variables)
    config_overrides.update(session.routing.config_overrides)
    config_overrides.update(_parse_set_args(set_args))
    if vars_file:
        config_overrides.update(yaml.safe_load(Path(vars_file).read_text()))

    # Compute area assignments for target matching (empty if not configured)
    pc, spp = _constellation_dims(constellation)
    gs_names = [s.name for s in gs_file.stations]
    area_assignments: dict[str, str] = {}
    if session.routing.area_assignment is not None:
        area_assignments = compute_area_assignments(
            session.routing.area_assignment,
            pc,
            spp,
            addressing,
            gs_names,
        )

    env = Environment(
        loader=FileSystemLoader(str(Path("configs/templates/frr").resolve())),
        keep_trailing_newline=True,
    )

    reconfigured = 0

    # Process satellites
    for sat in satellites:
        node_id = addressing.sat_id(sat.plane, sat.slot)
        area_id = area_assignments.get(node_id, "")
        if not _match_target(target, node_id, "satellite", sat.plane, area_id):
            continue

        vars = build_template_vars(
            session=session,
            constellation=constellation,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=sat.plane,
            slot=sat.slot,
            config_overrides=config_overrides,
            neighbors=resolution.neighbors,
            node_sid_index=sid_by_node[node_id],
        )
        _render_and_push(env, resolved_stack, node_id, vars)
        reconfigured += 1

    # Process ground stations
    for i, station in enumerate(gs_file.stations):
        node_id = addressing.gs_id(station.name)
        area_id = area_assignments.get(node_id, "")
        if not _match_target(target, node_id, "ground_station", None, area_id):
            continue

        vars = build_template_vars(
            session=session,
            constellation=constellation,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="ground_station",
            gs_name=station.name,
            gs_index=i,
            config_overrides=config_overrides,
            neighbors=resolution.neighbors,
            node_sid_index=sid_by_node[node_id],
        )
        _render_and_push(env, resolved_stack, node_id, vars)
        reconfigured += 1

    log.info(f"Reconfigured {reconfigured} nodes")


def _render_and_push(env, resolved_stack, node_id, vars):
    """Render templates and push to pod.

    Uses the stack's reconfigure_command with {config_path} placeholder
    (PRD Section 13.19). The command is executed once per config template.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for tpl_config in resolved_stack.template_files:
            tpl = env.get_template(tpl_config.src)
            rendered = tpl.render(**vars)
            dest_name = Path(tpl_config.dst).name
            (tmp_path / dest_name).write_text(rendered)

        # kubectl cp into pod — copy to the directory containing the config files
        # Derive the common config directory from the first template's dst
        config_dirs = {str(Path(tc.dst).parent) for tc in resolved_stack.template_files}
        for config_dir in config_dirs:
            result = subprocess.run(
                [
                    "kubectl",
                    "cp",
                    str(tmp_path) + "/.",
                    f"nodalarc/{node_id}:{config_dir}/",
                    "-c",
                    "frr",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log.error(f"Config copy failed for {node_id}: {result.stderr}")
                sys.exit(1)

        # Apply using reconfigure_command from stack.yaml (PRD 13.19)
        if not resolved_stack.reconfigure_command:
            raise RuntimeError("resolved routing stack has no reconfigure_command")
        for tpl_config in resolved_stack.template_files:
            cmd = resolved_stack.reconfigure_command.format(
                config_path=tpl_config.dst,
            )
            result = subprocess.run(
                ["kubectl", "exec", "-n", "nodalarc", node_id, "-c", "frr", "--", "sh", "-c", cmd],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log.error(f"Reconfigure failed for {node_id} ({cmd}): {result.stderr}")
                sys.exit(1)

        log.info(f"Reconfigured {node_id}")


def _parse_flow_spec(spec: str) -> dict:
    """Parse flow spec string: flow_id:src:dst:protocol:bandwidth_kbps:probe_type"""
    parts = spec.split(":")
    if len(parts) != 6:
        raise ValueError(
            f"Flow spec must be flow_id:src:dst:protocol:bandwidth_kbps:probe_type, got: {spec}"
        )
    return {
        "flow_id": parts[0],
        "src": parts[1],
        "dst": parts[2],
        "protocol": parts[3],
        "bandwidth_kbps": float(parts[4]),
        "probe_type": parts[5],
    }


def add_flow(session_path: str, flow_spec: str) -> None:
    """Add a probe flow to a running session.

    Configures the probe daemon on the source GS pod directly and
    records the flow in the session database.
    """
    resolution = load_session_resolution_from_file(session_path, origin="na-reconfig")
    session = resolution.runtime_session
    gs_file = resolution.primary_ground_set.config
    addressing = resolution.addressing

    spec = _parse_flow_spec(flow_spec)
    from measurement import probe_client
    from measurement.flow_manager import resolve_dst_ip, resolve_src_pod_ip

    dst_ip = resolve_dst_ip(spec["dst"], gs_file, session, addressing)
    src_pod_ip = resolve_src_pod_ip(spec["src"])
    if src_pod_ip is None:
        log.error(f"Cannot resolve pod IP for {spec['src']}")
        sys.exit(1)

    probe_client.configure_flow(
        pod_ip=src_pod_ip,
        flow_id=spec["flow_id"],
        dst_ip=dst_ip,
        protocol=spec["protocol"],
        bandwidth_kbps=spec["bandwidth_kbps"],
        probe_type=spec["probe_type"],
    )
    log.info(f"Added flow {spec['flow_id']}: {spec['src']} → {spec['dst']} ({dst_ip})")


def remove_flow(session_path: str, flow_id: str) -> None:
    """Remove a probe flow from a running session."""
    resolution = load_session_resolution_from_file(session_path, origin="na-reconfig")
    gs_file = resolution.primary_ground_set.config
    addressing = resolution.addressing

    # We need to find which GS pod this flow runs on.
    # Check all GS pods for the flow.
    from measurement import probe_client
    from measurement.flow_manager import resolve_src_pod_ip

    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        pod_ip = resolve_src_pod_ip(gs_id)
        if pod_ip is None:
            continue
        try:
            probe_client.delete_flow(pod_ip, flow_id)
            log.info(f"Removed flow {flow_id} from {gs_id}")
            return
        except Exception:
            continue

    log.warning(f"Flow {flow_id} not found on any GS pod")


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc reconfiguration tool")
    parser.add_argument("--session", required=True)
    parser.add_argument(
        "--target", help="Target: all, plane:N, node:ID, area:N, type:satellite|ground_station"
    )
    parser.add_argument("--set", nargs="*", dest="set_args", help="Override variables: key=value")
    parser.add_argument("--vars-file", help="YAML file with override variables")
    parser.add_argument(
        "--add-flow", help="Add probe flow: flow_id:src:dst:protocol:bandwidth_kbps:probe_type"
    )
    parser.add_argument("--remove-flow", help="Remove probe flow by flow_id")
    args = parser.parse_args()

    if args.add_flow:
        add_flow(args.session, args.add_flow)
    elif args.remove_flow:
        remove_flow(args.session, args.remove_flow)
    elif args.target:
        reconfig(args.session, args.target, args.set_args, args.vars_file)
    else:
        parser.error("One of --target, --add-flow, or --remove-flow is required")


if __name__ == "__main__":
    main()
