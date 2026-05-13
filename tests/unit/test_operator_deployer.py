"""Unit tests for nodalarc_operator/session_deployer.py.

Tests pure-logic functions and K8s-mocked deploy pipeline. All test inputs
are inline - no dependency on production config files except one regression
test per class that explicitly references demo-36-ospf.yaml.

Uses create_autospec for K8s client mocks to catch signature drift.
"""

from __future__ import annotations

import base64
import gzip
import json
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
    ensure_session_configmaps,
    ensure_session_pods,
    write_wiring_manifest,
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
        "orbit": {"propagator": "keplerian-circular"},
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


# ---------------------------------------------------------------------------
# Inline config fixtures for Phase 2 (fully self-contained, no external files)
# ---------------------------------------------------------------------------

# Constellation with inline default_terminals - no satellite_type file reference.
_INLINE_CONSTELLATION = {
    "mode": "parametric",
    "name": "test-4sat",
    "default_terminals": {
        "isl": [
            {
                "type": "optical",
                "count": 4,
                "max_range_km": 5000,
                "bandwidth_mbps": 100,
                "max_tracking_rate_deg_s": 3.0,
                "field_of_regard_deg": 140,
            }
        ],
        "ground": [{"type": "rf", "count": 1, "bandwidth_mbps": 1000}],
    },
    "orbit": {
        "altitude_km": 550,
        "inclination_deg": 53,
        "pattern": "walker-delta",
    },
    "planes": {
        "count": 2,
        "raan_spacing_deg": 180,
        "sats_per_plane": 2,
        "phase_offset_deg": 90,
    },
}

# Ground stations with inline station definitions.
_INLINE_GROUND_STATIONS = {
    "default_terminals": [
        {"type": "rf", "count": 1, "bandwidth_mbps": 1000, "tracking_capacity": 1}
    ],
    "stations": [
        {"name": "alpha", "lat_deg": 34.0, "lon_deg": -118.0, "alt_m": 20},
        {"name": "beta", "lat_deg": 50.0, "lon_deg": 8.0, "alt_m": 100},
    ],
}


def _make_inline_spec(tmp_path, protocol="ospf", constellation=None, ground_stations=None):
    """Build a fully self-contained CRD spec using tempfiles.

    Writes constellation and ground station YAML to tmp_path so
    load_constellation/load_ground_stations can resolve them.
    Returns a spec dict with sessionYaml.
    """
    const = constellation or _INLINE_CONSTELLATION
    gs = ground_stations or _INLINE_GROUND_STATIONS

    const_path = tmp_path / "constellation.yaml"
    const_path.write_text(yaml.dump(const, default_flow_style=False))

    gs_path = tmp_path / "ground_stations.yaml"
    gs_path.write_text(yaml.dump(gs, default_flow_style=False))

    session = {
        "session": {"name": "test-session"},
        "constellation": str(const_path),
        "ground_stations": str(gs_path),
        "orbit": {"propagator": "keplerian-circular"},
        "routing": {"protocol": protocol, "area_assignment": {"strategy": "flat"}},
        "time": {"step_seconds": 1},
    }
    return {"sessionYaml": yaml.dump(session, default_flow_style=False)}


def _extract_manifest(mock_v1):
    """Extract and decompress the wiring manifest from the mock K8s client."""
    for call in mock_v1.create_namespaced_config_map.call_args_list:
        body = call[1].get("body") or call[0][1]
        if hasattr(body, "data") and body.data and "manifest.json.gz.b64" in body.data:
            compressed = body.data["manifest.json.gz.b64"]
            raw = gzip.decompress(base64.b64decode(compressed))
            return json.loads(raw)
    for call in mock_v1.patch_namespaced_config_map.call_args_list:
        args = call[0] if call[0] else ()
        kwargs = call[1] if call[1] else {}
        body = kwargs.get("body") or (args[2] if len(args) > 2 else None)
        if body and hasattr(body, "data") and body.data and "manifest.json.gz.b64" in body.data:
            compressed = body.data["manifest.json.gz.b64"]
            raw = gzip.decompress(base64.b64decode(compressed))
            return json.loads(raw)
    pytest.fail("Wiring manifest ConfigMap not found in mock calls")


# ---------------------------------------------------------------------------
# Class 5: TestWiringManifest
# ---------------------------------------------------------------------------


