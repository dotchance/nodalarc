"""Unit tests for nodalarc_operator/session_deployer.py.

Tests pure-logic functions and the K8s-mocked deploy pipeline with canonical
ref-composed sessions and explicit catalog roots.

Uses create_autospec for K8s client mocks to catch signature drift.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from unittest.mock import create_autospec, patch

import kubernetes.client
import pytest
import yaml
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_upload import CatalogUploadSelection
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.resolved_session import SourceContext
from nodalarc.platform_config import _deterministic_node, compute_pod_placement
from nodalarc.resolve_session import resolve_session_with_assets
from nodalarc.runtime_config import (
    ResolvedRuntimeConfig,
    RuntimeConfigProof,
    RuntimeDeploymentContext,
)
from nodalarc.semantic_projection import resolved_session_semantic_digest
from nodalarc.substrate.manifest_contract import (
    WIRING_MANIFEST_PAYLOAD_KEY,
    decode_wiring_manifest_payload,
)
from nodalarc_operator.session_deployer import (
    _pod_inventory,
    _required_substrate_pairs,
    compute_platform_hash,
    compute_runtime_hash,
    write_wiring_manifest,
)

from tests.catalog_session_fixtures import build_catalog_session_fixture, resolve_catalog_session

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_operator_module_state():
    """Clear all cached state between tests."""
    import nodalarc_operator.session_deployer as sd

    def _explicit_test_resolution(spec, active_session, *, namespace, origin, run_id=None):
        if active_session is not None:
            return active_session
        root_yaml = spec.get("sessionYaml")
        if not isinstance(root_yaml, str) or not root_yaml:
            raise ValueError("spec.sessionYaml is missing")
        resolution = resolve_session_with_assets(
            load_configuration_yaml(root_yaml),
            catalog=_spec_catalog(spec),
            source_context=SourceContext(origin=origin, run_id=run_id),
        )
        digest = "sha256:" + hashlib.sha256(root_yaml.encode()).hexdigest()
        selection = CatalogUploadSelection(
            upload_id="operator-test-upload",
            closure_digest=digest,
            file_count=0,
        )
        return ResolvedRuntimeConfig(
            resolution=resolution,
            proof=RuntimeConfigProof(
                source_origin=origin,
                run_id=run_id,
                upload_id=selection.upload_id,
                document_digest=digest,
                closure_digest=digest,
                resolved_semantic_digest=resolved_session_semantic_digest(resolution.resolved),
                file_count=selection.file_count,
                total_bytes=len(root_yaml.encode()),
                resolved_node_count=len(resolution.resolved.nodes),
            ),
            root_yaml=root_yaml.encode("utf-8"),
            selection=selection,
        )

    sd._v1 = None
    sd._apps_v1 = None
    with patch(
        "nodalarc_operator.session_deployer._operator_session_config",
        side_effect=_explicit_test_resolution,
    ):
        yield
    sd._v1 = None
    sd._apps_v1 = None


def _spec_catalog(spec: dict) -> FilesystemCatalogReadView:
    """Read the shipped catalog unless the test spec carries its own roots."""
    roots = spec.get("_test_catalog_roots")
    if roots is None:
        roots = CatalogRoots.from_catalog_root("catalog/nodalarc")
    return FilesystemCatalogReadView(roots)


def _make_pod_inventory(planes=4, sats_per_plane=3, gs_count=2):
    """Build a minimal pod inventory for placement tests.
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
    constellation_ref="nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
    site_set_ref="nodalarc:site-sets/earth/leo/earth-leo-polar-gateway-sites.yaml",
    protocol="ospf",
    strategy="flat",
    step_seconds=1,
    placement_policy=None,
):
    """Build a segment-session YAML string with configurable fields."""
    d = build_catalog_session_fixture(
        name="test-session",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": ["a"]},
        protocol=protocol,
        extensions=[],
        routing={"area_assignment": {"strategy": strategy}},
        time={"step_seconds": step_seconds},
    )
    if placement_policy:
        d["placement"] = {"policy": placement_policy}
    d["segments"][0]["source"] = constellation_ref
    d["segments"][1]["placement"]["from_site_set"] = site_set_ref
    return yaml.safe_dump(dict(d), sort_keys=False)


# ---------------------------------------------------------------------------
# Class 1: TestPodPlacement
# ---------------------------------------------------------------------------


