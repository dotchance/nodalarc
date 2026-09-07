# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""VXLAN utility functions shared between Scheduler and Node Agent.

Pure computation — no I/O, no pyroute2, no kernel operations.
"""

from __future__ import annotations

import hashlib

# The 24-bit VXLAN identifier space; 0 and 16777215 are reserved.
VNI_MIN = 1
VNI_MAX = 16777214
# Default destination port for VXLAN (IANA standard).
VXLAN_DST_PORT = 4789
# VXLAN overhead: 8 VXLAN + 8 UDP + 20 IP + 14 outer Ethernet = 50 bytes.
VXLAN_OVERHEAD_BYTES = 50


def compute_vni(node_a: str, node_b: str, iface_a: str, iface_b: str) -> int:
    """Deterministic VNI from link identity. Same result on both ends.

    Uses canonical ordering so (A,B) and (B,A) produce the same VNI.
    Range: 1 to 16777214 (24-bit VXLAN VNI space, 0 and 16777215 reserved).
    """
    pair = sorted([(node_a, iface_a), (node_b, iface_b)])
    key = f"{pair[0][0]}:{pair[0][1]}:{pair[1][0]}:{pair[1][1]}"
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return (h % VNI_MAX) + VNI_MIN


def compute_site_vni(site_id: str) -> int:
    """Deterministic VNI for a site LAN segment.

    Site LANs are multipoint segments keyed by site identity, not by link
    pairs. Same 24-bit space and collision posture as link VNIs; the deployer
    validates site VNIs pairwise at manifest build.
    """
    h = int(hashlib.sha256(f"site-lan:{site_id}".encode()).hexdigest()[:8], 16)
    return (h % VNI_MAX) + VNI_MIN
