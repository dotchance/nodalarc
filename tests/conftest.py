"""Shared test fixtures for Nodal Arc.

Expanded incrementally as Steps 2-8 add new test needs.
"""

import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

# Path constants for local test assets.
PROJECT_ROOT = Path(__file__).parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def _init_platform_config():
    """Initialize PlatformConfig for all tests from standard values."""
    from nodalarc.platform_config import PlatformConfig, init_platform_config, reset_platform_config

    cfg = PlatformConfig(
        kubernetes_namespace="nodalarc",
        default_service_host="127.0.0.1",
        vs_api_http_port=8080,
        vf_static_file_server_port=8081,
        nodalpath_console_http_port=3100,
        nodalpath_fwd_grpc_port=50051,
        nodalpath_fwd_netconf_port=830,
        probe_daemon_http_api_port=9100,
        probe_daemon_udp_data_port=19100,
        deploy_daemon_unix_socket_path="/tmp/nodal-deploy.sock",
        session_data_root="/var/nodalarc/sessions",
        frr_config_directory_in_container="/etc/frr",
        frr_config_ready_sentinel_path="/etc/frr/.config-ready",
        veth_interface_mtu_bytes=9000,
        mpls_kernel_max_platform_labels=100000,
        pod_ready_timeout_seconds=600,
        pod_termination_timeout_seconds=120,
        deploy_operation_timeout_seconds=600,
        deploy_daemon_accept_timeout_seconds=660,
        frr_config_delivery_settle_seconds=5,
        kubectl_exec_max_parallel_workers=20,
        vs_api_max_websocket_connections=50,
        vs_api_visual_beam_falloff_exponent=2.0,
        vs_api_actuation_expected_latency_ms=250.0,
        vs_api_actuation_fault_after_ms=1200.0,
        scheduler_clean_kernel_audit_interval_s=60.0,
        default_session_pod_placement_policy="planePerNode",
        default_session_pod_planes_per_group=1,
        vs_api_introspect_max_requests_per_minute=10,
        vs_api_playback_max_requests_per_minute=30,
        vs_api_session_switch_max_requests_per_minute=5,
        vs_api_introspect_max_response_bytes=65536,
        vs_api_introspect_command_timeout_seconds=15,
        trace_interval_seconds=3.0,
        trace_interval_fast_seconds=1.0,
        trace_fast_window_seconds=30.0,
        host_inotify_max_user_instances=512,
        host_file_descriptor_limit=65536,
        # Unit tests must not silently bind to a developer's live local NATS.
        nats_url="nats://unit-test-nats.invalid:4222",
    )
    init_platform_config(cfg)
    yield
    reset_platform_config()


@pytest.fixture(autouse=True)
def _node_agent_ops_spool_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keep Node Agent pre-init OpsEvent spooling inside the test temp dir."""
    monkeypatch.setenv("NODE_AGENT_OPS_SPOOL", str(tmp_path / "node-agent-ops-events.jsonl"))


def build_segment_session_dict(
    *,
    name: str,
    constellation,
    ground_stations,
    data_dir: str | None = None,
    protocol: str = "isis",
    extensions: list[str] | None = None,
    orbit_propagator: str = "keplerian-circular",
    routing: dict | None = None,
    scheduling: dict | None = None,
    time: dict | None = None,
    candidate_limit: int = 100000,
) -> dict:
    """Build a segment-grammar session for product/runtime tests."""
    constellation_source = _catalog_constellation_source(
        constellation, propagator=_catalog_propagator(orbit_propagator)
    )
    ground_source = _catalog_site_set_source(ground_stations)
    routing_data = _catalog_routing(protocol=protocol, extensions=extensions or [], routing=routing)
    scheduling_data = scheduling or {
        "selection_policy": {"highest_elevation": {}},
        "handover_policy": {"hysteresis": {"discount_factor": 1.15, "mask_fade_range_deg": 5.0}},
        "handover_mode": "bbm",
        "mbb_overlap_ticks": 0,
        "mbb_reserve": 0,
    }
    time_data = {
        "start_time": "2026-06-08T00:00:00Z",
        "step_seconds": 1,
        "compression": 1,
    }
    if time is not None:
        time_data.update(time)
    session_data = {"name": name}
    if data_dir is not None:
        session_data["data_dir"] = data_dir
    data = {
        "session": session_data,
        "segments": [
            {
                "id": "space",
                "source": constellation_source,
            },
            {
                "id": "ground",
                "placement": {"from_site_set": ground_source},
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
        "routing": routing_data,
        "time": time_data,
        "dispatch": {"latency_authority": "ome", "max_latency_age_ticks": 3},
    }
    return data


def load_runtime_segment_test_resolution(*, origin: str, name: str = "earth-leo-simple"):
    """Build and resolve a runtime-complete catalog session for unit tests."""
    from nodalarc.resolve_session import load_session_resolution_from_file

    with tempfile.TemporaryDirectory() as tmp_dir:
        session_path = Path(tmp_dir) / f"{name}.yaml"
        session_path.write_text(
            yaml.dump(
                build_segment_session_dict(
                    name=name,
                    constellation="configs/constellations/demo-36.yaml",
                    ground_stations="configs/ground-stations/sets/demo.yaml",
                    protocol="ospf",
                    orbit_propagator="j2-mean-elements",
                ),
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return load_session_resolution_from_file(session_path, origin=origin)


def load_runtime_ome_test_inputs(*, origin: str, name: str = "earth-leo-simple"):
    """Return OME's current resolved runtime inputs for a catalog test session."""
    from nodalarc.ome_inputs import build_ome_inputs_from_resolved

    resolution = load_runtime_segment_test_resolution(origin=origin, name=name)
    runtime = build_ome_inputs_from_resolved(resolution.resolved)
    if resolution.resolved.time is None:
        raise AssertionError("test session must include time")
    session_view = SimpleNamespace(
        time=SimpleNamespace(step_seconds=int(resolution.resolved.time.step_seconds)),
        orbit=SimpleNamespace(propagator=runtime.propagator_id),
        scheduling=SimpleNamespace(ground=runtime.ground_scheduling),
        ground_link_model=runtime.ground_link_model,
        node_metadata=runtime.node_metadata,
        body_frames=runtime.body_frames,
    )
    return (
        session_view,
        resolution.resolved,
        runtime.gs_file,
        runtime.satellites,
        runtime.addressing,
        runtime.neighbors,
        dict(runtime.ground_candidate_satellites_by_gs),
    )


