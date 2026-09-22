import base64
import gzip

import pytest
from nodalarc.substrate.manifest_contract import (
    REQUIRED_WIRING_PHASES,
    WIRING_MANIFEST_PAYLOAD_KEY,
    WiringManifest,
    WiringManifestPayloadError,
    decode_wiring_manifest,
    decode_wiring_manifest_payload,
    derive_wiring_generation,
    encode_wiring_manifest_payload,
)
from pydantic import ValidationError


def _manifest():
    return {
        "session_id": "demo",
        "session_run_id": "run-demo-0001",
        "owner_uid": "owner-uid-1",
        "wiring_generation": "sha256:" + "a" * 64,
        "required_phases": list(REQUIRED_WIRING_PHASES),
        "nodes": {
            "sat-a": {
                "node_type": "satellite",
                "host": "node02",
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
                "host": "node02",
                "gs_name": "den",
                "gs_index": 0,
                "sysctls": {"net.ipv6.conf.all.forwarding": "1"},
                "isl_interfaces": [],
                "gnd_interfaces": [{"name": "term0"}],
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
        "session_run_id": data["session_run_id"],
        "owner_uid": data["owner_uid"],
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


# --- ConfigMap wire encoding -------------------------------------------------


def _stdlib_decode(encoded: str) -> bytes:
    """The encoded payload's JSON bytes, read without the codec under test."""
    return gzip.decompress(base64.b64decode(encoded))


def _payload(raw: bytes) -> dict[str, str]:
    return {WIRING_MANIFEST_PAYLOAD_KEY: base64.b64encode(gzip.compress(raw)).decode()}


def test_encoded_bytes_are_the_existing_wire_format() -> None:
    """Sorted keys, compact separators, non-ASCII escaped: pinned byte for byte."""
    encoded = encode_wiring_manifest_payload({"b": "Zürich", "a": [1, {"d": None, "c": True}]})

    assert _stdlib_decode(encoded) == (b'{"a":[1,{"c":true,"d":null}],"b":"Z\\u00fcrich"}')


def test_payload_escapes_non_ascii_while_the_generation_hash_does_not() -> None:
    data = _manifest()
    data["nodes"]["gs-den"]["gs_name"] = "Zürich"
    encoded = encode_wiring_manifest_payload(data)

    assert b"Z\\u00fcrich" in _stdlib_decode(encoded)
    ascii_material = dict(data)
    ascii_material["nodes"] = {
        **data["nodes"],
        "gs-den": {**data["nodes"]["gs-den"], "gs_name": "Z\\u00fcrich"},
    }
    # The hash reads the characters themselves, so the escaped spelling differs.
    assert derive_wiring_generation(data) != derive_wiring_generation(ascii_material)


def test_round_trip_through_the_codec() -> None:
    data = _manifest()
    decoded = decode_wiring_manifest(
        {WIRING_MANIFEST_PAYLOAD_KEY: encode_wiring_manifest_payload(data)}
    )

    assert decoded == WiringManifest.model_validate(data)


@pytest.mark.parametrize(
    ("data", "stage"),
    [
        (None, "missing"),
        ({}, "missing"),
        ({WIRING_MANIFEST_PAYLOAD_KEY: ""}, "missing"),
        ({"manifest.json": "{}"}, "missing"),
        ({WIRING_MANIFEST_PAYLOAD_KEY: "not base64!"}, "base64"),
        ({WIRING_MANIFEST_PAYLOAD_KEY: base64.b64encode(b"plain").decode()}, "gzip"),
        (_payload(b"\xff\xfe{}"), "utf-8"),
        (_payload("{}".encode("utf-16")), "utf-8"),
        (_payload("{}".encode("utf-32")), "utf-8"),
        (_payload(b"\xef\xbb\xbf{}"), "json"),
        (_payload(b"{not json"), "json"),
        pytest.param(
            _payload(b'{"n": ' + b"9" * 4301 + b"}"), "json", id="json-integer-digit-limit"
        ),
        (_payload(b"[1, 2]"), "shape"),
        (_payload(b'"text"'), "shape"),
    ],
)
def test_each_decode_failure_is_refused_at_its_stage(data, stage) -> None:
    with pytest.raises(WiringManifestPayloadError) as raised:
        decode_wiring_manifest_payload(data)

    assert raised.value.stage == stage
    assert f"refused at {stage}: " in str(raised.value)
    if stage in ("missing", "shape"):
        assert raised.value.__cause__ is None
    else:
        # The underlying diagnostic is chained and also in the logged message.
        assert raised.value.__cause__ is not None
        assert str(raised.value.__cause__) in str(raised.value)


def test_the_uncompressed_manifest_key_is_not_read() -> None:
    with pytest.raises(WiringManifestPayloadError, match="missing manifest.json.gz.b64"):
        decode_wiring_manifest_payload({"manifest.json": '{"session_id": "demo"}'})


def test_decode_payload_does_not_validate_the_model() -> None:
    encoded = encode_wiring_manifest_payload({"nodes": {}})

    assert decode_wiring_manifest_payload({WIRING_MANIFEST_PAYLOAD_KEY: encoded}) == {"nodes": {}}


def test_decode_manifest_validates_the_model() -> None:
    encoded = encode_wiring_manifest_payload({"nodes": {}})

    with pytest.raises(ValidationError):
        decode_wiring_manifest({WIRING_MANIFEST_PAYLOAD_KEY: encoded})
