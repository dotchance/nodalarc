from __future__ import annotations

import pytest
from nodalpath.models.topology import TopologySnapshot, TopologyNode, TopologyEdge


@pytest.fixture
def simple_4node_topology() -> TopologySnapshot:
    """A minimal topology: 2 satellites, 2 ground stations, fully connected.

    gs-alpha -- sat-P00S00 -- sat-P00S01 -- gs-beta
                    |_________________________|

    Both ground stations connect to both satellites.
    Direct ISL between the two satellites.
    """
    nodes = [
        TopologyNode(node_id="sat-P00S00", node_type="satellite", sid=16001,
                     loopback_ipv4="10.0.0.1", plane=0, slot=0),
        TopologyNode(node_id="sat-P00S01", node_type="satellite", sid=16002,
                     loopback_ipv4="10.0.0.2", plane=0, slot=1),
        TopologyNode(node_id="gs-alpha", node_type="ground_station", sid=24000,
                     loopback_ipv4="10.2.0.1"),
        TopologyNode(node_id="gs-beta", node_type="ground_station", sid=24001,
                     loopback_ipv4="10.2.1.1"),
    ]
    edges = [
        # ISL between satellites
        TopologyEdge(src_node_id="sat-P00S00", dst_node_id="sat-P00S01",
                     src_interface="isl0", dst_interface="isl0",
                     latency_ms=3.5, bandwidth_mbps=1000.0, link_type="isl"),
        # Ground links
        TopologyEdge(src_node_id="gs-alpha", dst_node_id="sat-P00S00",
                     src_interface="gnd0", dst_interface="gnd0",
                     latency_ms=5.0, bandwidth_mbps=200.0, link_type="ground"),
        TopologyEdge(src_node_id="gs-beta", dst_node_id="sat-P00S01",
                     src_interface="gnd0", dst_interface="gnd0",
                     latency_ms=4.5, bandwidth_mbps=200.0, link_type="ground"),
        # Alternate ground link (gs-beta can also reach sat-P00S00)
        TopologyEdge(src_node_id="gs-beta", dst_node_id="sat-P00S00",
                     src_interface="gnd1", dst_interface="gnd1",
                     latency_ms=7.0, bandwidth_mbps=200.0, link_type="ground"),
    ]
    return TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)


@pytest.fixture
def disconnected_topology() -> TopologySnapshot:
    """A topology where two ground stations have no path between them.

    gs-alpha -- sat-P00S00    sat-P01S00 -- gs-beta
    (no link between the two satellites)
    """
    nodes = [
        TopologyNode(node_id="sat-P00S00", node_type="satellite", sid=16001,
                     loopback_ipv4="10.0.0.1", plane=0, slot=0),
        TopologyNode(node_id="sat-P01S00", node_type="satellite", sid=16012,
                     loopback_ipv4="10.0.1.1", plane=1, slot=0),
        TopologyNode(node_id="gs-alpha", node_type="ground_station", sid=24000,
                     loopback_ipv4="10.2.0.1"),
        TopologyNode(node_id="gs-beta", node_type="ground_station", sid=24001,
                     loopback_ipv4="10.2.1.1"),
    ]
    edges = [
        TopologyEdge(src_node_id="gs-alpha", dst_node_id="sat-P00S00",
                     src_interface="gnd0", dst_interface="gnd0",
                     latency_ms=5.0, bandwidth_mbps=200.0, link_type="ground"),
        TopologyEdge(src_node_id="gs-beta", dst_node_id="sat-P01S00",
                     src_interface="gnd0", dst_interface="gnd0",
                     latency_ms=4.5, bandwidth_mbps=200.0, link_type="ground"),
    ]
    return TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)


