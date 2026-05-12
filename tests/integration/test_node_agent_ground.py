"""Integration tests for Node Agent ground link wiring.

Ground proof now targets the NATS request/reply production handlers and the
kernel verifier contract. Cross-node ground shaping is covered by unit handler
tests with mocked netlink, while privileged tc/netem proof runs through
``tests/integration/test_node_agent_netem.py`` and ``sudo make test-root``.
"""

import pytest

pytestmark = pytest.mark.integration
