"""Unit tests for nodalarc_operator/session_deployer.py.

Tests pure-logic functions and K8s-mocked deploy pipeline. All test inputs
are inline - no dependency on production config files except one regression
test per class that explicitly references earth-leo-simple.yaml.

Uses create_autospec for K8s client mocks to catch signature drift.
"""

from __future__ import annotations

import base64
import gzip
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import kopf
import kubernetes.client
import pytest
import yaml
from nodalarc.models.session import (
    AllOnOnePlacementConfig,
    PlacementConfig,
    PlaneGroupPerNodePlacementConfig,
    PlanePerNodePlacementConfig,
)
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
from nodalarc.substrate.wiring_status import failed_status, ready_status, status_configmap_data
from nodalarc_operator.session_deployer import (
    _create_terminal_ssh_keys,
    _deterministic_node,
    _required_substrate_pairs,
    check_wiring_complete,
    compute_expected_placement_node_count,
    compute_expected_pod_count,
    compute_platform_hash,
    compute_pod_placement,
    compute_runtime_hash,
    discover_available_nodes,
    ensure_session_configmaps,
    ensure_session_pods,
    session_runtime_purge_targets,
    teardown_session,
    write_wiring_manifest,
)
from pydantic import TypeAdapter, ValidationError

from tests.conftest import build_segment_session_dict

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
    constellation_path="configs/constellations/demo-36.yaml",
    gs_path="configs/ground-stations/sets/demo.yaml",
    protocol="ospf",
    strategy="flat",
    step_seconds=1,
    placement_policy=None,
):
    """Build a segment-session YAML string with configurable fields."""
    d = build_segment_session_dict(
        name="test-session",
        constellation=constellation_path,
        ground_stations=gs_path,
        protocol=protocol,
        extensions=[],
        routing={"area_assignment": {"strategy": strategy}},
        time={"step_seconds": step_seconds},
    )
    if placement_policy:
        d["placement"] = {"policy": placement_policy}
    return yaml.dump(d, default_flow_style=False)


def _make_wiring_manifest(node_ids=("sat-P00S00", "sat-P00S01")):
    nodes = {}
    for index, node_id in enumerate(node_ids):
        nodes[node_id] = {
            "node_type": "satellite",
            "sysctls": {"net.ipv4.ip_forward": "1"},
            "isl_interfaces": [],
            "gnd_interfaces": [],
            "mpls_enable": False,
            "segment_routing": False,
            "mtu": 1500,
            "remove_default_route": False,
            "plane": 0,
            "slot": index,
        }
    return WiringManifest.model_validate(
        {
            "session_id": "test-session",
            "wiring_generation": "sha256:" + "a" * 64,
            "required_phases": list(REQUIRED_WIRING_PHASES),
            "nodes": nodes,
            "ground_bridges": {},
            "required_substrate_pairs": [],
            "isl_link_count": 0,
        }
    )


def _manifest_configmap(manifest: WiringManifest):
    payload = manifest.model_dump(mode="json")
    encoded = base64.b64encode(
        gzip.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    ).decode()
    cm = MagicMock()
    cm.data = {
        "manifest.json.gz.b64": encoded,
        "session_id": manifest.session_id,
        "wiring_generation": manifest.wiring_generation,
        "node_count": str(len(manifest.nodes)),
    }
    return cm


def _status_configmap(data: dict[str, str]):
    cm = MagicMock()
    cm.data = data
    return cm


# ---------------------------------------------------------------------------
# Class 1: TestPodPlacement
# ---------------------------------------------------------------------------


