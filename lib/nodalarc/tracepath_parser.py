"""Parse tracepath output into structured results.

Pure parsing module — no I/O. Handles the output of `tracepath -n -b`.

Tracepath output format:
 1?: [LOCALHOST]                        pmtu 9000
 1:  10.0.0.1                           8.613ms
 2:  10.0.0.2                          22.147ms asymm  3
 3:  10.0.100.2                        35.512ms reached
     Resume: pmtu 9000 hops 3 back 4
"""

from __future__ import annotations

import re

from nodalarc.models.path import TracepathHop, TracepathResult

_HOP_RE = re.compile(
    r"^\s*(\d+):\s+(\d+\.\d+\.\d+\.\d+)\s+(?:\(\S+\)\s+)?([\d.]+)ms(?:\s+asymm\s+(\d+))?(\s+reached)?"
)
_PMTU_RE = re.compile(r"^\s*\d+\?:\s+\[LOCALHOST\]\s+pmtu\s+(\d+)")
_PMTU_CHANGE_RE = re.compile(r"^\s*(\d+):\s+pmtu\s+(\d+)")
_RESUME_RE = re.compile(r"^\s+Resume:\s+pmtu\s+(\d+)\s+hops\s+(\d+)\s+back\s+(\d+)")


def parse_tracepath(stdout: str) -> TracepathResult:
    """Parse tracepath -n -b output into a TracepathResult."""
    if not stdout or not stdout.strip():
        return TracepathResult(hops=[], raw_output=stdout)

    hops: list[TracepathHop] = []
    seen_hops: set[int] = set()
    pmtu: int | None = None
    forward_hops: int | None = None
    return_hops: int | None = None

    for line in stdout.splitlines():
        # PMTU from LOCALHOST line
        m = _PMTU_RE.match(line)
        if m:
            pmtu = int(m.group(1))
            continue

        # Mid-path PMTU change
        m = _PMTU_CHANGE_RE.match(line)
        if m:
            hop_num = int(m.group(1))
            hop_pmtu = int(m.group(2))
            hops.append(TracepathHop(hop_num=hop_num, pmtu=hop_pmtu))
            continue

        # Hop line with IP and RTT — deduplicate retries at the same
        # hop_num (tracepath sends multiple probes per TTL; keep first
        # successful probe per hop_num)
        m = _HOP_RE.match(line)
        if m:
            hop_num = int(m.group(1))
            if hop_num in seen_hops:
                continue
            seen_hops.add(hop_num)
            ip = m.group(2)
            rtt_ms = float(m.group(3))
            asymm = int(m.group(4)) if m.group(4) else None
            reached = m.group(5) is not None
            hops.append(
                TracepathHop(
                    hop_num=hop_num,
                    ip=ip,
                    rtt_ms=rtt_ms,
                    asymm=asymm,
                    reached=reached,
                )
            )
            continue

        # Resume line
        m = _RESUME_RE.match(line)
        if m:
            pmtu = int(m.group(1))
            forward_hops = int(m.group(2))
            return_hops = int(m.group(3))
            continue

    return TracepathResult(
        hops=hops,
        pmtu=pmtu,
        forward_hops=forward_hops,
        return_hops=return_hops,
        raw_output=stdout,
    )
