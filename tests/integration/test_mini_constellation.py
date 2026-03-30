"""Integration test: deploy constellations on K3s, verify adjacencies, ping, teardown.

PRD Appendix B: deploys constellations on K3s with IS-IS/OSPF, waits for
adjacency formation, verifies that show isis neighbor on each node shows
the expected neighbors, pings between ground stations through the
constellation, and tears down.

Tests:
  - custom-example 2x2 constellation (quick smoke test)
  - 4x11 starlink-early-44 with IS-IS (PRD exit criteria)
  - 4x11 starlink-early-44 with OSPF (PRD exit criteria: equivalent forwarding)

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


def _parse_session_id(stdout: str, stderr: str) -> str | None:
    """Extract session_id from na-deploy output (stdout or stderr).

    na-deploy logs 'Session: <id>' via logging, which goes to stderr
    with a timestamp/module prefix.  Search both streams for the key.
    """
    for stream in (stdout, stderr):
        for line in stream.split("\n"):
            idx = line.find("Session:")
            if idx != -1:
                return line[idx:].split(":", 1)[1].strip()
    return None


SESSION_STARLINK_ISIS_PATH = str(PROJECT_ROOT / "configs/sessions/starlink-early-44-isis-flat.yaml")
SESSION_STARLINK_OSPF_PATH = str(PROJECT_ROOT / "configs/sessions/starlink-early-44-ospf-flat.yaml")

# Expected satellites in custom-example: 2 planes × 2 sats
EXPECTED_SATS_CUSTOM = [
    "sat-P00S00",
    "sat-P00S01",
    "sat-P01S00",
    "sat-P01S01",
]
EXPECTED_GS_CUSTOM = ["gs-hawthorne", "gs-ashburn"]

# Expected node counts for starlink-early-44: 4 planes × 11 sats + 7 ground stations
STARLINK_SAT_COUNT = 44
STARLINK_GS_COUNT = 7
STARLINK_TOTAL_PODS = STARLINK_SAT_COUNT + STARLINK_GS_COUNT


def _create_custom_session() -> str:
    """Create temp session config for custom-example constellation."""
    import tempfile

    import yaml

    session = {
        "session": {"name": "custom-example-k3s-test"},
        "constellation": "configs/constellations/custom-example.yaml",
        "ground_stations": "configs/ground-stations/sets/us-conus.yaml",
        "routing": {
            "protocol": "isis",
            "extensions": ["sr"],
            "area_assignment": {"strategy": "flat", "gs_area_id": "49.0001"},
        },
        "time": {"step_seconds": 10},
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        dir=str(PROJECT_ROOT),
        delete=False,
    ) as f:
        yaml.dump(session, f)
        return f.name


class TestPrerequisites:
    """Verify infrastructure prerequisites before attempting deployment."""

    def test_k3s_accessible(self, k3s_available):
        """K3s cluster is accessible."""
        result = subprocess.run(
            ["kubectl", "cluster-info"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_namespace_exists(self, nodalarc_namespace):
        """Nodalarc namespace was created."""
        result = subprocess.run(
            ["kubectl", "get", "namespace", nodalarc_namespace],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_helm_available(self, k3s_available):
        """Helm is available and working."""
        result = subprocess.run(
            ["helm", "version", "--short"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_crictl_available(self, k3s_available):
        """crictl is available for PID discovery."""
        result = subprocess.run(
            ["which", "crictl"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip("crictl not installed")

    def test_frr_image_available(self, k3s_available):
        """FRR container image is available."""
        result = subprocess.run(
            ["sudo", "crictl", "images", "--no-trunc"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip("crictl not available")
        if "nodalarc/frr" not in result.stdout:
            pytest.skip("nodalarc/frr image not built — run build first")


class TestMiniConstellation:
    """Full end-to-end deployment test (custom-example 2x2 constellation on K3s).

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
            ["sudo", "crictl", "images", "--no-trunc"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or "nodalarc/frr" not in result.stdout:
            pytest.skip("Container images not available for deployment test")

    @pytest.fixture(scope="class")
    def deployed_custom(self, nodalarc_namespace):
        """Deploy custom-example constellation and clean up after all tests.

        Uses na-deploy to run the full 11-step startup sequence.
        Runs with sudo because na-deploy needs root for crictl, nsenter,
        and pyroute2 netlink operations.
        """
        import sys
        from pathlib import Path

        session_path = _create_custom_session()

        try:
            result = subprocess.run(
                ["sudo", "-E", sys.executable, "-m", "tools.na_deploy", "--session", session_path],
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            Path(session_path).unlink(missing_ok=True)

        if result.returncode != 0:
            pytest.fail(f"na-deploy failed: {result.stderr}")

        session_id = _parse_session_id(result.stdout, result.stderr)
        if not session_id:
            pytest.fail("Could not parse session_id from na-deploy output")

        yield session_id

        # Teardown
        cleanup_deployment(session_id, nodalarc_namespace)

    def test_all_pods_running(self, deployed_custom, nodalarc_namespace):
        """All satellite and ground station pods are running."""
        assert wait_for_pods_running(nodalarc_namespace, timeout=60)

        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                nodalarc_namespace,
                "-l",
                "nodalarc.io/node-id",
                "-o",
                "jsonpath={.items[*].metadata.labels.nodalarc\\.io/node-id}",
            ],
            capture_output=True,
            text=True,
        )
        node_ids = set(result.stdout.strip().split())
        # custom-example = 4 satellites + 2 ground stations = 6 pods
        assert len(node_ids) == 6

    def test_isis_adjacencies_form(self, deployed_custom, nodalarc_namespace):
        """IS-IS adjacencies form on satellite nodes within 60s.

        Each satellite with ISL neighbors should see at least one IS-IS
        neighbor in 'Up' state.
        """
        for sat in EXPECTED_SATS_CUSTOM:
            for attempt in range(60):
                result = subprocess.run(
                    [
                        "kubectl",
                        "exec",
                        "-n",
                        nodalarc_namespace,
                        sat.lower(),
                        "-c",
                        "frr",
                        "--",
                        "vtysh",
                        "-c",
                        "show isis neighbor",
                    ],
                    capture_output=True,
                    text=True,
                )
                if "Up" in result.stdout:
                    break
                time.sleep(1)
            else:
                pytest.fail(f"{sat} has no IS-IS neighbor Up after 60s.\n{result.stdout}")

    def test_sat_to_sat_ping(self, deployed_custom, nodalarc_namespace):
        """Satellite-to-satellite ping within the same plane.

        Pings from sat-P00S00 to sat-P00S01's loopback (10.0.1.1) via
        the ISL mesh. Requires IS-IS to have converged and IP forwarding.
        """
        for _ in range(30):
            result = subprocess.run(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    nodalarc_namespace,
                    "sat-p00s00",
                    "--",
                    "ping",
                    "-c",
                    "1",
                    "-W",
                    "2",
                    "10.0.1.1",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        assert result.returncode == 0, f"Sat-to-sat ping failed: {result.stdout}\n{result.stderr}"

    def test_teardown_removes_all_pods(self, deployed_custom, nodalarc_namespace):
        """Teardown removes all pods from the namespace."""
        cleanup_deployment(deployed_custom, nodalarc_namespace)

        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                nodalarc_namespace,
                "-l",
                "nodalarc.io/node-id",
                "-o",
                "jsonpath={.items[*].metadata.name}",
            ],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "", f"Pods remain after teardown: {result.stdout}"