class TestPodPlacement:
    """Tests compute_pod_placement() - assigns pods to K8s nodes."""

    def test_all_on_one_single_node(self):
        nv = _make_node_vars(planes=2, sats_per_plane=3, gs_count=2)
        placement = AllOnOnePlacementConfig(policy="allOnOne")
        result = compute_pod_placement(placement, nv, ["node01"])
        assert all(v == "node01" for v in result.values())
        assert len(result) == len(nv)

    def test_all_on_one_ignores_extra_nodes(self):
        nv = _make_node_vars(planes=2, sats_per_plane=3, gs_count=2)
        placement = AllOnOnePlacementConfig(policy="allOnOne")
        result = compute_pod_placement(placement, nv, ["node01", "node02", "node03", "node04"])
        assert all(v == "node01" for v in result.values())

    def test_plane_per_node_same_plane_same_node(self):
        nv = _make_node_vars(planes=4, sats_per_plane=3, gs_count=0)
        placement = PlanePerNodePlacementConfig(policy="planePerNode")
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        plane0_nodes = {result[nid] for nid, v in nv.items() if v["plane"] == 0}
        plane1_nodes = {result[nid] for nid, v in nv.items() if v["plane"] == 1}
        assert len(plane0_nodes) == 1
        assert len(plane1_nodes) == 1
        assert plane0_nodes != plane1_nodes

    def test_plane_per_node_wraps_modulo(self):
        nv = _make_node_vars(planes=6, sats_per_plane=2, gs_count=0)
        placement = PlanePerNodePlacementConfig(policy="planePerNode")
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        plane0_node = result["sat-P00S00"]
        plane4_node = result["sat-P04S00"]
        assert plane0_node == plane4_node

    def test_plane_per_node_gs_uses_hrw(self):
        nv = _make_node_vars(planes=2, sats_per_plane=2, gs_count=7)
        placement = PlanePerNodePlacementConfig(policy="planePerNode")
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        gs_nodes = {result[nid] for nid in nv if nid.startswith("gs-")}
        assert len(gs_nodes) > 1

    def test_plane_group_per_node_groups(self):
        nv = _make_node_vars(planes=4, sats_per_plane=2, gs_count=0)
        placement = PlaneGroupPerNodePlacementConfig(policy="planeGroupPerNode", planes_per_group=2)
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        assert result["sat-P00S00"] == result["sat-P01S00"]
        assert result["sat-P02S00"] == result["sat-P03S00"]
        assert result["sat-P00S00"] != result["sat-P02S00"]

    def test_plane_group_per_node_requires_explicit_group_size(self):
        with pytest.raises(ValidationError, match="planes_per_group"):
            TypeAdapter(PlacementConfig).validate_python({"policy": "planeGroupPerNode"})

    def test_no_nodes_raises(self):
        nv = _make_node_vars(planes=1, sats_per_plane=1, gs_count=0)
        placement = AllOnOnePlacementConfig(policy="allOnOne")
        with pytest.raises(ValueError, match="No available"):
            compute_pod_placement(placement, nv, [])

    def test_unknown_policy_rejected_at_parse_boundary(self):
        with pytest.raises(ValidationError):
            TypeAdapter(PlacementConfig).validate_python({"policy": "bogus"})

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
        counts = dict.fromkeys(nodes, 0)
        for i in range(1000):
            result = _deterministic_node(f"pod-{i}", nodes)
            counts[result] += 1
        for n, c in counts.items():
            assert 200 <= c <= 300, f"Node {n} has {c} pods, expected 200-300"

    def test_single_node(self):
        assert _deterministic_node("gs-anything", ["only-node"]) == "only-node"


# ---------------------------------------------------------------------------
# Class 3: TestWiringCompletion
# ---------------------------------------------------------------------------


class TestWiringCompletion:
    """Tests check_wiring_complete() against typed wiring status data."""

    def test_metadata_keys_are_not_counted_as_wired_nodes(self):
        manifest = _make_wiring_manifest()
        statuses = {node_id: ready_status(node_id, manifest) for node_id in manifest.nodes}
        status_data = status_configmap_data(statuses, manifest)
        status_data["_progress"] = "Finalized 2/2 pods. Wiring complete."

        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)

        def read_cm(name, namespace):
            assert namespace == "nodalarc"
            if name == "nodalarc-topology-wiring":
                return _manifest_configmap(manifest)
            if name == "nodalarc-wiring-status":
                return _status_configmap(status_data)
            raise AssertionError(f"unexpected ConfigMap read: {name}")

        mock_v1.read_namespaced_config_map.side_effect = read_cm

        with patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1):
            complete, wired_count, progress = check_wiring_complete("nodalarc", 2)

        assert complete is True
        assert wired_count == 2
        assert progress is None

    def test_unknown_status_node_fails_loudly(self):
        manifest = _make_wiring_manifest()
        statuses = {node_id: ready_status(node_id, manifest) for node_id in manifest.nodes}
        statuses["sat-P99S99"] = ready_status("sat-P99S99", manifest)
        status_data = status_configmap_data(statuses, manifest)

        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)

        def read_cm(name, namespace):
            assert namespace == "nodalarc"
            if name == "nodalarc-topology-wiring":
                return _manifest_configmap(manifest)
            if name == "nodalarc-wiring-status":
                return _status_configmap(status_data)
            raise AssertionError(f"unexpected ConfigMap read: {name}")

        mock_v1.read_namespaced_config_map.side_effect = read_cm

        with patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1):
            with pytest.raises(ValueError, match="unknown node entries"):
                check_wiring_complete("nodalarc", 2)

    def test_dirty_kernel_status_names_first_failure(self):
        manifest = _make_wiring_manifest()
        statuses = {node_id: ready_status(node_id, manifest) for node_id in manifest.nodes}
        statuses["sat-P00S00"] = failed_status(
            "sat-P00S00",
            manifest,
            phase="sysctls",
            error_message="sysctl net.mpls.platform_labels=100000 failed",
            dirty_kernel=True,
        )
        status_data = status_configmap_data(statuses, manifest)

        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)

        def read_cm(name, namespace):
            assert namespace == "nodalarc"
            if name == "nodalarc-topology-wiring":
                return _manifest_configmap(manifest)
            if name == "nodalarc-wiring-status":
                return _status_configmap(status_data)
            raise AssertionError(f"unexpected ConfigMap read: {name}")

        mock_v1.read_namespaced_config_map.side_effect = read_cm

        with patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1):
            with pytest.raises(ValueError, match="first failure: sat-P00S00 sysctls"):
                check_wiring_complete("nodalarc", 2)


