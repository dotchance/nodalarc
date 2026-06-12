import pytest
from nodalarc.substrate.manifest_contract import (
    REQUIRED_WIRING_PHASES,
    WiringManifest,
    derive_wiring_generation,
)
from pydantic import ValidationError


def _manifest():
    return {
        "session_id": "demo",
        "wiring_generation": "sha256:" + "a" * 64,
        "required_phases": list(REQUIRED_WIRING_PHASES),
        "nodes": {
            "sat-a": {
                "node_type": "satellite",
                "plane": 0,
                "slot": 0,
                "sysctls": {"net.ipv6.conf.all.forwarding": "1"},
                "isl_interfaces": [],
                "gnd_interfaces": [],
                "mpls_enable": True,
                "segment_routing": False,
                "mtu": 9000,
                "remove_default_route": True,
            },
            "gs-den": {
                "node_type": "ground_station",
                "gs_name": "den",
                "gs_index": 0,
                "sysctls": {"net.ipv6.conf.all.forwarding": "1"},
                "isl_interfaces": [],
                "gnd_interfaces": [{"name": "term0"}],
                "terrestrial": {"addresses": []},
                "mpls_enable": True,
                "segment_routing": False,
                "mtu": 9000,
                "remove_default_route": True,
            },
        },
        "ground_bridges": {"gs-den": {}},
        "site_lans": {},
        "required_substrate_pairs": [],
        "isl_link_count": 0,
    }


def test_manifest_contract_accepts_strict_ground_bridge_specs() -> None:
    manifest = WiringManifest.model_validate(_manifest())

    assert set(manifest.ground_bridges) == {"gs-den"}


def test_wiring_generation_canonicalizes_keys_and_ignores_existing_generation() -> None:
    data = _manifest()
    first = derive_wiring_generation(data)
    reordered = {
        "wiring_generation": "sha256:" + "f" * 64,
        "required_substrate_pairs": data["required_substrate_pairs"],
        "ground_bridges": data["ground_bridges"],
        "nodes": dict(reversed(list(data["nodes"].items()))),
        "required_phases": data["required_phases"],
        "site_lans": data["site_lans"],
        "isl_link_count": data["isl_link_count"],
        "session_id": data["session_id"],
    }

    assert derive_wiring_generation(reordered) == first


def test_manifest_contract_rejects_untyped_ground_bridge_fields() -> None:
    data = _manifest()
    data["ground_bridges"]["gs-den"] = {"unexpected": True}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WiringManifest.model_validate(data)


def test_manifest_contract_rejects_half_set_grid_coordinates() -> None:
    # plane without slot (or vice versa) is corruption, not a shape.
    data = _manifest()
    data["nodes"]["sat-a"].pop("plane")

    with pytest.raises(ValidationError, match="set together or not at all"):
        WiringManifest.model_validate(data)


def test_manifest_contract_accepts_non_grid_satellite() -> None:
    # Individually placed satellites (GEO longitude slots, state vectors)
    # carry no grid coordinates at all — a legitimate resolved shape.
    data = _manifest()
    data["nodes"]["sat-a"].pop("plane")
    data["nodes"]["sat-a"].pop("slot")

    manifest = WiringManifest.model_validate(data)
    assert manifest.nodes["sat-a"].plane is None
    assert manifest.nodes["sat-a"].slot is None


def test_manifest_contract_enforces_ground_station_identity_fields() -> None:
    data = _manifest()
    data["nodes"]["gs-den"].pop("gs_name")

    with pytest.raises(ValidationError, match="ground_station nodes require gs_name and gs_index"):
        WiringManifest.model_validate(data)


def test_manifest_contract_requires_security_stage() -> None:
    assert "pod_security" in REQUIRED_WIRING_PHASES
    data = _manifest()
    data["required_phases"].remove("pod_security")

    with pytest.raises(ValidationError, match="required_phases missing: pod_security"):
        WiringManifest.model_validate(data)


def test_manifest_contract_ground_bridges_match_ground_station_nodes() -> None:
    data = _manifest()
    data["ground_bridges"] = {}

    with pytest.raises(ValidationError, match="ground_bridges must exactly match"):
        WiringManifest.model_validate(data)


def test_manifest_contract_requires_unique_substrate_pair_directions() -> None:
    data = _manifest()
    pair = {
        "source_node": "node-a",
        "source_ip": "10.0.0.1",
        "target_node": "node-b",
        "target_ip": "10.0.0.2",
        "reasons": ["isl"],
        "pair_key": "node-a<->node-b",
        "directional_key": "node-a->node-b",
    }
    data["required_substrate_pairs"] = [pair, dict(pair)]

    with pytest.raises(
        ValidationError, match="required_substrate_pairs must not contain duplicate directions"
    ):
        WiringManifest.model_validate(data)
