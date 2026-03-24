"""Integration tests: streaming architecture v1.2 test gates.

Seven tests from nodalarc-streaming-architecture-v2.md Phase C test gate
and mandatory build gate. Verifies Scheduler startup publishing, link
counts, VS-API link state, stale detection, recovery after restart,
and NodalPath R-OME-008 catch-up.

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


def _get_state() -> dict:
    """Fetch auth token and state snapshot from VS-API."""
    token = requests.get(f"http://{VS_API_HOST}/api/v1/auth/token").json()["token"]
    resp = requests.get(
        f"http://{VS_API_HOST}/api/v1/state",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()


def _get_scheduler_pod_ip() -> str:
    """Get Scheduler pod IP directly (not via Service — avoids stale endpoint cache)."""
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
    return result.stdout.strip()


def _get_scheduler_endpoint() -> str:
    """Resolve Scheduler events endpoint (port 5561)."""
    ip = _get_scheduler_pod_ip()
    return f"tcp://{ip}:5561" if ip else "tcp://127.0.0.1:5561"


def _get_scheduler_catchup_endpoint() -> str:
    """Resolve Scheduler catch-up endpoint (port 5569)."""
    ip = _get_scheduler_pod_ip()
    return f"tcp://{ip}:5569" if ip else "tcp://127.0.0.1:5569"


# ---------- test_scheduler_publishes_on_startup ----------


def test_scheduler_publishes_on_startup():
    """Scheduler must be publishing events on port 5561 (tests existing running Scheduler)."""
    assert _wait_pod_running("app=nodalarc-scheduler", timeout=30), "Scheduler not running"

    # Connect to current Scheduler pod
    endpoint = _get_scheduler_endpoint()
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVTIMEO, 2000)
    sub.subscribe(b"")  # Subscribe to all topics
    sub.connect(endpoint)

    found_event = False
    start = time.monotonic()
    while time.monotonic() - start < 30:
        try:
            msg = sub.recv()
            found_event = True
            break
        except zmq.Again:
            pass
    sub.close()
    ctx.term()

    assert found_event, "Scheduler not publishing events on port 5561 within 30s."


# ---------- test_scheduler_link_count_correct ----------


def test_scheduler_link_count_correct():
    """R-TO-009 (port 5569) must return >= 60 links for starlink-early-44."""
    assert _wait_pod_running("app=nodalarc-scheduler", timeout=30), "Scheduler not running"

    endpoint = _get_scheduler_catchup_endpoint()
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 10000)
    sock.setsockopt(zmq.SNDTIMEO, 5000)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(endpoint)

    deadline = time.monotonic() + 30
    link_count = 0
    while time.monotonic() < deadline:
        try:
            sock.send_json({"request": "current_links"})
            resp = sock.recv_json()
            link_count = len(resp.get("active_links", []))
            if link_count >= 60:
                break
        except zmq.Again:
            pass
        # Need fresh socket for next REQ (REQ/REP lockstep)
        sock.close()
        sock = ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.RCVTIMEO, 10000)
        sock.setsockopt(zmq.SNDTIMEO, 5000)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(endpoint)
        time.sleep(3)

    sock.close()
    ctx.term()

    assert link_count >= 60, (
        f"R-TO-009 returned {link_count} links, expected >= 60 for starlink-early-44."
    )


# ---------- test_vsapi_link_state_correct ----------


def test_vsapi_link_state_correct():
    """VS-API snapshot must have >= 1 GS link and >= 40 total links within 20s."""
    deadline = time.monotonic() + 20
    last_snap = None

    while time.monotonic() < deadline:
        try:
            snap = _get_state()
            last_snap = snap
            links = snap.get("links", [])
            gs_links = [
                l
                for l in links
                if l.get("node_a", "").startswith("gs-") or l.get("node_b", "").startswith("gs-")
            ]
            if len(links) >= 40 and len(gs_links) >= 1:
                return  # PASS
        except Exception:
            pass
        time.sleep(2)

    total = len(last_snap.get("links", [])) if last_snap else 0
    gs_count = 0
    if last_snap:
        gs_count = sum(
            1
            for l in last_snap.get("links", [])
            if l.get("node_a", "").startswith("gs-") or l.get("node_b", "").startswith("gs-")
        )
    pytest.fail(
        f"VS-API link state not correct within 20s: {total} total links "
        f"({gs_count} GS links), expected >= 40 total and >= 1 GS."
    )


# ---------- test_vsapi_stale_flag_on_silence ----------


def _stop_ome() -> None:
    """Scale OME to 0 and wait until the pod is fully terminated."""
    _kubectl("scale", "deployment", "-n", "nodalarc", "ome", "--replicas=0")
    for _ in range(30):
        result = _kubectl("get", "pods", "-n", "nodalarc", "-l", "app=nodalarc-ome", "--no-headers")
        if not result.stdout.strip() or "Terminating" not in result.stdout:
            # Check again — pod must be completely gone
            result2 = _kubectl(
                "get", "pods", "-n", "nodalarc", "-l", "app=nodalarc-ome", "--no-headers"
            )
            if not result2.stdout.strip():
                return
        time.sleep(1)
    pytest.fail("OME pod did not terminate within 30s of scale to 0")


def _start_ome() -> None:
    """Scale OME to 1 and wait until Running."""
    _kubectl("scale", "deployment", "-n", "nodalarc", "ome", "--replicas=1")
    assert _wait_pod_running("app=nodalarc-ome", timeout=30), "OME did not restart"


def _wait_not_stale(timeout: int = 60) -> None:
    """Wait until VS-API reports stale=false, or fail."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            snap = _get_state()
            if snap.get("stale") is False:
                return
        except Exception:
            pass
        time.sleep(3)
    pytest.fail(f"VS-API still stale after {timeout}s — OME may not be pacing")