# ---------------------------------------------------------------------------
# Class 4: TestPlatformHash
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

    def test_session_owned_placement_is_rejected(self):
        body = yaml.safe_load(_make_session_yaml())
        body["placement"] = {"policy": "allOnOne"}

        with pytest.raises(Exception, match="placement"):
            compute_platform_hash({"sessionYaml": yaml.safe_dump(body)})

    def test_runtime_semantics_change_hash(self):
        base = yaml.safe_load(_make_session_yaml())

        scheduling = yaml.safe_load(_make_session_yaml())
        scheduling["segments"][1]["apply"]["scheduling"]["selection_policy"] = {
            "longest_remaining_pass": {"lookahead_horizon_ticks": 4}
        }

        simulation = yaml.safe_load(_make_session_yaml())
        simulation["simulation"]["candidate_limits"]["max_pairs_per_tick"] = 1001

        dispatch = yaml.safe_load(_make_session_yaml())
        dispatch["dispatch"]["max_latency_age_ticks"] = 7

        addressing = yaml.safe_load(_make_session_yaml())
        addressing["addressing"]["loopbacks"][0]["ipv4_pool"] = "10.1.0.0/16"

        hashes = {
            compute_platform_hash({"sessionYaml": yaml.dump(candidate, default_flow_style=False)})
            for candidate in (base, scheduling, simulation, dispatch, addressing)
        }
        assert len(hashes) == 5

    def test_session_yaml_run_id_is_rejected(self):
        with_run_id = yaml.safe_load(_make_session_yaml())
        with_run_id["session"]["run_id"] = "operator-owned-run"

        with pytest.raises(Exception, match="run_id"):
            compute_platform_hash({"sessionYaml": yaml.dump(with_run_id)})

    def test_empty_session_yaml(self):
        h1 = compute_platform_hash({"sessionYaml": ""})
        h2 = compute_platform_hash({})
        assert isinstance(h1, str) and len(h1) == 64
        assert isinstance(h2, str) and len(h2) == 64

    def test_runtime_hash_includes_run_id(self):
        platform_hash = "a" * 64
        assert compute_runtime_hash(platform_hash, "run-a") != compute_runtime_hash(
            platform_hash, "run-b"
        )


class TestRuntimeIdentityCleanup:
    def test_teardown_does_not_touch_retired_ephemeral_config_roots(self):
        source = Path("services/nodalarc_operator/session_deployer.py").read_text(encoding="utf-8")

        assert "configs/constellations/_ephemeral" not in source
        assert "configs/ground-stations/_ephemeral" not in source

    def test_purge_targets_are_session_scoped(self):
        targets = session_runtime_purge_targets("run-test-0001")

        assert targets
        for _stream, subject in targets:
            assert "run-test-0001" in subject
            assert subject.endswith(".>")
            assert subject != ">"

    def test_teardown_purges_before_deleting_identity_configmap(self):
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.read_namespaced_config_map.return_value = kubernetes.client.V1ConfigMap(
            data={"session_run_id": "run-test-0001"}
        )

        with (
            patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1),
            patch(
                "nodalarc_operator.session_deployer.purge_session_runtime_state",
                side_effect=RuntimeError("nats unavailable"),
            ) as purge,
        ):
            with pytest.raises(RuntimeError, match="nats unavailable"):
                teardown_session("nodalarc")

        purge.assert_called_once_with("nodalarc", "run-test-0001")
        mock_v1.delete_namespaced_config_map.assert_not_called()


