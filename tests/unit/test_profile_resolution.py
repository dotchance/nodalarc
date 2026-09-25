# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Effective workload-profile resolution: three levels, provenance, refusal."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_closure import CatalogDocumentNotFound, FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.resolve_session import SessionResolutionError, resolve_session
from nodalarc.runtime_support import UnsupportedFeatureError
from nodalarc.workloads.adapter import AdapterRenderRefusal

from tests.catalog_session_fixtures import shipped_read_view

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
QUIC_SESSION = SHIPPED_ROOT / "sessions" / "earth-luna-quic.yaml"
BASE_SITE = SHIPPED_ROOT / "sites" / "earth" / "de" / "earth-de-berlin.yaml"
BASE_NODE = SHIPPED_ROOT / "nodes" / "ground" / "starlink-gateway.yaml"

FRR_PROFILE = "nodalarc:profiles/frr-router.yaml"
USER_PROFILE = "user:profiles/override-profile.yaml"


def _write_yaml(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _user_profile_document(profile_id: str) -> dict[str, Any]:
    return {
        "profile": {
            "id": profile_id,
            "registry": "registry.example",
            "image": f"nodalarc/base@sha256:{'0' * 64}",
            "command": ["/bin/bash", "-c", "sleep infinity"],
            "resources": {
                "requests": {"cpu_m": 10, "memory_mi": 16},
                "limits": {"cpu_m": 100, "memory_mi": 64},
            },
        }
    }


def _session_with_user_ground(
    tmp_path: Path,
    *,
    site_mutation=None,
    node_mutation=None,
    segment_mutation=None,
) -> tuple[dict[str, Any], CatalogRoots]:
    user_root = tmp_path / "user"
    _write_yaml(
        user_root / "profiles" / "override-profile.yaml", _user_profile_document("override-profile")
    )

    node_document = yaml.safe_load(BASE_NODE.read_text(encoding="utf-8"))
    node_document["node"]["id"] = "profile-test-node"
    if node_mutation is not None:
        node_mutation(node_document["node"])
    _write_yaml(user_root / "nodes" / "profile-test-node.yaml", node_document)

    site_document = yaml.safe_load(BASE_SITE.read_text(encoding="utf-8"))
    site_document["site"]["id"] = "profile-test-site"
    for site_node in site_document["site"]["nodes"]:
        site_node["node"] = "user:nodes/profile-test-node.yaml"
    if site_mutation is not None:
        site_mutation(site_document["site"])
    _write_yaml(user_root / "sites" / "profile-test-site.yaml", site_document)
    _write_yaml(
        user_root / "site-sets" / "profile-test-sites.yaml",
        {"site_set": {"id": "profile-test-sites", "sites": ["user:sites/profile-test-site.yaml"]}},
    )

    session = yaml.safe_load(SIMPLE_SESSION.read_text(encoding="utf-8"))
    ground_segment = next(segment for segment in session["segments"] if "placement" in segment)
    ground_segment["placement"]["from_site_set"] = "user:site-sets/profile-test-sites.yaml"
    if segment_mutation is not None:
        segment_mutation(session, ground_segment)
    roots = CatalogRoots.from_catalog_root(SHIPPED_ROOT, user_root=user_root)
    return session, roots


def test_every_shipped_simple_session_node_inherits_its_definition_default() -> None:
    resolution = resolve_session(
        yaml.safe_load(SIMPLE_SESSION.read_text(encoding="utf-8")), catalog=shipped_read_view()
    )

    assert resolution.nodes
    for node in resolution.nodes:
        assert node.profile == FRR_PROFILE
        assert node.profile_level == "node_definition"


def test_segment_profile_overrides_the_node_definition(tmp_path: Path) -> None:
    def override_segment(session, ground_segment):
        ground_segment["profile"] = USER_PROFILE

    session, roots = _session_with_user_ground(tmp_path, segment_mutation=override_segment)
    resolution = resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    ground = [node for node in resolution.nodes if node.kind == "ground_station"]
    assert ground
    for node in ground:
        assert node.profile == USER_PROFILE
        assert node.profile_level == "segment"
    satellites = [node for node in resolution.nodes if node.kind == "satellite"]
    assert all(node.profile_level == "node_definition" for node in satellites)


def test_placed_node_profile_overrides_segment_and_definition(tmp_path: Path) -> None:
    def override_segment(session, ground_segment):
        ground_segment["profile"] = FRR_PROFILE

    def override_site(site):
        site["nodes"][0]["profile"] = USER_PROFILE

    session, roots = _session_with_user_ground(
        tmp_path, segment_mutation=override_segment, site_mutation=override_site
    )
    resolution = resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    overridden = [node for node in resolution.nodes if node.profile == USER_PROFILE]
    assert len(overridden) == 1
    assert overridden[0].profile_level == "node"


def test_missing_profile_at_every_level_is_refused(tmp_path: Path) -> None:
    def strip_default(node):
        node.pop("profile", None)

    session, roots = _session_with_user_ground(tmp_path, node_mutation=strip_default)

    with pytest.raises(SessionResolutionError, match="no workload profile at any level"):
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))


