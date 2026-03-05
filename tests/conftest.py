"""Shared test fixtures for Nodal Arc.

Expanded incrementally as Steps 2-8 add new test needs.
"""

from pathlib import Path

import pytest
import zmq

# Path constants — tests load valid configs from configs/, not duplicated fixtures
PROJECT_ROOT = Path(__file__).parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def zmq_context():
    """Provide a ZeroMQ context, cleaned up after use."""
    ctx = zmq.Context()
    yield ctx
    ctx.destroy(linger=0)
