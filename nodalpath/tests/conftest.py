from __future__ import annotations

import pytest

from nodalpath.models.topology import TopologyEdge, TopologyNode, TopologySnapshot


@pytest.fixture(autouse=True, scope="session")
def _init_platform_configs():
    """Initialize PlatformConfig and NodalPathPlatformConfig for all NodalPath tests."""
    from nodalarc.platform_config import PlatformConfig, init_platform_config, reset_platform_config

    from nodalpath.platform import (
        NodalPathPlatformConfig,
        init_nodalpath_config,
        reset_nodalpath_config,
    )

    platform_cfg = PlatformConfig(
        kubernetes_namespace="nodalarc",
        vs_api_http_port=8080,
        vf_static_file_server_port=8081,
        nodalpath_console_http_port=3100,
        nodalpath_fwd_grpc_port=50052,
        nodalpath_fwd_netconf_port=830,
        probe_daemon_http_api_port=9100,
        probe_daemon_udp_data_port=19100,
        deploy_daemon_unix_socket_path="/tmp/nodal-deploy.sock",
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
    )
    init_platform_config(platform_cfg)

    np_cfg = NodalPathPlatformConfig(
        platform_config_path="configs/platform.yaml",
        satellite_sid_range_start=16000,
        ground_station_sid_range_start=24000,
        adjacency_sid_range_start=32000,
        grpc_push_timeout_seconds=10,
        grpc_push_max_parallel_workers=20,
        lookahead_horizon_sim_seconds=5700,
        lookahead_poll_interval_seconds=5.0,
        push_lead_time_sim_seconds=3,
        inspection_max_retained_runs=50,
        inspection_heartbeat_interval_seconds=0,
        console_push_history_max_entries=100,
        console_deviation_history_max_entries=100,
        console_almanac_history_max_entries=200,
        console_event_log_max_entries=300,
    )
    init_nodalpath_config(np_cfg)
    yield
    reset_platform_config()
    reset_nodalpath_config()


@pytest.fixture
def simple_4node_topology() -> TopologySnapshot:
    """A minimal topology: 2 satellites, 2 ground stations, fully connected.

    gs-alpha -- sat-P00S00 -- sat-P00S01 -- gs-beta
                    |_________________________|

    Both ground stations connect to both satellites.
    Direct ISL between the two satellites.
    """
    nodes = [
        TopologyNode(
            node_id="sat-P00S00",
            node_type="satellite",
            sid=16001,
            loopback_ipv4="10.0.0.1",
            plane=0,
            slot=0,
        ),
        TopologyNode(
            node_id="sat-P00S01",
            node_type="satellite",
            sid=16002,
            loopback_ipv4="10.0.0.2",
            plane=0,
            slot=1,
        ),
        TopologyNode(
            node_id="gs-alpha", node_type="ground_station", sid=24000, loopback_ipv4="10.2.0.1"
        ),
        TopologyNode(
            node_id="gs-beta", node_type="ground_station", sid=24001, loopback_ipv4="10.2.1.1"
        ),
    ]
    edges = [
        # ISL between satellites
        TopologyEdge(
            src_node_id="sat-P00S00",
            dst_node_id="sat-P00S01",
            src_interface="isl0",
            dst_interface="isl0",
            latency_ms=3.5,
            bandwidth_mbps=1000.0,
            link_type="isl",
        ),
        # Ground links
        TopologyEdge(
            src_node_id="gs-alpha",
            dst_node_id="sat-P00S00",
            src_interface="gnd0",
            dst_interface="gnd0",
            latency_ms=5.0,
            bandwidth_mbps=200.0,
            link_type="ground",
        ),
        TopologyEdge(
            src_node_id="gs-beta",
            dst_node_id="sat-P00S01",
            src_interface="gnd0",
            dst_interface="gnd0",
            latency_ms=4.5,
            bandwidth_mbps=200.0,
            link_type="ground",
        ),
        # Alternate ground link (gs-beta can also reach sat-P00S00)
        TopologyEdge(
            src_node_id="gs-beta",
            dst_node_id="sat-P00S00",
            src_interface="gnd1",
            dst_interface="gnd1",
            latency_ms=7.0,
            bandwidth_mbps=200.0,
            link_type="ground",
        ),
    ]
    return TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)


