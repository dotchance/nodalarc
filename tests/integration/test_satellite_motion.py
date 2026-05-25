"""Integration test: satellite motion liveness.

Polls the VS-API REST state endpoint and verifies that sim_time
advances, satellite positions change, and plane/slot metadata is
populated. This is the core liveness test: if this fails, satellites
are frozen in the VF regardless of what other tests pass.

Requires a running session with OME pacing active.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

import pytest
import requests

pytestmark = pytest.mark.integration

VS_API_HOST = os.environ.get("VS_API_HOST", "192.168.10.201:8080")
VS_API_BASE = f"http://{VS_API_HOST}"


@pytest.fixture(scope="module")
def vs_api_available():
    """Skip live liveness tests when the VS-API endpoint is not reachable."""
    try:
        response = requests.get(f"{VS_API_BASE}/api/v1/auth/token", timeout=2.0)
        response.raise_for_status()
        assert response.json().get("token")
    except (AssertionError, requests.RequestException, ValueError) as exc:
        pytest.skip(f"VS-API not available at {VS_API_BASE}: {exc}")


def _get_state():
    """Fetch auth token and state snapshot from VS-API."""
    token_response = requests.get(f"{VS_API_BASE}/api/v1/auth/token", timeout=5.0)
    token_response.raise_for_status()
    token = token_response.json()["token"]
    resp = requests.get(
        f"{VS_API_BASE}/api/v1/state",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()


def _wait_for_changed_snapshot(
    changed: Callable[[dict, dict], bool],
    failure_message: str,
    *,
    timeout_s: float = 10.0,
    poll_s: float = 0.5,
) -> tuple[dict, dict]:
    first = _get_state()
    deadline = time.monotonic() + timeout_s
    last = first
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        current = _get_state()
        if changed(first, current):
            return first, current
        last = current
    pytest.fail(f"{failure_message}; first={first.get('sim_time')}, last={last.get('sim_time')}")


def test_sim_time_advances_between_snapshots(vs_api_available):
    """sim_time must change between consecutive state queries."""

    def sim_time_changed(snap1: dict, snap2: dict) -> bool:
        assert snap1.get("sim_time") is not None, "first snapshot has no sim_time"
        assert snap2.get("sim_time") is not None, "latest snapshot has no sim_time"
        return snap1["sim_time"] != snap2["sim_time"]

    _wait_for_changed_snapshot(
        sim_time_changed,
        "sim_time did not advance while polling VS-API; satellites may be frozen",
    )


def test_satellite_positions_change(vs_api_available):
    """At least one satellite must move between state queries."""

    def any_satellite_moved(snap1: dict, snap2: dict) -> bool:
        nodes1 = {n["node_id"]: n for n in snap1["nodes"]}
        nodes2 = {n["node_id"]: n for n in snap2["nodes"]}
        return any(
            nodes1[nid]["lat_deg"] != nodes2[nid]["lat_deg"]
            or nodes1[nid]["lon_deg"] != nodes2[nid]["lon_deg"]
            for nid in nodes1
            if nid in nodes2 and nid.startswith("sat-")
        )

    _wait_for_changed_snapshot(
        any_satellite_moved,
        "no satellite positions changed while polling VS-API; satellites may be frozen",
    )


def test_satellite_plane_slot_populated(vs_api_available):
    """Every satellite must have integer plane and slot; topology view depends on it."""
    snap = _get_state()
    satellites = [node for node in snap["nodes"] if node["node_id"].startswith("sat-")]

    assert satellites, "state snapshot contained no satellite nodes"
    for node in satellites:
        assert isinstance(node.get("plane"), int), (
            f"{node['node_id']} plane={node.get('plane')!r} is not int."
        )
        assert isinstance(node.get("slot"), int), (
            f"{node['node_id']} slot={node.get('slot')!r} is not int."
        )
