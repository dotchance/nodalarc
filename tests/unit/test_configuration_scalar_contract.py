"""Strict YAML scalar-kind contracts for canonical persisted configuration."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.model_validation import (
    FiniteFloat,
    NonNegativeInteger,
    PositiveFiniteFloat,
    PositiveInteger,
    StrictBoolean,
    StrictInteger,
)
from nodalarc.models.catalog import Body, NodeTagRule, OrbitElements, PlaneParams, SiteLocation
from nodalarc.models.link_rules import LinkRule, LinkRuleConstraints, NearestNTopology, NodeSelector
from nodalarc.models.segment_session import (
    AddressPoolAssignment,
    AreaMapping,
    BfdConfig,
    CandidateLimits,
    Dispatch,
    ExportRule,
    RoutingTimers,
    SpfThrottle,
)
from nodalarc.models.segments import GroundScheduling, StateVector
from pydantic import BaseModel, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_MODEL_FILES = (
    ROOT / "lib/nodalarc/models/catalog.py",
    ROOT / "lib/nodalarc/models/segment_session.py",
    ROOT / "lib/nodalarc/models/segments.py",
    ROOT / "lib/nodalarc/models/link_rules.py",
    ROOT / "lib/nodalarc/models/configuration.py",
)


def _yaml_value(token: str) -> Any:
    return load_configuration_yaml(f"value: {token}\n")["value"]


@pytest.mark.parametrize(
    ("scalar_type", "valid_tokens", "invalid_tokens"),
    (
        (StrictInteger, ("-1", "0", "1"), ('"1"', "1.0", "true", "false", "null")),
        (PositiveInteger, ("1", "2"), ('"1"', "1.0", "true", "0", "-1")),
        (NonNegativeInteger, ("0", "1"), ('"0"', "0.0", "false", "-1")),
        (StrictBoolean, ("true", "false"), ('"true"', '"false"', "1", "0", "1.0")),
        (FiniteFloat, ("-1", "0", "1.5"), ('"1"', "true", "false", "null")),
        (PositiveFiniteFloat, ("1", "1.5"), ('"1"', "true", "0", "-1")),
    ),
)
def test_shared_configuration_scalars_preserve_yaml_kinds(
    scalar_type: Any,
    valid_tokens: tuple[str, ...],
    invalid_tokens: tuple[str, ...],
) -> None:
    adapter = TypeAdapter(scalar_type)

    for token in valid_tokens:
        adapter.validate_python(_yaml_value(token))
    for token in invalid_tokens:
        with pytest.raises(ValidationError):
            adapter.validate_python(_yaml_value(token))


@dataclass(frozen=True)
class ScalarFieldCase:
    model: type[BaseModel]
    document: str
    path: tuple[str | int, ...]
    invalid_tokens: tuple[str, ...]


INTEGER_KIND_ERRORS = ('"1"', "1.0", "true")
NUMBER_KIND_ERRORS = ('"1"', "true")


SCALAR_FIELD_CASES = (
    ScalarFieldCase(
        Body,
        "id: earth\ndisplay_name: Earth\ngravitational_parameter_km3_s2: 398600.4418\n"
        "mean_radius_km: 6371\nequatorial_radius_km: 6378.137\n"
        "polar_radius_km: 6356.752\nreference: urn:test\n",
        ("gravitational_parameter_km3_s2",),
        NUMBER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        OrbitElements,
        "semi_major_axis_km: 7000\neccentricity: 0.1\n",
        ("eccentricity",),
        NUMBER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        SiteLocation,
        "lat_deg: 1\nlon_deg: 2.5\nalt_m: 0\n",
        ("lat_deg",),
        NUMBER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        PlaneParams,
        "count: 2\nraan_spacing_deg: 180\n",
        ("count",),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        NodeTagRule,
        "tag: polar\nplanes: [0, 1]\n",
        ("planes", 0),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        GroundScheduling,
        "handover_mode: mbb\nmbb_overlap_ticks: 2\n",
        ("mbb_overlap_ticks",),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(NodeSelector, "plane: 1\n", ("plane",), INTEGER_KIND_ERRORS),
    ScalarFieldCase(
        NearestNTopology,
        "mode: nearest_n\nn: 2\n",
        ("n",),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        LinkRuleConstraints,
        "max_links_per_node: {leo: 2}\n",
        ("max_links_per_node", "leo"),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        AddressPoolAssignment,
        "id: loopbacks\napplies_to: {segment: leo}\nipv4_pool: 10.0.0.0/24\nprefix_length: 32\n",
        ("prefix_length",),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        AreaMapping,
        "planes: [0, 1]\narea_id: '49.0001'\n",
        ("planes", 0),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        SpfThrottle,
        "init_delay_ms: 50\n",
        ("init_delay_ms",),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        BfdConfig,
        "detect_multiplier: 3\n",
        ("detect_multiplier",),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        RoutingTimers,
        "hello_interval_s: 1\nhold_interval_s: 3\n",
        ("hello_interval_s",),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        CandidateLimits,
        "max_pairs_per_rule: 10\nmax_pairs_per_tick: 20\n",
        ("max_pairs_per_rule",),
        INTEGER_KIND_ERRORS,
    ),
    ScalarFieldCase(
        Dispatch,
        "latency_authority: ome\nmax_latency_age_ticks: 3\n",
        ("max_latency_age_ticks",),
        INTEGER_KIND_ERRORS,
    ),
)


def _replace_path(document: Any, path: tuple[str | int, ...], value: Any) -> Any:
    changed = deepcopy(document)
    current = changed
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value
    return changed


@pytest.mark.parametrize("case", SCALAR_FIELD_CASES, ids=lambda case: case.model.__name__)
def test_canonical_integer_and_number_fields_reject_wrong_yaml_scalar_kinds(
    case: ScalarFieldCase,
) -> None:
    document = load_configuration_yaml(case.document)

    for wrong_token in case.invalid_tokens:
        with pytest.raises(ValidationError):
            case.model.model_validate(_replace_path(document, case.path, _yaml_value(wrong_token)))


@pytest.mark.parametrize(
    ("model", "document", "path"),
    (
        (
            LinkRule,
            "id: test\nenabled: true\nendpoints:\n"
            "- select: {segment: left}\n  terminal: {role: isl}\n"
            "- select: {segment: right}\n  terminal: {role: isl}\n"
            "topology: {mode: visible_candidates}\n",
            ("enabled",),
        ),
        (
            LinkRuleConstraints,
            "require_mutual_visibility: true\n",
            ("require_mutual_visibility",),
        ),
        (BfdConfig, "enabled: true\n", ("enabled",)),
        (
            ExportRule,
            "from: core\nto: edge\nprefixes: {aggregate_of: originated}\n"
            "export_node_loopbacks: true\n",
            ("export_node_loopbacks",),
        ),
    ),
)
@pytest.mark.parametrize("wrong_token", ('"true"', '"false"', "1", "0", "1.0"))
def test_canonical_boolean_fields_reject_wrong_yaml_scalar_kinds(
    model: type[BaseModel],
    document: str,
    path: tuple[str | int, ...],
    wrong_token: str,
) -> None:
    raw = load_configuration_yaml(document)

    with pytest.raises(ValidationError):
        model.model_validate(_replace_path(raw, path, _yaml_value(wrong_token)))


def test_valid_yaml_lists_still_materialize_as_frozen_tuples() -> None:
    tags = NodeTagRule.model_validate(
        load_configuration_yaml("tag: polar\nplanes: [0, 2]\nslots: [1, 3]\n")
    )
    vector = StateVector.model_validate(
        load_configuration_yaml(
            "epoch: '2026-01-01T00:00:00Z'\nframe: gcrs\n"
            "position_km: [1, 2.5, 3]\nvelocity_km_s: [0.1, 0, -0.1]\n"
        )
    )

    assert tags.planes == (0, 2)
    assert tags.slots == (1, 3)
    assert vector.position_km == (1.0, 2.5, 3.0)
    assert vector.velocity_km_s == (0.1, 0.0, -0.1)


def test_canonical_models_never_reintroduce_coercive_scalar_annotations() -> None:
    forbidden_names = {"PositiveInt", "NonNegativeInt", "PositiveFloat", "int", "float", "bool"}
    violations: list[str] = []

    for path in CANONICAL_MODEL_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.AnnAssign):
                    continue
                names = {
                    child.id
                    for child in ast.walk(statement.annotation)
                    if isinstance(child, ast.Name)
                }
                forbidden = sorted(names & forbidden_names)
                if forbidden:
                    field = statement.target.id if isinstance(statement.target, ast.Name) else "?"
                    violations.append(
                        f"{path.relative_to(ROOT)}:{statement.lineno} {node.name}.{field}: "
                        + ", ".join(forbidden)
                    )

    assert not violations, "coercive persisted scalar annotations:\n" + "\n".join(violations)