@pytest.fixture
def disconnected_topology() -> TopologySnapshot:
    """A topology where two ground stations have no path between them.

    gs-alpha -- sat-P00S00    sat-P01S00 -- gs-beta
    (no link between the two satellites)
    """
    nodes = [
        TopologyNode(
            node_id="sat-P00S00",
            node_type="satellite",
            sid=16001,
            loopback_ipv4="10.0.0.1",
            plane=0,
            slot=0,
        ),
        TopologyNode(
            node_id="sat-P01S00",
            node_type="satellite",
            sid=16012,
            loopback_ipv4="10.0.1.1",
            plane=1,
            slot=0,
        ),
        TopologyNode(
            node_id="gs-alpha", node_type="ground_station", sid=24000, loopback_ipv4="10.2.0.1"
        ),
        TopologyNode(
            node_id="gs-beta", node_type="ground_station", sid=24001, loopback_ipv4="10.2.1.1"
        ),
    ]
    edges = [
        TopologyEdge(
            src_node_id="gs-alpha",
            dst_node_id="sat-P00S00",
            src_interface="gnd0",
            dst_interface="gnd0",
            latency_ms=5.0,
            bandwidth_mbps=200.0,
            link_type="ground",
        ),
        TopologyEdge(
            src_node_id="gs-beta",
            dst_node_id="sat-P01S00",
            src_interface="gnd0",
            dst_interface="gnd0",
            latency_ms=4.5,
            bandwidth_mbps=200.0,
            link_type="ground",
        ),
    ]
    return TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)


@pytest.fixture
def linear_6node_topology() -> TopologySnapshot:
    """A linear chain: gs-alpha -- sat0 -- sat1 -- sat2 -- sat3 -- gs-beta

    Tests multi-hop path computation and label stacking.
    Latencies increase along the chain to make the path deterministic.
    """
    nodes = [
        TopologyNode(
            node_id="gs-alpha", node_type="ground_station", sid=24000, loopback_ipv4="10.2.0.1"
        ),
        TopologyNode(
            node_id="sat-P00S00",
            node_type="satellite",
            sid=16001,
            loopback_ipv4="10.0.0.1",
            plane=0,
            slot=0,
        ),
        TopologyNode(
            node_id="sat-P00S01",
            node_type="satellite",
            sid=16002,
            loopback_ipv4="10.0.0.2",
            plane=0,
            slot=1,
        ),
        TopologyNode(
            node_id="sat-P00S02",
            node_type="satellite",
            sid=16003,
            loopback_ipv4="10.0.0.3",
            plane=0,
            slot=2,
        ),
        TopologyNode(
            node_id="sat-P00S03",
            node_type="satellite",
            sid=16004,
            loopback_ipv4="10.0.0.4",
            plane=0,
            slot=3,
        ),
        TopologyNode(
            node_id="gs-beta", node_type="ground_station", sid=24001, loopback_ipv4="10.2.1.1"
        ),
    ]
    edges = [
        TopologyEdge(
            src_node_id="gs-alpha",
            dst_node_id="sat-P00S00",
            src_interface="gnd0",
            dst_interface="gnd0",
            latency_ms=5.0,
            bandwidth_mbps=200.0,
            link_type="ground",
        ),
        TopologyEdge(
            src_node_id="sat-P00S00",
            dst_node_id="sat-P00S01",
            src_interface="isl0",
            dst_interface="isl1",
            latency_ms=3.0,
            bandwidth_mbps=1000.0,
            link_type="isl",
        ),
        TopologyEdge(
            src_node_id="sat-P00S01",
            dst_node_id="sat-P00S02",
            src_interface="isl0",
            dst_interface="isl1",
            latency_ms=3.2,
            bandwidth_mbps=1000.0,
            link_type="isl",
        ),
        TopologyEdge(
            src_node_id="sat-P00S02",
            dst_node_id="sat-P00S03",
            src_interface="isl0",
            dst_interface="isl1",
            latency_ms=3.1,
            bandwidth_mbps=1000.0,
            link_type="isl",
        ),
        TopologyEdge(
            src_node_id="sat-P00S03",
            dst_node_id="gs-beta",
            src_interface="gnd0",
            dst_interface="gnd0",
            latency_ms=4.8,
            bandwidth_mbps=200.0,
            link_type="ground",
        ),
    ]
    return TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)


@pytest.fixture
def prefix_map_simple() -> dict[str, list[str]]:
    """Terrestrial prefix map for the simple and linear topologies."""
    return {
        "gs-alpha": ["172.16.0.0/24"],
        "gs-beta": ["172.16.1.0/24"],
    }


