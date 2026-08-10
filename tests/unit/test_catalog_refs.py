"""Tests for the neutral typed catalog-reference contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest
import yaml
from nodalarc.catalog_refs import (
    BodyRef,
    CatalogFamily,
    CatalogRef,
    CatalogReferenceError,
    ConstellationRef,
    NodeRef,
    OrbitRef,
    PayloadRef,
    ProfileRef,
    SessionRef,
    SiteRef,
    SiteSetRef,
    SpaceNodeSetRef,
    SpaceSourceRef,
    TerminalRef,
    catalog_reference_namespace,
    parse_catalog_reference,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

FAMILY_REFERENCE_TYPES = (
    (BodyRef, "bodies"),
    (TerminalRef, "terminals"),
    (PayloadRef, "payloads"),
    (ProfileRef, "profiles"),
    (OrbitRef, "orbits"),
    (NodeRef, "nodes"),
    (SiteRef, "sites"),
    (SiteSetRef, "site-sets"),
    (ConstellationRef, "constellations"),
    (SpaceNodeSetRef, "space-node-sets"),
    (SessionRef, "sessions"),
)


class ReferenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: BodyRef
    source: SpaceSourceRef
    notes: str


def test_catalog_family_vocabulary_is_closed() -> None:
    assert set(get_args(CatalogFamily)) == {
        family for _reference_type, family in FAMILY_REFERENCE_TYPES
    }


@pytest.mark.parametrize("reference_type,family", FAMILY_REFERENCE_TYPES)
@pytest.mark.parametrize("namespace", ["nodalarc", "user"])
def test_family_reference_types_accept_both_catalog_namespaces(
    reference_type,
    family: str,
    namespace: str,
) -> None:
    token = f"{namespace}:{family}/nested/example.yaml"

    value = TypeAdapter(reference_type).validate_python(token)

    assert value == token
    assert isinstance(value, reference_type)
    assert isinstance(value, CatalogRef)
    assert isinstance(value, str)
    assert value.namespace == namespace
    assert value.family == family
    assert value.relative_path == Path(f"{family}/nested/example.yaml")


@pytest.mark.parametrize("reference_type,family", FAMILY_REFERENCE_TYPES)
def test_family_reference_types_reject_the_wrong_family(reference_type, family: str) -> None:
    wrong_family = "nodes" if family != "nodes" else "bodies"

    with pytest.raises(ValidationError, match="catalog family"):
        TypeAdapter(reference_type).validate_python(f"nodalarc:{wrong_family}/nested/example.yaml")


def test_space_source_ref_accepts_only_public_space_source_families() -> None:
    adapter = TypeAdapter(SpaceSourceRef)

    assert (
        adapter.validate_python("nodalarc:constellations/earth/leo/ring.yaml")
        == "nodalarc:constellations/earth/leo/ring.yaml"
    )
    assert (
        adapter.validate_python("user:space-node-sets/earth/geo/relays.yml")
        == "user:space-node-sets/earth/geo/relays.yml"
    )
    with pytest.raises(ValidationError, match="catalog family"):
        adapter.validate_python("user:nodes/space/relay.yaml")


def test_pydantic_model_preserves_reference_types_without_classifying_prose() -> None:
    envelope = ReferenceEnvelope.model_validate(
        {
            "body": "nodalarc:bodies/earth.yaml",
            "source": "user:space-node-sets/earth/geo/relays.yaml",
            "notes": "Literal example user:nodes/not-a-dependency.yaml stays prose.",
        }
    )

    assert type(envelope.body) is BodyRef
    assert isinstance(envelope.body, CatalogRef)
    assert envelope.body.family == "bodies"
    assert type(envelope.source) is SpaceSourceRef
    assert envelope.source.allowed_families == frozenset({"constellations", "space-node-sets"})
    assert type(envelope.notes) is str
    assert not isinstance(envelope.notes, CatalogRef)


def test_reference_model_dump_serializes_plain_json_and_yaml_strings() -> None:
    envelope = ReferenceEnvelope(
        body="user:bodies/luna.yaml",
        source="nodalarc:constellations/earth/leo/ring.yaml",
        notes="user:nodes/example.yaml",
    )

    python_dump = envelope.model_dump()
    dumped = envelope.model_dump(mode="json")

    assert type(python_dump["body"]) is str
    assert type(python_dump["source"]) is str
    assert type(dumped["body"]) is str
    assert type(dumped["source"]) is str
    assert type(dumped["notes"]) is str
    assert json.loads(envelope.model_dump_json()) == dumped
    assert yaml.safe_load(yaml.safe_dump(python_dump)) == dumped


def test_reference_json_schema_exposes_namespace_and_family_constraints() -> None:
    body_pattern = TypeAdapter(BodyRef).json_schema()["pattern"]
    source_pattern = TypeAdapter(SpaceSourceRef).json_schema()["pattern"]
    generic_pattern = TypeAdapter(CatalogRef).json_schema()["pattern"]

    assert re.fullmatch(body_pattern, "user:bodies/luna.yaml")
    assert not re.fullmatch(body_pattern, "user:nodes/luna.yaml")
    assert re.fullmatch(source_pattern, "nodalarc:constellations/earth/leo/ring.yml")
    assert re.fullmatch(source_pattern, "user:space-node-sets/relay.yaml")
    assert not re.fullmatch(source_pattern, "user:space-nodes/relay.yaml")
    assert re.fullmatch(generic_pattern, "nodalarc:nodes/nested/example_name.yaml")
    assert not re.fullmatch(generic_pattern, "nodalarc:custom-family/example.yaml")


def test_generic_reference_accepts_only_registered_canonical_paths() -> None:
    token = "nodalarc:nodes/nested/example_name.yml"

    parsed = parse_catalog_reference(token)

    assert TypeAdapter(CatalogRef).validate_python(token) == token
    assert parsed.namespace == "nodalarc"
    assert parsed.family == "nodes"
    assert parsed.relative_path == Path("nodes/nested/example_name.yml")


def test_reference_rejects_noncanonical_suffix_case() -> None:
    with pytest.raises(CatalogReferenceError):
        CatalogRef("user:nodes/router.YAML")


def test_generic_reference_requires_a_registered_family() -> None:
    with pytest.raises(CatalogReferenceError, match="catalog family"):
        CatalogRef("user:example.yaml")


@pytest.mark.parametrize(
    "token",
    [
        "nodes/router.yaml",
        "catalog:nodes/router.yaml",
        "nodalarc:",
        "nodalarc:/tmp/router.yaml",
        "nodalarc:nodes/../router.yaml",
        "user:nodes//router.yaml",
        "user:nodes/./router.yaml",
        "user:./nodes/router.yaml",
        "user:nodes/router.yaml/",
        r"nodalarc:nodes\router.yaml",
        "nodalarc:nodes/router.json",
        "nodalarc:nodes/router name.yaml",
        "nodalarc:nodes/router.YAML",
        "nodalarc:Custom_Family/Nested/Example_Name.yml",
        "nodalarc:custom-family/example.yaml",
    ],
)
def test_reference_parser_rejects_unsafe_or_non_catalog_tokens(token: str) -> None:
    with pytest.raises(CatalogReferenceError):
        parse_catalog_reference(token)


def test_catalog_reference_namespace_only_recognizes_supported_prefixes() -> None:
    assert catalog_reference_namespace("nodalarc:nodes/router.yaml") == "nodalarc"
    assert catalog_reference_namespace("user:nodes/router.yaml") == "user"
    assert catalog_reference_namespace("catalog:nodes/router.yaml") is None


@pytest.mark.parametrize(
    "token",
    [
        "user:nodes/router.yaml",
        "user:nodes/router.yml",
        "nodalarc:sites/nested/example_name.yaml",
        "user:nodes//router.yaml",
        "user:nodes/./router.yaml",
        "user:./nodes/router.yaml",
        "user:nodes/router.yaml/",
        "user:nodes/router.json",
        "user:nodes/router name.yaml",
    ],
)
def test_generic_reference_parser_and_generated_pattern_accept_same_tokens(token: str) -> None:
    pattern_accepts = (
        re.fullmatch(TypeAdapter(CatalogRef).json_schema()["pattern"], token) is not None
    )
    try:
        TypeAdapter(CatalogRef).validate_python(token)
    except ValidationError:
        model_accepts = False
    else:
        model_accepts = True

    assert model_accepts is pattern_accepts