class TestPodPlacement:
    """Tests compute_pod_placement() - assigns pods to K8s nodes."""

    def test_all_on_one_single_node(self):
        nv = _make_pod_inventory(planes=2, sats_per_plane=3, gs_count=2)
        placement = {"policy": "allOnOne"}
        result = compute_pod_placement(placement, nv, ["node01"])
        assert all(v == "node01" for v in result.values())
        assert len(result) == len(nv)

    def test_all_on_one_ignores_extra_nodes(self):
        nv = _make_pod_inventory(planes=2, sats_per_plane=3, gs_count=2)
        placement = {"policy": "allOnOne"}
        result = compute_pod_placement(placement, nv, ["node01", "node02", "node03", "node04"])
        assert all(v == "node01" for v in result.values())

    def test_plane_per_node_same_plane_same_node(self):
        nv = _make_pod_inventory(planes=4, sats_per_plane=3, gs_count=0)
        placement = {"policy": "planePerNode"}
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        plane0_nodes = {result[nid] for nid, v in nv.items() if v["plane"] == 0}
        plane1_nodes = {result[nid] for nid, v in nv.items() if v["plane"] == 1}
        assert len(plane0_nodes) == 1
        assert len(plane1_nodes) == 1
        assert plane0_nodes != plane1_nodes

    def test_plane_per_node_wraps_modulo(self):
        nv = _make_pod_inventory(planes=6, sats_per_plane=2, gs_count=0)
        placement = {"policy": "planePerNode"}
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        plane0_node = result["sat-P00S00"]
        plane4_node = result["sat-P04S00"]
        assert plane0_node == plane4_node

    def test_plane_per_node_gs_uses_hrw(self):
        nv = _make_pod_inventory(planes=2, sats_per_plane=2, gs_count=7)
        placement = {"policy": "planePerNode"}
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        gs_nodes = {result[nid] for nid in nv if nid.startswith("gs-")}
        assert len(gs_nodes) > 1

    def test_plane_group_per_node_groups(self):
        nv = _make_pod_inventory(planes=4, sats_per_plane=2, gs_count=0)
        placement = {"policy": "planeGroupPerNode", "planes_per_group": 2}
        nodes = ["node01", "node02", "node03", "node04"]
        result = compute_pod_placement(placement, nv, nodes)
        assert result["sat-P00S00"] == result["sat-P01S00"]
        assert result["sat-P02S00"] == result["sat-P03S00"]
        assert result["sat-P00S00"] != result["sat-P02S00"]

    def test_plane_group_per_node_requires_explicit_group_size(self):
        with pytest.raises(ValueError, match="planes_per_group"):
            compute_pod_placement(
                {"policy": "planeGroupPerNode"},
                _make_pod_inventory(planes=1, sats_per_plane=1, gs_count=0),
                ["node01"],
            )

    def test_no_nodes_raises(self):
        nv = _make_pod_inventory(planes=1, sats_per_plane=1, gs_count=0)
        placement = {"policy": "allOnOne"}
        with pytest.raises(ValueError, match="No available"):
            compute_pod_placement(placement, nv, [])

    def test_unknown_policy_rejected_at_parse_boundary(self):
        with pytest.raises(ValueError, match="Unknown placement policy"):
            compute_pod_placement(
                {"policy": "bogus"},
                _make_pod_inventory(planes=1, sats_per_plane=1, gs_count=0),
                ["node01"],
            )


# ---------------------------------------------------------------------------
# Class 2: TestDeterministicNode
# ---------------------------------------------------------------------------


class TestDeterministicNode:
    """Tests _deterministic_node() - HRW hashing for GS placement."""

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


# ---------------------------------------------------------------------------
# Class 3: TestWiringCompletion
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Class 4: TestPlatformHash
# ---------------------------------------------------------------------------


