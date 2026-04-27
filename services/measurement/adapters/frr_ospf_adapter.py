# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""FRR OSPF protocol adapter — vtysh polling + log file parsing.

Collects OSPF adjacency state via `show ip ospf neighbor` polling and
SPF/LSA events from OSPF log file tailing. Both via kubectl exec.
"""

from __future__ import annotations

import re

from measurement.adapters.base_frr_adapter import BaseFrrAdapter

# Regex patterns for OSPF log events
_SPF_START_RE = re.compile(
    r"SPF.*timer.*fire|ospf_spf.*schedule|SPF.*calculation.*started",
    re.IGNORECASE,
)
_SPF_END_RE = re.compile(
    r"SPF.*processing.*completed|ospf_spf.*completed|SPF.*calculation.*complete",
    re.IGNORECASE,
)
_LSA_FLOOD_RE = re.compile(
    r"LSA.*flood|ospf_flood.*lsa|Originating.*LSA",
    re.IGNORECASE,
)


def parse_ospf_neighbors(output: str) -> dict[str, dict[str, str]]:
    """Parse `show ip ospf neighbor` output into {router_id:iface: {state, ...}}.

    Example output:
      Neighbor ID     Pri State           Up Time         Dead Time Address         Interface
      10.0.0.1          1 Full/DROther    0:01:23         0:00:35   10.0.1.1        isl0:10.0.1.2
      10.0.1.1          1 Full/DR         0:01:20         0:00:38   10.0.2.1        isl1:10.0.2.2
    """
    neighbors: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Neighbor") or "---" in line:
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        router_id = parts[0]
        # State is typically parts[2] and may contain "/" (Full/DR, Full/DROther, etc.)
        state = parts[2]
        # Interface is last column, may contain ":"
        interface = parts[-1].split(":")[0] if ":" in parts[-1] else parts[-1]
        key = f"{router_id}:{interface}"
        neighbors[key] = {
            "router_id": router_id,
            "interface": interface,
            "state": state,
        }
    return neighbors


def parse_ospf_log_line(line: str) -> dict[str, str] | None:
    """Parse a single OSPF log line for SPF/LSA events."""
    if _SPF_START_RE.search(line):
        return {"type": "spf_start", "detail": line.strip()}
    if _SPF_END_RE.search(line):
        return {"type": "spf_end", "detail": line.strip()}
    if _LSA_FLOOD_RE.search(line):
        return {"type": "lsa_flood", "detail": line.strip()}
    return None


def _is_full_state(state: str) -> bool:
    """Check if OSPF neighbor state indicates full adjacency."""
    return state.startswith("Full")


class FrrOspfAdapter(BaseFrrAdapter):
    """FRR OSPF adapter — collects adjacency and SPF events."""

    protocol_name = "OSPF"
    neighbor_command = "show ip ospf neighbor"
    log_file_path = "/var/log/frr/ospfd.log"
    log_patterns = [
        (_SPF_START_RE, "spf_start"),
        (_SPF_END_RE, "spf_end"),
        (_LSA_FLOOD_RE, "lsa_flood"),
    ]

    def parse_neighbors(self, output: str) -> dict[str, dict[str, str]]:
        return parse_ospf_neighbors(output)

    def is_adjacency_up(self, state: str) -> bool:
        return _is_full_state(state)
