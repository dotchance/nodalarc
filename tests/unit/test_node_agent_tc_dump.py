# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""One interface's qdiscs out of a whole-namespace qdisc dump."""

from __future__ import annotations

import errno
import struct

import pytest
from node_agent import tc_dump
from pyroute2.netlink.rtnl.tcmsg import tcmsg

SEQUENCE = 1


def _qdisc(index: int, handle: int, parent: int, kind: str, *, sequence: int = SEQUENCE) -> bytes:
    msg = tcmsg()
    msg["family"] = 0
    msg["index"] = index
    msg["handle"] = handle
    msg["parent"] = parent
    msg["attrs"] = [("TCA_KIND", kind)]
    msg["header"]["type"] = 36  # RTM_NEWQDISC
    msg["header"]["flags"] = 2  # NLM_F_MULTI
    msg["header"]["sequence_number"] = sequence
    msg.encode()
    return bytes(msg.data)


def _done() -> bytes:
    return struct.pack("=LHHLLi", 20, 3, 2, SEQUENCE, 0, 0)


def _error(code: int) -> bytes:
    return struct.pack("=LHHLLi", 36, 2, 0, SEQUENCE, 0, -code) + bytes(16)


class _Socket:
    def __init__(self, datagrams: list[bytes]) -> None:
        self._datagrams = list(datagrams)

    def recv(self, _size: int) -> bytes:
        return self._datagrams.pop(0)


def _query(datagrams: list[bytes], ifindex: int) -> list[tcmsg]:
    messages = tc_dump._dump_messages(_Socket(datagrams), SEQUENCE)
    return list(tc_dump._qdiscs_of(messages, ifindex))


def test_only_the_named_interface_is_decoded_across_datagrams() -> None:
    first = _qdisc(3, 0x10000, 0xFFFFFFFF, "htb") + _qdisc(7, 0x10000, 0xFFFFFFFF, "htb")
    second = _qdisc(7, 0x100000, 0x10001, "netem") + _qdisc(9, 0, 0xFFFFFFFF, "noqueue") + _done()
    rows = _query([first, second], 7)
    assert [(q["index"], q["handle"], q.get_attr("TCA_KIND")) for q in rows] == [
        (7, 0x10000, "htb"),
        (7, 0x100000, "netem"),
    ]


def test_an_interface_without_qdiscs_yields_none() -> None:
    assert _query([_qdisc(3, 0, 0xFFFFFFFF, "noqueue") + _done()], 7) == []


def test_a_kernel_error_raises() -> None:
    with pytest.raises(OSError) as exc:
        _query([_error(errno.EPERM)], 7)
    assert exc.value.errno == errno.EPERM


def test_a_truncated_message_raises() -> None:
    whole = _qdisc(7, 0x10000, 0xFFFFFFFF, "htb")
    with pytest.raises(OSError, match="truncated"):
        _query([whole[:-4]], 7)


def test_a_reply_to_another_request_raises() -> None:
    with pytest.raises(OSError, match="sequence"):
        _query([_qdisc(7, 0x10000, 0xFFFFFFFF, "htb", sequence=9)], 7)
