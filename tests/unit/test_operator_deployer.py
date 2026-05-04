"""Unit tests for nodalarc_operator/session_deployer.py.

Tests pure-logic functions and K8s-mocked deploy pipeline. All test inputs
are inline - no dependency on production config files except one regression
test per class that explicitly references demo-36-ospf.yaml.

Uses create_autospec for K8s client mocks to catch signature drift.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import kubernetes.client
import pytest
import yaml
from nodalarc.models.session import PlacementConfig
from nodalarc_operator.session_deployer import (
    _deterministic_node,
    compute_expected_pod_count,
    compute_platform_hash,
    compute_pod_placement,
    discover_available_nodes,
)

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_operator_module_state():
    """Clear all cached state between tests."""
    import nodalarc_operator.session_deployer as sd

    sd._v1 = None
    sd._apps_v1 = None
    yield
    sd._v1 = None
    sd._apps_v1 = None


def _make_node_vars(planes=4, sats_per_plane=3, gs_count=2):
    """Build minimal node_vars dict for placement tests.
    Pure dict construction - no file I/O, no K8s, no constellation expansion."""
    nv = {}
    for p in range(planes):
        for s in range(sats_per_plane):
            nid = f"sat-P{p:02d}S{s:02d}"
            nv[nid] = {"node_type": "satellite", "plane": p, "slot": s}
    for g in range(gs_count):
        nv[f"gs-station{g}"] = {"node_type": "ground_station"}
    return nv


def _make_session_yaml(
    constellation_path="configs/constellations/custom-example.yaml",
    gs_path="configs/ground-stations/sets/demo.yaml",
    protocol="ospf",
    strategy="flat",
    step_seconds=1,
    placement_policy=None,
):
    """Build a session YAML string with configurable fields."""
    d = {
        "session": {"name": "test-session"},
        "constellation": constellation_path,
        "ground_stations": gs_path,
        "routing": {
            "protocol": protocol,
            "area_assignment": {"strategy": strategy},
        },
        "time": {"step_seconds": step_seconds},
    }
    if placement_policy:
        d["placement"] = {"policy": placement_policy}
    return yaml.dump(d, default_flow_style=False)


# ---------------------------------------------------------------------------
# Class 1: TestPodPlacement
# ---------------------------------------------------------------------------


class TestPodPlacement:
    """Tests compute_pod_placement() - assigns pods to K8s nodes."""

    def test_all_on_one_single_node(self):
        nv = _make_node_vars(planes=2, sats_per_plane=3, gs_count=2)
        placement = PlacementConfig(policy="allOnOne")
        result = compute_pod_placement(placement, nv, ["node01"])
        assert all(v == "node01" for v in result.values())
        assert len(result) == len(nv)

    def test_all_on_one_ignores_extra_nodes(self):
        nv = _make_node_vars(planes=2, sats_per_plane=3, gs_count=2)
        placement = PlacementConfig(policy="allOnOne")
        result = compute_pod_placement(placement, nv, ["node01", "node02", "node03", "node04"])
        assert all(v == "node01" for v in result.values())

    def test_plane_per_node_same_plane_same_node(self):
        nv = _make_node_vars(planes=4, sats_per_plane=3, gs_count=0)
        placement = PlacementConfig(policy="planePerNode")
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        plane0_nodes = {result[nid] for nid, v in nv.items() if v["plane"] == 0}
        plane1_nodes = {result[nid] for nid, v in nv.items() if v["plane"] == 1}
        assert len(plane0_nodes) == 1
        assert len(plane1_nodes) == 1
        assert plane0_nodes != plane1_nodes

    def test_plane_per_node_wraps_modulo(self):
        nv = _make_node_vars(planes=6, sats_per_plane=2, gs_count=0)
        placement = PlacementConfig(policy="planePerNode")
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        plane0_node = result["sat-P00S00"]
        plane4_node = result["sat-P04S00"]
        assert plane0_node == plane4_node

    def test_plane_per_node_gs_uses_hrw(self):
        nv = _make_node_vars(planes=2, sats_per_plane=2, gs_count=7)
        placement = PlacementConfig(policy="planePerNode")
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        gs_nodes = {result[nid] for nid in nv if nid.startswith("gs-")}
        assert len(gs_nodes) > 1

    def test_plane_group_per_node_groups(self):
        nv = _make_node_vars(planes=4, sats_per_plane=2, gs_count=0)
        placement = PlacementConfig(policy="planeGroupPerNode", planes_per_group=2)
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        assert result["sat-P00S00"] == result["sat-P01S00"]
        assert result["sat-P02S00"] == result["sat-P03S00"]
        assert result["sat-P00S00"] != result["sat-P02S00"]

    def test_plane_group_per_node_default_ppg(self):
        nv = _make_node_vars(planes=8, sats_per_plane=1, gs_count=0)
        placement = PlacementConfig(policy="planeGroupPerNode")
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        assert len(set(result.values())) <= len(nodes)

    def test_no_nodes_raises(self):
        nv = _make_node_vars(planes=1, sats_per_plane=1, gs_count=0)
        placement = PlacementConfig(policy="allOnOne")
        with pytest.raises(ValueError, match="No available"):
            compute_pod_placement(placement, nv, [])

    def test_unknown_policy_raises(self):
        nv = _make_node_vars(planes=1, sats_per_plane=1, gs_count=0)
        placement = PlacementConfig(policy="bogus")
        with pytest.raises(ValueError, match="Unknown placement policy"):
            compute_pod_placement(placement, nv, ["node01"])

    def test_tainted_node_excluded(self):
        """discover_available_nodes filters out tainted nodes."""
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)

        good_node = MagicMock()
        good_node.metadata.name = "node02"
        good_node.spec.taints = []

        tainted_node = MagicMock()
        tainted_node.metadata.name = "node03"
        taint = MagicMock()
        taint.key = "nodalarc.io/not-ready"
        taint.effect = "NoSchedule"
        tainted_node.spec.taints = [taint]

        node_list = MagicMock()
        node_list.items = [good_node, tainted_node]
        mock_v1.list_node.return_value = node_list

        with patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1):
            result = discover_available_nodes()

        assert "node02" in result
        assert "node03" not in result


# ---------------------------------------------------------------------------
# Class 2: TestDeterministicNode
# ---------------------------------------------------------------------------


class TestDeterministicNode:
    """Tests _deterministic_node() - HRW hashing for GS placement."""

    def test_stable_across_calls(self):
        nodes = ["node01", "node02", "node03", "node04"]
        results = [_deterministic_node("gs-hawthorne", nodes) for _ in range(100)]
        assert len(set(results)) == 1

    def test_node_removal_minimal_migration(self):
        nodes_4 = ["node01", "node02", "node03", "node04"]
        nodes_3 = ["node01", "node02", "node04"]
        gs_names = [f"gs-station{i}" for i in range(7)]

        placement_4 = {gs: _deterministic_node(gs, nodes_4) for gs in gs_names}
        placement_3 = {gs: _deterministic_node(gs, nodes_3) for gs in gs_names}

        changes = sum(1 for gs in gs_names if placement_4[gs] != placement_3[gs])
        max_expected = math.ceil(7 / 4)
        assert changes <= max_expected + 1

    def test_node_addition_minimal_migration(self):
        nodes_3 = ["node01", "node02", "node03"]
        nodes_4 = ["node01", "node02", "node03", "node04"]
        gs_names = [f"gs-station{i}" for i in range(7)]

        placement_3 = {gs: _deterministic_node(gs, nodes_3) for gs in gs_names}
        placement_4 = {gs: _deterministic_node(gs, nodes_4) for gs in gs_names}

        changes = sum(1 for gs in gs_names if placement_3[gs] != placement_4[gs])
        max_expected = math.ceil(7 / 4)
        assert changes <= max_expected + 1

    def test_distribution_uniform(self):
        nodes = ["node01", "node02", "node03", "node04"]
        counts = {n: 0 for n in nodes}
        for i in range(1000):
            result = _deterministic_node(f"pod-{i}", nodes)
            counts[result] += 1
        for n, c in counts.items():
            assert 200 <= c <= 300, f"Node {n} has {c} pods, expected 200-300"

    def test_single_node(self):
        assert _deterministic_node("gs-anything", ["only-node"]) == "only-node"


# ---------------------------------------------------------------------------
# Class 3: TestPlatformHash
# ---------------------------------------------------------------------------


class TestPlatformHash:
    """Tests compute_platform_hash() - determines if platform services need restart."""

    def test_same_config_same_hash(self):
        yaml_str = _make_session_yaml()
        spec1 = {"sessionYaml": yaml_str}
        spec2 = {"sessionYaml": yaml_str}
        assert compute_platform_hash(spec1) == compute_platform_hash(spec2)

    def test_different_constellation_different_hash(self):
        spec1 = {
            "sessionYaml": _make_session_yaml(
                constellation_path="configs/constellations/demo-36.yaml"
            )
        }
        spec2 = {
            "sessionYaml": _make_session_yaml(
                constellation_path="configs/constellations/starlink-176.yaml"
            )
        }
        assert compute_platform_hash(spec1) != compute_platform_hash(spec2)

    def test_different_routing_different_hash(self):
        spec1 = {"sessionYaml": _make_session_yaml(protocol="ospf")}
        spec2 = {"sessionYaml": _make_session_yaml(protocol="isis")}
        assert compute_platform_hash(spec1) != compute_platform_hash(spec2)

    def test_different_time_different_hash(self):
        spec1 = {"sessionYaml": _make_session_yaml(step_seconds=1)}
        spec2 = {"sessionYaml": _make_session_yaml(step_seconds=5)}
        assert compute_platform_hash(spec1) != compute_platform_hash(spec2)

    def test_placement_change_same_hash(self):
        spec1 = {"sessionYaml": _make_session_yaml()}
        spec2 = {"sessionYaml": _make_session_yaml(placement_policy="planePerNode")}
        assert compute_platform_hash(spec1) == compute_platform_hash(spec2)

    def test_empty_session_yaml(self):
        h1 = compute_platform_hash({"sessionYaml": ""})
        h2 = compute_platform_hash({})
        assert isinstance(h1, str) and len(h1) == 64
        assert isinstance(h2, str) and len(h2) == 64


# ---------------------------------------------------------------------------
# Class 4: TestExpectedPodCount
# ---------------------------------------------------------------------------


class TestExpectedPodCount:
    """Tests compute_expected_pod_count() - must raise on invalid, never return 0."""

    def test_inline_config_count(self):
        spec = {
            "sessionYaml": _make_session_yaml(
                constellation_path="configs/constellations/custom-example.yaml",
                gs_path="configs/ground-stations/sets/demo.yaml",
            )
        }
        count = compute_expected_pod_count(spec)
        assert count > 0

    def test_demo_36_regression(self):
        session_yaml = (PROJECT_ROOT / "configs/sessions/demo-36-ospf.yaml").read_text()
        spec = {"sessionYaml": session_yaml}
        assert compute_expected_pod_count(spec) == 43

    def test_missing_session_yaml_raises(self):
        with pytest.raises(ValueError, match="sessionYaml"):
            compute_expected_pod_count({})

    def test_bad_constellation_path_raises(self):
        spec = {"sessionYaml": _make_session_yaml(constellation_path="/nonexistent/path.yaml")}
        with pytest.raises(Exception):
            compute_expected_pod_count(spec)
