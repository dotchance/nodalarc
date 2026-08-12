from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from nodalarc.models.catalog import Constellation, Node, Payload, Site, SiteSet, SpaceNodeSet
from nodalarc.models.segments import ConfiguredStateLagrange, LagrangeFrame
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def _payload_data() -> dict[str, Any]:
    return {
        "id": "test-payload",
        "forwarding": "host",
        "profile": "nodalarc:profiles/test.yaml",
    }


def _node_data() -> dict[str, Any]:
    return {
        "id": "test-node",
        "forwarding": "routed",
        "ethernet": [{"id": "lan"}],
        "terminals": [
            {
                "id": "access",
                "role": "access",
                "terminal": "nodalarc:terminals/test.yaml",
                "count": 1,
            }
        ],
        "payloads": [
            {
                "id": "science",
                "payload": "nodalarc:payloads/test.yaml",
                "count": 1,
                "attach": "lan",
            }
        ],
    }


def _site_node_data(node_id: str, index: int) -> dict[str, Any]:
    del index
    return {
        "id": node_id,
        "node": "nodalarc:nodes/test.yaml",
        "terminals": {},
        "payloads": {},
        "interfaces": {"terr0": "lan0"},
    }


def _site_data() -> dict[str, Any]:
    return {
        "id": "test-site",
        "ethernet": [{"id": "lan0"}],
        "nodes": [_site_node_data("router-a", 1), _site_node_data("router-b", 2)],
        "frame": {"body_fixed": {"body": "nodalarc:bodies/earth.yaml"}},
        "location": {"lat_deg": 0, "lon_deg": 0, "alt_m": 0},
    }


def _constellation_data() -> dict[str, Any]:
    return {
        "id": "test-constellation",
        "node": "nodalarc:nodes/test.yaml",
        "orbit": "nodalarc:orbits/test.yaml",
        "planes": {"count": 2, "raan_spacing_deg": 180},
        "slots_per_plane": 2,
        "phasing": {"mode": "walker_delta", "phase_offset_deg": 90},
        "node_tags": [{"tag": "all"}],
    }


def _space_node_set_data() -> dict[str, Any]:
    return {
        "id": "test-space-nodes",
        "nodes": [
            {
                "id": "relay-a",
                "node": "nodalarc:nodes/test.yaml",
                "orbit": "nodalarc:orbits/test.yaml",
            },
            {
                "id": "relay-b",
                "node": "nodalarc:nodes/test.yaml",
                "orbit": "nodalarc:orbits/test.yaml",
            },
        ],
    }


def test_no_user_catalog_or_obsolete_example_roots_remain() -> None:
    assert not (ROOT / "catalog" / "user").exists()
    assert not (ROOT / "sessions").exists()
    for child in (
        "constellations",
        "ground-stations",
        "presets",
        "satellite-types",
        "scenarios",
        "sessions",
    ):
        assert not (ROOT / "configs" / child).exists(), child


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"forwarding": None}, "forwarding"),
        ({"profile": None}, "profile"),
        ({"terminal_slots": []}, "Extra inputs are not permitted"),
        ({"ethernet": []}, "Extra inputs are not permitted"),
        ({"payloads": []}, "Extra inputs are not permitted"),
    ],
)
def test_payload_is_a_carried_environment_and_nothing_else(
    mutation: dict[str, Any],
    message: str,
) -> None:
    # A payload is forwarding + profile. It structurally cannot carry
    # terminal slots, Ethernet ports, or payload mounts, so composition
    # cannot recurse.
    payload = _payload_data()
    payload.update(mutation)

    with pytest.raises(ValidationError, match=message):
        Payload.model_validate(payload)


def test_payload_mount_attach_must_name_a_declared_port() -> None:
    node = _node_data()
    node["payloads"][0]["attach"] = "bus9"

    with pytest.raises(ValidationError, match="attach must name a declared ethernet port"):
        Node.model_validate(node)


@pytest.mark.parametrize(
    ("collection", "message"),
    [
        ("ethernet", "ethernet port ids must be unique"),
        ("terminals", "terminal mount ids must be unique"),
        ("payloads", "payload mount ids must be unique"),
    ],
)
def test_node_component_ids_are_unique(collection: str, message: str) -> None:
    node = _node_data()
    node[collection].append(dict(node[collection][0]))

    with pytest.raises(ValidationError, match=message):
        Node.model_validate(node)