def test_conflicting_shared_site_segment_profiles_are_refused(tmp_path: Path) -> None:
    def add_conflicting_segment(session, ground_segment):
        ground_segment["profile"] = USER_PROFILE
        duplicate = dict(ground_segment)
        duplicate["id"] = "ground-two"
        duplicate["profile"] = FRR_PROFILE
        session["segments"].append(duplicate)

    session, roots = _session_with_user_ground(tmp_path, segment_mutation=add_conflicting_segment)

    with pytest.raises(SessionResolutionError, match="conflicting\\s+profile statements"):
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))


def test_profile_reference_must_load_a_profile_document(tmp_path: Path) -> None:
    # A dangling profile reference fails the same way every dangling catalog
    # reference fails today: loudly, from the reference resolver. The
    # deployment loader rejects it with its own typed message before
    # resolution in the real path.
    def dangling(site):
        site["nodes"][0]["profile"] = "user:profiles/absent.yaml"

    session, roots = _session_with_user_ground(tmp_path, site_mutation=dangling)

    with pytest.raises(CatalogDocumentNotFound):
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    def wrong_family(site):
        site["nodes"][0]["profile"] = "user:profiles/wrong-family.yaml"

    session, roots = _session_with_user_ground(tmp_path / "wrong", site_mutation=wrong_family)
    _write_yaml(
        (tmp_path / "wrong" / "user") / "profiles" / "wrong-family.yaml",
        {
            "node": {
                "id": "wrong-family",
                "forwarding": "host",
                "ethernet": [],
                "terminals": [],
                "payloads": [],
            }
        },
    )

    with pytest.raises(SessionResolutionError):
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))


def test_non_router_workload_on_a_routed_bus_stands_outside_domains(tmp_path: Path) -> None:
    # The probe-01 pattern: a routed-wired bus running a non-routing workload
    # resolves, and the node simply is not a router, so no domain contains it.
    def override_site(site):
        site["nodes"][0]["profile"] = "nodalarc:profiles/linux-host.yaml"

    session, roots = _session_with_user_ground(tmp_path, site_mutation=override_site)
    resolution = resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    observers = [
        node for node in resolution.nodes if node.profile == "nodalarc:profiles/linux-host.yaml"
    ]
    assert len(observers) == 1
    member_ids = {node_id for domain in resolution.routing_domains for node_id in domain.node_ids}
    assert observers[0].node_id not in member_ids
    routers = [node for node in resolution.nodes if node.profile == FRR_PROFILE]
    assert routers
    assert all(router.node_id in member_ids for router in routers)


def test_profile_naming_an_unavailable_adapter_is_refused(tmp_path: Path) -> None:
    def use_bogus_adapter(site):
        site["nodes"][0]["profile"] = "user:profiles/bogus-adapter.yaml"

    session, roots = _session_with_user_ground(tmp_path, site_mutation=use_bogus_adapter)
    bogus = _user_profile_document("bogus-adapter")
    bogus["profile"]["adapter"] = "bogus"
    _write_yaml(tmp_path / "user" / "profiles" / "bogus-adapter.yaml", bogus)

    with pytest.raises(UnsupportedFeatureError, match="workload adapter 'bogus'"):
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))


