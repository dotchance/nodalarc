# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Parse BusyBox ``traceroute -I -n -q 1`` output.

Pure parsing, no I/O. With one probe per hop and numeric addresses, every hop
is one line: the hop number, then either the answering address and its round
trip, or ``*`` when nothing answered. An ICMP unreachable adds a ``!`` code
after the round trip::

    traceroute to 10.0.0.9 (10.0.0.9), 20 hops max, 46 byte packets
     1  10.0.0.1  5.123 ms
     2  *
     3  10.0.0.9  12.400 ms
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADER_PREFIX = "traceroute to "
_HOP_LINE = re.compile(
    r"^\s*(?P<hop>\d+)\s+"
    r"(?:\*|(?P<address>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<rtt>\d+(?:\.\d+)?)\s+ms(?:\s+!\S*)?)"
    r"\s*$"
)


class TracerouteOutputError(ValueError):
    """The output holds a line that is not a traceroute header or hop."""


@dataclass(frozen=True, slots=True)
class TracerouteHop:
    """One hop: the answering address and its round trip, or neither."""

    hop: int
    address: str | None
    rtt_ms: float | None


def parse_traceroute(stdout: str) -> tuple[TracerouteHop, ...]:
    """Every hop line of complete traceroute output, in order."""
    hops: list[TracerouteHop] = []
    for line in stdout.splitlines():
        if not line.strip() or line.startswith(_HEADER_PREFIX):
            continue
        match = _HOP_LINE.match(line)
        if match is None:
            raise TracerouteOutputError(f"not a traceroute hop line: {line.strip()!r}")
        rtt = match.group("rtt")
        hops.append(
            TracerouteHop(
                hop=int(match.group("hop")),
                address=match.group("address"),
                rtt_ms=float(rtt) if rtt is not None else None,
            )
        )
    return tuple(hops)