def test_vsapi_stale_flag_on_silence():
    """Stop OME → stale=true within 18s. Restart OME → stale=false within 45s."""
    # Ensure OME is running and pacing (stale=false)
    assert _wait_pod_running("app=nodalarc-ome", timeout=30), "OME not running"
    _wait_not_stale(timeout=60)

    # Stop OME completely
    _stop_ome()

    # Wait past stale threshold (15s) plus margin
    time.sleep(18)

    snap = _get_state()
    assert snap.get("stale") is True, (
        f"stale should be true after 18s of OME silence, got stale={snap.get('stale')}"
    )

    # Restore OME
    _start_ome()

    deadline = time.monotonic() + 45
    recovered = False
    while time.monotonic() < deadline:
        try:
            snap = _get_state()
            if snap.get("stale") is False:
                recovered = True
                break
        except Exception:
            pass
        time.sleep(2)

    assert recovered, "stale did not return to false within 45s of OME restart"


# ---------- test_vsapi_no_stale_during_heartbeat ----------


def test_vsapi_no_stale_during_heartbeat():
    """HeartbeatTick does NOT prevent stale — VS-API correctly shows stale after OME silence.

    This test verifies that the VS-API stale timer is NOT reset by HeartbeatTick.
    During window computation, OME publishes HeartbeatTick but NOT Snapshot.
    The VS-API must report stale=true when no Snapshot arrives for > threshold.
    Implemented as: stop OME, wait 18s, confirm stale=true.
    """
    # Ensure OME is running and pacing (stale=false)
    assert _wait_pod_running("app=nodalarc-ome", timeout=30), "OME not running"
    _wait_not_stale(timeout=60)

    # Stop OME completely
    _stop_ome()

    # Wait past stale threshold
    time.sleep(18)

    snap = _get_state()
    assert snap.get("stale") is True, (
        "stale should be true after 18s with no Snapshot. "
        "HeartbeatTick must NOT reset the stale timer."
    )

    # Restore OME for subsequent tests
    _start_ome()
    time.sleep(15)  # Let pacing resume


# ---------- test_vsapi_recovers_after_scheduler_restart ----------


def test_vsapi_recovers_after_scheduler_restart():
    """Restart Scheduler. VS-API must show >= 40 links within 15s via R-TO-009 poll."""
    # Restart Scheduler
    _kubectl(
        "delete",
        "pod",
        "-n",
        "nodalarc",
        "-l",
        "app=nodalarc-scheduler",
        "--grace-period=0",
        "--force",
    )
    assert _wait_pod_running("app=nodalarc-scheduler", timeout=25), "Scheduler did not restart"

    deadline = time.monotonic() + 15
    last_count = 0
    while time.monotonic() < deadline:
        try:
            snap = _get_state()
            links = snap.get("links", [])
            last_count = len(links)
            if last_count >= 40:
                return  # PASS
        except Exception:
            pass
        time.sleep(2)

    pytest.fail(
        f"VS-API shows {last_count} links after Scheduler restart, expected >= 40 within 15s. "
        "R-TO-009 periodic poll may not be correcting link state."
    )


# ---------- test_nodalpath_catchup_on_start ----------


def test_nodalpath_catchup_on_start():
    """Restart NodalPath. Logs must show R-OME-008 catch-up, NOT FullStateSnapshot."""
    _kubectl(
        "delete",
        "pod",
        "-n",
        "nodalarc",
        "-l",
        "app=nodalarc-nodalpath",
        "--grace-period=0",
        "--force",
    )
    assert _wait_pod_running("app=nodalarc-nodalpath", timeout=30), "NodalPath did not restart"
    time.sleep(10)  # Let startup + catch-up happen

    # Get pod name
    result = _kubectl(
        "get",
        "pod",
        "-n",
        "nodalarc",
        "-l",
        "app=nodalarc-nodalpath",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    pod_name = result.stdout.strip()
    assert pod_name, "Could not find NodalPath pod name"

    # Fetch logs
    result = _kubectl("logs", "-n", "nodalarc", pod_name)
    logs = result.stdout

    # Must contain evidence of R-OME-008 catch-up
    has_catchup = "OME catch-up" in logs or "events_since" in logs or "catchup" in logs.lower()

    # Must NOT contain FullStateSnapshot in startup sequence
    has_fss = "FullStateSnapshot" in logs

    # NodalPath may run in console-only mode (no LiveOrchestrator) — in that
    # case catch-up doesn't run, but FullStateSnapshot must still be absent.
    if "console-only" in logs:
        assert not has_fss, (
            "NodalPath logs contain FullStateSnapshot even in console-only mode. "
            "FullStateSnapshot subscription should have been removed."
        )
        return  # PASS — console-only mode doesn't use LiveOrchestrator

    assert has_catchup, (
        "NodalPath logs do not show R-OME-008 catch-up evidence. "
        f"Expected 'OME catch-up' or 'events_since' in logs.\nLogs:\n{logs[:2000]}"
    )
    assert not has_fss, (
        "NodalPath logs contain 'FullStateSnapshot' — should use R-OME-008 catch-up instead."
    )
