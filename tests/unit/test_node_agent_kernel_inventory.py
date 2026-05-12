# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for Node Agent kernel inventory aggregation."""

from __future__ import annotations

from node_agent import kernel_inventory


def test_collect_builds_bounded_inventory(monkeypatch) -> None:
    monkeypatch.setattr(
        kernel_inventory,
        "host_interface",
        lambda ifname: kernel_inventory.InterfaceInventory(
            namespace="host",
            ifname=ifname,
            exists=True,
            admin_up=True,
        ),
    )
    monkeypatch.setattr(
        kernel_inventory,
        "pod_interface",
        lambda pid, ifname: kernel_inventory.InterfaceInventory(
            namespace=f"pid:{pid}",
            ifname=ifname,
            exists=True,
            admin_up=True,
        ),
    )
    monkeypatch.setattr(
        kernel_inventory,
        "pod_qdisc",
        lambda pid, ifname: kernel_inventory.QdiscInventory(
            namespace=f"pid:{pid}",
            ifname=ifname,
            kinds=("tbf", "netem"),
            netem_delay_us=1000,
            tbf_rate_bps=1_000_000,
        ),
    )
    monkeypatch.setattr(
        kernel_inventory,
        "vxlan_interface",
        lambda vni: kernel_inventory.VxlanInventory(
            vni=vni,
            ifname="vx01001",
            exists=True,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            dst_port=4789,
        ),
    )

    inventory = kernel_inventory.collect(
        host_ifnames=("vh01001",),
        pod_ifnames=((1234, "isl0"),),
        qdisc_ifnames=((1234, "isl0"),),
        vnis=(1001,),
    )

    assert [iface.ifname for iface in inventory.interfaces] == ["vh01001", "isl0"]
    assert inventory.qdiscs[0].kinds == ("tbf", "netem")
    assert inventory.vxlans[0].remote_ip == "10.0.0.2"
