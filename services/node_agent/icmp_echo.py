# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""ICMP echo from the Node Agent's own process, in the calling thread.

The substrate monitor measures round-trip time to peer hosts and proves that
the host path carries whole packets of the size emulated links need. Both
send ICMP echo requests on a raw socket in the host network namespace (the
Node Agent runs on the host network) and wait for the matching replies.
Fragmentation is forbidden, so a request larger than any hop's MTU never
returns: the host refuses it locally (``EMSGSIZE``) or a router on the path
drops it.
"""

from __future__ import annotations

import ipaddress
import itertools
import os
import select
import socket
import struct
import time
from dataclasses import dataclass

# ICMP echo overhead on the wire: IP header plus the 8-byte ICMP header.
ECHO_OVERHEAD_BYTES = {4: 20 + 8, 6: 40 + 8}
_ECHO_REQUEST = {4: 8, 6: 128}
_ECHO_REPLY = {4: 0, 6: 129}
# Forbid fragmentation: IP_MTU_DISCOVER / IPV6_MTU_DISCOVER set to *_PMTUDISC_DO.
_PMTUDISC_OPTION = {4: (socket.IPPROTO_IP, 10), 6: (socket.IPPROTO_IPV6, 23)}
_PMTUDISC_DO = 2
# Echo identifiers for concurrent probes in this process never repeat within
# 65536 probes, so one probe never counts another probe's replies.
_identifiers = itertools.count((os.getpid() << 4) & 0xFFFF)


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


@dataclass(frozen=True, slots=True)
class EchoResult:
    """The echoes sent to one target and what came back."""

    target_ip: str
    packet_bytes: int
    sent: int
    rtts_ms: tuple[float, ...]
    error: str = ""

    @property
    def received(self) -> int:
        return len(self.rtts_ms)

    def summary(self) -> str:
        text = f"{self.sent} sent, {self.received} received, {self.packet_bytes}-byte packets"
        return f"{text}; {self.error}" if self.error else text


def echo(
    target_ip: str,
    *,
    packet_bytes: int,
    count: int,
    interval_s: float,
    timeout_s: float,
) -> EchoResult:
    """Send ``count`` unfragmentable echoes of ``packet_bytes`` to ``target_ip``.

    ``packet_bytes`` is the whole IP packet. Requests go out ``interval_s``
    apart; each reply is matched by identifier and sequence. Replies are
    awaited until ``timeout_s`` after the last request. A local refusal ends
    the probe with the kernel's error; nothing is raised.
    """
    if count < 1:
        raise ValueError("an echo probe sends at least one request")
    version = ipaddress.ip_address(target_ip).version
    family = socket.AF_INET if version == 4 else socket.AF_INET6
    protocol = socket.IPPROTO_ICMP if version == 4 else socket.IPPROTO_ICMPV6
    payload_bytes = packet_bytes - ECHO_OVERHEAD_BYTES[version]
    if payload_bytes < 8:
        raise ValueError(f"{packet_bytes}-byte packets leave no room for an echo payload")
    identifier = next(_identifiers) & 0xFFFF
    sent_at: dict[int, float] = {}
    rtts: list[float] = []
    error = ""
    with socket.socket(family, socket.SOCK_RAW, protocol) as sock:
        level, option = _PMTUDISC_OPTION[version]
        sock.setsockopt(level, option, _PMTUDISC_DO)
        sock.setblocking(False)
        deadline = None
        next_send = time.monotonic()
        sequence = 0
        while True:
            now = time.monotonic()
            if sequence < count and now >= next_send:
                payload = os.urandom(payload_bytes)
                header = struct.pack("!BBHHH", _ECHO_REQUEST[version], 0, 0, identifier, sequence)
                message = header + payload
                if version == 4:
                    message = (
                        header[:2] + struct.pack("!H", _checksum(message)) + header[4:] + payload
                    )
                # The kernel fills in the ICMPv6 checksum on raw ICMPv6 sockets.
                try:
                    sock.sendto(message, (target_ip, 0))
                except OSError as exc:
                    error = f"send refused: {exc.strerror or exc}"
                    break
                sent_at[sequence] = time.monotonic()
                sequence += 1
                next_send = now + interval_s
                if sequence == count:
                    deadline = time.monotonic() + timeout_s
            if deadline is not None and (now >= deadline or len(rtts) == count):
                break
            wake = deadline if sequence == count else next_send
            ready, _, _ = select.select([sock], [], [], max(0.0, wake - time.monotonic()))
            if not ready:
                continue
            while True:
                try:
                    data, (source, *_rest) = sock.recvfrom(65535)
                except BlockingIOError:
                    break
                received_at = time.monotonic()
                if source != target_ip:
                    continue
                offset = (data[0] & 0x0F) * 4 if version == 4 else 0
                if len(data) < offset + 8:
                    continue
                kind, _code, _sum, reply_id, reply_seq = struct.unpack_from("!BBHHH", data, offset)
                if kind != _ECHO_REPLY[version] or reply_id != identifier:
                    continue
                start = sent_at.pop(reply_seq, None)
                if start is not None:
                    rtts.append((received_at - start) * 1000.0)
    return EchoResult(
        target_ip=target_ip,
        packet_bytes=packet_bytes,
        sent=sequence,
        rtts_ms=tuple(rtts),
        error=error,
    )