# ---------------------------------------------------------------------------
# Class 4: TestExpectedPodCount
# ---------------------------------------------------------------------------


class TestExpectedPodCount:
    """Tests compute_expected_pod_count() - must raise on invalid, never return 0."""

    def test_inline_config_count(self):
        spec = {
            "sessionYaml": _make_session_yaml(
                constellation_path="configs/constellations/demo-36.yaml",
                gs_path="configs/ground-stations/sets/demo.yaml",
            )
        }
        count = compute_expected_pod_count(spec)
        assert count > 0

    def test_demo_36_regression(self):
        spec = {
            "sessionYaml": _make_session_yaml(
                constellation_path="configs/constellations/demo-36.yaml",
                gs_path="configs/ground-stations/sets/demo.yaml",
            )
        }
        assert compute_expected_pod_count(spec) == 43

    def test_missing_session_yaml_raises(self):
        with pytest.raises(ValueError, match="sessionYaml"):
            compute_expected_pod_count({})

    def test_bad_constellation_path_raises(self):
        spec = {
            "sessionYaml": _make_session_yaml(
                constellation_path="nodalarc:constellations/no-such-file.yaml"
            )
        }
        with pytest.raises(Exception):
            compute_expected_pod_count(spec)


class TestExpectedPlacementNodeCount:
    """Expected placement must use all resolved segments, not only the primary constellation."""

    def test_multi_segment_session_counts_relay_segment_placement(self):
        body = yaml.safe_load(_make_session_yaml())
        relay_segment = dict(body["segments"][0])
        relay_segment["id"] = "relay"
        body["segments"].append(relay_segment)
        body["addressing"]["loopbacks"].append(
            {
                "id": "relay-loopbacks-v4",
                "applies_to": {"segment": "relay"},
                "ipv4_pool": "10.2.0.0/16",
                "prefix_length": 32,
                "allocation": "by_node_order",
            }
        )
        body["addressing"]["loopbacks"].append(
            {
                "id": "relay-loopbacks-v6",
                "applies_to": {"segment": "relay"},
                "ipv6_pool": "fd00:2::/64",
                "prefix_length": 128,
                "allocation": "by_node_order",
            }
        )
        body["routing"]["domains"][0]["selectors"] = [
            {"any": [{"segment": "space"}, {"segment": "relay"}, {"segment": "ground"}]}
        ]
        session_yaml = yaml.safe_dump(body)

        count = compute_expected_placement_node_count(
            {"sessionYaml": session_yaml},
            ["node01", "node02", "node03"],
        )

        assert count == 3

    def test_missing_session_yaml_raises(self):
        with pytest.raises(ValueError, match="sessionYaml"):
            compute_expected_placement_node_count({}, ["node01"])


# ---------------------------------------------------------------------------
# Inline config fixtures (fully self-contained, no external files)
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
        "ground": [
            {
                "type": "rf",
                "count": 1,
                "bandwidth_mbps": 1000,
                "max_range_km": 2000,
                "field_of_regard_deg": 120,
                "max_tracking_rate_deg_s": 1.5,
                "boresight": {
                    "target_body": "earth",
                    "mode": "nadir",
                },
            }
        ],
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
        {
            "type": "rf",
            "count": 1,
            "bandwidth_mbps": 1000,
            "tracking_capacity": 1,
            "max_range_km": 2000,
            "field_of_regard_deg": 120,
            "max_tracking_rate_deg_s": 1.5,
            "boresight": {
                "mode": "local_vertical",
            },
        }
    ],
    "stations": [
        {"name": "alpha", "lat_deg": 34.0, "lon_deg": -118.0, "alt_m": 20},
        {"name": "beta", "lat_deg": 50.0, "lon_deg": 8.0, "alt_m": 100},
    ],
}


def _make_inline_spec(
    tmp_path,
    protocol="ospf",
    constellation=None,
    ground_stations=None,
    extensions=None,
):
    """Build a fully self-contained CRD spec using tempfiles.

    Writes constellation and ground station YAML to tmp_path so
    load_constellation/load_ground_stations can resolve them.
    Returns a spec dict with sessionYaml.
    """
    const = constellation or _INLINE_CONSTELLATION
    gs = ground_stations or _INLINE_GROUND_STATIONS

    session = build_segment_session_dict(
        name="test-session",
        constellation=const,
        ground_stations=gs,
        protocol=protocol,
        extensions=extensions or [],
        time={"step_seconds": 1},
    )
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


