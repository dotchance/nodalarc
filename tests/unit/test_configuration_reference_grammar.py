"""Canonical reference grammar coverage for every catalog-valued slot."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from nodalarc.catalog_refs import CatalogRef
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.catalog import (
    Constellation,
    Node,
    Orbit,
    Payload,
    Site,
    SiteSet,
    SpaceNodeSet,
)
from nodalarc.models.segment_session import SegmentSessionConfig
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_SESSIONS = ROOT / "catalog" / "nodalarc" / "sessions"
ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"


def _orbit() -> dict[str, Any]:
    return {
        "id": "test-orbit",
        "central_body": "nodalarc:bodies/earth.yaml",
        "epoch": "2026-01-01T00:00:00Z",
        "shape": {"altitude_km": 550.0},
        "orientation": {
            "inclination_deg": 53.0,
            "raan_deg": 0.0,
            "argument_of_perigee_deg": 0.0,
        },
        "phase": {"mean_anomaly_deg": 0.0},
        "propagator": "two_body",
        "reference": "urn:nodalarc:test",
    }


def _payload() -> dict[str, Any]:
    return {
        "id": "test-payload",
        "forwarding": "host",
        "profile": "nodalarc:profiles/linux-host.yaml",
    }


def _node() -> dict[str, Any]:
    return {
        "id": "test-node",
        "forwarding": "routed",
        "ethernet": [{"id": "terr0"}],
        "terminals": [
            {
                "id": "access",
                "role": "access",
                "terminal": "nodalarc:terminals/rf/access.yaml",
                "count": 1,
            }
        ],
        "payloads": [
            {
                "id": "payload",
                "payload": "nodalarc:payloads/test-payload.yaml",
                "count": 1,
                "attach": "terr0",
            }
        ],
    }


def _site_node() -> dict[str, Any]:
    return {
        "id": "gateway",
        "node": "nodalarc:nodes/ground/gateway.yaml",
        "terminals": {},
        "payloads": {},
        "interfaces": {"terr0": "lan0"},
    }


def _body_fixed_site() -> dict[str, Any]:
    return {
        "id": "test-site",
        "ethernet": [{"id": "lan0"}],
        "nodes": [_site_node()],
        "frame": {"body_fixed": {"body": "nodalarc:bodies/earth.yaml"}},
        "location": {"lat_deg": 0.0, "lon_deg": 0.0, "alt_m": 0.0},
    }


def _lagrange_frame() -> dict[str, Any]:
    return {
        "lagrange": {
            "primary_body": "nodalarc:bodies/earth.yaml",
            "secondary_body": "nodalarc:bodies/luna.yaml",
            "point": "l1",
            "ephemeris": {"lagrange_approximation": {}},
        }
    }


def _lagrange_site() -> dict[str, Any]:
    return {
        "id": "test-lagrange-site",
        "ethernet": [{"id": "lan0"}],
        "nodes": [_site_node()],
        "frame": _lagrange_frame(),
    }


def _site_set() -> dict[str, Any]:
    return {
        "id": "test-sites",
        "sites": ["nodalarc:sites/earth/test-site.yaml"],
    }


def _constellation() -> dict[str, Any]:
    return {
        "id": "test-constellation",
        "node": "nodalarc:nodes/space/relay.yaml",
        "orbit": "nodalarc:orbits/earth/leo/test-orbit.yaml",
        "planes": {"count": 1, "raan_spacing_deg": 0.0},
        "slots_per_plane": 1,
        "phasing": {"mode": "evenly_spaced_mean_anomaly"},
        "node_tags": [],
    }


def _space_node_set() -> dict[str, Any]:
    return {
        "id": "test-space-nodes",
        "nodes": [
            {
                "id": "relay-1",
                "node": "nodalarc:nodes/space/relay.yaml",
                "orbit": "nodalarc:orbits/earth/geo/test-orbit.yaml",
            }
        ],
    }


def _sgp4_space_node_set() -> dict[str, Any]:
    return {
        "id": "test-sgp4-space-nodes",
        "nodes": [
            {
                "id": "relay-1",
                "node": "nodalarc:nodes/space/relay.yaml",
                "sgp4_tle": {
                    "central_body": "nodalarc:bodies/earth.yaml",
                    "line_1": ISS_TLE_LINE_1,
                    "line_2": ISS_TLE_LINE_2,
                },
            }
        ],
    }


def _space_session() -> dict[str, Any]:
    return {
        "session": {"name": "test-session"},
        "time": {
            "start_time": "2026-06-08T00:00:00Z",
            "step_seconds": 1,
            "compression": 1,
        },
        "segments": [
            {
                "id": "space",
                "source": "nodalarc:constellations/earth/leo/test.yaml",
            }
        ],
    }


def _ground_session() -> dict[str, Any]:
    return {
        "session": {"name": "test-session"},
        "time": {
            "start_time": "2026-06-08T00:00:00Z",
            "step_seconds": 1,
            "compression": 1,
        },
        "segments": [
            {
                "id": "ground",
                "placement": {"from_site_set": "nodalarc:site-sets/earth/test-sites.yaml"},
            }
        ],
    }


def _lagrange_session() -> dict[str, Any]:
    return {
        "session": {"name": "test-session"},
        "time": {
            "start_time": "2026-06-08T00:00:00Z",
            "step_seconds": 1,
            "compression": 1,
        },
        "segments": [
            {
                "id": "lagrange",
                "node": "nodalarc:nodes/space/relay.yaml",
                "frame": _lagrange_frame(),
            }
        ],
    }


def _ephemeris_session() -> dict[str, Any]:
    document = _space_session()
    document["ephemeris"] = {
        "provider": "skyfield_bsp",
        "quality_tier": "test",
        "kernels": [
            {
                "id": "test-kernel",
                "path": "configs/ephemerides/test.bsp",
                "targets": ["nodalarc:bodies/earth.yaml"],
                "frame": "icrf",
            }
        ],
    }
    return document


@pytest.mark.parametrize(
    ("coverage", "message"),
    (
        ({"coverage_start": "2026-01-01T00:00:00Z"}, "declared together"),
        (
            {
                "coverage_start": "2026-01-01T00:00:00Z",
                "coverage_end": "2026-01-01T00:00:00Z",
            },
            "later than",
        ),
        (
            {
                "coverage_start": "2026-01-02T00:00:00Z",
                "coverage_end": "2026-01-01T00:00:00Z",
            },
            "later than",
        ),
    ),
)
def test_ephemeris_coverage_is_a_complete_positive_window(
    coverage: dict[str, str],
    message: str,
) -> None:
    document = _ephemeris_session()
    document["ephemeris"]["kernels"][0].update(coverage)

    with pytest.raises(ValidationError, match=message):
        SegmentSessionConfig.model_validate(document)


@dataclass(frozen=True)
class ReferenceSlotCase:
    id: str
    model_type: type[BaseModel]
    document_factory: Any
    path: tuple[str | int, ...]
    family: str


REFERENCE_SLOT_CASES = (
    ReferenceSlotCase("orbit.central_body", Orbit, _orbit, ("central_body",), "bodies"),
    ReferenceSlotCase(
        "payload.profile",
        Payload,
        _payload,
        ("profile",),
        "profiles",
    ),
    ReferenceSlotCase(
        "node.terminals.terminal",
        Node,
        _node,
        ("terminals", 0, "terminal"),
        "terminals",
    ),
    ReferenceSlotCase(
        "node.payloads.payload",
        Node,
        _node,
        ("payloads", 0, "payload"),
        "payloads",
    ),
    ReferenceSlotCase(
        "site.frame.body_fixed.body",
        Site,
        _body_fixed_site,
        ("frame", "body_fixed", "body"),
        "bodies",
    ),
    ReferenceSlotCase(
        "site.frame.lagrange.primary_body",
        Site,
        _lagrange_site,
        ("frame", "lagrange", "primary_body"),
        "bodies",
    ),
    ReferenceSlotCase(
        "site.frame.lagrange.secondary_body",
        Site,
        _lagrange_site,
        ("frame", "lagrange", "secondary_body"),
        "bodies",
    ),
    ReferenceSlotCase(
        "site.nodes.node",
        Site,
        _body_fixed_site,
        ("nodes", 0, "node"),
        "nodes",
    ),
    ReferenceSlotCase("site_set.sites", SiteSet, _site_set, ("sites", 0), "sites"),
    ReferenceSlotCase(
        "constellation.node",
        Constellation,
        _constellation,
        ("node",),
        "nodes",
    ),
    ReferenceSlotCase(
        "constellation.orbit",
        Constellation,
        _constellation,
        ("orbit",),
        "orbits",
    ),
    ReferenceSlotCase(
        "space_node_set.nodes.node",
        SpaceNodeSet,
        _space_node_set,
        ("nodes", 0, "node"),
        "nodes",
    ),
    ReferenceSlotCase(
        "space_node_set.nodes.orbit",
        SpaceNodeSet,
        _space_node_set,
        ("nodes", 0, "orbit"),
        "orbits",
    ),
    ReferenceSlotCase(
        "space_node_set.nodes.sgp4_tle.central_body",
        SpaceNodeSet,
        _sgp4_space_node_set,
        ("nodes", 0, "sgp4_tle", "central_body"),
        "bodies",
    ),
    ReferenceSlotCase(
        "session.ground.from_site_set",
        SegmentSessionConfig,
        _ground_session,
        ("segments", 0, "placement", "from_site_set"),
        "site-sets",
    ),
    ReferenceSlotCase(
        "session.space.source",
        SegmentSessionConfig,
        _space_session,
        ("segments", 0, "source"),
        "constellations",
    ),
    ReferenceSlotCase(
        "session.lagrange.node",
        SegmentSessionConfig,
        _lagrange_session,
        ("segments", 0, "node"),
        "nodes",
    ),
    ReferenceSlotCase(
        "session.lagrange.frame.primary_body",
        SegmentSessionConfig,
        _lagrange_session,
        ("segments", 0, "frame", "lagrange", "primary_body"),
        "bodies",
    ),
    ReferenceSlotCase(
        "session.lagrange.frame.secondary_body",
        SegmentSessionConfig,
        _lagrange_session,
        ("segments", 0, "frame", "lagrange", "secondary_body"),
        "bodies",
    ),
    ReferenceSlotCase(
        "session.ephemeris.targets",
        SegmentSessionConfig,
        _ephemeris_session,
        ("ephemeris", "kernels", 0, "targets", 0),
        "bodies",
    ),
)


def _set_path(document: Any, path: tuple[str | int, ...], value: Any) -> None:
    current = document
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value


def _typed_references(value: Any) -> list[CatalogRef]:
    if isinstance(value, CatalogRef):
        return [value]
    if isinstance(value, BaseModel):
        refs: list[CatalogRef] = []
        for field_name in type(value).model_fields:
            refs.extend(_typed_references(getattr(value, field_name)))
        return refs
    if isinstance(value, dict):
        return [ref for child in value.values() for ref in _typed_references(child)]
    if isinstance(value, list | tuple):
        return [ref for child in value for ref in _typed_references(child)]
    return []


@pytest.mark.parametrize("case", REFERENCE_SLOT_CASES, ids=lambda case: case.id)
@pytest.mark.parametrize("namespace", ["nodalarc", "user"])
def test_every_canonical_catalog_slot_accepts_typed_refs_in_both_namespaces(
    case: ReferenceSlotCase,
    namespace: str,
) -> None:
    document = case.document_factory()
    token = f"{namespace}:{case.family}/nested/example.yaml"
    _set_path(document, case.path, token)

    model = case.model_type.model_validate(document)

    refs = _typed_references(model)
    assert token in refs
    matched = next(ref for ref in refs if ref == token)
    assert matched.namespace == namespace
    assert matched.family == case.family


@pytest.mark.parametrize("case", REFERENCE_SLOT_CASES, ids=lambda case: case.id)
def test_every_canonical_catalog_slot_rejects_inline_objects(case: ReferenceSlotCase) -> None:
    document = case.document_factory()
    _set_path(document, case.path, {"inline": "object"})

    with pytest.raises(ValidationError):
        case.model_type.model_validate(document)


@pytest.mark.parametrize("case", REFERENCE_SLOT_CASES, ids=lambda case: case.id)
def test_every_canonical_catalog_slot_rejects_wrong_family(case: ReferenceSlotCase) -> None:
    document = case.document_factory()
    wrong_family = "nodes" if case.family != "nodes" else "bodies"
    _set_path(document, case.path, f"nodalarc:{wrong_family}/wrong.yaml")

    with pytest.raises(ValidationError, match="catalog family"):
        case.model_type.model_validate(document)


def test_space_source_accepts_both_approved_source_families() -> None:
    for family in ("constellations", "space-node-sets"):
        document = _space_session()
        document["segments"][0]["source"] = f"user:{family}/example.yaml"

        session = SegmentSessionConfig.model_validate(document)

        assert session.segments[0].source.family == family


def test_link_class_is_resolver_owned() -> None:
    document = _space_session()
    document["link_rules"] = [
        {
            "id": "space-links",
            "class": "isl",
            "endpoints": [
                {"select": {"segment": "space"}, "terminal": {"role": "isl"}},
                {"select": {"segment": "space"}, "terminal": {"role": "isl"}},
            ],
            "topology": {"mode": "visible_candidates"},
        }
    ]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SegmentSessionConfig.model_validate(document)


@pytest.mark.parametrize(
    "model_type,document",
    [
        (Orbit, _orbit()),
        (SegmentSessionConfig, _space_session()),
    ],
)
def test_canonical_models_reject_unknown_fields(model_type, document: dict[str, Any]) -> None:
    document = deepcopy(document)
    document["private_state"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model_type.model_validate(document)


def test_all_shipped_session_roots_pass_canonical_parsing_unchanged() -> None:
    paths = sorted(SHIPPED_SESSIONS.glob("*.yaml"))
    assert paths

    for path in paths:
        document = load_configuration_yaml(path.read_bytes())
        original = deepcopy(document)
        strict = SegmentSessionConfig.model_validate(document)

        assert document == original
        assert strict.session.name == document["session"]["name"]
