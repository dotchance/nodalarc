"""Tests for the canonical catalog-family registry."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from nodalarc.catalog_refs import CatalogFamily
from nodalarc.catalog_registry import (
    CATALOG_FAMILY_REGISTRY,
    CATALOG_WRAPPER_TO_FAMILY,
    catalog_family_spec,
    validate_catalog_document,
    validate_configuration_document,
    validate_referenced_configuration_document,
)
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.catalog import Orbit, OrbitDocument, SpaceNode
from nodalarc.models.configuration import CONFIGURATION_DOCUMENT_MODELS
from nodalarc.models.segment_session import SegmentSessionConfig
from pydantic import ValidationError

from tests.catalog_session_fixtures import ISS_TLE_LINE_1, ISS_TLE_LINE_2

ROOT = Path(__file__).resolve().parents[2]


def test_registry_covers_the_closed_catalog_family_vocabulary() -> None:
    assert set(CATALOG_FAMILY_REGISTRY) == set(get_args(CatalogFamily))
    assert set(CONFIGURATION_DOCUMENT_MODELS) == set(get_args(CatalogFamily))
    assert {
        family: spec.document_model_type for family, spec in CATALOG_FAMILY_REGISTRY.items()
    } == dict(CONFIGURATION_DOCUMENT_MODELS)
    assert CONFIGURATION_DOCUMENT_MODELS["sessions"] is SegmentSessionConfig
    assert CATALOG_WRAPPER_TO_FAMILY["orbit"] == "orbits"
    assert "session" not in CATALOG_WRAPPER_TO_FAMILY


def test_session_family_is_an_unwrapped_strict_session_document() -> None:
    path = ROOT / "catalog" / "nodalarc" / "sessions" / "earth-leo-simple.yaml"
    document = load_configuration_yaml(path.read_bytes())

    spec = catalog_family_spec("sessions")
    model = validate_configuration_document("sessions", document)

    assert spec.wrapper is None
    assert spec.is_wrapped is False
    assert isinstance(model, SegmentSessionConfig)


@pytest.mark.parametrize(
    ("ref", "path", "identity_path"),
    (
        (
            "nodalarc:orbits/earth/leo/earth-leo-starlink.yaml",
            ROOT / "catalog/nodalarc/orbits/earth/leo/earth-leo-starlink.yaml",
            ("orbit", "id"),
        ),
        (
            "nodalarc:sessions/earth-leo-simple.yaml",
            ROOT / "catalog/nodalarc/sessions/earth-leo-simple.yaml",
            ("session", "name"),
        ),
    ),
)
def test_referenced_documents_require_identity_to_match_filename(
    ref: str,
    path: Path,
    identity_path: tuple[str, str],
) -> None:
    document = load_configuration_yaml(path.read_bytes())
    document[identity_path[0]][identity_path[1]] = "wrong-identity"

    with pytest.raises(ValueError, match="must match filename stem"):
        validate_referenced_configuration_document(ref, document)


def test_primitive_family_requires_its_wrapper_and_strict_model() -> None:
    path = ROOT / "catalog" / "nodalarc" / "orbits" / "earth" / "leo" / "earth-leo-starlink.yaml"
    document = load_configuration_yaml(path.read_bytes())

    model = validate_configuration_document("orbits", document)

    assert isinstance(model, Orbit)
    wrapped = CONFIGURATION_DOCUMENT_MODELS["orbits"].model_validate(document)
    assert isinstance(wrapped, OrbitDocument)
    assert wrapped.orbit == model
    wrapper, generic_model = validate_catalog_document(document)
    assert wrapper == "orbit"
    assert generic_model == model
    with pytest.raises(ValueError, match="requires wrapper"):
        validate_configuration_document("orbits", {"node": document["orbit"]})


def test_reusable_orbit_rejects_sgp4_without_spacecraft_tle_placement() -> None:
    path = ROOT / "catalog" / "nodalarc" / "orbits" / "earth" / "leo" / "earth-leo-starlink.yaml"
    document = load_configuration_yaml(path.read_bytes())
    document["orbit"]["propagator"] = "sgp4_tle"

    with pytest.raises(ValidationError, match="propagator"):
        validate_configuration_document("orbits", document)


def test_space_node_accepts_exact_canonical_sgp4_tle_placement() -> None:
    node = SpaceNode.model_validate(
        {
            "id": "iss",
            "node": "nodalarc:nodes/space/leo-relay.yaml",
            "sgp4_tle": {
                "central_body": "nodalarc:bodies/earth.yaml",
                "line_1": ISS_TLE_LINE_1,
                "line_2": ISS_TLE_LINE_2,
            },
        }
    )

    assert node.sgp4_tle is not None
    assert node.sgp4_tle.line_1 == ISS_TLE_LINE_1
    assert node.sgp4_tle.line_2 == ISS_TLE_LINE_2


def test_space_node_rejects_invalid_or_ambiguous_sgp4_tle_placement() -> None:
    base = {
        "id": "iss",
        "node": "nodalarc:nodes/space/leo-relay.yaml",
        "sgp4_tle": {
            "central_body": "nodalarc:bodies/earth.yaml",
            "line_1": ISS_TLE_LINE_1,
            "line_2": ISS_TLE_LINE_2,
        },
    }
    mismatched = {
        **base,
        "sgp4_tle": {
            **base["sgp4_tle"],
            "line_2": ISS_TLE_LINE_2.replace("25544", "99999", 1),
        },
    }
    with pytest.raises(ValidationError, match="line number mismatch"):
        SpaceNode.model_validate(mismatched)
    with pytest.raises(ValidationError, match="exactly one"):
        SpaceNode.model_validate({**base, "orbit": "nodalarc:orbits/earth/leo/demo.yaml"})
    with pytest.raises(ValidationError, match="exactly one"):
        SpaceNode.model_validate({"id": "iss", "node": base["node"]})


def test_registry_rejects_a_session_wrapper_envelope() -> None:
    path = ROOT / "catalog" / "nodalarc" / "sessions" / "earth-leo-simple.yaml"
    document = load_configuration_yaml(path.read_bytes())

    with pytest.raises(ValidationError):
        validate_configuration_document("sessions", {"session_document": document})


def test_registry_rejects_unknown_families_and_is_immutable() -> None:
    with pytest.raises(ValueError, match="unknown catalog family"):
        catalog_family_spec("space-nodes")
    with pytest.raises(TypeError):
        CATALOG_FAMILY_REGISTRY["bodies"] = CATALOG_FAMILY_REGISTRY["bodies"]


def test_entire_shipped_catalog_conforms_to_canonical_grammar() -> None:
    catalog_root = ROOT / "catalog" / "nodalarc"
    seen: set[Path] = set()

    for family, spec in CATALOG_FAMILY_REGISTRY.items():
        family_root = catalog_root / family
        family_documents = sorted(
            path
            for path in family_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        )
        for path in family_documents:
            document = load_configuration_yaml(path.read_bytes())
            model = spec.validate_document(document)
            identity = model.session.name if family == "sessions" else model.id

            assert identity == path.stem, path
            seen.add(path)

    all_yaml = {
        path
        for path in catalog_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    }
    assert seen == all_yaml
