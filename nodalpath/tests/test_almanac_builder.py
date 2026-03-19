from __future__ import annotations

from nodalpath.engine.almanac_builder import compute_almanac_entry


class TestComputeAlmanacEntry:
    def test_simple_4node_all_tables(self, simple_4node_topology, prefix_map_simple):
        entry = compute_almanac_entry(simple_4node_topology, prefix_map_simple)
        assert len(entry.forwarding_tables) == 4
        node_ids = {ft.node_id for ft in entry.forwarding_tables}
        assert node_ids == {"sat-P00S00", "sat-P00S01", "gs-alpha", "gs-beta"}

    def test_ground_stations_have_ingress_rules(self, simple_4node_topology, prefix_map_simple):
        entry = compute_almanac_entry(simple_4node_topology, prefix_map_simple)
        for ft in entry.forwarding_tables:
            if ft.node_id.startswith("gs-"):
                assert len(ft.ler_ingress_rules) > 0, f"GS {ft.node_id} should have ingress rules"

    def test_satellites_have_lsr_bindings(self, simple_4node_topology, prefix_map_simple):
        entry = compute_almanac_entry(simple_4node_topology, prefix_map_simple)
        # At least one satellite should have LSR bindings (transit traffic)
        sat_tables = [ft for ft in entry.forwarding_tables if ft.node_id.startswith("sat-")]
        has_bindings = any(len(ft.lsr_bindings) > 0 for ft in sat_tables)
        assert has_bindings

    def test_topology_state_id_generated(self, simple_4node_topology, prefix_map_simple):
        entry = compute_almanac_entry(simple_4node_topology, prefix_map_simple)
        assert entry.topology_state_id.startswith("ts-")
        assert "20260301" in entry.topology_state_id

    def test_computation_time_positive(self, simple_4node_topology, prefix_map_simple):
        entry = compute_almanac_entry(simple_4node_topology, prefix_map_simple)
        assert entry.computation_time_ms > 0

    def test_computed_paths_list(self, simple_4node_topology, prefix_map_simple):
        entry = compute_almanac_entry(simple_4node_topology, prefix_map_simple)
        # GS-to-GS paths must exist
        assert "gs-alpha->gs-beta" in entry.computed_paths
        assert "gs-beta->gs-alpha" in entry.computed_paths
        # Satellite-to-GS paths may also exist (any-to-any computation)
        assert len(entry.computed_paths) >= 2

    def test_disconnected_no_cross_gs_paths(self, disconnected_topology, prefix_map_simple):
        """No GS-to-GS paths exist, but satellite-to-local-GS paths do."""
        entry = compute_almanac_entry(disconnected_topology, prefix_map_simple)
        assert len(entry.forwarding_tables) == 4
        # No cross-GS paths
        assert "gs-alpha->gs-beta" not in entry.computed_paths
        assert "gs-beta->gs-alpha" not in entry.computed_paths
        # Satellite-to-local-GS paths exist (directly connected)
        assert "sat-P00S00->gs-alpha" in entry.computed_paths
        assert "sat-P01S00->gs-beta" in entry.computed_paths

    def test_iridium_36_all_nodes(self, iridium_36_topology, prefix_map_36):
        entry = compute_almanac_entry(iridium_36_topology, prefix_map_36)
        assert len(entry.forwarding_tables) == 42  # 36 sats + 6 GS

    def test_forwarding_table_sim_time(self, simple_4node_topology, prefix_map_simple):
        entry = compute_almanac_entry(simple_4node_topology, prefix_map_simple)
        for ft in entry.forwarding_tables:
            assert ft.sim_time == "2026-03-01T14:30:00Z"
