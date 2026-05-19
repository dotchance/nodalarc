# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed kernel mutation facade for Node Agent operation plans."""

from __future__ import annotations

from node_agent import ground_bridge, namespace_ops, vxlan


def set_pod_interface_up(pid: int, ifname: str) -> None:
    namespace_ops.set_interface_up(pid, ifname)


def set_pod_interface_down(pid: int, ifname: str) -> None:
    namespace_ops.set_interface_down(pid, ifname)


def apply_terminal_shaping(pid: int, ifname: str, delay_ms: float, rate_mbps: float) -> None:
    namespace_ops.apply_link_shaping(pid, ifname, delay_ms, rate_mbps)


def update_terminal_delay(pid: int, ifname: str, delay_ms: float) -> None:
    namespace_ops.update_delay(pid, ifname, delay_ms)


def create_cross_node_vxlan(
    *,
    pid: int,
    ifname: str,
    local_ip: str,
    remote_ip: str,
    vni: int,
) -> None:
    vxlan.create_vxlan_link(
        pid=pid,
        ifname=ifname,
        local_ip=local_ip,
        remote_ip=remote_ip,
        vni=vni,
    )


def destroy_cross_node_vxlan(*, pid: int, ifname: str, vni: int) -> None:
    vxlan.destroy_vxlan_link(pid, ifname, vni)


def attach_cross_node_ground(
    *,
    local_host_ifname: str,
    local_ip: str,
    remote_ip: str,
    vni: int,
    sat_pid: int | None,
    sat_ifname: str,
) -> None:
    vxlan.attach_cross_node_ground(
        local_host_ifname=local_host_ifname,
        local_ip=local_ip,
        remote_ip=remote_ip,
        vni=vni,
        sat_pid=sat_pid,
        sat_ifname=sat_ifname,
    )


def detach_cross_node_ground(
    *,
    local_host_ifname: str,
    vni: int,
    sat_pid: int | None,
    sat_ifname: str,
) -> None:
    vxlan.detach_cross_node_ground(local_host_ifname, vni, sat_pid, sat_ifname)


def detach_local_isl(node_a: str, iface_a: str, node_b: str, iface_b: str) -> None:
    ground_bridge.detach_isl_interface(node_a, iface_a, node_b, iface_b)