class TestWiringManifest:
    """Tests write_wiring_manifest() - the contract between Operator and Node Agent."""

    def _build_and_extract(self, tmp_path, **kwargs):
        spec = _make_inline_spec(tmp_path, **kwargs)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        # wiring-status delete returns 404 (normal for fresh deploy)
        mock_v1.delete_namespaced_config_map.side_effect = kubernetes.client.rest.ApiException(
            status=404
        )
        owner_ref = {
            "apiVersion": "nodalarc.io/v1alpha1",
            "kind": "ConstellationSpec",
            "name": "current-session",
            "uid": "test-uid",
        }
        with patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1):
            write_wiring_manifest(spec, "nodalarc", owner_ref)
        return _extract_manifest(mock_v1)

    def test_manifest_node_agent_schema(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)
        assert "session_id" in manifest
        assert isinstance(manifest["session_id"], str)
        assert "isl_link_count" in manifest
        assert isinstance(manifest["isl_link_count"], int)
        assert "nodes" in manifest
        assert "ground_bridges" in manifest
        for node_id, node in manifest["nodes"].items():
            assert "node_type" in node, f"{node_id} missing node_type"
            assert node["node_type"] in ("satellite", "ground_station"), f"{node_id} bad node_type"
            assert "isl_interfaces" in node, f"{node_id} missing isl_interfaces"
            assert isinstance(node["isl_interfaces"], list), f"{node_id} isl_interfaces not list"
            assert "gnd_interfaces" in node, f"{node_id} missing gnd_interfaces"
            assert isinstance(node["gnd_interfaces"], list), f"{node_id} gnd_interfaces not list"
            assert "sysctls" in node, f"{node_id} missing sysctls"
            assert isinstance(node["sysctls"], dict), f"{node_id} sysctls not dict"
            assert "mpls_enable" in node, f"{node_id} missing mpls_enable"
            assert "segment_routing" in node, f"{node_id} missing segment_routing"
            assert "remove_default_route" in node, f"{node_id} missing remove_default_route"
            assert "mtu" in node, f"{node_id} missing mtu"

    def test_isl_peer_symmetry_graph_walk(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)
        forward_edges = set()
        for node_id, node in manifest["nodes"].items():
            for isl in node["isl_interfaces"]:
                forward_edges.add((node_id, isl["name"], isl["peer_node"], isl["peer_iface"]))
        reverse_edges = set()
        for a, a_iface, b, b_iface in forward_edges:
            reverse_edges.add((b, b_iface, a, a_iface))
        missing = forward_edges - reverse_edges
        assert not missing, f"Asymmetric ISL links (forward without reverse): {missing}"

    def test_isl_interfaces_fully_resolved(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)
        for node_id, node in manifest["nodes"].items():
            for isl in node["isl_interfaces"]:
                assert isl["peer_node"], f"{node_id}/{isl['name']} has empty peer_node"
                assert isl["peer_iface"], f"{node_id}/{isl['name']} has empty peer_iface"

    def test_ground_station_has_term_interfaces(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)
        gs_nodes = {
            nid: n for nid, n in manifest["nodes"].items() if n["node_type"] == "ground_station"
        }
        assert len(gs_nodes) > 0
        for gs_id, gs in gs_nodes.items():
            assert len(gs["gnd_interfaces"]) >= 1, f"{gs_id} has no gnd_interfaces"

    def test_sysctls_include_rp_filter(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)
        for node_id, node in manifest["nodes"].items():
            sysctls = node["sysctls"]
            assert sysctls.get("net.ipv4.conf.all.rp_filter") == "0", (
                f"{node_id} missing rp_filter=0 on all"
            )
            assert sysctls.get("net.ipv4.conf.default.rp_filter") == "0", (
                f"{node_id} missing rp_filter=0 on default"
            )

    def test_ground_bridges_match_gs_nodes(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)
        gs_ids = {nid for nid, n in manifest["nodes"].items() if n["node_type"] == "ground_station"}
        bridge_ids = set(manifest["ground_bridges"].keys())
        assert gs_ids == bridge_ids, f"GS nodes {gs_ids} != bridges {bridge_ids}"

    def test_manifest_compressed_roundtrip(self, tmp_path):
        spec = _make_inline_spec(tmp_path)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.delete_namespaced_config_map.side_effect = kubernetes.client.rest.ApiException(
            status=404
        )
        owner_ref = {
            "apiVersion": "nodalarc.io/v1alpha1",
            "kind": "ConstellationSpec",
            "name": "current-session",
            "uid": "test-uid",
        }
        with patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1):
            write_wiring_manifest(spec, "nodalarc", owner_ref)
        manifest = _extract_manifest(mock_v1)
        assert isinstance(manifest, dict)
        assert len(manifest["nodes"]) > 0

    def test_manifest_size_at_scale(self, tmp_path):
        large_constellation = dict(_INLINE_CONSTELLATION)
        large_constellation["planes"] = {
            "count": 80,
            "raan_spacing_deg": 4.5,
            "sats_per_plane": 20,
            "phase_offset_deg": 0.225,
        }
        spec = _make_inline_spec(tmp_path, constellation=large_constellation)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.delete_namespaced_config_map.side_effect = kubernetes.client.rest.ApiException(
            status=404
        )
        owner_ref = {
            "apiVersion": "nodalarc.io/v1alpha1",
            "kind": "ConstellationSpec",
            "name": "current-session",
            "uid": "test-uid",
        }
        with patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1):
            write_wiring_manifest(spec, "nodalarc", owner_ref)
        manifest = _extract_manifest(mock_v1)
        assert len(manifest["nodes"]) == 1602
        raw_json = json.dumps(manifest).encode()
        compressed = base64.b64encode(gzip.compress(raw_json))
        size_bytes = len(compressed)
        assert size_bytes < 1_048_576, (
            f"Compressed manifest is {size_bytes} bytes ({size_bytes / 1024:.0f} KB), exceeds 1 MiB K8s ConfigMap limit"
        )


