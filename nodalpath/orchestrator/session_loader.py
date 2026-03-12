"""Session loader — config loading without OME imports.

Replicates the minimal loading logic from ome/constellation_loader.py
so that nodalpath can build topology context from session YAML without
importing from ome/, orchestrator/, vs_api/, or measurement/.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path

import yaml
from pydantic import TypeAdapter

log = logging.getLogger(__name__)

DEPLOY_SOCKET_PATH: str = os.environ.get("NODAL_DEPLOY_SOCKET", "/tmp/nodal-deploy.sock")

from nodalarc.models.addressing import (
    AddressingScheme,
    assign_isl_neighbors,
    neighbors_by_node,
)
from nodalarc.models.constellation import (
    ConstellationConfig,
    ExplicitConstellation,
    GroundTerminal,
    IslTerminal,
    ParametricConstellation,
    TerminalConfig,
)
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundStationSetConfig,
    GroundTerminalDef,
    TerrestrialPrefixTemplate,
)
from nodalarc.models.satellite_type import SatelliteTypeConfig
from nodalarc.models.session import SessionConfig
from nodalpath.engine.labels import compute_sid
from nodalpath.models.topology import TopologyNode

_constellation_adapter = TypeAdapter(ConstellationConfig)

# Default GS terminal/elevation/policy (mirrors ome/constellation_loader.py)
_DEFAULT_GS_TERMINALS = [
    GroundTerminalDef(type="optical", count=1, bandwidth_mbps=1000, tracking_capacity=1),
]
_DEFAULT_MIN_ELEVATION_DEG = 25.0
_DEFAULT_SCHEDULING_POLICY = "highest-elevation"


def _resolve_path(spec: str, project_root: Path) -> Path:
    """Resolve a path spec relative to project_root if not absolute."""
    p = Path(spec)
    if p.is_absolute():
        return p
    return project_root / p


def _load_satellite_type(name: str, project_root: Path) -> SatelliteTypeConfig:
    """Load satellite type YAML by name from configs/satellite-types/."""
    path = project_root / "configs" / "satellite-types" / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Satellite type file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if isinstance(data, dict) and "satellite_type" in data:
        data = data["satellite_type"]
    return SatelliteTypeConfig.model_validate(data)


def _sat_type_to_terminal_config(sat_type: SatelliteTypeConfig) -> TerminalConfig:
    """Convert SatelliteTypeConfig to TerminalConfig for addressing."""
    isl_terminals = [
        IslTerminal(
            type=td.type, count=td.count, max_range_km=td.max_range_km,
            bandwidth_mbps=td.bandwidth_mbps,
            max_tracking_rate_deg_s=td.max_tracking_rate_deg_s,
            field_of_regard_deg=td.field_of_regard_deg,
        )
        for td in sat_type.isl_terminals
    ]
    ground_terminals = [
        GroundTerminal(type=td.type, count=td.count, bandwidth_mbps=td.bandwidth_mbps)
        for td in sat_type.ground_terminals
    ]
    return TerminalConfig(isl=isl_terminals, ground=ground_terminals)


def _resolve_terminals(
    config: ConstellationConfig, project_root: Path,
) -> None:
    """Resolve satellite_type → default_terminals if needed."""
    if not isinstance(config, (ParametricConstellation, ExplicitConstellation)):
        return
    if config.default_terminals is not None:
        return
    if config.satellite_type is None:
        raise ValueError("Constellation must specify satellite_type or default_terminals")
    sat_type = _load_satellite_type(config.satellite_type, project_root)
    config.default_terminals = _sat_type_to_terminal_config(sat_type)


def _get_satellite_pairs(
    config: ConstellationConfig,
) -> tuple[list[tuple[int, int]], int]:
    """Extract (plane, slot) pairs and sats_per_plane from constellation config."""
    if isinstance(config, ParametricConstellation):
        sats_per_plane = config.planes.sats_per_plane
        pairs = [
            (p, s)
            for p in range(config.planes.count)
            for s in range(sats_per_plane)
        ]
        return pairs, sats_per_plane
    if isinstance(config, ExplicitConstellation):
        pairs = [(sat.plane, sat.slot) for sat in config.satellites]
        planes: dict[int, list[int]] = {}
        for sat in config.satellites:
            planes.setdefault(sat.plane, []).append(sat.slot)
        sats_per_plane = max(len(slots) for slots in planes.values())
        return pairs, sats_per_plane
    raise NotImplementedError(f"Unsupported constellation type: {type(config)}")


def _load_gs_individual(name: str, project_root: Path) -> GroundStationConfig:
    """Load an individual ground station YAML by name."""
    path = project_root / "configs" / "ground-stations" / "stations" / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Ground station file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if isinstance(data, dict) and "ground_station" in data:
        return GroundStationConfig.model_validate(data["ground_station"])
    return GroundStationConfig.model_validate(data)


def _build_gs_file(
    stations: list[GroundStationConfig],
    default_prefixes: TerrestrialPrefixTemplate | None = None,
) -> GroundStationFile:
    """Wrap individual stations into a GroundStationFile."""
    return GroundStationFile(
        default_terminals=_DEFAULT_GS_TERMINALS,
        default_min_elevation_deg=_DEFAULT_MIN_ELEVATION_DEG,
        default_scheduling_policy=_DEFAULT_SCHEDULING_POLICY,
        default_terrestrial_prefixes=default_prefixes,
        stations=stations,
    )


def _load_ground_stations(
    spec: str | list[str], project_root: Path,
) -> GroundStationFile:
    """Load ground stations from path, set name, or station name list."""
    if isinstance(spec, list):
        stations = [_load_gs_individual(name, project_root) for name in spec]
        return _build_gs_file(stations)

    path = _resolve_path(spec, project_root)
    if not path.exists():
        raise FileNotFoundError(f"Ground station file not found: {path}")
    data = yaml.safe_load(path.read_text())

    if isinstance(data, dict):
        if "ground_station" in data:
            station = GroundStationConfig.model_validate(data["ground_station"])
            return _build_gs_file([station])
        if "ground_station_set" in data:
            gs_set = GroundStationSetConfig.model_validate(data["ground_station_set"])
            stations = [_load_gs_individual(n, project_root) for n in gs_set.stations]
            return _build_gs_file(stations, gs_set.default_terrestrial_prefixes)

    return GroundStationFile.model_validate(data)


def load_session_context(
    session_path: Path,
    project_root: Path | None = None,
) -> tuple[
    dict[str, TopologyNode],
    dict[tuple[str, str], tuple[str, str]],
    dict[str, str],
    dict[tuple[str, str], float],
]:
    """Load session config and build topology context.

    Returns (node_registry, interface_map, prefix_map, bandwidth_map).

    Imports only from lib/nodalarc/ and nodalpath/ — never from ome/,
    orchestrator/, vs_api/, or measurement/.
    """
    if project_root is None:
        project_root = Path.cwd()

    # 1. Load session
    session = SessionConfig.model_validate(yaml.safe_load(session_path.read_text()))

    # 2. Load constellation + resolve terminals
    const_path = _resolve_path(session.constellation, project_root)
    constellation = _constellation_adapter.validate_python(
        yaml.safe_load(const_path.read_text()),
    )
    _resolve_terminals(constellation, project_root)

    # 3. Load ground stations
    gs_file = _load_ground_stations(session.ground_stations, project_root)

    # 4. Addressing + ISL neighbors
    addressing = AddressingScheme(session.addressing)
    neighbors = assign_isl_neighbors(constellation, addressing)
    by_node = neighbors_by_node(neighbors)

    # 5. Build interface_map and bandwidth_map
    interface_map: dict[tuple[str, str], tuple[str, str]] = {}
    bandwidth_map: dict[tuple[str, str], float] = {}
    _iface_buf: dict[tuple[str, str], list[str]] = {}

    for node_id, assignments in by_node.items():
        for na in assignments:
            a, b = min(node_id, na.peer_node_id), max(node_id, na.peer_node_id)
            pair = (a, b)
            if pair not in _iface_buf:
                _iface_buf[pair] = ["", ""]
            if node_id == a:
                _iface_buf[pair][0] = na.interface
            else:
                _iface_buf[pair][1] = na.interface
            bandwidth_map[pair] = 1000.0

    for pair, ifaces in _iface_buf.items():
        interface_map[pair] = (ifaces[0], ifaces[1])

    # GS-satellite links (all use gnd0)
    sat_pairs, _ = _get_satellite_pairs(constellation)
    for station in gs_file.stations:
        gs_id = addressing.gs_id(station.name)
        for plane, slot in sat_pairs:
            sat_id = addressing.sat_id(plane, slot)
            pair = (min(gs_id, sat_id), max(gs_id, sat_id))
            interface_map[pair] = ("gnd0", "gnd0")
            bandwidth_map[pair] = 1000.0

    # 6. Build node_registry
    node_registry: dict[str, TopologyNode] = {}
    _, sats_per_plane = _get_satellite_pairs(constellation)

    for plane, slot in sat_pairs:
        node_id = addressing.sat_id(plane, slot)
        sid = compute_sid(
            node_id, "satellite",
            plane=plane, slot=slot, sats_per_plane=sats_per_plane,
        )
        node_registry[node_id] = TopologyNode(
            node_id=node_id, node_type="satellite", sid=sid,
            loopback_ipv4=addressing.sat_ipv4(plane, slot),
            plane=plane, slot=slot,
        )

    for gs_index, station in enumerate(gs_file.stations):
        node_id = addressing.gs_id(station.name)
        sid = compute_sid(node_id, "ground_station", gs_index=gs_index)
        node_registry[node_id] = TopologyNode(
            node_id=node_id, node_type="ground_station", sid=sid,
            loopback_ipv4=addressing.gs_ipv4(gs_index),
        )

    # 7. Build prefix_map (advertised prefix per node)
    #    GS: first IPv4 terrestrial prefix (or default template)
    #    Satellites: loopback /32 so they can be path derivation destinations
    prefix_map: dict[str, str] = {}

    for plane, slot in sat_pairs:
        sat_id = addressing.sat_id(plane, slot)
        prefix_map[sat_id] = f"{addressing.sat_ipv4(plane, slot)}/32"

    template = gs_file.default_terrestrial_prefixes
    for gs_index, station in enumerate(gs_file.stations):
        gs_id = addressing.gs_id(station.name)
        if station.terrestrial_prefixes:
            prefix_map[gs_id] = station.terrestrial_prefixes[0].prefix
        elif template is not None:
            prefix_map[gs_id] = template.ipv4_template.format(gs_index=gs_index)
        else:
            prefix_map[gs_id] = f"172.16.{gs_index}.0/24"

    return node_registry, interface_map, prefix_map, bandwidth_map


def _daemon_request(
    request: dict,
    socket_path: str = DEPLOY_SOCKET_PATH,
    timeout: float = 10.0,
) -> dict | None:
    """Send a JSON request to the deploy daemon and return the response.

    Returns None on any error (connection, timeout, parse failure).
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        sock.sendall((json.dumps(request) + "\n").encode())
        buf = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
            if b"\n" in buf:
                line = buf[:buf.index(b"\n")]
                return json.loads(line)
        return None
    except Exception as exc:
        log.warning("Deploy daemon request failed: %s", exc)
        return None
    finally:
        sock.close()


def load_pod_ip_map(
    node_ids: list[str],
    socket_path: str = DEPLOY_SOCKET_PATH,
    namespace: str = "nodalarc",
) -> dict[str, str]:
    """Query pod IPs for a list of node_ids via the deploy daemon.

    Skips nodes whose pod IP cannot be resolved (logs warning).
    """
    from nodalpath.push.kubectl_exec import node_id_to_pod_name

    result: dict[str, str] = {}
    for node_id in node_ids:
        pod_name = node_id_to_pod_name(node_id)
        resp = _daemon_request(
            {"action": "get_pod_ip", "pod": pod_name, "namespace": namespace},
            socket_path=socket_path,
        )
        if resp and resp.get("ok") and resp.get("pod_ip"):
            result[node_id] = resp["pod_ip"]
        else:
            error = resp.get("error", "no response") if resp else "no response"
            log.warning("Failed to get pod IP for %s (%s): %s", node_id, pod_name, error)

    log.info("Resolved %d/%d pod IPs", len(result), len(node_ids))
    return result