def test_env_value_from_resolves_and_refuses_honestly(tmp_path: Path) -> None:
    def with_env(site):
        site["nodes"][0]["tags"] = list(site["nodes"][0].get("tags") or []) + ["env_target"]

    def profile_with_env(entries):
        document = _user_profile_document("override-profile")
        document["profile"]["env"] = entries
        return document

    good = [
        {
            "name": "PEER",
            "value_from": {"tag": "env_target", "interface": "terr0", "family": "ipv4"},
        },
        {"name": "MODE", "value": "test"},
    ]
    session, roots = _session_with_user_ground(tmp_path, site_mutation=with_env)
    _write_yaml(tmp_path / "user" / "profiles" / "override-profile.yaml", profile_with_env(good))
    session["segments"][1]["profile"] = USER_PROFILE
    resolution = resolve_session(session, catalog=FilesystemCatalogReadView(roots))
    assert resolution.nodes

    bad = [
        {
            "name": "PEER",
            "value_from": {"tag": "absent_tag", "interface": "terr0", "family": "ipv4"},
        }
    ]
    session, roots = _session_with_user_ground(tmp_path / "bad", site_mutation=with_env)
    _write_yaml(
        tmp_path / "bad" / "user" / "profiles" / "override-profile.yaml", profile_with_env(bad)
    )
    session["segments"][1]["profile"] = USER_PROFILE
    with pytest.raises(SessionResolutionError, match="matches no node"):
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    # Allocated segments always carry both families, so the live refusal
    # for a bad interface reference is the missing-interface one.
    no_interface = [
        {"name": "PEER", "value_from": {"tag": "env_target", "interface": "bus7", "family": "ipv4"}}
    ]
    session, roots = _session_with_user_ground(tmp_path / "noif", site_mutation=with_env)
    _write_yaml(
        tmp_path / "noif" / "user" / "profiles" / "override-profile.yaml",
        profile_with_env(no_interface),
    )
    session["segments"][1]["profile"] = USER_PROFILE
    with pytest.raises(SessionResolutionError, match="has no interface"):
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))


def test_shipped_quic_session_records_node_level_endpoint_profiles() -> None:
    resolution = resolve_session(
        yaml.safe_load(QUIC_SESSION.read_text(encoding="utf-8")), catalog=shipped_read_view()
    )

    by_profile: dict[str, list[str]] = {}
    for node in resolution.nodes:
        by_profile.setdefault(node.profile, []).append(node.node_id)

    assert any("picoquic-client" in profile for profile in by_profile)
    assert any("picoquic-server" in profile for profile in by_profile)
    endpoint_nodes = [node for node in resolution.nodes if "picoquic" in node.profile]
    assert endpoint_nodes
    # Endpoint members inherit their profile from the mounted payload
    # object, the definition level of the three-level rule.
    assert all(node.profile_level == "node_definition" for node in endpoint_nodes)
    assert all("-quic-" in node.node_id for node in endpoint_nodes)
    routers = [node for node in resolution.nodes if node.profile == FRR_PROFILE]
    assert routers
    assert all(node.profile_level == "node_definition" for node in routers)


def _narrow_adapter_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: dict):
    """One site node runs a routing adapter that renders plain IS-IS only."""
    import nodalarc.runtime_support as runtime_support
    from nodalarc.workloads.adapter import AdapterSupport, RoutingProtocolSupport

    from adapters.frr.support import FRR_SUPPORT

    monkeypatch.setattr(
        runtime_support,
        "registered_adapter_support",
        lambda: {
            "frr": FRR_SUPPORT,
            "narrow": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                        link_rate_floor_mbps=None,
                    )
                }
            ),
        },
    )

    def use_narrow_adapter(site):
        site["nodes"][0]["profile"] = "user:profiles/narrow-router.yaml"

    def add_domain(session, _ground_segment):
        session["routing"] = {"domains": [domain]}

    session, roots = _session_with_user_ground(
        tmp_path, site_mutation=use_narrow_adapter, segment_mutation=add_domain
    )
    narrow = _user_profile_document("narrow-router")
    narrow["profile"]["adapter"] = "narrow"
    _write_yaml(tmp_path / "user" / "profiles" / "narrow-router.yaml", narrow)
    return session, roots


def _domain(**fields) -> dict[str, Any]:
    return {
        "id": "earth_domain",
        "protocol": "isis",
        "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
        **fields,
    }


def test_member_whose_adapter_lacks_a_capability_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, roots = _narrow_adapter_session(
        tmp_path, monkeypatch, _domain(capabilities={"mpls": {}})
    )

    with pytest.raises(UnsupportedFeatureError) as refused:
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    [feature] = refused.value.features
    assert feature.category == "routing_capability"
    assert feature.value == "isis:mpls"
    assert "routing domain 'earth_domain'" in feature.message
    assert "workload adapter 'narrow'" in feature.message
    assert "profile-test-site-" in feature.message


def test_member_whose_adapter_lacks_the_protocol_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, roots = _narrow_adapter_session(tmp_path, monkeypatch, _domain(protocol="ospf"))

    with pytest.raises(UnsupportedFeatureError) as refused:
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    [feature] = refused.value.features
    assert feature.category == "routing_protocol"
    assert feature.value == "ospf"
    assert "workload adapter 'narrow'" in feature.message


def test_members_rendering_the_domain_join_it_whatever_their_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, roots = _narrow_adapter_session(tmp_path, monkeypatch, _domain())

    resolution = resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    narrow = [node for node in resolution.nodes if node.profile.endswith("narrow-router.yaml")]
    [domain] = resolution.routing_domains
    assert len(narrow) == 1
    assert narrow[0].node_id in domain.node_ids


