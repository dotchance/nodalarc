# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime naming contract tests."""

import pytest
from nodalarc.runtime_naming import (
    gs_bridge_port_name,
    is_managed_host_ifname,
    isl_host_name,
    satellite_ground_host_name,
    validate_runtime_node_id,
    vxlan_host_ifnames,
)
from nodalarc.vxlan import VNI_MAX, VNI_MIN


def test_host_interface_names_are_bounded_and_terminal_distinct():
    node_id = "space-sat-p00s00"
    names = {
        isl_host_name(node_id, 0),
        isl_host_name(node_id, 1),
        satellite_ground_host_name(node_id, 0),
        satellite_ground_host_name(node_id, 1),
        gs_bridge_port_name("ground-gs-denver", 0),
        gs_bridge_port_name("ground-gs-denver", 1),
    }
    assert len(names) == 6
    assert all(len(name) <= 15 for name in names)
    assert all(is_managed_host_ifname(name) for name in names)


def test_cleanup_matcher_keeps_retired_names_for_reused_nodes():
    for name in (
        "_isl_sat-a_sat-b",
        "_gnd_sat-gs",
        "_gbr-gs",
        "br-gnd-denver",
        "_na_tmp",
        "_g0abc",
    ):
        assert is_managed_host_ifname(name)
    for name in ("eth0", "cni0", "lo", "term0", "gnd0", "isl0"):
        assert not is_managed_host_ifname(name)


def test_runtime_node_id_rejects_kubernetes_unsafe_values():
    with pytest.raises(ValueError, match="lowercase DNS-label safe"):
        validate_runtime_node_id("space-SAT-p00s00")
    with pytest.raises(ValueError, match="Kubernetes label value limit"):
        validate_runtime_node_id("n" * 64)


def test_vxlan_host_names_are_distinct_over_the_whole_vni_space():
    low = vxlan_host_ifnames(1)
    folded = vxlan_host_ifnames(100000)  # shared names with VNI 1 under the retired rule
    top = vxlan_host_ifnames(VNI_MAX)

    assert low == ("vx000001", "vh000001", "vp000001")
    assert len({*low, *folded, *top}) == 9
    assert all(len(name) <= 15 for name in (*low, *folded, *top))
    assert all(is_managed_host_ifname(name) for name in (*low, *folded, *top))
    assert low.tunnel == "vx000001" and low.host_veth == "vh000001" and low.pod_veth == "vp000001"


@pytest.mark.parametrize("vni", [0, VNI_MIN - 1, VNI_MAX + 1, True, "7", 1.0])
def test_vxlan_host_names_refuse_values_outside_the_vni_space(vni):
    with pytest.raises((ValueError, TypeError)):
        vxlan_host_ifnames(vni)


def test_cleanup_matcher_recognizes_retired_five_digit_vxlan_names_too():
    for name in ("vx00001", "vh00001", "vp00001", "vx99999"):
        assert is_managed_host_ifname(name)
    for name in ("vx12345g", "vq000001", "vx0000001", "vx1234", "vxlan0"):
        assert not is_managed_host_ifname(name)
