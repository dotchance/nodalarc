"""Node Agent gRPC servicer — delegates to handler functions.

Each RPC method delegates to the corresponding function in handlers.py,
which calls the appropriate namespace_ops.py and ground_bridge.py functions
(migrated from link_manager.py).
"""

from __future__ import annotations

import logging

from node_agent.handlers import (
    handle_batch_link_down,
    handle_batch_link_up,
    handle_get_topology,
    handle_set_latency,
)
from node_agent.proto.node_agent_pb2_grpc import NodeAgentServiceServicer

log = logging.getLogger(__name__)


class NodeAgentServicer(NodeAgentServiceServicer):
    """gRPC servicer for the Node Agent DaemonSet."""

    def BatchLinkDown(self, request, context):
        return handle_batch_link_down(request, context)

    def BatchLinkUp(self, request, context):
        return handle_batch_link_up(request, context)

    def SetLatency(self, request, context):
        return handle_set_latency(request, context)

    def GetTopology(self, request, context):
        return handle_get_topology(request, context)
