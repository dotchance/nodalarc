# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""NATS client pool for Node Agent connections.

The Scheduler uses this to route BatchLinkDown/Up to the correct agent(s).

Uses NATS request/reply -- each NodeAgentClient shares the Scheduler's
NATS connection and sends to nodalarc.agent.{hostname}.
"""

from __future__ import annotations

import logging

import nats

from scheduler.node_agent_client import NodeAgentClient

log = logging.getLogger(__name__)


class AgentPool:
    """Manages NATS clients to Node Agent instances.

    Lazily creates clients on first use. All clients share one NATS connection.
    """

    def __init__(self) -> None:
        self._clients: dict[str, NodeAgentClient] = {}
        self._nc: nats.NATS | None = None

    def set_nc(self, nc: nats.NATS) -> None:
        """Set shared NATS connection for all clients."""
        self._nc = nc
        for client in self._clients.values():
            client.set_nc(nc)

    def get_stub(self, agent_addr: str) -> NodeAgentClient:
        """Get or create a NATS client for the given agent address."""
        if agent_addr not in self._clients:
            client = NodeAgentClient(agent_addr)
            if self._nc is not None:
                client.set_nc(self._nc)
            self._clients[agent_addr] = client
        return self._clients[agent_addr]

    def close(self) -> None:
        """Close all clients."""
        for client in self._clients.values():
            client.close()
        self._clients.clear()