# ---------------------------------------------------------------------------
# Class 7: TestConfigRendering
# ---------------------------------------------------------------------------


class TestConfigRendering:
    """Tests FRR config rendering via ensure_session_configmaps()."""

    def _render_configs(self, tmp_path, protocol="ospf"):
        """Run the full config pipeline and capture rendered ConfigMaps."""
        spec = _make_inline_spec(tmp_path, protocol=protocol)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        # SSH key creation - return existing secret (already exists path)
        mock_v1.read_namespaced_secret.return_value = MagicMock()
        owner_ref = {
            "apiVersion": "nodalarc.io/v1alpha1",
            "kind": "ConstellationSpec",
            "name": "current-session",
            "uid": "test-uid",
        }
        with (
            patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1),
            patch(
                "nodalarc_operator.session_deployer.discover_available_nodes",
                return_value=["node01", "node02"],
            ),
        ):
            context = ensure_session_configmaps(spec, "current-session", "nodalarc", owner_ref)

        # Collect rendered configs from ConfigMap create/patch calls
        configs = {}
        for call in mock_v1.create_namespaced_config_map.call_args_list:
            body = call[1].get("body") or call[0][1]
            if hasattr(body, "metadata") and hasattr(body, "data"):
                name = body.metadata.name if hasattr(body.metadata, "name") else ""
                if name.startswith("frr-config-"):
                    configs[name] = body.data
        return configs, context

    def test_ospf_config_contains_router_ospf(self, tmp_path):
        configs, _ = self._render_configs(tmp_path, protocol="ospf")
        assert len(configs) > 0, "No FRR config ConfigMaps created"
        for cm_name, data in configs.items():
            if "frr.conf" in data:
                assert "router ospf" in data["frr.conf"], f"{cm_name} missing 'router ospf'"

    def test_isis_config_contains_router_isis(self, tmp_path):
        configs, _ = self._render_configs(tmp_path, protocol="isis")
        assert len(configs) > 0, "No FRR config ConfigMaps created"
        for cm_name, data in configs.items():
            if "frr.conf" in data:
                assert "router isis" in data["frr.conf"], f"{cm_name} missing 'router isis'"

    def test_config_version_hash_present(self, tmp_path):
        configs, _ = self._render_configs(tmp_path)
        for cm_name, data in configs.items():
            assert "_config_version" in data, f"{cm_name} missing _config_version"
            assert len(data["_config_version"]) == 16, f"{cm_name} _config_version wrong length"

    def test_config_version_changes_with_content(self, tmp_path):
        """Different routing configs must produce different _config_version hashes."""
        configs_ospf, _ = self._render_configs(tmp_path, protocol="ospf")
        configs_isis, _ = self._render_configs(tmp_path, protocol="isis")
        # Pick the same node from both renders
        ospf_names = sorted(configs_ospf.keys())
        isis_names = sorted(configs_isis.keys())
        assert ospf_names == isis_names, "Different node sets for ospf vs isis"
        first = ospf_names[0]
        v_ospf = configs_ospf[first]["_config_version"]
        v_isis = configs_isis[first]["_config_version"]
        assert v_ospf != v_isis, (
            f"_config_version identical for ospf and isis ({v_ospf}). "
            "Hash must be derived from rendered content, not template filename."
        )


