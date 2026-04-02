"""VXLAN utility functions shared between Scheduler and Node Agent.

Pure computation — no I/O, no pyroute2, no kernel operations.
"""

from __future__ import annotations

import hashlib


def compute_vni(node_a: str, node_b: str, iface_a: str, iface_b: str) -> int:
    """Deterministic VNI from link identity. Same result on both ends.

    Uses canonical ordering so (A,B) and (B,A) produce the same VNI.
    Range: 1 to 16777214 (24-bit VXLAN VNI space, 0 and 16777215 reserved).
    """
    pair = sorted([(node_a, iface_a), (node_b, iface_b)])
    key = f"{pair[0][0]}:{pair[0][1]}:{pair[1][0]}:{pair[1][1]}"
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return (h % 16777214) + 1
