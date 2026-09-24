# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""VXLAN utility functions shared between Scheduler and Node Agent.

Pure computation — no I/O, no pyroute2, no kernel operations.
"""

from __future__ import annotations

import hashlib
import ipaddress

# The 24-bit VXLAN identifier space; 0 and 16777215 are reserved.
VNI_MIN = 1
VNI_MAX = 16777214
# Default destination port for VXLAN (IANA standard).
VXLAN_DST_PORT = 4789
# Bytes VXLAN wraps around an inner IP packet besides the outer IP header:
# the inner Ethernet header (14), UDP (8) and VXLAN (8).
_VXLAN_ENCAPSULATION_BYTES = 14 + 8 + 8
_OUTER_IP_HEADER_BYTES = {4: 20, 6: 40}


def host_path_mtu_for(inner_mtu: int, outer_ip: str) -> int:
    """The host path MTU that carries a VXLAN link's largest inner packet whole.

    Emulated interfaces keep their full MTU wherever their pods run, so the
    host network carries the encapsulation: 50 bytes over IPv4 hosts, 70 over
    IPv6 hosts.
    """
    version = ipaddress.ip_address(outer_ip).version
    return inner_mtu + _VXLAN_ENCAPSULATION_BYTES + _OUTER_IP_HEADER_BYTES[version]


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
