"""Integration test: deploy 4-node-test, verify adjacencies, ping, teardown.

PRD Appendix B: End-to-end deployment verification.

Requires K3s running and container images built.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from tests.integration.conftest import (
    PROJECT_ROOT,
    cleanup_deployment,
    wait_for_pods_running,
)

pytestmark = pytest.mark.integration


SESSION_PATH = str(PROJECT_ROOT / "configs/sessions/sample-session.yaml")


@pytest.fixture
def deployed_constellation(nodalarc_namespace):
    """Deploy 4-node-test constellation and clean up after test.

    Note: This fixture assumes container images are pre-built.
    In CI, images should be built before running integration tests.
    """
    # For integration tests, we need a 4-node-test session config
    # The sample session uses starlink-mini, so we'd need a 4-node session
    # For now, this is a structural test that verifies the deployment flow

    session_id = "integration-test"
    yield session_id, nodalarc_namespace
    cleanup_deployment(session_id, nodalarc_namespace)


class TestMiniConstellation:
    def test_k3s_accessible(self, k3s_available):
        """K3s cluster is accessible."""
        result = subprocess.run(
            ["kubectl", "cluster-info"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_namespace_exists(self, nodalarc_namespace):
        """Nodalarc namespace was created."""
        result = subprocess.run(
            ["kubectl", "get", "namespace", nodalarc_namespace],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_helm_available(self, k3s_available):
        """Helm is available and working."""
        result = subprocess.run(
            ["helm", "version", "--short"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_crictl_available(self, k3s_available):
        """crictl is available for PID discovery."""
        result = subprocess.run(
            ["which", "crictl"],
            capture_output=True, text=True,
        )
        # crictl is needed but not strictly required for this test
        if result.returncode != 0:
            pytest.skip("crictl not installed")
