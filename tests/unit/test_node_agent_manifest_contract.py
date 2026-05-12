import pytest
from nodalarc.substrate.manifest_contract import REQUIRED_WIRING_PHASES, WiringManifest
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
        "isl_link_count": 0,
    }


def test_manifest_contract_accepts_strict_ground_bridge_specs() -> None:
    manifest = WiringManifest.model_validate(_manifest())

    assert set(manifest.ground_bridges) == {"gs-den"}


def test_manifest_contract_rejects_untyped_ground_bridge_fields() -> None:
    data = _manifest()
    data["ground_bridges"]["gs-den"] = {"unexpected": True}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WiringManifest.model_validate(data)


def test_manifest_contract_enforces_satellite_identity_fields() -> None:
    data = _manifest()
    data["nodes"]["sat-a"].pop("plane")

    with pytest.raises(ValidationError, match="satellite nodes require plane and slot"):
        WiringManifest.model_validate(data)


def test_manifest_contract_enforces_ground_station_identity_fields() -> None:
    data = _manifest()
    data["nodes"]["gs-den"].pop("gs_name")

    with pytest.raises(ValidationError, match="ground_station nodes require gs_name and gs_index"):
        WiringManifest.model_validate(data)


def test_manifest_contract_requires_security_phase() -> None:
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
