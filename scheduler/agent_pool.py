"""ZMQ client pool for Node Agent connections.

The Scheduler uses this to route BatchLinkDown/Up to the correct agent(s).
For M4 (single K3s node), there is exactly one agent.

Uses ZMQ DEALER sockets — replaces the gRPC stub pool. The proto
message definitions are unchanged, only the transport is ZMQ.
"""

from __future__ import annotations

import logging

from scheduler.node_agent_client import NodeAgentClient

log = logging.getLogger(__name__)


class AgentPool:
    """Manages ZMQ DEALER clients to Node Agent instances.

    Lazily creates clients on first use. Reuses clients for subsequent calls.
    """

    def __init__(self) -> None:
        self._clients: dict[str, NodeAgentClient] = {}

    def get_stub(self, agent_addr: str) -> NodeAgentClient:
        """Get or create a ZMQ client for the given agent address."""
        if agent_addr not in self._clients:
            self._clients[agent_addr] = NodeAgentClient(agent_addr)
        return self._clients[agent_addr]

    def wait_for_agents(self, addrs: list[str], timeout_s: int = 60) -> None:
        """Wait for all agents to be reachable. Called at Scheduler startup."""
        import time

        for addr in addrs:
            client = self.get_stub(addr)
            for attempt in range(timeout_s // 2):
                try:
                    client.get_topology()
                    log.info("Node Agent at %s is ready", addr)
                    break
                except Exception:
                    if attempt % 5 == 0:
                        log.info("Waiting for Node Agent at %s (attempt %d)...", addr, attempt + 1)
                    time.sleep(2)
            else:
                log.warning("Node Agent at %s did not become ready in %ds", addr, timeout_s)

    def close(self) -> None:
        """Close all ZMQ clients."""
        for addr, client in self._clients.items():
            client.close()
            log.debug("Closed client to %s", addr)
        self._clients.clear()
