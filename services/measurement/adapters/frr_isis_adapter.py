# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""FRR IS-IS protocol adapter — vtysh polling + log file parsing.

Collects IS-IS adjacency state via `show isis neighbor` polling and
SPF/LSP events from IS-IS log file tailing. Both via kubectl exec.
"""

from __future__ import annotations

import re

from measurement.adapters.base_frr_adapter import BaseFrrAdapter

# Regex patterns for IS-IS log events
_SPF_START_RE = re.compile(
    r"ISIS-SPF.*Scheduling.*SPF|isis_spf.*schedule|SPF.*algorithm.*started",
    re.IGNORECASE,
)
_SPF_END_RE = re.compile(
    r"ISIS-SPF.*completed|isis_spf.*run.*completed|SPF.*algorithm.*complete",
    re.IGNORECASE,
)
_LSP_FLOOD_RE = re.compile(
    r"LSP.*flood|isis_lsp.*generated|Sending.*LSP",
    re.IGNORECASE,
)


def parse_isis_neighbors(output: str) -> dict[str, dict[str, str]]:
    """Parse `show isis neighbor` output into {system_id: {state, interface}}.

    Example output:
      Area NODAL:
        System Id           Interface   L  State        Holdtime SNPA
        0000.0001.0001      isl0        1  Up           29       P2P
        0000.0001.0001      isl0        2  Up           28       P2P
    """
    neighbors: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Area") or line.startswith("System"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        system_id = parts[0]
        interface = parts[1]
        # Level is parts[2], State is parts[3]
        state = parts[3]
        # Use system_id + interface as key for deduplication across levels
        key = f"{system_id}:{interface}"
        # Keep highest-priority state (Up > Initializing > Down)
        if key not in neighbors or state == "Up":
            neighbors[key] = {
                "system_id": system_id,
                "interface": interface,
                "state": state,
            }
    return neighbors


def parse_isis_log_line(line: str) -> dict[str, str] | None:
    """Parse a single IS-IS log line for SPF/LSP events.

    Returns event dict with type and details, or None if not relevant.
    """
    if _SPF_START_RE.search(line):
        return {"type": "spf_start", "detail": line.strip()}
    if _SPF_END_RE.search(line):
        return {"type": "spf_end", "detail": line.strip()}
    if _LSP_FLOOD_RE.search(line):
        return {"type": "lsp_flood", "detail": line.strip()}
    return None


class FrrIsisAdapter(BaseFrrAdapter):
    """FRR IS-IS adapter — collects adjacency and SPF events."""

    protocol_name = "IS-IS"
    neighbor_command = "show isis neighbor"
    log_file_path = "/var/log/frr/isisd.log"
    log_patterns = [
        (_SPF_START_RE, "spf_start"),
        (_SPF_END_RE, "spf_end"),
        (_LSP_FLOOD_RE, "lsp_flood"),
    ]

    def parse_neighbors(self, output: str) -> dict[str, dict[str, str]]:
        return parse_isis_neighbors(output)

    def is_adjacency_up(self, state: str) -> bool:
        return state == "Up"