class TestPlatformHash:
    """Tests compute_platform_hash() - determines if platform services need restart."""

    def test_different_constellation_different_hash(self):
        spec1 = {
            "sessionYaml": _make_session_yaml(
                constellation_ref="nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml"
            )
        }
        spec2 = {
            "sessionYaml": _make_session_yaml(
                constellation_ref=(
                    "nodalarc:constellations/earth/leo/earth-leo-walker-delta-176.yaml"
                )
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

    @pytest.mark.parametrize("spec", ({"sessionYaml": ""}, {}))
    def test_empty_session_yaml_is_rejected(self, spec):
        with pytest.raises(ValueError, match="sessionYaml"):
            compute_platform_hash(spec)

    def test_runtime_hash_includes_run_id(self):
        platform_hash = "a" * 64
        assert compute_runtime_hash(platform_hash, "run-a") != compute_runtime_hash(
            platform_hash, "run-b"
        )

    def test_runtime_hash_includes_deployment_release_and_build(self):
        platform_hash = "a" * 64
        digest = "sha256:" + "1" * 64
        first = RuntimeDeploymentContext(
            cr_uid="cr-test",
            cr_generation=1,
            session_run_id="run-a",
            upload_id="operator-test-upload",
            document_digest=digest,
            closure_digest=digest,
            resolved_semantic_digest=digest,
            release="nodalarc-test",
            build="build-a",
        )
        second = RuntimeDeploymentContext.model_validate(
            {**first.model_dump(mode="json"), "build": "build-b"},
            strict=True,
        )

        assert compute_runtime_hash(
            platform_hash, "run-a", deployment_context=first
        ) != compute_runtime_hash(platform_hash, "run-a", deployment_context=second)


# ---------------------------------------------------------------------------
# Class 4: TestExpectedPodCount
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Ref-composed catalog fixture
# ---------------------------------------------------------------------------


def _make_catalog_spec(
    tmp_path,
    protocol="ospf",
    constellation=None,
    ground_stations=None,
    extensions=None,
):
    """Build a root session plus its real temporary catalog roots."""
    const = constellation or {"planes": {"count": 2, "sats_per_plane": 2}}
    gs = ground_stations or {
        "stations": [
            {"name": "alpha", "lat_deg": 34.0, "lon_deg": -118.0, "alt_m": 20},
            {"name": "beta", "lat_deg": 50.0, "lon_deg": 8.0, "alt_m": 100},
        ]
    }

    session = build_catalog_session_fixture(
        name="test-session",
        constellation=const,
        ground_stations=gs,
        protocol=protocol,
        extensions=extensions or [],
        time={"step_seconds": 1},
        base_path=tmp_path,
    )
    return {
        "sessionYaml": yaml.safe_dump(dict(session), sort_keys=False),
        "_test_catalog_roots": session.roots,
    }


def _written_manifest_data(mock_v1) -> dict[str, str]:
    """The wiring manifest ConfigMap data the Operator wrote to the mock K8s client."""
    for call in mock_v1.create_namespaced_config_map.call_args_list:
        body = call[1].get("body") or call[0][1]
        if hasattr(body, "data") and body.data and WIRING_MANIFEST_PAYLOAD_KEY in body.data:
            return body.data
    for call in mock_v1.patch_namespaced_config_map.call_args_list:
        args = call[0] if call[0] else ()
        kwargs = call[1] if call[1] else {}
        body = kwargs.get("body") or (args[2] if len(args) > 2 else None)
        if (
            body
            and hasattr(body, "data")
            and body.data
            and WIRING_MANIFEST_PAYLOAD_KEY in body.data
        ):
            return body.data
    pytest.fail("Wiring manifest ConfigMap not found in mock calls")


def _extract_manifest(mock_v1):
    """The wiring manifest the Operator wrote, decoded through the shared codec."""
    return decode_wiring_manifest_payload(_written_manifest_data(mock_v1))


class _EveryNodeOn(Mapping[str, str]):
    """Placement of every manifest node on one Kubernetes node."""

    def __init__(self, k8s_node: str) -> None:
        self._k8s_node = k8s_node

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str)

    def __getitem__(self, key: str) -> str:
        return self._k8s_node

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# Class 5: TestWiringManifest
# ---------------------------------------------------------------------------


class TestPodInventory:
    """The Operator's pod inventory comes from resolved nodes alone."""

    def test_inventory_carries_kind_grid_position_and_site_for_every_node(self, tmp_path):
        resolved = resolve_catalog_session(
            build_catalog_session_fixture(
                name="inventory-session",
                constellation={"planes": {"count": 2, "sats_per_plane": 2}},
                ground_stations={"stations": ["a", "b"]},
                base_path=tmp_path,
            )
        )

        inventory = _pod_inventory(resolved)

        assert set(inventory) == {node.node_id for node in resolved.nodes}
        for node in resolved.nodes:
            expected = {"node_type": "satellite" if node.kind == "satellite" else "ground_station"}
            if node.kind == "ground_station":
                expected["gs_name"] = node.local_node_id
            if node.plane is not None and node.slot is not None:
                expected.update({"plane": node.plane, "slot": node.slot})
            assert inventory[node.node_id] == expected


class TestWiringManifest:
    """Tests write_wiring_manifest() - the contract between Operator and Node Agent."""

    def _build_and_extract(self, tmp_path, spec=None, *, mock_v1=None, **kwargs):
        if spec is None:
            spec = _make_catalog_spec(tmp_path, **kwargs)
        if mock_v1 is None:
            mock_v1 = create_autospec(kubernetes.client.CoreV1Api, instance=True)
        owner_ref = {
            "apiVersion": "nodalarc.io/v1alpha1",
            "kind": "ConstellationSpec",
            "name": "current-session",
            "uid": "test-uid",
        }
        with (
            patch("nodalarc_operator.session_deployer._get_v1", return_value=mock_v1),
            patch(
                "nodalarc_operator.session_deployer._node_internal_ips",
                side_effect=lambda _v1, required: dict.fromkeys(required, "10.0.0.1"),
            ),
        ):
            mock_v1.list_node.return_value = kubernetes.client.V1NodeList(
                items=[
                    kubernetes.client.V1Node(
                        metadata=kubernetes.client.V1ObjectMeta(name=name),
                        spec=kubernetes.client.V1NodeSpec(pod_cidr=cidr),
                    )
                    for name, cidr in (
                        ("node01", "10.42.0.0/22"),
                        ("node02", "10.42.12.0/22"),
                        ("node03", "10.42.16.0/22"),
                    )
                ]
            )
            write_wiring_manifest(
                spec, "nodalarc", owner_ref, "run-test-0001", None, None, _EveryNodeOn("node01")
            )
        return _extract_manifest(mock_v1)

    def test_manifest_requires_runtime_session_id(self, tmp_path):
        spec = _make_catalog_spec(tmp_path)

        with pytest.raises(ValueError, match="session_run_id is required"):
            write_wiring_manifest(spec, "nodalarc", None, "", None, None, _EveryNodeOn("node01"))


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
            site_lans={},
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
            site_lans={},
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
            site_lans={},
            nodes=nodes,
            isl_pairs={("sat-a", "sat-b")},
            pod_placement={"sat-a": "node01", "sat-b": "node02", "gs-den": "node02"},
            node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2"},
        )

        by_key = {pair["directional_key"]: pair for pair in pairs}
        assert set(by_key) == {"node01->node02", "node02->node01"}
        assert by_key["node01->node02"]["reasons"] == ["ground", "isl"]
        assert by_key["node02->node01"]["reasons"] == ["ground", "isl"]

    def test_cross_host_site_lan_members_emit_both_directions(self):
        nodes = {
            "gs-a": {"node_type": "ground_station"},
            "gs-b": {"node_type": "ground_station"},
            "host-c": {"node_type": "host"},
        }
        site_lans = {
            "site-lan0": {
                "members": [
                    {"node_id": "gs-a"},
                    {"node_id": "gs-b"},
                    {"node_id": "host-c"},
                ]
            }
        }
        pairs = _required_substrate_pairs(
            site_lans=site_lans,
            nodes=nodes,
            isl_pairs=set(),
            pod_placement={"gs-a": "node01", "gs-b": "node02", "host-c": "node01"},
            node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2"},
            ground_candidate_satellites_by_gs={},
        )

        # gs-a and host-c share node01, so only the node01/node02 path is required.
        assert {pair["directional_key"] for pair in pairs} == {
            "node01->node02",
            "node02->node01",
        }
        assert all(pair["reasons"] == ["site_lan"] for pair in pairs)

    def test_resolved_candidate_map_scopes_active_ground_universe(self):
        nodes = {
            "sat-a": {"node_type": "satellite"},
            "sat-b": {"node_type": "satellite"},
            "gs-leo": {"node_type": "ground_station"},
            "gs-meo-unused": {"node_type": "ground_station"},
        }
        pairs = _required_substrate_pairs(
            site_lans={},
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
                site_lans={},
                nodes={"sat-a": {"node_type": "satellite"}},
                isl_pairs=set(),
                pod_placement={"sat-a": "node01", "gs-missing": "node02"},
                node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2"},
                ground_candidate_satellites_by_gs={"gs-missing": ("sat-a",)},
            )

    def test_resolved_candidate_map_rejects_unknown_satellite_node(self):
        with pytest.raises(ValueError, match="unknown substrate candidate satellite"):
            _required_substrate_pairs(
                site_lans={},
                nodes={"gs-den": {"node_type": "ground_station"}},
                isl_pairs=set(),
                pod_placement={"gs-den": "node01", "sat-missing": "node02"},
                node_ips={"node01": "10.0.0.1", "node02": "10.0.0.2"},
                ground_candidate_satellites_by_gs={"gs-den": ("sat-missing",)},
            )


# ---------------------------------------------------------------------------
# Class 7: TestConfigRendering
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Class 6: TestPodSpec
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Retired session services: waited for until every bound pod is gone
# ---------------------------------------------------------------------------
