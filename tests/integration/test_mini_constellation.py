"""Integration test: deploy 2x3 constellation, verify adjacencies, ping, teardown.

PRD Appendix B: deploys a 2x3 constellation on K3s with IS-IS, waits for
adjacency formation, verifies that show isis neighbor on each node shows
the expected neighbors, pings between ground stations through the
constellation, and tears down.

Requires K3s running and container images built.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from tests.integration.conftest import (
    PROJECT_ROOT,
    cleanup_deployment,
    wait_for_pods_running,
)

pytestmark = pytest.mark.integration


SESSION_PATH = str(PROJECT_ROOT / "configs/sessions/2x3-test.yaml")
SESSION_4NODE_PATH = str(PROJECT_ROOT / "configs/sessions/4-node-test.yaml")

# Expected satellites in 2x3: sat-P00S00, P00S01, P00S02, P01S00, P01S01, P01S02
EXPECTED_SATS_2X3 = [
    "sat-P00S00", "sat-P00S01", "sat-P00S02",
    "sat-P01S00", "sat-P01S01", "sat-P01S02",
]
EXPECTED_GS = ["gs-station-a", "gs-station-b"]


class TestPrerequisites:
    """Verify infrastructure prerequisites before attempting deployment."""

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
        if result.returncode != 0:
            pytest.skip("crictl not installed")

    def test_frr_image_available(self, k3s_available):
        """FRR container image is available."""
        result = subprocess.run(
            ["crictl", "images", "--no-trunc"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            pytest.skip("crictl not available")
        if "nodalarc/frr" not in result.stdout:
            pytest.skip("nodalarc/frr image not built — run build first")


class TestMiniConstellation:
    """Full end-to-end deployment test (2x3 constellation on K3s).

    This test class requires:
    - K3s running with KUBECONFIG set
    - Container images pre-built (nodalarc/frr:10)
    - Helm charts in deploy/helm/

    Tests are ordered: deploy → verify adjacencies → ping → teardown.
    """

    @pytest.fixture(autouse=True)
    def _skip_without_images(self, k3s_available):
        """Skip entire class if container images aren't available."""
        result = subprocess.run(
            ["crictl", "images", "--no-trunc"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or "nodalarc/frr" not in result.stdout:
            pytest.skip("Container images not available for deployment test")

    @pytest.fixture
    def deployed_2x3(self, nodalarc_namespace):
        """Deploy 2x3 constellation and clean up after test.

        Uses na-deploy to run the full 11-step startup sequence.
        """
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "tools.na_deploy", "--session", SESSION_PATH],
            capture_output=True, text=True,
            timeout=300,
        )
        if result.returncode != 0:
            pytest.fail(f"na-deploy failed: {result.stderr}")

        # Parse session_id from output
        session_id = None
        for line in result.stdout.split("\n"):
            if line.startswith("Session:"):
                session_id = line.split(":", 1)[1].strip()
                break

        if not session_id:
            pytest.fail("Could not parse session_id from na-deploy output")

        yield session_id

        # Teardown
        cleanup_deployment(session_id, nodalarc_namespace)

    def test_all_pods_running(self, deployed_2x3, nodalarc_namespace):
        """All satellite and ground station pods are running."""
        assert wait_for_pods_running(nodalarc_namespace, timeout=60)

        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", nodalarc_namespace,
             "-l", "nodalarc.io/node-id",
             "-o", "jsonpath={.items[*].metadata.labels.nodalarc\\.io/node-id}"],
            capture_output=True, text=True,
        )
        node_ids = set(result.stdout.strip().split())
        # 2x3 = 6 satellites + 2 ground stations = 8 pods
        assert len(node_ids) == 8

    def test_isis_adjacencies_form(self, deployed_2x3, nodalarc_namespace):
        """IS-IS adjacencies form on satellite nodes within 30s.

        Each satellite with ISL neighbors should see at least one IS-IS
        neighbor in 'Up' state.
        """
        # Wait for adjacencies
        for _ in range(30):
            result = subprocess.run(
                ["kubectl", "exec", "-n", nodalarc_namespace,
                 "sat-p00s01", "-c", "frr", "--",
                 "vtysh", "-c", "show isis neighbor"],
                capture_output=True, text=True,
            )
            if "Up" in result.stdout:
                break
            time.sleep(1)
        else:
            pytest.fail(f"IS-IS adjacencies did not form within 30s.\n{result.stdout}")

        # Verify each satellite sees at least one neighbor
        for sat in EXPECTED_SATS_2X3:
            result = subprocess.run(
                ["kubectl", "exec", "-n", nodalarc_namespace,
                 sat.lower(), "-c", "frr", "--",
                 "vtysh", "-c", "show isis neighbor"],
                capture_output=True, text=True,
            )
            assert "Up" in result.stdout, f"{sat} has no IS-IS neighbor Up"

    def test_gs_to_gs_ping(self, deployed_2x3, nodalarc_namespace):
        """Ground station to ground station ping through the constellation.

        This requires GS links to be up (satellite visible from both GS),
        IS-IS to have converged, and SR-MPLS forwarding to work.
        """
        # Get gs-station-b's loopback IP for the ping target
        result = subprocess.run(
            ["kubectl", "exec", "-n", nodalarc_namespace,
             "gs-station-b", "-c", "frr", "--",
             "vtysh", "-c", "show ip route"],
            capture_output=True, text=True,
        )
        # Look for station-b's loopback in the routing table

        # Ping from gs-station-a to gs-station-b's loopback
        # gs_index=1 → loopback 10.255.1.1
        result = subprocess.run(
            ["kubectl", "exec", "-n", nodalarc_namespace,
             "gs-station-a", "--",
             "ping", "-c", "3", "-W", "5", "10.255.1.1"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"GS-to-GS ping failed: {result.stdout}\n{result.stderr}"

    def test_teardown_removes_all_pods(self, deployed_2x3, nodalarc_namespace):
        """Teardown removes all pods from the namespace."""
        cleanup_deployment(deployed_2x3, nodalarc_namespace)

        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", nodalarc_namespace,
             "-l", "nodalarc.io/node-id",
             "-o", "jsonpath={.items[*].metadata.name}"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", f"Pods remain after teardown: {result.stdout}"
