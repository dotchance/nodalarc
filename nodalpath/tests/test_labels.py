from __future__ import annotations

from nodalpath.engine.graph import build_graph
from nodalpath.engine.pathcomp import dijkstra, compute_all_gs_paths
from nodalpath.engine.labels import (
    _srgb_base, _gs_sid_base,
    compute_sid, path_to_label_stack,
    build_lsr_bindings, build_ler_ingress_rules,
)


class TestComputeSid:
    def test_satellite_sid(self):
        sid = compute_sid("sat-P02S05", "satellite", plane=2, slot=5, sats_per_plane=11)
        assert sid == _srgb_base() + (2 * 11 + 5) + 1
        assert sid == 16028

    def test_ground_station_sid(self):
        sid = compute_sid("gs-hawthorne", "ground_station", gs_index=0)
        assert sid == _gs_sid_base() + 0
        assert sid == 24000

    def test_sid_uniqueness_36_constellation(self):
        """All SIDs across a 36-sat + 6 GS constellation must be unique."""
        sids = set()
        sats_per_plane = 6
        for p in range(6):
            for s in range(sats_per_plane):
                sid = compute_sid(f"sat-P{p:02d}S{s:02d}", "satellite",
                                  plane=p, slot=s, sats_per_plane=sats_per_plane)
                sids.add(sid)
        for i in range(6):
            sid = compute_sid(f"gs-{i}", "ground_station", gs_index=i)
            sids.add(sid)
        assert len(sids) == 42  # 36 sats + 6 GS


class TestPathToLabelStack:
    def test_label_stack_excludes_ingress(self, simple_4node_topology):
        graph = build_graph(simple_4node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        stack = path_to_label_stack(path)
        assert stack == path.label_stack
        # First hop is gs-alpha (ingress), should not be in stack
        assert path.hops[0].sid not in stack

    def test_6hop_path_has_5_labels(self, linear_6node_topology):
        graph = build_graph(linear_6node_topology)
        path = dijkstra(graph, "gs-alpha", "gs-beta")
        assert path is not None
        stack = path_to_label_stack(path)
        assert len(stack) == 5


class TestBuildLsrBindings:
    def test_transit_satellite_bindings(self, simple_4node_topology, prefix_map_simple):
        graph = build_graph(simple_4node_topology)
        paths = compute_all_gs_paths(graph)

        # sat-P00S00 is a transit node in the alpha->beta path
        bindings = build_lsr_bindings("sat-P00S00", paths, graph)
        assert len(bindings) > 0

        for binding in bindings:
            assert binding.in_label == graph.node_sids["sat-P00S00"]
            assert binding.action in ("swap", "pop")
            assert binding.out_interface != ""

    def test_penultimate_hop_pop(self, linear_6node_topology):
        """The penultimate hop (sat-P00S03) should have action 'pop'."""
        graph = build_graph(linear_6node_topology)
        paths = compute_all_gs_paths(graph)

        # For the alpha->beta path, sat-P00S03 is the penultimate hop
        bindings = build_lsr_bindings("sat-P00S03", paths, graph)
        # Find the binding for the alpha->beta direction
        pop_bindings = [b for b in bindings if b.action == "pop"]
        assert len(pop_bindings) >= 1
        for pb in pop_bindings:
            assert pb.out_label is None

    def test_node_not_in_any_path(self, disconnected_topology):
        """A node not transiting any path gets empty bindings."""
        graph = build_graph(disconnected_topology)
        paths = compute_all_gs_paths(graph)  # Empty since disconnected
        bindings = build_lsr_bindings("sat-P00S00", paths, graph)
        assert bindings == []


class TestBuildLerIngressRules:
    def test_ground_station_ingress_rules(self, simple_4node_topology, prefix_map_simple):
        graph = build_graph(simple_4node_topology)
        paths = compute_all_gs_paths(graph)

        rules = build_ler_ingress_rules("gs-alpha", paths, graph, prefix_map_simple)
        assert len(rules) == 1  # One rule for gs-beta destination
        assert rules[0].dst_prefix == "172.16.1.0/24"
        assert rules[0].push_label > 0
        assert rules[0].out_interface != ""

    def test_satellite_returns_empty(self, simple_4node_topology, prefix_map_simple):
        """Satellites are not LERs, so they get no ingress rules."""
        graph = build_graph(simple_4node_topology)
        paths = compute_all_gs_paths(graph)

        rules = build_ler_ingress_rules("sat-P00S00", paths, graph, prefix_map_simple)
        assert rules == []

    def test_multi_prefix_ingress_rules(self, simple_4node_topology):
        """A GS with 2 prefixes produces 2 IngressRules from each source."""
        graph = build_graph(simple_4node_topology)
        paths = compute_all_gs_paths(graph)

        prefix_map = {
            "gs-alpha": ["172.16.0.0/24"],
            "gs-beta": ["172.16.1.0/24", "10.99.0.0/16"],
        }
        rules = build_ler_ingress_rules("gs-alpha", paths, graph, prefix_map)
        prefixes = {r.dst_prefix for r in rules}
        assert prefixes == {"172.16.1.0/24", "10.99.0.0/16"}

    def test_shared_prefix_best_path(self, simple_4node_topology):
        """Shared prefix (e.g. 0.0.0.0/0) from 2 GS → picks nearer GS."""
        graph = build_graph(simple_4node_topology)
        from nodalpath.engine.pathcomp import compute_all_paths

        prefix_map = {
            "gs-alpha": ["0.0.0.0/0"],
            "gs-beta": ["0.0.0.0/0"],
        }
        paths = compute_all_paths(graph, prefix_map)

        # From sat-P00S00: gs-alpha is directly connected (5.0ms),
        # gs-beta is further (via sat-P00S01, 3.5+4.5=8.0ms or via gnd1 7.0ms)
        rules = build_ler_ingress_rules("sat-P00S00", paths, graph, prefix_map)
        assert len(rules) == 1
        assert rules[0].dst_prefix == "0.0.0.0/0"
        # The push_label should be for gs-alpha (nearest)
        assert rules[0].push_label == graph.node_sids["gs-alpha"]

    def test_empty_prefix_no_rules(self, simple_4node_topology):
        """Node with empty prefix list generates no ingress rules targeting it."""
        graph = build_graph(simple_4node_topology)
        paths = compute_all_gs_paths(graph)

        prefix_map = {
            "gs-alpha": [],
            "gs-beta": ["172.16.1.0/24"],
        }
        # gs-beta should still get a rule for gs-alpha (but gs-alpha has no prefixes)
        rules = build_ler_ingress_rules("gs-beta", paths, graph, prefix_map)
        # Only gs-alpha's prefixes could be targets, but it has none
        assert rules == []
