"""Integration test: Node Agent ground link wiring.

This file previously tested ground link BatchLinkDown/BatchLinkUp through
the gRPC-based NodeAgentServicer. The Node Agent was migrated from gRPC
to NATS request/reply dispatch. The test harness needs to be rewritten
against the NATS dispatch interface.

Placeholder for future ground link integration tests.
"""

import pytest

pytestmark = pytest.mark.integration