def test_non_access_terminal_mount_rejects_boresight() -> None:
    node = _node_data()
    node["terminals"][0]["role"] = "isl"
    node["terminals"][0]["boresight"] = {"mode": "nadir"}

    with pytest.raises(ValidationError, match="valid only on access terminal mounts"):
        Node.model_validate(node)


def test_catalog_tags_are_unique() -> None:
    node = _node_data()
    node["tags"] = ["gateway", "gateway"]

    with pytest.raises(ValidationError, match="tags must not contain duplicates"):
        Node.model_validate(node)


def test_site_set_references_are_unique() -> None:
    site_set = {
        "id": "test-sites",
        "sites": ["nodalarc:sites/test.yaml", "nodalarc:sites/test.yaml"],
    }

    with pytest.raises(ValidationError, match="sites must not contain duplicates"):
        SiteSet.model_validate(site_set)


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ({"tag": "selected", "planes": []}, "planes must not be empty"),
        ({"tag": "selected", "slots": [0, 0]}, "slots must not contain duplicates"),
        ({"tag": "selected", "planes": [2]}, "plane outside this constellation"),
        ({"tag": "selected", "slots": [2]}, "slot outside this constellation"),
        ({"tag": "selected", "node_ids": ["sat-p02s00"]}, "unknown generated node"),
    ],
)
def test_constellation_tag_filters_must_select_declared_nodes(
    rule: dict[str, Any], message: str
) -> None:
    constellation = _constellation_data()
    constellation["node_tags"] = [rule]

    with pytest.raises(ValidationError, match=message):
        Constellation.model_validate(constellation)


def test_space_node_set_node_ids_are_unique() -> None:
    node_set = _space_node_set_data()
    node_set["nodes"][1]["id"] = "relay-a"

    with pytest.raises(ValidationError, match="node ids must be unique"):
        SpaceNodeSet.model_validate(node_set)


def test_site_node_ids_are_unique() -> None:
    site = _site_data()
    site["nodes"][1]["id"] = site["nodes"][0]["id"]

    with pytest.raises(ValidationError, match="site node ids must be unique"):
        Site.model_validate(site)


def test_site_segment_ids_are_unique() -> None:
    site = _site_data()
    site["ethernet"].append({"id": "lan0"})

    with pytest.raises(ValidationError, match="segment ids must be unique"):
        Site.model_validate(site)


def test_site_node_binding_must_name_a_declared_segment() -> None:
    site = _site_data()
    site["nodes"][1]["interfaces"]["terr0"] = "lan9"

    with pytest.raises(ValidationError, match="binds undeclared segment"):
        Site.model_validate(site)


def test_site_lagrange_frame_uses_shared_typed_model() -> None:
    site = _site_data()
    site.pop("location")
    site["frame"] = {
        "lagrange": {
            "primary_body": "nodalarc:bodies/earth.yaml",
            "secondary_body": "nodalarc:bodies/luna.yaml",
            "point": "l1",
            "ephemeris": {
                "configured_state": {
                    "epoch": "2026-06-08T00:00:00Z",
                    "frame": "icrf",
                    "position_km": [1.0, 2.0, 3.0],
                    "velocity_km_s": [0.1, 0.2, 0.3],
                }
            },
        }
    }

    parsed = Site.model_validate(site)

    assert isinstance(parsed.frame, LagrangeFrame)
    assert isinstance(parsed.frame.lagrange.ephemeris, ConfiguredStateLagrange)
    assert parsed.frame.lagrange.ephemeris.configured_state.frame == "icrf"


def test_site_lagrange_frame_rejects_opaque_ephemeris_mapping() -> None:
    site = _site_data()
    site.pop("location")
    site["frame"] = {
        "lagrange": {
            "primary_body": "nodalarc:bodies/earth.yaml",
            "secondary_body": "nodalarc:bodies/luna.yaml",
            "point": "l1",
            "ephemeris": {"configured_state": {}},
        }
    }

    with pytest.raises(ValidationError, match="epoch"):
        Site.model_validate(site)
