"""na-reconfig — push config changes to running pods.

PRD 13.10: re-render templates and push to targeted nodes.

Usage:
  python -m tools.na_reconfig --session <path> --target all
  python -m tools.na_reconfig --session <path> --target plane:3
  python -m tools.na_reconfig --session <path> --target node:sat-P03S07
  python -m tools.na_reconfig --session <path> --target area:1
  python -m tools.na_reconfig --session <path> --target type:satellite
  python -m tools.na_reconfig --session <path> --target type:ground_station
  python -m tools.na_reconfig --session <path> --target all --set metric_type=wide
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from pydantic import TypeAdapter

from nodalarc.constants import LOG_FORMAT
from nodalarc.models.addressing import AddressingScheme, compute_area_assignments
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.routing_stack import RoutingStackConfig
from nodalarc.models.session import SessionConfig
from nodalarc.template_vars import build_template_vars, _constellation_dims
from ome.constellation_loader import expand_constellation

log = logging.getLogger(__name__)
adapter = TypeAdapter(ConstellationConfig)


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
            try:
                value = float(value)
            except ValueError:
                pass
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


def reconfig(session_path: str, target: str, set_args: list[str] | None = None, vars_file: str | None = None) -> None:
    """Re-render and push configs to targeted nodes."""
    raw = yaml.safe_load(Path(session_path).read_text())
    session = SessionConfig.model_validate(raw)
    constellation = adapter.validate_python(
        yaml.safe_load(Path(session.constellation).read_text()),
    )
    gs_file = GroundStationFile.model_validate(
        yaml.safe_load(Path(session.ground_stations).read_text()),
    )
    addressing = AddressingScheme(session.addressing)
    satellites = expand_constellation(constellation)

    stack_dir = Path(session.routing.stack)
    stack_yaml = yaml.safe_load((stack_dir / "stack.yaml").read_text())
    stack_config = RoutingStackConfig.model_validate(stack_yaml["stack"])

    # Build config overrides from stack + session + --set + --vars-file
    config_overrides = dict(stack_config.template_variables)
    config_overrides.update(session.routing.config_overrides)
    config_overrides.update(_parse_set_args(set_args))
    if vars_file:
        config_overrides.update(yaml.safe_load(Path(vars_file).read_text()))

    # Compute area assignments for target matching
    pc, spp = _constellation_dims(constellation)
    gs_names = [s.name for s in gs_file.stations]
    area_assignments = compute_area_assignments(
        session.routing.area_assignment, pc, spp, addressing, gs_names,
    )

    env = Environment(
        loader=FileSystemLoader(str(stack_dir)),
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
            session=session, constellation=constellation,
            ground_stations=gs_file, addressing=addressing,
            node_type="satellite", plane=sat.plane, slot=sat.slot,
            config_overrides=config_overrides,
        )
        _render_and_push(env, stack_config, node_id, vars)
        reconfigured += 1

    # Process ground stations
    for i, station in enumerate(gs_file.stations):
        node_id = addressing.gs_id(station.name)
        area_id = area_assignments.get(node_id, "")
        if not _match_target(target, node_id, "ground_station", None, area_id):
            continue

        vars = build_template_vars(
            session=session, constellation=constellation,
            ground_stations=gs_file, addressing=addressing,
            node_type="ground_station", gs_name=station.name, gs_index=i,
            config_overrides=config_overrides,
        )
        _render_and_push(env, stack_config, node_id, vars)
        reconfigured += 1

    log.info(f"Reconfigured {reconfigured} nodes")


def _render_and_push(env, stack_config, node_id, vars):
    """Render templates and push to pod."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for tpl_config in stack_config.config_templates:
            tpl = env.get_template(tpl_config.src)
            rendered = tpl.render(**vars)
            dest_name = Path(tpl_config.dst).name
            (tmp_path / dest_name).write_text(rendered)

        # kubectl cp into pod
        result = subprocess.run(
            ["kubectl", "cp", str(tmp_path) + "/.", f"nodalarc/{node_id}:/etc/frr/",
             "-c", "frr"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.error(f"Config copy failed for {node_id}: {result.stderr}")
            sys.exit(1)

        # Execute reconfigure command
        reconfig_cmd = stack_config.reconfigure_command.format(
            config_path="/etc/frr/frr.conf",
        )
        result = subprocess.run(
            ["kubectl", "exec", "-n", "nodalarc", node_id, "-c", "frr",
             "--", "bash", "-c", reconfig_cmd],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.error(f"Reconfigure failed for {node_id}: {result.stderr}")
            sys.exit(1)

        log.info(f"Reconfigured {node_id}")


def main() -> None:
    logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
    parser = argparse.ArgumentParser(description="Nodal Arc reconfiguration tool")
    parser.add_argument("--session", required=True)
    parser.add_argument("--target", required=True,
                        help="Target: all, plane:N, node:ID, area:N, type:satellite|ground_station")
    parser.add_argument("--set", nargs="*", dest="set_args",
                        help="Override variables: key=value")
    parser.add_argument("--vars-file", help="YAML file with override variables")
    args = parser.parse_args()
    reconfig(args.session, args.target, args.set_args, args.vars_file)


if __name__ == "__main__":
    main()