# ---------------------------------------------------------------------------
# Class 6: TestPodSpec
# ---------------------------------------------------------------------------


class TestPodSpec:
    """Tests pod creation through ensure_session_pods().

    Asserts on the V1Pod objects sent to v1.create_namespaced_pod().
    The pod spec IS the Operator's primary output.
    """

    def _create_pods(self, tmp_path):
        """Run the full pipeline and capture all created pods."""
        spec = _make_inline_spec(tmp_path)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.read_namespaced_secret.return_value = MagicMock()
        owner_ref = {
            "apiVersion": "nodalarc.io/v1alpha1",
            "kind": "ConstellationSpec",
            "name": "current-session",
            "uid": "test-uid-456",
            "blockOwnerDeletion": True,
        }
        with (
            patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1),
            patch(
                "nodalarc_operator.session_deployer.discover_available_nodes",
                return_value=["node01", "node02"],
            ),
            patch.dict(
                "os.environ",
                {
                    "FRR_IMAGE": "test/frr:1",
                    "PROBE_IMAGE": "test/probe:1",
                    "NODALPATH_FWD_IMAGE": "test/nodalpath-fwd:1",
                    "IMAGE_PULL_POLICY": "Never",
                },
            ),
        ):
            context = ensure_session_configmaps(spec, "current-session", "nodalarc", owner_ref)
            ensure_session_pods(context, "nodalarc", owner_ref)

        pods = []
        for call in mock_v1.create_namespaced_pod.call_args_list:
            pod = call[1].get("body") or call[0][1]
            pods.append(pod)
        return pods

    def test_service_account_token_not_mounted(self, tmp_path):
        pods = self._create_pods(tmp_path)
        assert len(pods) > 0
        for pod in pods:
            assert pod.spec.automount_service_account_token is False, (
                f"Pod {pod.metadata.name} has automount_service_account_token != False"
            )

    def test_security_context(self, tmp_path):
        pods = self._create_pods(tmp_path)
        for pod in pods:
            frr = pod.spec.containers[0]
            assert frr.name == "frr"
            caps = frr.security_context.capabilities.add
            assert "SYS_ADMIN" in caps, f"Pod {pod.metadata.name} missing SYS_ADMIN"
            assert "NET_ADMIN" in caps, f"Pod {pod.metadata.name} missing NET_ADMIN"
            assert "NET_RAW" in caps, f"Pod {pod.metadata.name} missing NET_RAW"
            assert frr.security_context.read_only_root_filesystem is True

    def test_labels(self, tmp_path):
        pods = self._create_pods(tmp_path)
        for pod in pods:
            labels = pod.metadata.labels
            assert labels.get("nodalarc.io/session") == "true"
            assert "nodalarc.io/node-id" in labels
            assert "nodalarc.io/role" in labels
            role = labels["nodalarc.io/role"]
            assert role in ("satellite", "ground-station")
            if role == "satellite":
                assert "nodalarc.io/plane" in labels
                assert "nodalarc.io/slot" in labels

    def test_owner_reference(self, tmp_path):
        pods = self._create_pods(tmp_path)
        for pod in pods:
            refs = pod.metadata.owner_references
            assert len(refs) >= 1
            ref = refs[0]
            assert ref["kind"] == "ConstellationSpec"
            assert ref["uid"] == "test-uid-456"

    def test_409_conflict_idempotent(self, tmp_path):
        """If create_namespaced_pod raises 409, ensure_session_pods continues."""
        spec = _make_inline_spec(tmp_path)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.read_namespaced_secret.return_value = MagicMock()
        mock_v1.create_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(status=409)
        owner_ref = {
            "apiVersion": "nodalarc.io/v1alpha1",
            "kind": "ConstellationSpec",
            "name": "current-session",
            "uid": "test-uid",
            "blockOwnerDeletion": True,
        }
        with (
            patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1),
            patch(
                "nodalarc_operator.session_deployer.discover_available_nodes",
                return_value=["node01"],
            ),
            patch.dict(
                "os.environ",
                {
                    "FRR_IMAGE": "test/frr:1",
                    "PROBE_IMAGE": "test/probe:1",
                    "NODALPATH_FWD_IMAGE": "test/nodalpath-fwd:1",
                    "IMAGE_PULL_POLICY": "Never",
                },
            ),
        ):
            context = ensure_session_configmaps(spec, "current-session", "nodalarc", owner_ref)
            total = ensure_session_pods(context, "nodalarc", owner_ref)
        assert total > 0
