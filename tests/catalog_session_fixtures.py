"""Canonical ref-composed catalog sessions for tests."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from nodalarc.catalog_paths import CatalogRoots, resolve_catalog_reference
from nodalarc.catalog_refs import CatalogRef
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import resolve_session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_ROOT = PROJECT_ROOT / "catalog" / "nodalarc"
ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"
VANGUARD_TLE_LINE_1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
VANGUARD_TLE_LINE_2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"


def _catalog_id(value: object, default: str) -> str:
    text = str(value or default)
    stem = Path(text).stem if "/" in text or text.endswith((".yaml", ".yml")) else text
    token = re.sub(r"[^a-z0-9_-]+", "-", stem.lower()).strip("-")
    return token or default


def _write_yaml(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8")


class CatalogSessionFixture(dict[str, Any]):
    """One persisted session and the catalog roots required to resolve it."""

    def __init__(
        self,
        session: dict[str, Any],
        *,
        roots: CatalogRoots,
        session_path: Path,
        constellation_ref: CatalogRef | None,
        orbit_ref: CatalogRef | None,
        space_node_ref: CatalogRef | None,
        site_set_ref: CatalogRef | None,
        site_refs: tuple[CatalogRef, ...],
        ground_node_ref: CatalogRef | None,
        temporary_directory: tempfile.TemporaryDirectory[str] | None,
    ) -> None:
        super().__init__(session)
        self.roots = roots
        self.session_path = session_path
        self.constellation_ref = constellation_ref
        self.orbit_ref = orbit_ref
        self.space_node_ref = space_node_ref
        self.site_set_ref = site_set_ref
        self.site_refs = site_refs
        self.ground_node_ref = ground_node_ref
        self._temporary_directory = temporary_directory

    def read_catalog(self, ref: CatalogRef | str) -> dict[str, Any]:
        path = resolve_catalog_reference(CatalogRef(str(ref)), self.roots)
        value = load_configuration_yaml(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise AssertionError(f"test catalog document {ref} must be a mapping")
        return value

    def write_catalog(self, ref: CatalogRef | str, document: Mapping[str, Any]) -> None:
        path = resolve_catalog_reference(CatalogRef(str(ref)), self.roots)
        _write_yaml(path, document)

    def create_catalog(self, ref: CatalogRef | str, document: Mapping[str, Any]) -> None:
        parsed = CatalogRef(str(ref))
        if parsed.namespace != "user" or self.roots.user_root is None:
            raise AssertionError("test-created catalog documents must use the user: namespace")
        _write_yaml(self.roots.user_root / parsed.relative_path, document)

    def write_session(self) -> Path:
        _write_yaml(self.session_path, self)
        return self.session_path


def resolve_catalog_session(
    session: dict[str, Any],
    *,
    origin: str = "test.catalog_session_fixture",
    run_id: str | None = None,
    **kwargs: Any,
):
    """Resolve a fixture through its explicit roots or a normal session as-is."""

    if isinstance(session, CatalogSessionFixture):
        source_context = kwargs.pop("source_context", None)
        return resolve_session(
            session,
            catalog_roots=session.roots,
            source_context=source_context or SourceContext(origin=origin, run_id=run_id),
            **kwargs,
        )
    return resolve_session(session, **kwargs)


def install_tle_space_node_set(
    fixture: CatalogSessionFixture,
    *,
    body_ref: str = "nodalarc:bodies/earth.yaml",
) -> CatalogRef:
    """Replace a generated test constellation with a canonical fixed TLE set."""

    if fixture.space_node_ref is None:
        raise AssertionError("TLE test fixture requires a persisted space node model")
    identifier = f"{fixture['session']['name']}-tle-set"
    ref = CatalogRef(f"user:space-node-sets/{identifier}.yaml")
    records = (
        ("iss", ISS_TLE_LINE_1, ISS_TLE_LINE_2),
        ("vanguard", VANGUARD_TLE_LINE_1, VANGUARD_TLE_LINE_2),
    )
    fixture.create_catalog(
        ref,
        {
            "space_node_set": {
                "id": identifier,
                "nodes": [
                    {
                        "id": node_id,
                        "node": str(fixture.space_node_ref),
                        "sgp4_tle": {
                            "central_body": body_ref,
                            "line_1": line_1,
                            "line_2": line_2,
                        },
                    }
                    for node_id, line_1, line_2 in records
                ],
            }
        },
    )
    fixture["segments"][0]["source"] = str(ref)
    fixture["time"]["start_time"] = "2021-03-16T12:15:00Z"
    fixture.write_session()
    return ref


def build_catalog_session_fixture(
    *,
    name: str,
    constellation: object,
    ground_stations: object,
    base_path: Path | None = None,
    protocol: str = "isis",
    extensions: list[str] | None = None,
    orbit_propagator: str = "j2_mean_elements",
    routing: dict[str, Any] | None = None,
    scheduling: dict[str, Any] | None = None,
    time: dict[str, Any] | None = None,
    candidate_limit: int = 100000,
) -> CatalogSessionFixture:
    """Persist a small canonical session and every user-owned dependency."""

    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if base_path is None:
        temporary_directory = tempfile.TemporaryDirectory(prefix="nodalarc-test-catalog-")
        root = Path(temporary_directory.name)
    else:
        root = Path(tempfile.mkdtemp(prefix="catalog-session-", dir=base_path))
    user_root = root / "user"
    roots = CatalogRoots.from_catalog_root(SHIPPED_ROOT, user_root=user_root)
    session_id = _catalog_id(name, "test-session")

    constellation_ref, orbit_ref, space_node_ref = _persist_constellation(
        user_root,
        session_id=session_id,
        source=constellation,
        propagator=orbit_propagator,
    )
    site_set_ref, site_refs, ground_node_ref = _persist_ground_set(
        user_root,
        session_id=session_id,
        source=ground_stations,
    )

    session_data: dict[str, Any] = {"name": name}
    scheduling_data: dict[str, Any] = {
        "selection_policy": {"highest_elevation": {}},
        "handover_policy": {"hysteresis": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0}},
        "handover_mode": "bbm",
        "mbb_overlap_ticks": 0,
        "mbb_reserve": 0,
        "handover_concurrency": "one_at_a_time",
        "ranking_order": [
            "service_priority",
            "selection_score",
            "satellite_ground_terminal_capacity",
            "lex_pair",
        ],
        "mbb_preemption": "off",
        "successor_abort_policy": "hard_release",
        "cross_tenant_displacement": "off",
        "bbm_acquire_timeout_ticks": 1,
    }
    if scheduling is not None:
        scheduling_data.update(scheduling)
    time_data: dict[str, Any] = {
        "start_time": "2026-06-08T00:00:00Z",
        "step_seconds": 1,
        "compression": 1,
    }
    if time is not None:
        time_data.update(time)

    session = {
        "session": session_data,
        "segments": [
            {"id": "space", "source": str(constellation_ref)},
            {
                "id": "ground",
                "placement": {"from_site_set": str(site_set_ref)},
                "apply": {"scheduling": scheduling_data},
            },
        ],
        "link_rules": [
            {
                "id": "ground-access",
                "endpoints": [
                    {
                        "select": {"segment": "ground"},
                        "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                        "min_elevation_deg": 10,
                    },
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                    },
                ],
                "topology": {"mode": "visible_candidates"},
            },
            {
                "id": "space-isl",
                "endpoints": [
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
                    },
                    {
                        "select": {"segment": "space"},
                        "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
                    },
                ],
                "topology": {"mode": "nearest_n", "n": 1},
            },
        ],
        "addressing": {
            "loopbacks": [
                {
                    "id": "space-loopbacks-v4",
                    "applies_to": {"segment": "space"},
                    "ipv4_pool": "10.0.0.0/16",
                    "prefix_length": 32,
                    "allocation": "by_node_order",
                },
                {
                    "id": "space-loopbacks-v6",
                    "applies_to": {"segment": "space"},
                    "ipv6_pool": "fd00::/64",
                    "prefix_length": 128,
                    "allocation": "by_node_order",
                },
            ]
        },
        "simulation": {
            "candidate_limits": {
                "max_pairs_per_rule": candidate_limit,
                "max_pairs_per_tick": candidate_limit,
            }
        },
        "routing": _catalog_routing(
            protocol=protocol,
            extensions=extensions or [],
            routing=routing,
        ),
        "time": time_data,
        "dispatch": {"latency_authority": "ome", "max_latency_age_ticks": 3},
    }
    session_path = root / f"{session_id}.yaml"
    fixture = CatalogSessionFixture(
        session,
        roots=roots,
        session_path=session_path,
        constellation_ref=constellation_ref if constellation_ref.namespace == "user" else None,
        orbit_ref=orbit_ref,
        space_node_ref=space_node_ref,
        site_set_ref=site_set_ref if site_set_ref.namespace == "user" else None,
        site_refs=site_refs,
        ground_node_ref=ground_node_ref,
        temporary_directory=temporary_directory,
    )
    fixture.write_session()
    return fixture


def _persist_constellation(
    user_root: Path,
    *,
    session_id: str,
    source: object,
    propagator: str,
) -> tuple[CatalogRef, CatalogRef | None, CatalogRef | None]:
    if isinstance(source, str) and source.startswith("nodalarc:"):
        return CatalogRef(source), None, None
    if isinstance(source, str) and source.startswith("user:"):
        return CatalogRef(source), None, None
    if isinstance(source, str):
        raise ValueError("test constellation strings must be catalog references")
    if not isinstance(source, Mapping):
        raise TypeError("test constellation input must be parameters or a catalog reference")

    planes = 2
    slots = 2
    if "constellation" in source:
        raise ValueError(
            "test constellation documents must be persisted separately and passed by ref"
        )
    unknown = set(source) - {"planes", "slots_per_plane"}
    if unknown:
        raise ValueError(f"unknown test constellation parameters: {sorted(unknown)}")
    plane_data = source.get("planes", {})
    if isinstance(plane_data, Mapping):
        unknown_planes = set(plane_data) - {"count", "sats_per_plane"}
        if unknown_planes:
            raise ValueError(f"unknown test plane parameters: {sorted(unknown_planes)}")
        planes = int(plane_data.get("count", planes))
        slots = int(plane_data.get("sats_per_plane", source.get("slots_per_plane", slots)))

    orbit_ref = CatalogRef(f"user:orbits/{session_id}-orbit.yaml")
    space_node_ref = CatalogRef(f"user:nodes/{session_id}-space-node.yaml")
    constellation_ref = CatalogRef(f"user:constellations/{session_id}-constellation.yaml")
    _write_yaml(
        user_root / orbit_ref.relative_path,
        {
            "orbit": {
                "id": orbit_ref.relative_path.stem,
                "central_body": "nodalarc:bodies/earth.yaml",
                "epoch": "2026-06-08T00:00:00Z",
                "shape": {"altitude_km": 550},
                "orientation": {
                    "inclination_deg": 53,
                    "raan_deg": 0,
                    "argument_of_perigee_deg": 0,
                },
                "phase": {"mean_anomaly_deg": 0},
                "propagator": propagator,
                "reference": "urn:nodalarc:test-fixture",
            }
        },
    )
    _write_yaml(
        user_root / space_node_ref.relative_path,
        {
            "node": {
                "id": space_node_ref.relative_path.stem,
                "display_name": "Test space router",
                "forwarding": "routed",
                "profile": "nodalarc:profiles/frr-router.yaml",
                "ethernet": [],
                "terminals": [
                    {
                        "id": "access",
                        "role": "access",
                        "terminal": "nodalarc:terminals/rf/rf-ka-leo-access.yaml",
                        "count": 1,
                        "boresight": {"mode": "nadir"},
                        "tags": ["access"],
                    },
                    {
                        "id": "isl",
                        "role": "isl",
                        "terminal": "nodalarc:terminals/optical/optical-low-orbit-isl.yaml",
                        "count": 4,
                        "tags": ["isl"],
                    },
                ],
                "payloads": [],
                "reference": "urn:nodalarc:test-fixture",
            }
        },
    )
    _write_yaml(
        user_root / constellation_ref.relative_path,
        {
            "constellation": {
                "id": constellation_ref.relative_path.stem,
                "display_name": "Test constellation",
                "node": str(space_node_ref),
                "orbit": str(orbit_ref),
                "planes": {
                    "count": planes,
                    "raan_spacing_deg": 0 if planes == 1 else 360 / planes,
                },
                "slots_per_plane": slots,
                "phasing": {
                    "mode": ("evenly_spaced_mean_anomaly" if planes == 1 else "walker_delta"),
                    "phase_offset_deg": 0,
                },
                "node_tags": [],
                "reference": "urn:nodalarc:test-fixture",
            }
        },
    )
    return constellation_ref, orbit_ref, space_node_ref


def _persist_ground_set(
    user_root: Path,
    *,
    session_id: str,
    source: object,
) -> tuple[CatalogRef, tuple[CatalogRef, ...], CatalogRef | None]:
    if isinstance(source, str) and source.startswith(("nodalarc:", "user:")):
        return CatalogRef(source), (), None
    if isinstance(source, str):
        raise ValueError("test ground-set strings must be catalog references")
    if not isinstance(source, Mapping):
        raise TypeError("test ground-set input must be station parameters or a catalog reference")

    if isinstance(source.get("site_set"), Mapping):
        site_set = dict(source["site_set"])
        raw_sites = list(site_set.get("sites") or ())
        if all(isinstance(item, str) for item in raw_sites):
            ident = _catalog_id(site_set.get("id"), f"{session_id}-sites")
            site_set["id"] = ident
            ref = CatalogRef(f"user:site-sets/{ident}.yaml")
            _write_yaml(user_root / ref.relative_path, {"site_set": site_set})
            return ref, (), None
        raise ValueError("test site-set documents may contain only catalog references")

    station_values: list[object]
    unknown = set(source) - {"stations", "host_endpoints"}
    if unknown:
        raise ValueError(f"unknown test ground-set parameters: {sorted(unknown)}")
    values = source.get("stations") or ()
    host_endpoints = bool(source.get("host_endpoints", False))
    station_values = list(values) if isinstance(values, (list, tuple)) else []
    if not station_values:
        station_values = [{}, {}]

    ground_node_ref = CatalogRef(f"user:nodes/{session_id}-ground-node.yaml")
    _write_yaml(
        user_root / ground_node_ref.relative_path,
        {
            "node": {
                "id": ground_node_ref.relative_path.stem,
                "display_name": "Test ground router",
                "forwarding": "routed",
                "profile": "nodalarc:profiles/frr-router.yaml",
                "ethernet": [{"id": "terr0"}],
                "terminals": [
                    {
                        "id": "access",
                        "role": "access",
                        "terminal": "nodalarc:terminals/rf/rf-ka-leo-access.yaml",
                        "count": 4,
                        "tags": ["access"],
                    }
                ],
                "payloads": [],
                "reference": "urn:nodalarc:test-fixture",
            }
        },
    )

    host_node_ref = CatalogRef(f"user:nodes/{session_id}-host-node.yaml")
    if host_endpoints:
        _write_yaml(
            user_root / host_node_ref.relative_path,
            {
                "node": {
                    "id": host_node_ref.relative_path.stem,
                    "display_name": "Test host endpoint",
                    "forwarding": "host",
                    "profile": "nodalarc:profiles/linux-host.yaml",
                    "ethernet": [{"id": "terr0"}],
                    "terminals": [],
                    "payloads": [],
                    "reference": "urn:nodalarc:test-fixture",
                }
            },
        )

    site_refs: list[CatalogRef] = []
    for index, station in enumerate(station_values):
        if not isinstance(station, (str, Mapping)):
            raise TypeError("test stations must be names or station parameter mappings")
        station_data = station if isinstance(station, Mapping) else {}
        unknown_station = set(station_data) - {"name", "lat_deg", "lon_deg", "alt_m"}
        if unknown_station:
            raise ValueError(f"unknown test station parameters: {sorted(unknown_station)}")
        raw_name = station_data.get("name") if station_data else station
        site_id = f"{session_id}-{_catalog_id(raw_name, f'site-{index:02d}')}"
        site_ref = CatalogRef(f"user:sites/{site_id}.yaml")
        site_refs.append(site_ref)
        latitude = station_data.get("lat_deg", 30 + index)
        longitude = station_data.get("lon_deg", -100 + index)
        altitude = station_data.get("alt_m", 100)
        _write_yaml(
            user_root / site_ref.relative_path,
            {
                "site": {
                    "id": site_id,
                    "display_name": f"Test site {index}",
                    "lan": {
                        "ipv4": f"172.16.{index}.0/24",
                        "ipv6": f"fd10:0:{index}::/64",
                    },
                    "tags": ["test_ground"],
                    "frame": {"body_fixed": {"body": "nodalarc:bodies/earth.yaml"}},
                    "location": {
                        "lat_deg": latitude,
                        "lon_deg": longitude,
                        "alt_m": altitude,
                    },
                    "nodes": [
                        {
                            "id": "router",
                            "node": str(ground_node_ref),
                            "terminals": {
                                "access": {
                                    "installed_count": 2,
                                    "capabilities": {"boresight": {"mode": "local_vertical"}},
                                }
                            },
                            "payloads": {},
                            "interfaces": {
                                "lo0": {
                                    "ipv4": f"10.255.{index}.1/32",
                                    "ipv6": f"fd00:ff::{index + 1}/128",
                                },
                                "terr0": {
                                    "ipv4": f"172.16.{index}.1/24",
                                    "ipv6": f"fd10:0:{index}::1/64",
                                },
                            },
                            "originated_prefixes": {
                                "ipv4": [f"172.16.{index}.0/24"],
                                "ipv6": [f"fd10:0:{index}::/64"],
                            },
                            "service_priority": 10,
                            "tags": ["test_ground"],
                        }
                    ]
                    + (
                        [
                            {
                                "id": "endpoint",
                                "node": str(host_node_ref),
                                "terminals": {},
                                "payloads": {},
                                "interfaces": {
                                    "lo0": {
                                        "ipv4": f"10.255.{index}.9/32",
                                        "ipv6": f"fd00:ff::9{index + 1}/128",
                                    },
                                    "terr0": {
                                        "ipv4": f"172.16.{index}.9/24",
                                        "ipv6": f"fd10:0:{index}::9/64",
                                    },
                                },
                                "tags": ["test_host"],
                            }
                        ]
                        if host_endpoints
                        else []
                    ),
                }
            },
        )
    site_set_ref = CatalogRef(f"user:site-sets/{session_id}-sites.yaml")
    _write_yaml(
        user_root / site_set_ref.relative_path,
        {
            "site_set": {
                "id": site_set_ref.relative_path.stem,
                "display_name": "Test sites",
                "sites": [str(ref) for ref in site_refs],
            }
        },
    )
    return site_set_ref, tuple(site_refs), ground_node_ref


def _catalog_routing(
    *,
    protocol: str,
    extensions: list[str],
    routing: dict[str, Any] | None,
) -> dict[str, Any]:
    if routing and "domains" in routing:
        return routing
    effective_protocol = str(routing["protocol"]) if routing and "protocol" in routing else protocol
    capabilities: dict[str, Any] = {}
    normalized = set(extensions)
    if "sr" in normalized or "segment-routing" in normalized:
        capabilities["segment_routing"] = {"data_plane": "mpls"}
    if "te" in normalized or "traffic-engineering" in normalized:
        capabilities["traffic_engineering"] = {
            "data_planes": ["mpls"] if "mpls" in normalized else []
        }
    if "mpls" in normalized:
        capabilities["mpls"] = {}
    domain: dict[str, Any] = {
        "id": "test_domain",
        "protocol": effective_protocol,
        "selectors": [{"any": [{"segment": "space"}, {"segment": "ground"}]}],
    }
    if effective_protocol in {"isis", "ospf"}:
        domain["area_assignment"] = {"strategy": "flat"}
    if capabilities:
        domain["capabilities"] = capabilities
    if routing:
        if "area_assignment" in routing:
            area = dict(routing["area_assignment"])
            area.pop("gs_area_id", None)
            domain["area_assignment"] = area
    return {"domains": [domain]}