def _catalog_id(value: object, default: str) -> str:
    text = str(value or default)
    stem = Path(text).stem if "/" in text or text.endswith((".yaml", ".yml")) else text
    token = re.sub(r"[^a-z0-9_-]+", "-", stem.lower()).strip("-")
    return token or default


def _catalog_body() -> dict:
    return {
        "body": {
            "id": "earth",
            "display_name": "Earth",
            "gravitational_parameter_km3_s2": 398600.4418,
            "mean_radius_km": 6371.0088,
            "equatorial_radius_km": 6378.137,
            "polar_radius_km": 6356.752,
            "reference": "test-fixture",
        }
    }


def _rf_terminal() -> dict:
    return {
        "terminal": {
            "id": "rf-test-access",
            "display_name": "RF test access",
            "medium": "rf",
            "signal": {"band": "ka", "frequency_hz": 20_000_000_000},
            "bandwidth_mbps": {"transmit": 1000, "receive": 1000},
            "tracking_capacity": 1,
            "max_range_km": 50000,
            "limits": {
                "azimuth_deg": {"min": -180, "max": 180},
                "elevation_deg": {"min": 0, "max": 90},
                "max_tracking_rate_deg_s": 2,
            },
            "reference": "test-fixture",
        }
    }


def _optical_terminal() -> dict:
    return {
        "terminal": {
            "id": "optical-test-isl",
            "display_name": "Optical test ISL",
            "medium": "optical",
            "signal": {"wavelength_nm": 1550},
            "bandwidth_mbps": {"transmit": 10000, "receive": 10000},
            "tracking_capacity": 1,
            "max_range_km": 100000,
            "limits": {
                "azimuth_deg": {"min": -180, "max": 180},
                "elevation_deg": {"min": -90, "max": 90},
                "max_tracking_rate_deg_s": 5,
            },
            "reference": "test-fixture",
        }
    }


def _space_node() -> dict:
    return {
        "node": {
            "id": "test-space-router",
            "display_name": "Test space router",
            "forwarding": "routed",
            "ethernet": [],
            "terminals": [
                {
                    "id": "access",
                    "role": "access",
                    "terminal": _rf_terminal(),
                    "count": 1,
                    "tags": ["access"],
                },
                {
                    "id": "isl",
                    "role": "isl",
                    "terminal": _optical_terminal(),
                    "count": 4,
                    "tags": ["isl"],
                },
            ],
            "payloads": [],
            "reference": "test-fixture",
        }
    }


