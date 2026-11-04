# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Netem delay in the kernel's 64-bit attribute: the request the Node Agent encodes and the dump
it decodes, against a dump the kernel produced."""

from __future__ import annotations

import math

import pytest
from node_agent import netem_plugin
from node_agent.tc_dump import _qdiscs_of
from node_agent.tc_units import delay_ms_to_netem_ns
from pyroute2.netlink.rtnl.tcmsg import tcmsg

# RTM_NEWQDISC from a qdisc dump on kernel 6.8.0-142 (node03): netem 10: under HTB class 1:1 on
# ifindex 2, programmed through this plugin with a delay of 1,200 s and a limit of 4,321 packets.
_KERNEL_DUMP_1200_S = bytes.fromhex(
    "100100002400020001000000005bacdd00000000020000000000100001000100010000000a0001006e6574656d"
    "0000007c000200ffffffffe1100000000000000000000000000000000000000c000a0000e09265170100000c00"
    "0b000000000000000000100001000000000000000000000000000c000300000000000000000000000c00040000"
    "0000000000000014000600000000000000000000000000000000000c000e0001cde9dc93b0787c05000c000000"
    "00003000070014000100000000000000000000000000000000001800030000000000000000000000000000000000"
    "0000002c000300000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000"
)
_RTM_NEWQDISC = 36
# The kernel's TCA_NETEM_LATENCY64 attribute in that dump: length 12, type 10, 1.2e12 ns.
_KERNEL_LATENCY64_ATTRIBUTE = bytes.fromhex("0c000a0000e0926517010000")
_DELAY_FIELD_MAX = 0xFFFFFFFF


def _kernel_netem() -> tcmsg:
    (netem,) = _qdiscs_of([(_RTM_NEWQDISC, _KERNEL_DUMP_1200_S)], 2)
    return netem


def test_a_kernel_dump_above_the_32_bit_range_decodes_to_the_programmed_delay() -> None:
    options = _kernel_netem().get_attr("TCA_OPTIONS")

    assert netem_plugin.latency_ns(options) == 1_200_000_000_000
    assert options["delay"] == _DELAY_FIELD_MAX
    assert options["limit"] == 4321


@pytest.mark.parametrize("delay_ns", [2_000_000, 1_300_000_123, 300_000_000_000, 1_200_000_000_000])
def test_a_request_carries_the_delay_in_the_kernel_attribute(delay_ns: int) -> None:
    netem_plugin.register()
    request = tcmsg()
    request["index"] = 2
    request["handle"] = 0x00100000
    request["parent"] = 0x00010001
    options = netem_plugin.get_parameters({"kind": "netem", "delay_ns": delay_ns, "limit": 4321})
    request["attrs"] = [["TCA_KIND", "netem"], ["TCA_OPTIONS", options]]
    request.encode()

    decoded = tcmsg(request.data)
    decoded.decode()
    assert netem_plugin.latency_ns(decoded.get_attr("TCA_OPTIONS")) == delay_ns
    if delay_ns == 1_200_000_000_000:
        assert _KERNEL_LATENCY64_ATTRIBUTE in request.data


def test_the_32_bit_field_saturates_where_the_kernel_saturates_it() -> None:
    kernel = _kernel_netem().get_attr("TCA_OPTIONS")["delay"]
    requested = netem_plugin.get_parameters({"delay_ns": 1_200_000_000_000, "limit": 1})["delay"]

    assert requested == kernel == _DELAY_FIELD_MAX


@pytest.mark.parametrize(
    "kwarg",
    [
        {"delay": 5, "limit": 1},
        {"delay": 5, "delay_ns": 5_000, "limit": 1},
        {"delay_ns": -1, "limit": 1},
        {"delay_ns": 1.5, "limit": 1},
    ],
)
def test_a_request_with_another_delay_unit_or_a_bad_delay_is_refused(kwarg: dict) -> None:
    with pytest.raises(ValueError):
        netem_plugin.get_parameters(kwarg)


def test_a_dump_without_the_64_bit_attribute_has_no_delay() -> None:
    assert netem_plugin.latency_ns(None) is None
    options = netem_plugin.options()
    options["attrs"] = []
    assert netem_plugin.latency_ns(options) is None


@pytest.mark.parametrize(
    ("delay_ms", "delay_ns"),
    [
        (0.0, 0),
        (2.0, 2_000_000),
        (1273.617123456, 1_273_617_123),
        (1_342_123.456789, 1_342_123_456_789),
    ],
)
def test_milliseconds_convert_to_the_nearest_nanosecond(delay_ms: float, delay_ns: int) -> None:
    assert delay_ms_to_netem_ns(delay_ms) == delay_ns


@pytest.mark.parametrize("bad", [-0.001, math.nan])
def test_an_unusable_delay_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        delay_ms_to_netem_ns(bad)