def _existing_terminal_secret(uid="test-uid", name="current-session"):
    return kubernetes.client.V1Secret(
        metadata=kubernetes.client.V1ObjectMeta(
            name="nodalarc-terminal-keys",
            owner_references=[
                kubernetes.client.V1OwnerReference(
                    api_version="nodalarc.io/v1alpha1",
                    kind="ConstellationSpec",
                    name=name,
                    uid=uid,
                )
            ],
        )
    )


def _existing_session_pod(
    pod_name="sat-p00s00",
    node_id="sat-P00S00",
    uid="test-uid",
    run_id="run-test-0001",
):
    return kubernetes.client.V1Pod(
        metadata=kubernetes.client.V1ObjectMeta(
            name=pod_name,
            labels={
                "nodalarc.io/session": "true",
                "nodalarc.io/node-id": node_id,
                "nodalarc.io/session-run-id": run_id,
                "nodalarc.io/owner-uid": uid,
            },
            owner_references=[
                kubernetes.client.V1OwnerReference(
                    api_version="nodalarc.io/v1alpha1",
                    kind="ConstellationSpec",
                    name="current-session",
                    uid=uid,
                )
            ],
        ),
        status=kubernetes.client.V1PodStatus(phase="Running", pod_ip="10.42.0.10"),
    )


# ---------------------------------------------------------------------------
# Class 5: TestWiringManifest
# ---------------------------------------------------------------------------


class TestWiringManifest:
    """Tests write_wiring_manifest() - the contract between Operator and Node Agent."""

    def _build_and_extract(self, tmp_path, spec=None, **kwargs):
        if spec is None:
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
        with (
            patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1),
            patch(
                "nodalarc_operator.session_deployer._discover_session_pod_placement",
                side_effect=lambda _v1, _ns, expected: dict.fromkeys(expected, "node01"),
            ),
            patch(
                "nodalarc_operator.session_deployer._node_internal_ips",
                side_effect=lambda _v1, required: dict.fromkeys(required, "10.0.0.1"),
            ),
        ):
            write_wiring_manifest(spec, "nodalarc", owner_ref, "run-test-0001")
        return _extract_manifest(mock_v1)

    def test_manifest_node_agent_schema(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)
        assert "session_id" in manifest
        assert manifest["session_id"] == "run-test-0001"
        assert "isl_link_count" in manifest
        assert isinstance(manifest["isl_link_count"], int)
        assert "required_substrate_pairs" in manifest
        assert isinstance(manifest["required_substrate_pairs"], list)
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

    def test_manifest_disables_mpls_for_plain_igp(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)

        assert manifest["nodes"]
        assert all(node["mpls_enable"] is False for node in manifest["nodes"].values())

    def test_manifest_enables_mpls_only_for_mpls_stack(self, tmp_path):
        spec = _make_inline_spec(tmp_path, protocol="isis", extensions=["te", "mpls"])
        manifest = self._build_and_extract(tmp_path, spec=spec)

        assert manifest["nodes"]
        assert all(node["mpls_enable"] is True for node in manifest["nodes"].values())

    def test_manifest_requires_runtime_session_id(self, tmp_path):
        spec = _make_inline_spec(tmp_path)

        with pytest.raises(ValueError, match="session_run_id is required"):
            write_wiring_manifest(spec, "nodalarc", None)

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

    def test_isl_interfaces_emit_in_deterministic_order(self, tmp_path):
        manifest = self._build_and_extract(tmp_path)
        for node in manifest["nodes"].values():
            interfaces = node["isl_interfaces"]
            ordered = sorted(
                interfaces,
                key=lambda iface: (iface["name"], iface["peer_node"], iface["peer_iface"]),
            )
            assert interfaces == ordered

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
        with (
            patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1),
            patch(
                "nodalarc_operator.session_deployer._discover_session_pod_placement",
                side_effect=lambda _v1, _ns, expected: dict.fromkeys(expected, "node01"),
            ),
            patch(
                "nodalarc_operator.session_deployer._node_internal_ips",
                side_effect=lambda _v1, required: dict.fromkeys(required, "10.0.0.1"),
            ),
        ):
            write_wiring_manifest(spec, "nodalarc", owner_ref, "run-test-0001")
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
        with (
            patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1),
            patch(
                "nodalarc_operator.session_deployer._discover_session_pod_placement",
                side_effect=lambda _v1, _ns, expected: dict.fromkeys(expected, "node01"),
            ),
            patch(
                "nodalarc_operator.session_deployer._node_internal_ips",
                side_effect=lambda _v1, required: dict.fromkeys(required, "10.0.0.1"),
            ),
        ):
            write_wiring_manifest(spec, "nodalarc", owner_ref, "run-test-0001")
        manifest = _extract_manifest(mock_v1)
        assert len(manifest["nodes"]) == 1602
        raw_json = json.dumps(manifest).encode()
        compressed = base64.b64encode(gzip.compress(raw_json))
        size_bytes = len(compressed)
        assert size_bytes < 1_048_576, (
            f"Compressed manifest is {size_bytes} bytes ({size_bytes / 1024:.0f} KB), exceeds 1 MiB K8s ConfigMap limit"
        )


