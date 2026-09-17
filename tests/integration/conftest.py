"""Integration test fixtures — K3s deployment helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope="session")
def k3s_available():
    """Skip integration tests if K3s is not available."""
    result = subprocess.run(
        ["kubectl", "cluster-info"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("K3s not available")