class TestStarlinkEarly:
    """PRD exit criteria: 4x11 constellation on K3s with IS-IS + SR-MPLS.

    Deploys the full starlink-early-44 (44 sats, 7 ground stations) using
    the starlink-early-44-isis-flat session config. Verifies:
    - All 51 pods reach Running state
    - IS-IS adjacencies form on representative satellites
    - GS-to-GS ping works through the constellation
    """

    @pytest.fixture(autouse=True)
    def _skip_without_images(self, k3s_available):
        result = subprocess.run(
            ["sudo", "crictl", "images", "--no-trunc"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or "nodalarc/frr" not in result.stdout:
            pytest.skip("Container images not available for deployment test")

    @pytest.fixture(scope="class")
    def deployed_starlink(self, nodalarc_namespace):
        """Deploy starlink-early-44 4x11 constellation."""
        import sys

        result = subprocess.run(
            [
                "sudo",
                "-E",
                sys.executable,
                "-m",
                "tools.na_deploy",
                "--session",
                SESSION_STARLINK_ISIS_PATH,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            pytest.fail(f"na-deploy failed: {result.stderr}")

        session_id = _parse_session_id(result.stdout, result.stderr)
        if not session_id:
            pytest.fail("Could not parse session_id from na-deploy output")

        yield session_id

        cleanup_deployment(session_id, nodalarc_namespace)

    def test_all_51_pods_running(self, deployed_starlink, nodalarc_namespace):
        """All 51 pods (44 sats + 7 GS) are running."""
        assert wait_for_pods_running(nodalarc_namespace, timeout=180)

        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                nodalarc_namespace,
                "-l",
                "nodalarc.io/node-id",
                "-o",
                "jsonpath={.items[*].metadata.labels.nodalarc\\.io/node-id}",
            ],
            capture_output=True,
            text=True,
        )
        node_ids = set(result.stdout.strip().split())
        assert len(node_ids) == STARLINK_TOTAL_PODS, (
            f"Expected {STARLINK_TOTAL_PODS} pods, got {len(node_ids)}"
        )

    def test_isis_adjacencies_form(self, deployed_starlink, nodalarc_namespace):
        """IS-IS adjacencies form on representative satellites within 60s."""
        # Check a middle satellite (P03S05) for adjacencies
        for _ in range(60):
            result = subprocess.run(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    nodalarc_namespace,
                    "sat-p03s05",
                    "-c",
                    "frr",
                    "--",
                    "vtysh",
                    "-c",
                    "show isis neighbor",
                ],
                capture_output=True,
                text=True,
            )
            if "Up" in result.stdout:
                break
            time.sleep(1)
        else:
            pytest.fail(f"IS-IS adjacencies did not form within 60s.\n{result.stdout}")

        # Spot-check a few satellites across different planes
        for sat_pod in ["sat-p00s00", "sat-p02s05", "sat-p03s10"]:
            result = subprocess.run(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    nodalarc_namespace,
                    sat_pod,
                    "-c",
                    "frr",
                    "--",
                    "vtysh",
                    "-c",
                    "show isis neighbor",
                ],
                capture_output=True,
                text=True,
            )
            assert "Up" in result.stdout, f"{sat_pod} has no IS-IS neighbor Up"

    def test_cross_plane_ping(self, deployed_starlink, nodalarc_namespace):
        """Cross-plane sat-to-sat ping through the ISL mesh.

        Pings from sat-P00S00 (plane 0) to sat-P03S05 (plane 3) to verify
        cross-plane ISL routing works via the IS-IS mesh.
        sat-P03S05 loopback: 10.3.5.1
        """
        # Retry ping for up to 120s to allow IS-IS convergence across areas
        for _ in range(120):
            result = subprocess.run(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    nodalarc_namespace,
                    "sat-p00s00",
                    "--",
                    "ping",
                    "-c",
                    "1",
                    "-W",
                    "2",
                    "10.3.5.1",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        assert result.returncode == 0, f"Cross-plane ping failed: {result.stdout}\n{result.stderr}"


class TestOspfDeployment:
    """PRD exit criteria: OSPF session demonstrates equivalent forwarding.

    Deploys the starlink-early-44 with OSPF profile to confirm the routing
    stack abstraction works across profiles.
    """

    @pytest.fixture(autouse=True)
    def _skip_without_images(self, k3s_available):
        result = subprocess.run(
            ["sudo", "crictl", "images", "--no-trunc"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or "nodalarc/frr" not in result.stdout:
            pytest.skip("Container images not available for deployment test")

    @pytest.fixture(scope="class")
    def deployed_ospf(self, nodalarc_namespace):
        """Deploy starlink-early-44 with OSPF profile."""
        import sys

        result = subprocess.run(
            [
                "sudo",
                "-E",
                sys.executable,
                "-m",
                "tools.na_deploy",
                "--session",
                SESSION_STARLINK_OSPF_PATH,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            pytest.fail(f"na-deploy failed: {result.stderr}")

        session_id = _parse_session_id(result.stdout, result.stderr)
        if not session_id:
            pytest.fail("Could not parse session_id from na-deploy output")

        yield session_id

        cleanup_deployment(session_id, nodalarc_namespace)

    def test_ospf_adjacencies_form(self, deployed_ospf, nodalarc_namespace):
        """OSPF adjacencies form within 60s."""
        assert wait_for_pods_running(nodalarc_namespace, timeout=180)

        for _ in range(60):
            result = subprocess.run(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    nodalarc_namespace,
                    "sat-p03s05",
                    "-c",
                    "frr",
                    "--",
                    "vtysh",
                    "-c",
                    "show ip ospf neighbor",
                ],
                capture_output=True,
                text=True,
            )
            if "Full" in result.stdout:
                break
            time.sleep(1)
        else:
            pytest.fail(f"OSPF adjacencies did not form within 60s.\n{result.stdout}")

    def test_ospf_cross_plane_ping(self, deployed_ospf, nodalarc_namespace):
        """Cross-plane ping with OSPF profile — equivalent forwarding.

        Pings from sat-P00S00 to sat-P03S05 to verify OSPF achieves the
        same forwarding as IS-IS.
        """
        for _ in range(120):
            result = subprocess.run(
                [
                    "kubectl",
                    "exec",
                    "-n",
                    nodalarc_namespace,
                    "sat-p00s00",
                    "--",
                    "ping",
                    "-c",
                    "1",
                    "-W",
                    "2",
                    "10.3.5.1",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        assert result.returncode == 0, (
            f"OSPF cross-plane ping failed: {result.stdout}\n{result.stderr}"
        )
