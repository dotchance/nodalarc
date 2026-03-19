"""Protocol adapter interface and factory.

Adapters passively collect routing protocol events from FRR containers
via kubectl exec (vtysh polling + log file tailing).
"""

from __future__ import annotations

import logging
from typing import Protocol

from nodalarc.models.metrics import AdapterEvent

log = logging.getLogger(__name__)


class ProtocolAdapter(Protocol):
    """Interface for FRR protocol adapters (PRD 2050-2056)."""

    def start(self, node_id: str, management_ip: str) -> None:
        """Begin collecting events from a specific node."""
        ...

    def stop(self, node_id: str) -> None:
        """Stop collection for a node."""
        ...

    def get_events(self, node_id: str) -> list[AdapterEvent]:
        """Drain buffered events since last call (non-blocking)."""
        ...

    def trace_path(self, node_id: str, dst_ip: str) -> list[str]:
        """Trace forwarding path from node to destination IP."""
        ...


def create_adapter(adapter_name: str) -> ProtocolAdapter:
    """Create adapter by name from stack.yaml mi_adapter field."""
    if adapter_name == "frr_isis_adapter":
        from measurement.adapters.frr_isis_adapter import FrrIsisAdapter

        return FrrIsisAdapter()
    if adapter_name == "frr_ospf_adapter":
        from measurement.adapters.frr_ospf_adapter import FrrOspfAdapter

        return FrrOspfAdapter()
    raise ValueError(f"Unknown MI adapter: {adapter_name}")
