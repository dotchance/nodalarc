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
    """Interface for FRR protocol adapters."""

    def start(self, node_id: str, management_ip: str) -> None:
        """Begin collecting events from a specific node."""
        ...

    def stop(self, node_id: str) -> None:
        """Stop collection for a node."""
        ...

    def get_events(self, node_id: str) -> list[AdapterEvent]:
        """Drain buffered events since last call (non-blocking)."""
        ...

    def poll(self, node_id: str) -> None:
        """Poll a node's protocol state now, buffering the events it finds."""
        ...

    def trace_path(self, node_id: str, dst_ip: str) -> list[str]:
        """Trace forwarding path from node to destination IP."""
        ...


def create_adapter(engine: str, protocol: str) -> ProtocolAdapter:
    """The measurement adapter that observes ``protocol`` on routing ``engine``.

    ``engine`` is the workload adapter the session's routers run (a profile's
    ``adapter:``); each measurement adapter reads that engine's own output.
    """
    if (engine, protocol) == ("frr", "isis"):
        from measurement.adapters.frr_isis_adapter import FrrIsisAdapter

        return FrrIsisAdapter()
    if (engine, protocol) == ("frr", "ospf"):
        from measurement.adapters.frr_ospf_adapter import FrrOspfAdapter

        return FrrOspfAdapter()
    raise ValueError(f"no measurement adapter observes {protocol!r} on engine {engine!r}")