# ---------------------------------------------------------------------------
# Class 6: TestRequiredSubstratePairs
# ---------------------------------------------------------------------------


class TestRequiredSubstratePairs:
    """Tests Operator computation of pre-dispatch substrate node pairs."""

    def test_single_node_requires_no_substrate_pairs(self):
        nodes = {
            "sat-a": {"node_type": "satellite"},
            "sat-b": {"node_type": "satellite"},
            "gs-den": {"node_type": "ground_station"},
        }
        pairs = _required_substrate_pairs(
            nodes=nodes,
            isl_pairs={("sat-a", "sat-b")},
            pod_placement={"sat-a": "node01", "sat-b": "node01", "gs-den": "node01"},
            node_ips={"node01": "10.0.0.1"},
        )

        assert pairs == []

    def test_isl_pairs_emit_both_directions(self):
        nodes = {
            "sat-a": {"node_type": "satellite"},
            "sat-b": {"node_type": "satellite"},
        }
        pairs = _required_substrate_pairs(
            nodes=nodes,
            isl_pairs={("sat-a", "sat-b")},
            pod_placement={"sat-a": "node01", "sat-b": "node02"},
            node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2"},
        )

        assert {pair["directional_key"] for pair in pairs} == {
            "node01->node02",
            "node02->node01",
        }
        assert all(pair["reasons"] == ["isl"] for pair in pairs)

    def test_ground_pairs_emit_both_directions_and_merge_reasons(self):
        nodes = {
            "sat-a": {"node_type": "satellite"},
            "sat-b": {"node_type": "satellite"},
            "gs-den": {"node_type": "ground_station"},
        }
        pairs = _required_substrate_pairs(
            nodes=nodes,
            isl_pairs={("sat-a", "sat-b")},
            pod_placement={"sat-a": "node01", "sat-b": "node02", "gs-den": "node02"},
            node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2"},
        )

        by_key = {pair["directional_key"]: pair for pair in pairs}
        assert set(by_key) == {"node01->node02", "node02->node01"}
        assert by_key["node01->node02"]["reasons"] == ["ground", "isl"]
        assert by_key["node02->node01"]["reasons"] == ["ground", "isl"]

    def test_resolved_candidate_map_scopes_active_ground_universe(self):
        nodes = {
            "sat-a": {"node_type": "satellite"},
            "sat-b": {"node_type": "satellite"},
            "gs-leo": {"node_type": "ground_station"},
            "gs-meo-unused": {"node_type": "ground_station"},
        }
        pairs = _required_substrate_pairs(
            nodes=nodes,
            isl_pairs=set(),
            pod_placement={
                "sat-a": "node01",
                "sat-b": "node01",
                "gs-leo": "node02",
                "gs-meo-unused": "node03",
            },
            node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2", "node03": "10.0.0.3"},
            ground_candidate_satellites_by_gs={"gs-leo": ("sat-a", "sat-b")},
        )

        assert {pair["directional_key"] for pair in pairs} == {
            "node01->node02",
            "node02->node01",
        }
        assert all(pair["reasons"] == ["ground"] for pair in pairs)

    def test_resolved_candidate_map_rejects_unknown_ground_node(self):
        with pytest.raises(ValueError, match="unknown ground station"):
            _required_substrate_pairs(
                nodes={"sat-a": {"node_type": "satellite"}},
                isl_pairs=set(),
                pod_placement={"sat-a": "node01", "gs-missing": "node02"},
                node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2"},
                ground_candidate_satellites_by_gs={"gs-missing": ("sat-a",)},
            )

    def test_resolved_candidate_map_rejects_unknown_satellite_node(self):
        with pytest.raises(ValueError, match="unknown substrate candidate satellite"):
            _required_substrate_pairs(
                nodes={"gs-den": {"node_type": "ground_station"}},
                isl_pairs=set(),
                pod_placement={"gs-den": "node01", "sat-missing": "node02"},
                node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2"},
                ground_candidate_satellites_by_gs={"gs-den": ("sat-missing",)},
            )