def _ground_node() -> dict:
    return {
        "node": {
            "id": "test-ground-router",
            "display_name": "Test ground router",
            "forwarding": "routed",
            "ethernet": [{"id": "terr0"}],
            "terminals": [
                {
                    "id": "access",
                    "role": "access",
                    "terminal": _rf_terminal(),
                    "count": 4,
                    "tags": ["access"],
                }
            ],
            "payloads": [],
            "reference": "test-fixture",
        }
    }


def _catalog_orbit(propagator: str = "j2_mean_elements") -> dict:
    return {
        "orbit": {
            "id": "earth-leo-test",
            "central_body": _catalog_body(),
            "epoch": "2026-06-08T00:00:00Z",
            "shape": {"altitude_km": 550},
            "orientation": {
                "inclination_deg": 53,
                "raan_deg": 0,
                "argument_of_perigee_deg": 0,
            },
            "phase": {"mean_anomaly_deg": 0},
            "propagator": propagator,
            "reference": "test-fixture",
        }
    }


def _catalog_constellation_source(
    source: object, *, propagator: str = "j2_mean_elements"
) -> object:
    if isinstance(source, str) and source.startswith("nodalarc:"):
        return source
    if isinstance(source, dict) and "constellation" in source:
        return source
    planes = 2
    slots = 2
    if isinstance(source, dict):
        plane_data = source.get("planes", {})
        planes = int(plane_data.get("count", planes))
        slots = int(plane_data.get("sats_per_plane", source.get("slots_per_plane", slots)))
    elif isinstance(source, str):
        if "demo-36" in source:
            planes, slots = 6, 6
        elif "starlink-176" in source:
            planes, slots = 8, 22
    ident = _catalog_id(source, "test-constellation")
    return {
        "constellation": {
            "id": ident,
            "display_name": ident,
            "node": _space_node(),
            "orbit": _catalog_orbit(propagator),
            "planes": {"count": planes, "raan_spacing_deg": 360 / planes},
            "slots_per_plane": slots,
            "phasing": {"mode": "walker_delta", "phase_offset_deg": 0},
            "node_tags": [],
            "reference": "test-fixture",
        }
    }


def _catalog_site_set_source(source: object) -> object:
    if isinstance(source, str) and source.startswith("nodalarc:"):
        return source
    if isinstance(source, dict) and "site_set" in source:
        return source
    count = 2
    if isinstance(source, dict):
        count = len(source.get("stations") or source.get("sites") or range(count))
    elif isinstance(source, str) and "demo" in source:
        count = 7
    sites = []
    for index in range(count):
        site_id = f"earth-test-site-{index:02d}"
        sites.append(
            {
                "site": {
                    "id": site_id,
                    "display_name": f"Earth test site {index}",
                    "lan": {
                        "ipv4": f"172.16.{index}.0/24",
                        "ipv6": f"fd10:0:{index}::/64",
                    },
                    "tags": ["test_ground"],
                    "frame": {"body_fixed": {"body": _catalog_body()}},
                    "location": {
                        "lat_deg": 30 + index,
                        "lon_deg": -100 + index,
                        "alt_m": 100,
                    },
                    "nodes": [
                        {
                            "id": "router",
                            "model": _ground_node(),
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
                    ],
                }
            }
        )
    ident = _catalog_id(source, "test-sites")
    return {"site_set": {"id": ident, "display_name": ident, "sites": sites}}


def _catalog_routing(
    *,
    protocol: str,
    extensions: list[str],
    routing: dict | None,
) -> dict:
    if routing and "domains" in routing:
        return routing
    capabilities: dict = {}
    normalized = set(extensions)
    if "sr" in normalized or "segment-routing" in normalized:
        capabilities["segment_routing"] = {"data_plane": "mpls"}
    if "te" in normalized or "traffic-engineering" in normalized:
        capabilities["traffic_engineering"] = {
            "data_planes": ["mpls"] if "mpls" in normalized else []
        }
    if "mpls" in normalized:
        capabilities["mpls"] = {}
    domain: dict = {
        "id": "test_domain",
        "protocol": protocol,
        "selectors": [{"any": [{"segment": "space"}, {"segment": "ground"}]}],
        "area_assignment": {"strategy": "flat"},
    }
    if capabilities:
        domain["capabilities"] = capabilities
    if routing:
        if "area_assignment" in routing:
            area = dict(routing["area_assignment"])
            area.pop("gs_area_id", None)
            domain["area_assignment"] = area
        if "protocol" in routing:
            domain["protocol"] = routing["protocol"]
    return {"domains": [domain]}


def _catalog_propagator(value: str) -> str:
    return {
        "keplerian-circular": "j2_mean_elements",
        "j2-mean-elements": "j2_mean_elements",
    }.get(value, value)