@pytest.fixture
def iridium_36_topology() -> TopologySnapshot:
    """A 36-satellite Iridium-like topology (6 planes, 6 sats/plane) with 6 ground stations.

    Generated programmatically with realistic ISL connectivity:
    - Intra-plane ISLs: each satellite connects to its two neighbors in the same plane
    - Cross-plane ISLs: each satellite connects to the nearest satellite in adjacent planes
      (not wrapping at the polar seam between plane 0 and plane 5)
    - Ground links: each ground station connects to its nearest visible satellite

    Latencies are synthetic but scaled to realistic LEO ranges (3-8ms ISL, 4-6ms ground).
    Offsets are deterministic based on node indices (no random module).
    """
    planes = 6
    sats_per_plane = 6
    nodes: list[TopologyNode] = []
    edges: list[TopologyEdge] = []

    # Create satellite nodes
    for p in range(planes):
        for s in range(sats_per_plane):
            sid = 16000 + (p * sats_per_plane + s) + 1
            nodes.append(
                TopologyNode(
                    node_id=f"sat-P{p:02d}S{s:02d}",
                    node_type="satellite",
                    sid=sid,
                    loopback_ipv4=f"10.0.{p}.{s + 1}",
                    plane=p,
                    slot=s,
                )
            )

    # Create ground station nodes
    gs_names = ["gs-newyork", "gs-london", "gs-tokyo", "gs-sydney", "gs-mumbai", "gs-saopaulo"]
    for i, name in enumerate(gs_names):
        nodes.append(
            TopologyNode(
                node_id=name,
                node_type="ground_station",
                sid=24000 + i,
                loopback_ipv4=f"10.2.{i}.1",
            )
        )

    # Intra-plane ISLs (ring within each plane)
    for p in range(planes):
        for s in range(sats_per_plane):
            next_s = (s + 1) % sats_per_plane
            src_id = f"sat-P{p:02d}S{s:02d}"
            dst_id = f"sat-P{p:02d}S{next_s:02d}"
            # Deterministic latency: 3.0 + 0.1 * (p + s) % 5
            latency = 3.0 + 0.1 * ((p + s) % 5)
            edges.append(
                TopologyEdge(
                    src_node_id=src_id,
                    dst_node_id=dst_id,
                    src_interface="isl0",
                    dst_interface="isl1",
                    latency_ms=latency,
                    bandwidth_mbps=1000.0,
                    link_type="isl",
                )
            )

    # Cross-plane ISLs (adjacent planes only, no wrap at polar seam)
    for p in range(planes - 1):
        for s in range(sats_per_plane):
            src_id = f"sat-P{p:02d}S{s:02d}"
            dst_id = f"sat-P{p + 1:02d}S{s:02d}"
            # Deterministic latency: 5.0 + 0.2 * (p + s) % 7
            latency = 5.0 + 0.2 * ((p + s) % 7)
            edges.append(
                TopologyEdge(
                    src_node_id=src_id,
                    dst_node_id=dst_id,
                    src_interface="isl2",
                    dst_interface="isl3",
                    latency_ms=latency,
                    bandwidth_mbps=1000.0,
                    link_type="isl",
                )
            )

    # Ground links: each GS connects to one satellite
    # GS i connects to sat-P(i)S00
    for i, name in enumerate(gs_names):
        sat_id = f"sat-P{i:02d}S00"
        latency = 4.5 + 0.15 * i
        edges.append(
            TopologyEdge(
                src_node_id=name,
                dst_node_id=sat_id,
                src_interface="gnd0",
                dst_interface="gnd0",
                latency_ms=latency,
                bandwidth_mbps=200.0,
                link_type="ground",
            )
        )

    return TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)


@pytest.fixture
def prefix_map_36() -> dict[str, list[str]]:
    """Terrestrial prefix map for the 36-node Iridium topology."""
    gs_names = ["gs-newyork", "gs-london", "gs-tokyo", "gs-sydney", "gs-mumbai", "gs-saopaulo"]
    return {name: [f"172.16.{i}.0/24"] for i, name in enumerate(gs_names)}


# ---------------------------------------------------------------------------
# Chunk 2: Orchestrator fixtures
# ---------------------------------------------------------------------------

import json
from datetime import UTC, datetime
from pathlib import Path

from nodalarc.models.events import (
    NodePosition,
    TimelinePositionSnapshot,
    VisibilityEvent,
)


