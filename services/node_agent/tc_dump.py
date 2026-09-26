# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The queueing disciplines of one interface, without decoding every other one.

The kernel answers a qdisc dump with the qdiscs of every device in the
network namespace, whatever ``tcm_ifindex`` the request names (measured on
6.8, with and without strict checking). pyroute2 decodes each of them and
filters afterwards: about 24 microseconds per qdisc, so one interface's
qdiscs cost 12 ms in a namespace of 500 devices, and a host's namespace holds
the host side of every session pod's links. This module reads the same dump
and decodes only the messages whose ``tcm_ifindex`` is the interface's.

The socket is opened in the calling thread's network namespace, which is the
namespace the caller's own IPRoute works in.

This read speaks netlink directly because pyroute2 cannot make it at the
needed cost: its own handling of each message is 5.6 ms per query at 500
devices, whatever it decodes, and link-up runs several such queries per
link. The read runs in the calling thread, starts no process and waits on no
child. Every kernel write still goes through pyroute2.
"""

from __future__ import annotations

import errno
import os
import socket
import struct
from collections.abc import Iterable, Iterator

from pyroute2.netlink.rtnl.tcmsg import tcmsg

_RTM_NEWQDISC = 36
_RTM_GETQDISC = 38
_NLM_F_REQUEST = 0x1
_NLM_F_DUMP = 0x300
_NLM_F_DUMP_INTR = 0x10
_NLMSG_ERROR = 2
_NLMSG_DONE = 3
_NLMSG_HEADER = struct.Struct("=LHHLL")
# struct tcmsg: family (B), three pad bytes, then the interface index (i).
_TCM_IFINDEX = struct.Struct("=Bxxxi")
_RECV_BYTES = 1 << 16


def interface_qdiscs(ifindex: int) -> list[tcmsg]:
    """Every qdisc on interface ``ifindex`` in the calling thread's namespace."""
    sequence = 1
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE)
    try:
        sock.bind((0, 0))
        request = struct.pack("=BxxxiIII", socket.AF_UNSPEC, 0, 0, 0, 0)
        sock.send(
            _NLMSG_HEADER.pack(
                _NLMSG_HEADER.size + len(request),
                _RTM_GETQDISC,
                _NLM_F_REQUEST | _NLM_F_DUMP,
                sequence,
                0,
            )
            + request
        )
        return list(_qdiscs_of(_dump_messages(sock, sequence), ifindex))
    finally:
        sock.close()


def _dump_messages(sock: socket.socket, sequence: int) -> Iterator[tuple[int, bytes]]:
    """(type, message) for every message of the dump, up to NLMSG_DONE."""
    while True:
        for kind, message in _messages(sock.recv(_RECV_BYTES), sequence):
            if kind == _NLMSG_DONE:
                return
            yield kind, message


def _messages(data: bytes, sequence: int) -> Iterator[tuple[int, bytes]]:
    """(type, message) for each netlink message in one datagram; errors raise.

    A message the kernel marked NLM_F_DUMP_INTR means a qdisc changed while the
    dump ran: such a dump proves nothing, and it raises.
    """
    offset = 0
    while offset + _NLMSG_HEADER.size <= len(data):
        length, kind, flags, seq, _pid = _NLMSG_HEADER.unpack_from(data, offset)
        if length < _NLMSG_HEADER.size or offset + length > len(data):
            raise OSError(errno.EBADMSG, f"truncated netlink message at offset {offset}")
        if seq != sequence:
            raise OSError(errno.EBADMSG, f"netlink reply for sequence {seq}, expected {sequence}")
        if flags & _NLM_F_DUMP_INTR:
            raise OSError(errno.EAGAIN, "qdisc dump interrupted by a concurrent change")
        if kind == _NLMSG_ERROR:
            (code,) = struct.unpack_from("=i", data, offset + _NLMSG_HEADER.size)
            if code:
                raise OSError(-code, f"qdisc dump refused: {os.strerror(-code)}")
        yield kind, data[offset : offset + length]
        offset += (length + 3) & ~3


def _qdiscs_of(messages: Iterable[tuple[int, bytes]], ifindex: int) -> Iterator[tcmsg]:
    """Decode the qdisc messages of ``ifindex``; every other message is skipped unread."""
    for kind, message in messages:
        if kind != _RTM_NEWQDISC:
            continue
        _family, index = _TCM_IFINDEX.unpack_from(message, _NLMSG_HEADER.size)
        if index != ifindex:
            continue
        qdisc = tcmsg(message)
        qdisc.decode()
        yield qdisc
