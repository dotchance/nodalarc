"""Tests for nodalpath.push.vtysh_push — pure string translation."""

from __future__ import annotations

import logging

import pytest

from nodalpath.models.almanac import ForwardingTable, IngressRule, LabelBinding
from nodalpath.push.vtysh_push import (
    diff_forwarding_tables,
    forwarding_table_to_vtysh,
    ingress_rule_remove_command,
    ingress_rule_to_command,
    lsr_binding_remove_command,
    lsr_binding_to_command,
    wrap_in_configure_block,
)


# ---------------------------------------------------------------------------
# lsr_binding_to_command
# ---------------------------------------------------------------------------


class TestLsrBindingToCommand:
    def test_swap_contains_in_label_nexthop_out_label(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=16002,
            out_interface="isl0",
        )
        result = lsr_binding_to_command(
            binding, "sat-P00S00", push_sid_to_loopback, push_iface_to_peer_loopback,
        )
        assert "16001" in result
        assert "16002" in result
        # nexthop should be the loopback of SID 16002
        assert push_sid_to_loopback[16002] in result
        assert "mpls lsp" in result

    def test_pop_contains_implicit_null(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        binding = LabelBinding(
            in_label=16001, action="pop", out_label=None,
            out_interface="isl0",
        )
        result = lsr_binding_to_command(
            binding, "sat-P00S00", push_sid_to_loopback, push_iface_to_peer_loopback,
        )
        assert "implicit-null" in result
        # nexthop from iface_to_peer_loopback for (sat-P00S00, isl0)
        assert push_iface_to_peer_loopback[("sat-P00S00", "isl0")] in result

    def test_swap_unknown_sid_returns_empty(
        self, push_sid_to_loopback, push_iface_to_peer_loopback, caplog,
    ):
        binding = LabelBinding(
            in_label=16001, action="swap", out_label=99999,
            out_interface="isl0",
        )
        with caplog.at_level(logging.ERROR):
            result = lsr_binding_to_command(
                binding, "sat-P00S00", push_sid_to_loopback, push_iface_to_peer_loopback,
            )
        assert result == ""
        assert "Unknown SID" in caplog.text

    def test_pop_unknown_interface_returns_empty(
        self, push_sid_to_loopback, push_iface_to_peer_loopback, caplog,
    ):
        binding = LabelBinding(
            in_label=16001, action="pop", out_label=None,
            out_interface="nonexistent99",
        )
        with caplog.at_level(logging.ERROR):
            result = lsr_binding_to_command(
                binding, "sat-P00S00", push_sid_to_loopback, push_iface_to_peer_loopback,
            )
        assert result == ""
        assert "Unknown interface" in caplog.text


# ---------------------------------------------------------------------------
# ingress_rule_to_command / ingress_rule_remove_command
# ---------------------------------------------------------------------------


class TestIngressRuleToCommand:
    def test_contains_prefix_label_nexthop(
        self, push_iface_to_peer_loopback,
    ):
        rule = IngressRule(
            dst_prefix="172.16.1.0/24", push_label=16002, out_interface="gnd0",
        )
        result = ingress_rule_to_command(rule, "gs-alpha", push_iface_to_peer_loopback)
        assert "ip route" in result
        assert "172.16.1.0/24" in result
        assert "16002" in result
        assert "label" in result
        nexthop = push_iface_to_peer_loopback[("gs-alpha", "gnd0")]
        assert nexthop in result

    def test_unknown_interface_returns_empty(
        self, push_iface_to_peer_loopback, caplog,
    ):
        rule = IngressRule(
            dst_prefix="172.16.1.0/24", push_label=16002, out_interface="nonexistent0",
        )
        with caplog.at_level(logging.ERROR):
            result = ingress_rule_to_command(rule, "gs-alpha", push_iface_to_peer_loopback)
        assert result == ""
        assert "Unknown interface" in caplog.text

    def test_remove_contains_no_ip_route(
        self, push_iface_to_peer_loopback,
    ):
        rule = IngressRule(
            dst_prefix="172.16.1.0/24", push_label=16002, out_interface="gnd0",
        )
        result = ingress_rule_remove_command(rule, "gs-alpha", push_iface_to_peer_loopback)
        assert "no ip route" in result
        assert "172.16.1.0/24" in result
        nexthop = push_iface_to_peer_loopback[("gs-alpha", "gnd0")]
        assert nexthop in result


# ---------------------------------------------------------------------------
# wrap_in_configure_block
# ---------------------------------------------------------------------------


class TestWrapInConfigureBlock:
    def test_wraps_with_configure_terminal_and_write_memory(self):
        result = wrap_in_configure_block([" mpls lsp 16001 10.0.0.2 16002"])
        assert result.startswith("configure terminal")
        assert result.endswith("write memory")
        assert "mpls lsp" in result

    def test_empty_returns_empty(self):
        assert wrap_in_configure_block([]) == ""

    def test_all_blank_returns_empty(self):
        assert wrap_in_configure_block(["", ""]) == ""


# ---------------------------------------------------------------------------
# forwarding_table_to_vtysh
# ---------------------------------------------------------------------------


class TestForwardingTableToVtysh:
    def test_satellite_with_bindings(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        table = ForwardingTable(
            node_id="sat-P00S00",
            topology_state_id="state-1",
            sim_time="2026-03-01T14:30:00Z",
            lsr_bindings=[
                LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0"),
            ],
            ler_ingress_rules=[],
        )
        result = forwarding_table_to_vtysh(table, push_sid_to_loopback, push_iface_to_peer_loopback)
        assert "configure terminal" in result
        assert "mpls lsp" in result
        assert "write memory" in result

    def test_ground_station_with_ingress_rules(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        table = ForwardingTable(
            node_id="gs-alpha",
            topology_state_id="state-1",
            sim_time="2026-03-01T14:30:00Z",
            lsr_bindings=[],
            ler_ingress_rules=[
                IngressRule(dst_prefix="172.16.1.0/24", push_label=16002, out_interface="gnd0"),
            ],
        )
        result = forwarding_table_to_vtysh(table, push_sid_to_loopback, push_iface_to_peer_loopback)
        assert "ip route" in result
        assert "172.16.1.0/24" in result

    def test_empty_table_returns_empty(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        table = ForwardingTable(
            node_id="sat-P00S00",
            topology_state_id="state-1",
            sim_time="2026-03-01T14:30:00Z",
            lsr_bindings=[],
            ler_ingress_rules=[],
        )
        result = forwarding_table_to_vtysh(table, push_sid_to_loopback, push_iface_to_peer_loopback)
        assert result == ""


# ---------------------------------------------------------------------------
# diff_forwarding_tables
# ---------------------------------------------------------------------------


class TestDiffForwardingTables:
    def _make_table(self, bindings=None, rules=None, node_id="sat-P00S00"):
        return ForwardingTable(
            node_id=node_id,
            topology_state_id="state-1",
            sim_time="2026-03-01T14:30:00Z",
            lsr_bindings=bindings or [],
            ler_ingress_rules=rules or [],
        )

    def test_identical_tables_returns_empty(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        binding = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t1 = self._make_table(bindings=[binding])
        t2 = self._make_table(bindings=[binding])
        result = diff_forwarding_tables(t1, t2, push_sid_to_loopback, push_iface_to_peer_loopback)
        assert result == ""

    def test_one_new_binding(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        t1 = self._make_table()
        binding = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t2 = self._make_table(bindings=[binding])
        result = diff_forwarding_tables(t1, t2, push_sid_to_loopback, push_iface_to_peer_loopback)
        assert "mpls lsp 16001" in result
        assert "no mpls lsp" not in result

    def test_one_removed_binding(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        binding = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t1 = self._make_table(bindings=[binding])
        t2 = self._make_table()
        result = diff_forwarding_tables(t1, t2, push_sid_to_loopback, push_iface_to_peer_loopback)
        assert "no mpls lsp 16001" in result

    def test_changed_binding_has_removal_and_addition(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        b1 = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        b2 = LabelBinding(in_label=16001, action="swap", out_label=16003, out_interface="isl1")
        t1 = self._make_table(bindings=[b1])
        t2 = self._make_table(bindings=[b2])
        result = diff_forwarding_tables(t1, t2, push_sid_to_loopback, push_iface_to_peer_loopback)
        assert "no mpls lsp 16001" in result
        assert "mpls lsp 16001" in result

    def test_current_none_returns_full_table(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        binding = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t2 = self._make_table(bindings=[binding])
        result = diff_forwarding_tables(None, t2, push_sid_to_loopback, push_iface_to_peer_loopback)
        full = forwarding_table_to_vtysh(t2, push_sid_to_loopback, push_iface_to_peer_loopback)
        assert result == full

    def test_removals_before_additions(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        b1 = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        b2 = LabelBinding(in_label=16001, action="swap", out_label=16003, out_interface="isl1")
        t1 = self._make_table(bindings=[b1])
        t2 = self._make_table(bindings=[b2])
        result = diff_forwarding_tables(t1, t2, push_sid_to_loopback, push_iface_to_peer_loopback)
        removal_pos = result.index("no mpls lsp")
        addition_pos = result.index(" mpls lsp 16001 ")
        assert removal_pos < addition_pos

    def test_no_python_repr_in_output(
        self, push_sid_to_loopback, push_iface_to_peer_loopback,
    ):
        binding = LabelBinding(in_label=16001, action="swap", out_label=16002, out_interface="isl0")
        t = self._make_table(bindings=[binding])
        result = forwarding_table_to_vtysh(t, push_sid_to_loopback, push_iface_to_peer_loopback)
        # No Python repr artifacts
        assert "LabelBinding(" not in result
        assert "IngressRule(" not in result
        assert "None" not in result