@pytest.fixture
def simple_node_registry() -> dict[str, TopologyNode]:
    """Node registry for 4 sats (2 planes x 2 sats) + 2 ground stations."""
    return {
        "sat-P00S00": TopologyNode(
            node_id="sat-P00S00",
            node_type="satellite",
            sid=16001,
            loopback_ipv4="10.0.0.1",
            plane=0,
            slot=0,
        ),
        "sat-P00S01": TopologyNode(
            node_id="sat-P00S01",
            node_type="satellite",
            sid=16002,
            loopback_ipv4="10.0.1.1",
            plane=0,
            slot=1,
        ),
        "sat-P01S00": TopologyNode(
            node_id="sat-P01S00",
            node_type="satellite",
            sid=16003,
            loopback_ipv4="10.1.0.1",
            plane=1,
            slot=0,
        ),
        "sat-P01S01": TopologyNode(
            node_id="sat-P01S01",
            node_type="satellite",
            sid=16004,
            loopback_ipv4="10.1.1.1",
            plane=1,
            slot=1,
        ),
        "gs-alpha": TopologyNode(
            node_id="gs-alpha",
            node_type="ground_station",
            sid=24000,
            loopback_ipv4="10.255.0.1",
        ),
        "gs-beta": TopologyNode(
            node_id="gs-beta",
            node_type="ground_station",
            sid=24001,
            loopback_ipv4="10.255.1.1",
        ),
    }


@pytest.fixture
def simple_interface_map() -> dict[tuple[str, str], tuple[str, str]]:
    """Interface map for the 4-sat + 2-GS topology.

    ISL pairs (canonical ordering):
    - sat-P00S00 <-> sat-P00S01: isl0 / isl0 (intra-plane 0)
    - sat-P01S00 <-> sat-P01S01: isl0 / isl0 (intra-plane 1)
    - sat-P00S00 <-> sat-P01S00: isl1 / isl1 (cross-plane)
    - sat-P00S01 <-> sat-P01S01: isl1 / isl1 (cross-plane)
    GS pairs: all gnd0/gnd0
    """
    return {
        ("sat-P00S00", "sat-P00S01"): ("isl0", "isl0"),
        ("sat-P01S00", "sat-P01S01"): ("isl0", "isl0"),
        ("sat-P00S00", "sat-P01S00"): ("isl1", "isl1"),
        ("sat-P00S01", "sat-P01S01"): ("isl1", "isl1"),
        ("gs-alpha", "sat-P00S00"): ("gnd0", "gnd0"),
        ("gs-beta", "sat-P01S01"): ("gnd0", "gnd0"),
    }


@pytest.fixture
def simple_prefix_map() -> dict[str, list[str]]:
    """Terrestrial prefix map for the 4-sat + 2-GS topology."""
    return {
        "gs-alpha": ["172.16.0.0/24"],
        "gs-beta": ["172.16.1.0/24"],
    }


@pytest.fixture
def simple_bandwidth_map() -> dict[tuple[str, str], float]:
    """Bandwidth map for the 4-sat + 2-GS topology."""
    return {
        ("sat-P00S00", "sat-P00S01"): 1000.0,
        ("sat-P01S00", "sat-P01S01"): 1000.0,
        ("sat-P00S00", "sat-P01S00"): 1000.0,
        ("sat-P00S01", "sat-P01S01"): 1000.0,
        ("gs-alpha", "sat-P00S00"): 200.0,
        ("gs-beta", "sat-P01S01"): 200.0,
    }