@pytest.fixture
def linear_6node_topology() -> TopologySnapshot:
    """A linear chain: gs-alpha -- sat0 -- sat1 -- sat2 -- sat3 -- gs-beta

    Tests multi-hop path computation and label stacking.
    Latencies increase along the chain to make the path deterministic.
    """
    nodes = [
        TopologyNode(node_id="gs-alpha", node_type="ground_station", sid=24000,
                     loopback_ipv4="10.2.0.1"),
        TopologyNode(node_id="sat-P00S00", node_type="satellite", sid=16001,
                     loopback_ipv4="10.0.0.1", plane=0, slot=0),
        TopologyNode(node_id="sat-P00S01", node_type="satellite", sid=16002,
                     loopback_ipv4="10.0.0.2", plane=0, slot=1),
        TopologyNode(node_id="sat-P00S02", node_type="satellite", sid=16003,
                     loopback_ipv4="10.0.0.3", plane=0, slot=2),
        TopologyNode(node_id="sat-P00S03", node_type="satellite", sid=16004,
                     loopback_ipv4="10.0.0.4", plane=0, slot=3),
        TopologyNode(node_id="gs-beta", node_type="ground_station", sid=24001,
                     loopback_ipv4="10.2.1.1"),
    ]
    edges = [
        TopologyEdge(src_node_id="gs-alpha", dst_node_id="sat-P00S00",
                     src_interface="gnd0", dst_interface="gnd0",
                     latency_ms=5.0, bandwidth_mbps=200.0, link_type="ground"),
        TopologyEdge(src_node_id="sat-P00S00", dst_node_id="sat-P00S01",
                     src_interface="isl0", dst_interface="isl1",
                     latency_ms=3.0, bandwidth_mbps=1000.0, link_type="isl"),
        TopologyEdge(src_node_id="sat-P00S01", dst_node_id="sat-P00S02",
                     src_interface="isl0", dst_interface="isl1",
                     latency_ms=3.2, bandwidth_mbps=1000.0, link_type="isl"),
        TopologyEdge(src_node_id="sat-P00S02", dst_node_id="sat-P00S03",
                     src_interface="isl0", dst_interface="isl1",
                     latency_ms=3.1, bandwidth_mbps=1000.0, link_type="isl"),
        TopologyEdge(src_node_id="sat-P00S03", dst_node_id="gs-beta",
                     src_interface="gnd0", dst_interface="gnd0",
                     latency_ms=4.8, bandwidth_mbps=200.0, link_type="ground"),
    ]
    return TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)


@pytest.fixture
def prefix_map_simple() -> dict[str, str]:
    """Terrestrial prefix map for the simple and linear topologies."""
    return {
        "gs-alpha": "172.16.0.0/24",
        "gs-beta": "172.16.1.0/24",
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
            nodes.append(TopologyNode(
                node_id=f"sat-P{p:02d}S{s:02d}",
                node_type="satellite",
                sid=sid,
                loopback_ipv4=f"10.0.{p}.{s + 1}",
                plane=p,
                slot=s,
            ))

    # Create ground station nodes
    gs_names = ["gs-newyork", "gs-london", "gs-tokyo", "gs-sydney", "gs-mumbai", "gs-saopaulo"]
    for i, name in enumerate(gs_names):
        nodes.append(TopologyNode(
            node_id=name,
            node_type="ground_station",
            sid=24000 + i,
            loopback_ipv4=f"10.2.{i}.1",
        ))

    # Intra-plane ISLs (ring within each plane)
    for p in range(planes):
        for s in range(sats_per_plane):
            next_s = (s + 1) % sats_per_plane
            src_id = f"sat-P{p:02d}S{s:02d}"
            dst_id = f"sat-P{p:02d}S{next_s:02d}"
            # Deterministic latency: 3.0 + 0.1 * (p + s) % 5
            latency = 3.0 + 0.1 * ((p + s) % 5)
            edges.append(TopologyEdge(
                src_node_id=src_id,
                dst_node_id=dst_id,
                src_interface="isl0",
                dst_interface="isl1",
                latency_ms=latency,
                bandwidth_mbps=1000.0,
                link_type="isl",
            ))

    # Cross-plane ISLs (adjacent planes only, no wrap at polar seam)
    for p in range(planes - 1):
        for s in range(sats_per_plane):
            src_id = f"sat-P{p:02d}S{s:02d}"
            dst_id = f"sat-P{p + 1:02d}S{s:02d}"
            # Deterministic latency: 5.0 + 0.2 * (p + s) % 7
            latency = 5.0 + 0.2 * ((p + s) % 7)
            edges.append(TopologyEdge(
                src_node_id=src_id,
                dst_node_id=dst_id,
                src_interface="isl2",
                dst_interface="isl3",
                latency_ms=latency,
                bandwidth_mbps=1000.0,
                link_type="isl",
            ))

    # Ground links: each GS connects to one satellite
    # GS i connects to sat-P(i)S00
    for i, name in enumerate(gs_names):
        sat_id = f"sat-P{i:02d}S00"
        latency = 4.5 + 0.15 * i
        edges.append(TopologyEdge(
            src_node_id=name,
            dst_node_id=sat_id,
            src_interface="gnd0",
            dst_interface="gnd0",
            latency_ms=latency,
            bandwidth_mbps=200.0,
            link_type="ground",
        ))

    return TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)


@pytest.fixture
def prefix_map_36() -> dict[str, str]:
    """Terrestrial prefix map for the 36-node Iridium topology."""
    gs_names = ["gs-newyork", "gs-london", "gs-tokyo", "gs-sydney", "gs-mumbai", "gs-saopaulo"]
    return {name: f"172.16.{i}.0/24" for i, name in enumerate(gs_names)}