def test_bfd_timers_outside_the_frr_range_are_refused(tmp_path: Path) -> None:
    def add_domain(session, _ground_segment):
        session["routing"] = {
            "domains": [
                _domain(
                    timers={
                        "bfd": {
                            "enabled": True,
                            "detect_multiplier": 300,
                            "rx_interval_ms": 5,
                            "tx_interval_ms": 300,
                        }
                    }
                )
            ]
        }

    session, roots = _session_with_user_ground(tmp_path, segment_mutation=add_domain)

    with pytest.raises(UnsupportedFeatureError) as refused:
        resolve_session(session, catalog=FilesystemCatalogReadView(roots))

    assert [feature.value for feature in refused.value.features] == [
        "bfd.detect_multiplier=300",
        "bfd.rx_interval_ms=5",
    ]
    assert all("workload adapter 'frr'" in f.message for f in refused.value.features)


def _host_endpoint_fixture():
    from tests.catalog_session_fixtures import build_catalog_session_fixture

    return build_catalog_session_fixture(
        name="forwarding-agreement",
        constellation={"planes": {"count": 1, "sats_per_plane": 2}},
        ground_stations={"stations": [{}], "host_endpoints": True},
    )


def test_a_host_may_participate_in_routing_without_being_a_router() -> None:
    # A host running a routing workload speaks the protocol but does not
    # forward, so it participates and is never a gateway.
    fixture = _host_endpoint_fixture()
    [payload_path] = sorted((fixture.roots.user_root / "payloads").glob("*.yaml"))
    payload = yaml.safe_load(payload_path.read_text(encoding="utf-8"))
    payload["payload"]["profile"] = FRR_PROFILE
    _write_yaml(payload_path, payload)

    resolution = resolve_session(fixture, catalog=FilesystemCatalogReadView(fixture.roots))

    [host] = [node for node in resolution.nodes if node.forwarding == "host"]
    assert resolution.routing_domains_for(host.node_id)
    assert resolution.node_roles()[host.node_id] == "host"
    assert host.host_attachment is not None
    gateway = resolution.node_by_id(host.host_attachment.gateway_node_id)
    assert gateway is not None and gateway.forwarding == "routed"


def test_a_host_gateway_is_always_a_router() -> None:
    # The site's only routed node runs no routing workload, so it forwards
    # but is not a router, and the host on its LAN has no gateway.
    fixture = _host_endpoint_fixture()
    for ref in fixture.site_refs:
        site = fixture.read_catalog(ref)
        for node in site["site"]["nodes"]:
            node["profile"] = "nodalarc:profiles/linux-host.yaml"
        fixture.write_catalog(ref, site)

    with pytest.raises(SessionResolutionError, match="has no router on its segment"):
        resolve_session(fixture, catalog=FilesystemCatalogReadView(fixture.roots))


@pytest.mark.parametrize("forwarding", ["bridge", "control_only"])
def test_an_unexecuted_forwarding_class_is_refused_with_a_typed_reason(forwarding) -> None:
    from nodalarc.runtime_support import FeatureCategory

    fixture = _host_endpoint_fixture()
    node_document = fixture.read_catalog(fixture.space_node_ref)
    node_document["node"]["forwarding"] = forwarding
    fixture.write_catalog(fixture.space_node_ref, node_document)

    with pytest.raises(UnsupportedFeatureError) as refused:
        resolve_session(fixture, catalog=FilesystemCatalogReadView(fixture.roots))

    assert {(feature.category, feature.value) for feature in refused.value.features} == {
        (FeatureCategory.FORWARDING_CLASS, forwarding)
    }


def test_a_routed_node_without_a_routing_workload_participates_in_no_domain(tmp_path: Path) -> None:
    from nodalarc.workloads.adapter import SessionContext

    from adapters.frr.adapter import FrrAdapter

    def override_site(site):
        site["nodes"][0]["profile"] = "nodalarc:profiles/linux-host.yaml"

    session, roots = _session_with_user_ground(tmp_path, site_mutation=override_site)
    resolution = resolve_session(session, catalog=FilesystemCatalogReadView(roots))
    [probe] = [
        node for node in resolution.nodes if node.profile == "nodalarc:profiles/linux-host.yaml"
    ]

    assert probe.forwarding == "routed"
    assert resolution.routing_domains_for(probe.node_id) == ()
    assert resolution.node_roles()[probe.node_id] == "forwarding_only"
    with pytest.raises(AdapterRenderRefusal, match="participates in no routing domain"):
        FrrAdapter().render_node(probe, SessionContext(resolution))