def _build_synthetic_records() -> list[dict]:
    """Build synthetic timeline records for testing.

    Topology: 4 sats (2 planes x 2 sats) + 2 ground stations.
    t=0: all links up (4 ISL + 2 GS = 6 links)
    t=30: cross-plane ISL sat-P00S01<->sat-P01S01 goes down
    t=60: that ISL comes back up
    """
    t0 = datetime(2026, 3, 1, 14, 30, 0, tzinfo=UTC)
    t30 = datetime(2026, 3, 1, 14, 30, 30, tzinfo=UTC)
    t60 = datetime(2026, 3, 1, 14, 31, 0, tzinfo=UTC)

    records: list[dict] = []

    # t=0: Position snapshot
    positions = {
        "sat-P00S00": NodePosition(
            lat_deg=45.0,
            lon_deg=0.0,
            alt_km=550.0,
            vel_x_km_s=0.0,
            vel_y_km_s=7.5,
            vel_z_km_s=0.0,
        ),
        "sat-P00S01": NodePosition(
            lat_deg=45.0,
            lon_deg=30.0,
            alt_km=550.0,
            vel_x_km_s=0.0,
            vel_y_km_s=7.5,
            vel_z_km_s=0.0,
        ),
        "sat-P01S00": NodePosition(
            lat_deg=45.0,
            lon_deg=90.0,
            alt_km=550.0,
            vel_x_km_s=0.0,
            vel_y_km_s=7.5,
            vel_z_km_s=0.0,
        ),
        "sat-P01S01": NodePosition(
            lat_deg=45.0,
            lon_deg=120.0,
            alt_km=550.0,
            vel_x_km_s=0.0,
            vel_y_km_s=7.5,
            vel_z_km_s=0.0,
        ),
        "gs-alpha": NodePosition(
            lat_deg=40.0,
            lon_deg=0.0,
            alt_km=0.0,
            vel_x_km_s=0.0,
            vel_y_km_s=0.0,
            vel_z_km_s=0.0,
        ),
        "gs-beta": NodePosition(
            lat_deg=40.0,
            lon_deg=120.0,
            alt_km=0.0,
            vel_x_km_s=0.0,
            vel_y_km_s=0.0,
            vel_z_km_s=0.0,
        ),
    }
    snap = TimelinePositionSnapshot(sim_time=t0, positions=positions)
    records.append(
        {
            "timestamp_s": 0.0,
            "event_type": "Snapshot",
            "data": snap.model_dump(mode="json"),
        }
    )

    # t=0: ISL link_ups (4 links)
    isl_pairs = [
        ("sat-P00S00", "sat-P00S01", 2000.0),
        ("sat-P01S00", "sat-P01S01", 2000.0),
        ("sat-P00S00", "sat-P01S00", 5000.0),
        ("sat-P00S01", "sat-P01S01", 5000.0),
    ]
    for a, b, range_km in isl_pairs:
        event = VisibilityEvent(
            sim_time=t0,
            node_a=a,
            node_b=b,
            visible=True,
            scheduled=True,
            range_km=range_km,
            elevation_deg=None,
            terminal_type="optical",
        )
        records.append(
            {
                "timestamp_s": 0.0,
                "event_type": "VisibilityEvent",
                "data": event.model_dump(mode="json"),
            }
        )

    # t=0: GS link_ups (2 links)
    gs_pairs = [
        ("gs-alpha", "sat-P00S00", 600.0, 45.0),
        ("gs-beta", "sat-P01S01", 600.0, 45.0),
    ]
    for a, b, range_km, elev in gs_pairs:
        event = VisibilityEvent(
            sim_time=t0,
            node_a=a,
            node_b=b,
            visible=True,
            scheduled=True,
            range_km=range_km,
            elevation_deg=elev,
            terminal_type="optical",
        )
        records.append(
            {
                "timestamp_s": 0.0,
                "event_type": "VisibilityEvent",
                "data": event.model_dump(mode="json"),
            }
        )

    # t=30: Cross-plane ISL link_down
    event = VisibilityEvent(
        sim_time=t30,
        node_a="sat-P00S01",
        node_b="sat-P01S01",
        visible=False,
        scheduled=True,
        range_km=5500.0,
        elevation_deg=None,
        terminal_type="optical",
    )
    records.append(
        {
            "timestamp_s": 30.0,
            "event_type": "VisibilityEvent",
            "data": event.model_dump(mode="json"),
        }
    )

    # t=60: Cross-plane ISL link_up
    event = VisibilityEvent(
        sim_time=t60,
        node_a="sat-P00S01",
        node_b="sat-P01S01",
        visible=True,
        scheduled=True,
        range_km=5000.0,
        elevation_deg=None,
        terminal_type="optical",
    )
    records.append(
        {
            "timestamp_s": 60.0,
            "event_type": "VisibilityEvent",
            "data": event.model_dump(mode="json"),
        }
    )

    return records


# ---------------------------------------------------------------------------
# Chunk 3: Push fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def push_sid_to_loopback(simple_node_registry: dict[str, TopologyNode]) -> dict[int, str]:
    """SID → loopback IP mapping built from simple_node_registry."""
    return {node.sid: node.loopback_ipv4 for node in simple_node_registry.values()}


@pytest.fixture
def push_iface_to_peer_loopback(
    simple_interface_map: dict[tuple[str, str], tuple[str, str]],
    simple_node_registry: dict[str, TopologyNode],
) -> dict[tuple[str, str], str]:
    """(node_id, interface) → peer loopback IP mapping."""
    result: dict[tuple[str, str], str] = {}
    for (src, dst), (src_iface, dst_iface) in simple_interface_map.items():
        src_lo = simple_node_registry[src].loopback_ipv4
        dst_lo = simple_node_registry[dst].loopback_ipv4
        result[(src, src_iface)] = dst_lo
        result[(dst, dst_iface)] = src_lo
    return result


@pytest.fixture
def synthetic_timeline_path(tmp_path: Path) -> Path:
    """Write a synthetic timeline JSONL file and return its path.

    3 timestamps: t=0 (initial), t=30 (link down), t=60 (link up).
    Expected transitions: 3 (initial + down + up).
    """
    records = _build_synthetic_records()
    path = tmp_path / "timeline.jsonl"
    with open(path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return path
