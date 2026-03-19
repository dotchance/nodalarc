"""gRPC channel management for Node Agent connections.

The Scheduler uses this to route BatchLinkDown/Up to the correct agent(s).
For M4 (single K3s node), there is exactly one agent.
"""

from __future__ import annotations

import logging

import grpc

from node_agent.proto.node_agent_pb2_grpc import NodeAgentServiceStub

log = logging.getLogger(__name__)


class AgentPool:
    """Manages gRPC channels to Node Agent instances.

    Lazily creates channels on first use. Reuses channels for subsequent calls.
    """

    def __init__(self) -> None:
        self._stubs: dict[str, NodeAgentServiceStub] = {}
        self._channels: dict[str, grpc.Channel] = {}

    def get_stub(self, agent_addr: str) -> NodeAgentServiceStub:
        """Get or create a gRPC stub for the given agent address."""
        if agent_addr not in self._stubs:
            channel = grpc.insecure_channel(agent_addr)
            self._channels[agent_addr] = channel
            self._stubs[agent_addr] = NodeAgentServiceStub(channel)
            log.info("Connected to Node Agent at %s", agent_addr)
        return self._stubs[agent_addr]

    def close(self) -> None:
        """Close all gRPC channels."""
        for addr, channel in self._channels.items():
            channel.close()
            log.debug("Closed channel to %s", addr)
        self._channels.clear()
        self._stubs.clear()
