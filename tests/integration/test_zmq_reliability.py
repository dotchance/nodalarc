"""Integration tests: ZMQ subscriber reliability after OME restart.

Verifies that the Scheduler and VS-API recover automatically when
the OME pod restarts mid-session — no manual intervention required.

Requires a running session with all pods healthy.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest
import requests
import zmq

pytestmark = pytest.mark.integration

VS_API_HOST = os.environ.get("VS_API_HOST", "192.168.10.202:8080")
KUBECTL = os.environ.get("KUBECTL", "sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl")


def _kubectl(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            KUBECTL.split() + list(args),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"kubectl {' '.join(args)} timed out after 30s")


def _wait_pod_running(label: str, timeout: int = 25) -> bool:
    """Wait for a pod matching the label to be Running."""
    for _ in range(timeout):
        result = _kubectl("get", "pods", "-n", "nodalarc", "-l", label, "--no-headers")
        if "Running" in result.stdout:
            return True
        time.sleep(1)
    return False


def _get_scheduler_endpoint() -> str:
    """Resolve Scheduler events endpoint from K8s."""
    result = _kubectl(
        "get",
        "endpoints",
        "-n",
        "nodalarc",
        "nodalarc-scheduler-events",
        "-o",
        "jsonpath={.subsets[0].addresses[0].ip}",
    )
    ip = result.stdout.strip()
    if not ip:
        # Fallback to pod IP directly
        result = _kubectl(
            "get",
            "pod",
            "-n",
            "nodalarc",
            "-l",
            "app=nodalarc-scheduler",
            "-o",
            "jsonpath={.items[0].status.podIP}",
        )
        ip = result.stdout.strip()
    return f"tcp://{ip}:5561" if ip else "tcp://127.0.0.1:5561"


def _subscribe_port_5561(timeout_s: int = 20) -> dict[str, int]:
    """Subscribe to Scheduler events on port 5561, return topic counts."""
    import collections

    endpoint = _get_scheduler_endpoint()
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVTIMEO, 2000)
    sub.subscribe(b"")
    sub.connect(endpoint)

    counts: dict[str, int] = collections.Counter()
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        try:
            msg = sub.recv()
            topic = msg.split(b"\x00")[0].decode()
            counts[topic] += 1
            if counts:
                break  # Got at least one event
        except zmq.Again:
            pass
    sub.close()
    ctx.term()
    return dict(counts)


def _get_sim_time() -> str | None:
    """Fetch sim_time from VS-API REST."""
    try:
        token = requests.get(f"http://{VS_API_HOST}/api/v1/auth/token").json()["token"]
        resp = requests.get(
            f"http://{VS_API_HOST}/api/v1/state",
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.json().get("sim_time")
    except Exception:
        return None


def test_scheduler_recovers_after_ome_restart():
    """Scheduler must resume publishing on port 5561 after OME restart."""
    # 1. Confirm Scheduler is publishing
    events_before = _subscribe_port_5561(timeout_s=20)
    assert events_before, "Scheduler is not publishing on port 5561 before test"

    # 2. Restart OME pod
    _kubectl(
        "delete", "pod", "-n", "nodalarc", "-l", "app=nodalarc-ome", "--wait=true", "--timeout=25s"
    )

    # 3. Wait for OME pod Running
    assert _wait_pod_running("app=nodalarc-ome", timeout=25), "OME pod did not restart"

    # 4. Wait for OME window computation + pacing to start
    time.sleep(15)

    # 5. Scheduler must resume publishing within 20s of OME ready
    events_after = _subscribe_port_5561(timeout_s=20)
    assert events_after, (
        "Scheduler did not resume publishing on port 5561 within 20s of OME restart. "
        f"Events: {events_after}"
    )


def test_vsapi_recovers_after_ome_restart():
    """VS-API must resume delivering advancing sim_time after OME restart."""
    # 1. Confirm sim_time advances before restart
    t1 = _get_sim_time()
    time.sleep(2)
    t2 = _get_sim_time()
    assert t1 and t2 and t1 != t2, f"sim_time not advancing before test: {t1} == {t2}"

    # 2. Restart OME pod
    _kubectl(
        "delete", "pod", "-n", "nodalarc", "-l", "app=nodalarc-ome", "--wait=true", "--timeout=25s"
    )

    # 3. Wait for OME pod Running
    assert _wait_pod_running("app=nodalarc-ome", timeout=25), "OME pod did not restart"

    # 4. Wait for OME window computation
    time.sleep(15)

    # 5. sim_time must resume advancing within 20s
    deadline = time.monotonic() + 20
    recovered = False
    while time.monotonic() < deadline:
        ta = _get_sim_time()
        time.sleep(2)
        tb = _get_sim_time()
        if ta and tb and ta != tb:
            recovered = True
            break
    assert recovered, "VS-API sim_time did not resume advancing within 20s of OME restart"


def test_scheduler_uses_catchup_on_start():
    """Scheduler must publish events within 20s of starting via R-OME-008 catch-up."""
    # 1. Restart Scheduler pod
    _kubectl(
        "delete",
        "pod",
        "-n",
        "nodalarc",
        "-l",
        "app=nodalarc-scheduler",
        "--wait=true",
        "--timeout=25s",
    )

    # 2. Wait for Scheduler pod Running
    assert _wait_pod_running("app=nodalarc-scheduler", timeout=25), "Scheduler pod did not restart"

    # 3. Events must appear within 20s (catch-up, not waiting for FullStateSnapshot)
    events = _subscribe_port_5561(timeout_s=20)
    assert events, (
        "Scheduler did not publish events within 30s of startup. "
        "R-OME-008 catch-up may not be working."
    )
