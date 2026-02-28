"""Integration test fixtures — K3s deployment helpers."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope="session")
def k3s_available():
    """Skip integration tests if K3s is not available."""
    result = subprocess.run(
        ["kubectl", "cluster-info"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip("K3s not available")


@pytest.fixture
def nodalarc_namespace(k3s_available):
    """Ensure nodalarc namespace exists."""
    subprocess.run(
        ["kubectl", "create", "namespace", "nodalarc"],
        capture_output=True, check=False,
    )
    yield "nodalarc"


def wait_for_pods_running(namespace: str, timeout: int = 120) -> bool:
    """Wait for all pods in namespace to be Running."""
    for _ in range(timeout):
        result = subprocess.run(
            [
                "kubectl", "get", "pods", "-n", namespace,
                "-l", "nodalarc.io/node-id",
                "-o", "jsonpath={.items[*].status.phase}",
            ],
            capture_output=True, text=True,
        )
        phases = result.stdout.strip().split()
        if phases and all(p == "Running" for p in phases):
            return True
        time.sleep(1)
    return False


def cleanup_deployment(session_id: str, namespace: str = "nodalarc") -> None:
    """Clean up a Helm deployment."""
    subprocess.run(
        ["helm", "uninstall", session_id, "-n", namespace],
        capture_output=True, check=False,
    )
    # Wait for pods to terminate
    subprocess.run(
        [
            "kubectl", "wait", "--for=delete", "pod",
            "-l", "nodalarc.io/node-id", "-n", namespace,
            "--timeout=60s",
        ],
        capture_output=True, check=False,
    )
