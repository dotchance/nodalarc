"""Integration test: satellite motion liveness.

Polls the VS-API REST state endpoint and verifies that sim_time
advances, satellite positions change, and plane/slot metadata is
populated. This is the core liveness test — if this fails,
satellites are frozen in the VF regardless of what other tests pass.

Requires a running session with OME pacing active.
"""

from __future__ import annotations

import os
import time

import pytest
import requests

pytestmark = pytest.mark.integration

VS_API_HOST = os.environ.get("VS_API_HOST", "192.168.10.201:8080")


def _get_state():
    """Fetch auth token and state snapshot from VS-API."""
    token = requests.get(f"http://{VS_API_HOST}/api/v1/auth/token").json()["token"]
    resp = requests.get(
        f"http://{VS_API_HOST}/api/v1/state",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()


def test_sim_time_advances_between_snapshots():
    """sim_time must change between consecutive state queries."""
    snap1 = _get_state()
    time.sleep(2)
    snap2 = _get_state()

    t1 = snap1.get("sim_time")
    t2 = snap2.get("sim_time")
    assert t1 is not None, "snap1 has no sim_time"
    assert t2 is not None, "snap2 has no sim_time"
    assert t1 != t2, (
        f"sim_time did not advance between snapshots: both are {t1}. Satellites are frozen."
    )


def test_satellite_positions_change():
    """At least one satellite must move between state queries."""
    snap1 = _get_state()
    time.sleep(2)
    snap2 = _get_state()

    nodes1 = {n["node_id"]: n for n in snap1["nodes"]}
    nodes2 = {n["node_id"]: n for n in snap2["nodes"]}

    moved = any(
        nodes1[nid]["lat_deg"] != nodes2[nid]["lat_deg"]
        for nid in nodes1
        if nid in nodes2 and nid.startswith("sat-")
    )
    assert moved, "No satellite positions changed between snapshots. Satellites are frozen."


def test_satellite_plane_slot_populated():
    """Every satellite must have integer plane and slot — topology view depends on it."""
    snap = _get_state()

    for node in snap["nodes"]:
        if node["node_id"].startswith("sat-"):
            assert node.get("plane") is not None, (
                f"{node['node_id']} has plane=null. Topology view will be broken."
            )
            assert isinstance(node["plane"], int), (
                f"{node['node_id']} plane={node['plane']!r} is not int."
            )
            assert node.get("slot") is not None, (
                f"{node['node_id']} has slot=null. Topology view will be broken."
            )
            assert isinstance(node["slot"], int), (
                f"{node['node_id']} slot={node['slot']!r} is not int."
            )
