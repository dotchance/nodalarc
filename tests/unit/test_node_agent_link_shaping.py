# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Per-terminal directional link shaping commanded through tc."""

from __future__ import annotations

import math

import pytest
from node_agent import namespace_ops
from node_agent.tc_units import (
    delay_ms_to_netem_us,
    mbps_to_bytes_per_second,
    netem_limit_packets,
)

_ROOT = 0x00010000
_CLASS = 0x00010001
_NETEM = 0x00100000


class _Nlmsg(dict):
    def __init__(self, fields: dict, attrs: dict) -> None:
        super().__init__(fields)
        self._attrs = attrs

    def get_attr(self, name: str):
        return self._attrs.get(name)


def _root(kind: str, *, handle: int = _ROOT, default_class: int = 1) -> _Nlmsg:
    options = _Nlmsg({}, {"TCA_HTB_INIT": {"defcls": default_class}}) if kind == "htb" else None
    return _Nlmsg(
        {"handle": handle, "parent": 0xFFFFFFFF}, {"TCA_KIND": kind, "TCA_OPTIONS": options}
    )


class _RecordingIpr:
    """Records tc commands against one interface; reports a configurable root."""

    def __init__(self, root: _Nlmsg | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._qdiscs = [] if root is None else [root]

    def link_lookup(self, *, ifname: str):
        return [7]

    def get_qdiscs(self, index: int):
        assert index == 7
        return self._qdiscs

    def tc(self, command: str, **kwargs) -> None:
        self.calls.append((command, kwargs))


@pytest.mark.parametrize(
    ("rate_mbps", "bytes_per_second"),
    [
        (2.0, 250_000),
        (23.6, 2_950_000),
        (1000.0, 125_000_000),
        (2000.0, 250_000_000),
        (200_000.0, 25_000_000_000),
    ],
)
def test_terminal_rates_convert_to_tc_bytes_per_second(
    rate_mbps: float, bytes_per_second: int
) -> None:
    assert mbps_to_bytes_per_second(rate_mbps) == bytes_per_second


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan])
def test_unusable_rate_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        mbps_to_bytes_per_second(bad)


@pytest.mark.parametrize(
    ("transmit_mbps", "delay_ms", "packets"),
    [
        # In flight at 1500 bytes per packet, rounded up, plus the 1000-packet buffer.
        (600.0, 120.0, 6000 + 1000),
        (2000.0, 10.0, 1667 + 1000),
        (1000.0, 1501.0, 125084 + 1000),
        (2.0, 0.0, 0 + 1000),
    ],
)
def test_netem_limit_holds_the_packets_in_flight_plus_the_buffer(
    transmit_mbps: float, delay_ms: float, packets: int
) -> None:
    assert netem_limit_packets(transmit_mbps, delay_ms) == packets


def test_transmit_shaping_installs_htb_rate_with_netem_beneath_it(monkeypatch) -> None:
    # The device's default qdisc (handle 0) is replaced by adding the root.
    ipr = _RecordingIpr(_root("noqueue", handle=0))
    monkeypatch.setattr(namespace_ops, "_in_namespace", lambda pid, fn: fn(ipr))

    namespace_ops.apply_transmit_shaping(1234, "gnd0", delay_ms=120.5, transmit_mbps=50.0)

    assert [command for command, _ in ipr.calls] == ["add", "replace-class", "replace"]
    root, rate_class, netem = (kwargs for _, kwargs in ipr.calls)
    assert root == {"kind": "htb", "index": 7, "handle": _ROOT, "default": 1}
    assert rate_class["kind"] == "htb"
    assert rate_class["handle"] == _CLASS
    assert rate_class["parent"] == _ROOT
    # 50 Mbit/s is 6,250,000 bytes/s; rate and ceil are the same terminal limit.
    assert rate_class["rate"] == 6_250_000
    assert rate_class["ceil"] == 6_250_000
    # 50 Mbit/s over 120.5 ms holds 503 full-size packets in flight, plus the buffer.
    assert netem == {
        "kind": "netem",
        "index": 7,
        "handle": _NETEM,
        "parent": _CLASS,
        "delay": delay_ms_to_netem_us(120.5),
        "limit": 503 + 1000,
    }


def test_receive_shaping_rate_limits_the_host_veth_egress(monkeypatch) -> None:
    ipr = _RecordingIpr()
    monkeypatch.setattr(namespace_ops, "in_host_namespace", lambda fn: fn(ipr))
    monkeypatch.setattr(
        namespace_ops,
        "_in_namespace",
        lambda pid, fn: pytest.fail("receive shaping must not enter a pod namespace"),
    )

    namespace_ops.apply_receive_shaping("vh0003e9", receive_mbps=600.0)

    assert [command for command, _ in ipr.calls] == ["add", "replace-class"]
    rate_class = ipr.calls[1][1]
    assert rate_class["rate"] == 75_000_000
    assert rate_class["ceil"] == 75_000_000
    assert all(kwargs["kind"] != "netem" for _, kwargs in ipr.calls)


def test_repeat_shaping_keeps_the_shaper_root_and_changes_the_class(monkeypatch) -> None:
    """The kernel cannot change an HTB root in place; a repeat LinkUp must not try."""
    ipr = _RecordingIpr(_root("htb"))
    monkeypatch.setattr(namespace_ops, "_in_namespace", lambda pid, fn: fn(ipr))

    namespace_ops.apply_transmit_shaping(1234, "isl0", delay_ms=4.0, transmit_mbps=2000.0)

    assert [command for command, _ in ipr.calls] == ["replace-class", "replace"]
    assert ipr.calls[0][1]["rate"] == 250_000_000


@pytest.mark.parametrize(
    "root",
    [_root("tbf"), _root("htb", default_class=0x10)],
    ids=["former-tbf-shaper", "htb-root-with-another-default-class"],
)
def test_shaper_replaces_any_other_root(monkeypatch, root) -> None:
    ipr = _RecordingIpr(root)
    monkeypatch.setattr(namespace_ops, "_in_namespace", lambda pid, fn: fn(ipr))

    namespace_ops.apply_transmit_shaping(1234, "isl0", delay_ms=4.0, transmit_mbps=2000.0)

    # The delete names no kind: pyroute2 would build that kind's add parameters.
    assert ipr.calls[0] == ("del", {"index": 7, "parent": 0xFFFFFFFF})
    assert [command for command, _ in ipr.calls[1:]] == ["add", "replace-class", "replace"]


def test_delay_updates_change_only_the_netem_beneath_the_shaper(monkeypatch) -> None:
    ipr = _RecordingIpr(_root("htb"))
    monkeypatch.setattr(namespace_ops, "_in_namespace", lambda pid, fn: fn(ipr))

    namespace_ops.update_delay(1234, "isl0", delay_ms=7.25, transmit_mbps=2000.0)

    # Every change carries the limit; pyroute2 resets an omitted limit to 1000.
    assert ipr.calls == [
        (
            "change",
            {
                "kind": "netem",
                "index": 7,
                "handle": _NETEM,
                "parent": _CLASS,
                "delay": delay_ms_to_netem_us(7.25),
                "limit": netem_limit_packets(2000.0, 7.25),
            },
        )
    ]
