from __future__ import annotations

from nodalpath.engine.graph import build_graph
from nodalpath.models.topology import TopologyEdge, TopologyNode, TopologySnapshot


class TestBuildGraph:
    def test_node_count(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        assert len(graph.adjacency) == 4

    def test_bidirectional_edges(self, simple_4node_topology):
        """Each TopologyEdge produces two directed GraphEdges."""
        graph = build_graph(simple_4node_topology)
        # sat-P00S00 should have edges to sat-P00S01 (from ISL)
        # and edges from ground links (gs-alpha via gnd0, gs-beta via gnd1)
        dsts = [e.dst for e in graph.adjacency["sat-P00S00"]]
        assert "sat-P00S01" in dsts
        assert "gs-alpha" in dsts
        assert "gs-beta" in dsts

    def test_directed_edge_count(self, simple_4node_topology):
        """simple_4node has 4 TopologyEdges → 8 directed GraphEdges."""
        graph = build_graph(simple_4node_topology)
        total = sum(len(edges) for edges in graph.adjacency.values())
        assert total == 8

    def test_node_sids(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        assert graph.node_sids["sat-P00S00"] == 16001
        assert graph.node_sids["sat-P00S01"] == 16002
        assert graph.node_sids["gs-alpha"] == 24000
        assert graph.node_sids["gs-beta"] == 24001

    def test_node_types(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        assert graph.node_types["sat-P00S00"] == "satellite"
        assert graph.node_types["gs-alpha"] == "ground_station"

    def test_ground_stations_list(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        assert set(graph.ground_stations) == {"gs-alpha", "gs-beta"}

    def test_isolated_nodes(self):
        """Isolated nodes (no edges) appear in adjacency dict with empty lists."""
        snapshot = TopologySnapshot(
            sim_time="2026-03-01T14:30:00Z",
            nodes=[
                TopologyNode(
                    node_id="isolated",
                    node_type="satellite",
                    sid=16001,
                    loopback_ipv4="10.0.0.1",
                    plane=0,
                    slot=0,
                ),
            ],
            edges=[],
        )
        graph = build_graph(snapshot)
        assert "isolated" in graph.adjacency
        assert graph.adjacency["isolated"] == []

    def test_disconnected_topology(self, disconnected_topology):
        """Both components are present in a disconnected topology."""
        graph = build_graph(disconnected_topology)
        assert len(graph.adjacency) == 4
        # sat-P00S00 connects to gs-alpha only
        dsts_p00 = {e.dst for e in graph.adjacency["sat-P00S00"]}
        assert dsts_p00 == {"gs-alpha"}
        # sat-P01S00 connects to gs-beta only
        dsts_p01 = {e.dst for e in graph.adjacency["sat-P01S00"]}
        assert dsts_p01 == {"gs-beta"}

    def test_terrestrial_excluded_from_graph(self):
        """Terrestrial edges are excluded from the CSPF graph; nodes remain."""
        nodes = [
            TopologyNode(
                node_id="gs-alpha", node_type="ground_station", sid=24000, loopback_ipv4="10.2.0.1"
            ),
            TopologyNode(
                node_id="gs-beta", node_type="ground_station", sid=24001, loopback_ipv4="10.2.1.1"
            ),
            TopologyNode(
                node_id="sat-P00S00",
                node_type="satellite",
                sid=16001,
                loopback_ipv4="10.0.0.1",
                plane=0,
                slot=0,
            ),
        ]
        edges = [
            TopologyEdge(
                src_node_id="gs-alpha",
                dst_node_id="gs-beta",
                src_interface="terr1",
                dst_interface="terr1",
                latency_ms=5.0,
                bandwidth_mbps=10000.0,
                link_type="terrestrial",
            ),
            TopologyEdge(
                src_node_id="gs-alpha",
                dst_node_id="sat-P00S00",
                src_interface="gnd0",
                dst_interface="gnd0",
                latency_ms=5.0,
                bandwidth_mbps=200.0,
                link_type="ground",
            ),
        ]
        snapshot = TopologySnapshot(sim_time="2026-03-01T14:30:00Z", nodes=nodes, edges=edges)
        graph = build_graph(snapshot)
        # All 3 nodes are in the graph
        assert len(graph.adjacency) == 3
        # Only the ground link is in the graph (not the terrestrial link)
        total_directed = sum(len(e) for e in graph.adjacency.values())
        assert total_directed == 2  # bidirectional ground link
        # gs-beta has no edges (terrestrial was excluded)
        assert graph.adjacency["gs-beta"] == []