# ---------------------------------------------------------------------------
# Class 7: TestConfigRendering
# ---------------------------------------------------------------------------


class TestPreDeployValidationGate:
    """ensure_session_configmaps() must refuse unready sessions before any
    ConfigMap or pod exists — the readiness validator is a deploy gate, not
    an advisory report."""

    def test_zero_candidate_link_rule_blocks_deploy(self, tmp_path):
        # A 1-satellite constellation leaves the ISL rule with zero candidate
        # pairs: resolvable, but not deployable as declared.
        spec = _make_inline_spec(
            tmp_path,
            constellation={"planes": {"count": 1, "sats_per_plane": 1}},
        )
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.read_namespaced_secret.return_value = _existing_terminal_secret()
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
            with pytest.raises(kopf.PermanentError, match="Session validation failed"):
                ensure_session_configmaps(
                    spec, "current-session", "nodalarc", owner_ref, session_run_id="run-test-0001"
                )
        # Nothing was written before the gate fired.
        mock_v1.create_namespaced_config_map.assert_not_called()


class TestConfigRendering:
    """Tests FRR config rendering via ensure_session_configmaps()."""

    def _render_configs(self, tmp_path, protocol="ospf"):
        """Run the full config pipeline and capture rendered ConfigMaps."""
        spec = _make_inline_spec(tmp_path, protocol=protocol)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        # SSH key creation - return existing secret (already exists path)
        mock_v1.read_namespaced_secret.return_value = _existing_terminal_secret()
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
            context = ensure_session_configmaps(
                spec, "current-session", "nodalarc", owner_ref, session_run_id="run-test-0001"
            )

        # Collect rendered configs from ConfigMap create/patch calls
        configs = {}
        for call in mock_v1.create_namespaced_config_map.call_args_list:
            body = call[1].get("body") or call[0][1]
            if hasattr(body, "metadata") and hasattr(body, "data"):
                name = body.metadata.name if hasattr(body.metadata, "name") else ""
                if name.startswith("frr-config-"):
                    configs[name] = body.data
        return configs, context

    def test_runtime_session_configmap_records_run_id_without_mutating_yaml(self, tmp_path):
        spec = _make_inline_spec(tmp_path)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.read_namespaced_secret.return_value = _existing_terminal_secret()
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
            context = ensure_session_configmaps(
                spec, "current-session", "nodalarc", owner_ref, session_run_id="run-test-0001"
            )

        session_cms = [
            (call[1].get("body") or call[0][1]).data
            for call in mock_v1.create_namespaced_config_map.call_args_list
            if (call[1].get("body") or call[0][1]).metadata.name == "nodalarc-session"
        ]
        assert len(session_cms) == 1
        runtime_yaml = yaml.safe_load(session_cms[0]["session.yaml"])
        assert context["session_id"] == "run-test-0001"
        assert context["session_run_id"] == "run-test-0001"
        assert "run_id" not in runtime_yaml["session"]
        assert session_cms[0]["session_run_id"] == "run-test-0001"

    def test_terminal_keys_are_not_rotated_when_secret_exists(self):
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        owner_ref = {
            "apiVersion": "nodalarc.io/v1alpha1",
            "kind": "ConstellationSpec",
            "name": "current-session",
            "uid": "test-uid",
        }
        mock_v1.read_namespaced_secret.return_value = kubernetes.client.V1Secret(
            metadata=kubernetes.client.V1ObjectMeta(
                name="nodalarc-terminal-keys",
                owner_references=[
                    kubernetes.client.V1OwnerReference(
                        api_version="nodalarc.io/v1alpha1",
                        kind="ConstellationSpec",
                        name="current-session",
                        uid="test-uid",
                    )
                ],
            )
        )

        _create_terminal_ssh_keys(mock_v1, "nodalarc", owner_ref)

        mock_v1.read_namespaced_secret.assert_called_once_with("nodalarc-terminal-keys", "nodalarc")
        mock_v1.create_namespaced_secret.assert_not_called()
        mock_v1.replace_namespaced_secret.assert_not_called()

    def test_terminal_keys_reject_secret_owned_by_previous_cr(self):
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.read_namespaced_secret.return_value = kubernetes.client.V1Secret(
            metadata=kubernetes.client.V1ObjectMeta(
                name="nodalarc-terminal-keys",
                owner_references=[
                    kubernetes.client.V1OwnerReference(
                        api_version="nodalarc.io/v1alpha1",
                        kind="ConstellationSpec",
                        name="current-session",
                        uid="old-uid",
                    )
                ],
            )
        )

        with pytest.raises(RuntimeError, match="not owned by the current ConstellationSpec"):
            _create_terminal_ssh_keys(
                mock_v1,
                "nodalarc",
                {
                    "apiVersion": "nodalarc.io/v1alpha1",
                    "kind": "ConstellationSpec",
                    "name": "current-session",
                    "uid": "new-uid",
                },
            )

        mock_v1.create_namespaced_secret.assert_not_called()

    def test_terminal_keys_reject_secret_that_is_deleting(self):
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.read_namespaced_secret.return_value = kubernetes.client.V1Secret(
            metadata=kubernetes.client.V1ObjectMeta(
                name="nodalarc-terminal-keys",
                deletion_timestamp=datetime.now(UTC),
                owner_references=[
                    kubernetes.client.V1OwnerReference(
                        api_version="nodalarc.io/v1alpha1",
                        kind="ConstellationSpec",
                        name="current-session",
                        uid="test-uid",
                    )
                ],
            )
        )

        with pytest.raises(RuntimeError, match="not owned by the current ConstellationSpec"):
            _create_terminal_ssh_keys(
                mock_v1,
                "nodalarc",
                {
                    "apiVersion": "nodalarc.io/v1alpha1",
                    "kind": "ConstellationSpec",
                    "name": "current-session",
                    "uid": "test-uid",
                },
            )

        mock_v1.create_namespaced_secret.assert_not_called()

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
        mock_v1.read_namespaced_secret.return_value = _existing_terminal_secret("test-uid-456")
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
            context = ensure_session_configmaps(
                spec, "current-session", "nodalarc", owner_ref, session_run_id="run-test-0001"
            )
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
            assert labels.get("nodalarc.io/session-run-id") == "run-test-0001"
            assert labels.get("nodalarc.io/owner-uid") == "test-uid-456"
            assert "nodalarc.io/node-id" in labels
            assert "nodalarc.io/role" in labels
            role = labels["nodalarc.io/role"]
            assert role in ("satellite", "ground-station")
            if role == "satellite":
                assert "nodalarc.io/plane" in labels
                assert "nodalarc.io/slot" in labels

    def test_probe_sidecar_is_not_created_from_retired_session_mi_field(self, tmp_path):
        pods = self._create_pods(tmp_path)
        probe_containers = [
            container
            for pod in pods
            for container in pod.spec.containers
            if container.name == "probe"
        ]

        assert probe_containers == []

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
        mock_v1.read_namespaced_secret.return_value = _existing_terminal_secret()
        mock_v1.create_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(status=409)
        mock_v1.read_namespaced_pod.side_effect = lambda pod_name, _namespace: (
            _existing_session_pod(
                pod_name=pod_name,
                node_id=pod_name,
                uid="test-uid",
                run_id="run-test-0001",
            )
        )
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
            context = ensure_session_configmaps(
                spec, "current-session", "nodalarc", owner_ref, session_run_id="run-test-0001"
            )
            total = ensure_session_pods(context, "nodalarc", owner_ref)
        assert total > 0

    def test_409_conflict_rejects_pod_owned_by_previous_cr(self, tmp_path):
        spec = _make_inline_spec(tmp_path)
        mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        mock_v1.read_namespaced_secret.return_value = _existing_terminal_secret()
        mock_v1.create_namespaced_pod.side_effect = kubernetes.client.rest.ApiException(status=409)
        mock_v1.read_namespaced_pod.side_effect = lambda pod_name, _namespace: (
            _existing_session_pod(
                pod_name=pod_name,
                node_id=pod_name,
                uid="old-uid",
                run_id="run-old-0001",
            )
        )
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
            context = ensure_session_configmaps(
                spec, "current-session", "nodalarc", owner_ref, session_run_id="run-test-0001"
            )
            with pytest.raises(RuntimeError, match="not owned by the current ConstellationSpec"):
                ensure_session_pods(context, "nodalarc", owner_ref)
