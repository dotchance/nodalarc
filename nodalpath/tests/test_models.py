from __future__ import annotations

import pytest
from nodalpath.models.topology import TopologyNode, TopologyEdge, TopologySnapshot
from nodalarc.models.path import PathHop
from nodalpath.models.path import ComputedPath
from nodalpath.models.almanac import LabelBinding, IngressRule, ForwardingTable, AlmanacEntry


class TestTopologyNode:
    def test_round_trip(self):
        node = TopologyNode(
            node_id="sat-P02S05", node_type="satellite", sid=16028,
            loopback_ipv4="10.0.2.6", plane=2, slot=5,
        )
        data = node.model_dump_json()
        restored = TopologyNode.model_validate_json(data)
        assert restored == node

    def test_satellite_type(self):
        node = TopologyNode(
            node_id="sat-P00S00", node_type="satellite", sid=16001,
            loopback_ipv4="10.0.0.1", plane=0, slot=0,
        )
        assert node.node_type == "satellite"

    def test_ground_station_type(self):
        node = TopologyNode(
            node_id="gs-hawthorne", node_type="ground_station", sid=24000,
            loopback_ipv4="10.2.0.1",
        )
        assert node.node_type == "ground_station"

    def test_reject_invalid_type(self):
        with pytest.raises(ValueError, match="node_type must be"):
            TopologyNode(
                node_id="x", node_type="router", sid=1,
                loopback_ipv4="10.0.0.1",
            )


class TestTopologyEdge:
    def test_round_trip(self):
        edge = TopologyEdge(
            src_node_id="sat-P00S00", dst_node_id="sat-P00S01",
            src_interface="isl0", dst_interface="isl0",
            latency_ms=3.5, bandwidth_mbps=1000.0, link_type="isl",
        )
        data = edge.model_dump_json()
        restored = TopologyEdge.model_validate_json(data)
        assert restored == edge

    def test_reject_negative_latency(self):
        with pytest.raises(ValueError, match="latency_ms must be non-negative"):
            TopologyEdge(
                src_node_id="a", dst_node_id="b",
                src_interface="isl0", dst_interface="isl0",
                latency_ms=-1.0, bandwidth_mbps=1000.0, link_type="isl",
            )

    def test_terrestrial_link_type_accepted(self):
        edge = TopologyEdge(
            src_node_id="gs-alpha", dst_node_id="gs-beta",
            src_interface="terr1", dst_interface="terr1",
            latency_ms=5.0, bandwidth_mbps=10000.0, link_type="terrestrial",
        )
        assert edge.link_type == "terrestrial"

    def test_reject_invalid_link_type(self):
        with pytest.raises(ValueError, match="link_type must be"):
            TopologyEdge(
                src_node_id="a", dst_node_id="b",
                src_interface="isl0", dst_interface="isl0",
                latency_ms=1.0, bandwidth_mbps=1000.0, link_type="fiber",
            )


class TestTopologySnapshot:
    def test_round_trip(self, simple_4node_topology):
        data = simple_4node_topology.model_dump_json()
        restored = TopologySnapshot.model_validate_json(data)
        assert restored == simple_4node_topology

    def test_reject_duplicate_node_ids(self):
        with pytest.raises(ValueError, match="Duplicate node IDs"):
            TopologySnapshot(
                sim_time="2026-03-01T14:30:00Z",
                nodes=[
                    TopologyNode(node_id="dup", node_type="satellite", sid=16001,
                                 loopback_ipv4="10.0.0.1", plane=0, slot=0),
                    TopologyNode(node_id="dup", node_type="satellite", sid=16002,
                                 loopback_ipv4="10.0.0.2", plane=0, slot=1),
                ],
                edges=[],
            )


class TestTerrestrialLinkConfig:
    def test_round_trip(self):
        from nodalarc.models.session import TerrestrialLinkConfig
        config = TerrestrialLinkConfig(
            station_a="alpha", station_b="beta",
            bandwidth_mbps=10000.0, latency_ms=5.0, loss_pct=0.0,
        )
        data = config.model_dump_json()
        restored = TerrestrialLinkConfig.model_validate_json(data)
        assert restored == config

    def test_defaults(self):
        from nodalarc.models.session import TerrestrialLinkConfig
        config = TerrestrialLinkConfig(station_a="a", station_b="b")
        assert config.bandwidth_mbps == 10000.0
        assert config.latency_ms == 5.0
        assert config.loss_pct == 0.0


class TestPathHop:
    def test_round_trip(self):
        hop = PathHop(node_id="sat-P00S00", node_type="satellite", sid=16001,
                      in_interface="gnd0", out_interface="isl0",
                      latency_to_next_ms=3.5)
        data = hop.model_dump_json()
        restored = PathHop.model_validate_json(data)
        assert restored == hop


class TestComputedPath:
    def test_round_trip(self):
        path = ComputedPath(
            path_id="gs-alpha->gs-beta",
            src_node_id="gs-alpha", dst_node_id="gs-beta",
            hops=[
                PathHop(node_id="gs-alpha", node_type="ground_station",
                        sid=24000, out_interface="gnd0",
                        latency_to_next_ms=5.0),
                PathHop(node_id="gs-beta", node_type="ground_station",
                        sid=24001, in_interface="gnd0"),
            ],
            total_latency_ms=5.0, hop_count=2,
            label_stack=[24001],
        )
        data = path.model_dump_json()
        restored = ComputedPath.model_validate_json(data)
        assert restored == path

    def test_reject_fewer_than_2_hops(self):
        with pytest.raises(ValueError, match="at least 2 hops"):
            ComputedPath(
                path_id="a->b", src_node_id="a", dst_node_id="b",
                hops=[PathHop(node_id="a", node_type="satellite", sid=1)],
                total_latency_ms=0.0, hop_count=1,
                label_stack=[],
            )


class TestLabelBinding:
    def test_round_trip(self):
        binding = LabelBinding(in_label=16001, action="swap",
                               out_label=16002, out_interface="isl0")
        data = binding.model_dump_json()
        restored = LabelBinding.model_validate_json(data)
        assert restored == binding

    def test_reject_invalid_action(self):
        with pytest.raises(ValueError, match="action must be"):
            LabelBinding(in_label=16001, action="drop",
                         out_label=16002, out_interface="isl0")


class TestIngressRule:
    def test_round_trip(self):
        rule = IngressRule(dst_prefix="172.16.1.0/24", push_label=16001,
                           out_interface="gnd0")
        data = rule.model_dump_json()
        restored = IngressRule.model_validate_json(data)
        assert restored == rule


class TestForwardingTable:
    def test_round_trip(self):
        table = ForwardingTable(
            node_id="sat-P00S00",
            topology_state_id="ts-20260301T143000Z",
            sim_time="2026-03-01T14:30:00Z",
            lsr_bindings=[],
            ler_ingress_rules=[],
        )
        data = table.model_dump_json()
        restored = ForwardingTable.model_validate_json(data)
        assert restored == table


class TestAlmanacEntry:
    def test_round_trip(self):
        entry = AlmanacEntry(
            topology_state_id="ts-20260301T143000Z",
            sim_time="2026-03-01T14:30:00Z",
            forwarding_tables=[],
            computed_paths=["gs-alpha->gs-beta"],
            computation_time_ms=12.5,
        )
        data = entry.model_dump_json()
        restored = AlmanacEntry.model_validate_json(data)
        assert restored == entry
