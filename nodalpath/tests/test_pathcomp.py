from __future__ import annotations

import time

from nodalpath.engine.graph import build_graph
from nodalpath.engine.pathcomp import dijkstra, compute_all_gs_paths


class TestDijkstra:
    def test_simple_shortest_path(self, simple_4node_topology):
        """gs-alpha to gs-beta: shortest goes through sat-P00S00 and sat-P00S01."""
        graph = build_graph(simple_4node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        node_ids = [h.node_id for h in path.hops]
        # Shortest: gs-alpha(5.0)->sat-P00S00(3.5)->sat-P00S01(4.5)->gs-beta = 13.0
        # vs gs-alpha(5.0)->sat-P00S00(7.0)->gs-beta via gnd1 = 12.0... wait
        # Actually gs-beta->sat-P00S00 via gnd1 is 7.0, but we need gs-alpha->gs-beta
        # gs-alpha(5.0)->sat-P00S00(3.5)->sat-P00S01(4.5)->gs-beta = 13.0
        # The alternate would be gs-alpha(5.0)->sat-P00S00(7.0)->gs-beta = 12.0
        # But wait: the edge gs-beta->sat-P00S00 has latency 7.0, so
        # gs-alpha->sat-P00S00 is 5.0, sat-P00S00->gs-beta via gnd1 is 7.0 = 12.0
        # That's shorter! So the path should be gs-alpha->sat-P00S00->gs-beta
        assert node_ids == ["gs-alpha", "sat-P00S00", "gs-beta"]

    def test_total_latency(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        # gs-alpha(5.0)->sat-P00S00(7.0)->gs-beta = 12.0
        assert path.total_latency_ms == pytest.approx(12.0)

    def test_hop_count_equals_len_hops(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        assert path.hop_count == len(path.hops)

    def test_first_hop_in_interface_none(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        assert path.hops[0].in_interface is None

    def test_last_hop_out_interface_none(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        assert path.hops[-1].out_interface is None

    def test_disconnected_returns_none(self, disconnected_topology):
        graph = build_graph(disconnected_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is None

    def test_same_src_dst_returns_none(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-alpha")
        assert path is None

    def test_linear_6node_path(self, linear_6node_topology):
        """6-node linear chain should produce a 6-hop path."""
        graph = build_graph(linear_6node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        assert path.hop_count == 6
        node_ids = [h.node_id for h in path.hops]
        assert node_ids == [
            "gs-alpha", "sat-P00S00", "sat-P00S01",
            "sat-P00S02", "sat-P00S03", "gs-beta",
        ]

    def test_linear_total_latency(self, linear_6node_topology):
        graph = build_graph(linear_6node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        expected = 5.0 + 3.0 + 3.2 + 3.1 + 4.8  # = 19.1
        assert path.total_latency_ms == pytest.approx(expected)

    def test_label_stack_transit_sids(self, linear_6node_topology):
        """label_stack contains SIDs for hops[1:] (all except ingress LER)."""
        graph = build_graph(linear_6node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        assert path.label_stack == [16001, 16002, 16003, 16004, 24001]
        assert len(path.label_stack) == 5


class TestComputeAllGsPaths:
    def test_simple_topology_path_count(self, simple_4node_topology):
        """2 GS → 2 directed paths (alpha->beta, beta->alpha)."""
        graph = build_graph(simple_4node_topology)
        paths = compute_all_gs_paths(graph)
        assert len(paths) == 2

    def test_disconnected_topology_no_paths(self, disconnected_topology):
        graph = build_graph(disconnected_topology)
        paths = compute_all_gs_paths(graph)
        assert len(paths) == 0

    def test_iridium_36_all_reachable(self, iridium_36_topology):
        """All 6 GS should be reachable from each other (6*5=30 paths)."""
        graph = build_graph(iridium_36_topology)
        paths = compute_all_gs_paths(graph)
        assert len(paths) == 30
        for path in paths:
            assert path.total_latency_ms > 0
            assert path.total_latency_ms < 500  # Reasonable upper bound for LEO

    def test_iridium_36_performance(self, iridium_36_topology):
        """compute_all_gs_paths on 36-node topology completes in under 1 second."""
        graph = build_graph(iridium_36_topology)
        t0 = time.monotonic()
        paths = compute_all_gs_paths(graph)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0
        assert len(paths) > 0


import pytest
