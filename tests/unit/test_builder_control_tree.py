"""Backend-derived graphical controls for the complete session grammar."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from nodalarc.models.builder_controls_api import (
    BuilderChoiceControl,
    BuilderControl,
    BuilderMapControl,
    BuilderObjectControl,
    BuilderScalarControl,
    BuilderSequenceControl,
)
from nodalarc.models.link_rules import Endpoint, NearestVisibleTopology
from nodalarc.models.segment_session import RoutingBoundary, SegmentSessionConfig
from nodalarc.models.segments import LagrangeSegment, SpaceSegment
from vs_api.builder_control_tree import _sequence_values, build_session_control_tree
from vs_api.builder_visual_draft import BUILDER_VISUAL_SPECIALIZED_FIELDS

from tests.support.builder_model_coverage import (
    BuilderGraphicalCoverageRecorder,
    assert_complete_builder_graphical_coverage,
    discover_builder_model_graph,
)


def _session() -> SegmentSessionConfig:
    return SegmentSessionConfig.model_validate(
        {
            "session": {"name": "control-tree"},
            "segments": [
                {
                    "id": "space",
                    "source": "nodalarc:constellations/control-tree.yaml",
                }
            ],
            "time": {
                "start_time": "2026-01-01T00:00:00Z",
                "step_seconds": 1,
                "compression": 1,
            },
        }
    )


def _session_with_nested_selector() -> SegmentSessionConfig:
    document = _session().model_dump(mode="json", by_alias=True, exclude_none=True)
    document["link_rules"] = [
        {
            "id": "deep-selector",
            "endpoints": [
                {
                    "select": {
                        "all": [
                            {"segment": "space"},
                            {
                                "not": {
                                    "any": [
                                        {"tag": "relay"},
                                        {"plane": 2},
                                    ]
                                }
                            },
                        ]
                    },
                    "terminal": {"medium": "rf"},
                },
                {
                    "select": {"segment": "space"},
                    "terminal": {"medium": "rf"},
                },
            ],
            "topology": {"mode": "visible_candidates"},
        }
    ]
    return SegmentSessionConfig.model_validate(document)


def _walk(control: BuilderControl) -> Iterator[BuilderControl]:
    yield control
    if isinstance(control, BuilderObjectControl):
        for field in control.fields:
            yield from _walk(field.control)
    elif isinstance(control, BuilderChoiceControl):
        for branch in control.branches:
            if branch.control is not None:
                yield from _walk(branch.control)
    elif isinstance(control, BuilderSequenceControl):
        for item in control.items:
            yield from _walk(item.control)
        if control.add_item_control is not None:
            yield from _walk(control.add_item_control)
    elif isinstance(control, BuilderMapControl):
        for entry in control.entries:
            yield from _walk(entry.key)
            yield from _walk(entry.value)
        yield from _walk(control.add_key_control)
        yield from _walk(control.add_value_control)


def test_factory_records_complete_live_graphical_coverage() -> None:
    graph = discover_builder_model_graph(SegmentSessionConfig)
    recorder = BuilderGraphicalCoverageRecorder()

    built = build_session_control_tree(
        _session(),
        projection_revision=7,
        instrument=recorder.record,
    )

    assert len(graph.obligation_keys) >= 400
    assert_complete_builder_graphical_coverage(graph, recorder.obligation_keys)
    assert set(built.bindings) >= {control.control_id for control in _walk(built.tree.root)}
    assert all(control.editable for control in _walk(built.tree.root))
    with pytest.raises(TypeError):
        built.bindings["ctl_00000000000000000000000000000000"] = next(iter(built.bindings.values()))


def test_runtime_unsupported_grammar_branches_remain_graphically_bound() -> None:
    built = build_session_control_tree(_session(), projection_revision=3)
    branch_values = {
        binding.choice_value
        for binding in built.bindings.values()
        if binding.role in {"choice_branch", "literal_branch"}
    }

    assert LagrangeSegment in branch_values
    assert "nearest_visible" in branch_values
    assert "dtn_bundle" in branch_values

    owners = {(binding.owner_model, binding.field_name) for binding in built.bindings.values()}
    assert (NearestVisibleTopology, "mode") in owners
    assert (RoutingBoundary, "adapter") in owners


def test_catalog_refs_are_typed_reference_pickers() -> None:
    built = build_session_control_tree(_session(), projection_revision=1)
    reference_controls = [
        control
        for control in _walk(built.tree.root)
        if isinstance(control, BuilderScalarControl) and control.scalar_kind == "reference"
    ]

    source = next(
        control for control in reference_controls if control.json_pointer.endswith("/source")
    )
    assert source.reference_families == ("constellations", "space-node-sets")
    assert source.constraints.pattern is not None
    assert "nodalarc|user" in source.constraints.pattern


def test_empty_parameter_forms_are_structured_objects_not_maps_or_text() -> None:
    built = build_session_control_tree(_session(), projection_revision=2)
    empty_parameters = [
        control
        for control in _walk(built.tree.root)
        if isinstance(control, BuilderObjectControl) and control.empty_parameters
    ]

    pointers = {control.json_pointer for control in empty_parameters}
    assert any(pointer.endswith("/highest_elevation") for pointer in pointers)
    assert any(pointer.endswith("/hard_release") for pointer in pointers)
    assert any(pointer.endswith("/lagrange_approximation") for pointer in pointers)
    assert all(not control.fields for control in empty_parameters)


def test_scalar_and_collection_constraints_come_from_pydantic_fields() -> None:
    built = build_session_control_tree(_session(), projection_revision=4)
    controls_by_id = {control.control_id: control for control in _walk(built.tree.root)}
    elevation = next(
        controls_by_id[control_id]
        for control_id, binding in built.bindings.items()
        if binding.owner_model is Endpoint
        and binding.field_name == "min_elevation_deg"
        and binding.role == "scalar"
    )

    assert isinstance(elevation, BuilderScalarControl)
    assert elevation.constraints.minimum == 0
    assert elevation.constraints.maximum == 90

    segment_sequences = [
        control
        for control in _walk(built.tree.root)
        if isinstance(control, BuilderSequenceControl) and control.json_pointer == "/segments"
    ]
    assert len(segment_sequences) == 1
    assert segment_sequences[0].min_items == 1
    assert segment_sequences[0].can_add is True
    assert segment_sequences[0].can_remove is True
    assert segment_sequences[0].can_reorder is True


def test_ids_are_stable_within_and_fenced_across_projection_revisions() -> None:
    session = _session()
    first = build_session_control_tree(session, projection_revision=8)
    repeated = build_session_control_tree(session, projection_revision=8)
    advanced = build_session_control_tree(session, projection_revision=9)

    assert first.tree == repeated.tree
    assert set(first.bindings) == set(repeated.bindings)
    assert set(first.bindings).isdisjoint(advanced.bindings)
    assert {binding.projection_revision for binding in first.bindings.values()} == {8}


def test_concrete_recursive_selectors_expand_to_their_actual_finite_depth() -> None:
    built = build_session_control_tree(
        _session_with_nested_selector(),
        projection_revision=10,
    )
    controls = tuple(_walk(built.tree.root))
    expected_values = {
        "/link_rules/0/endpoints/0/select/all/0/segment": "space",
        "/link_rules/0/endpoints/0/select/all/1/not/any/0/tag": "relay",
        "/link_rules/0/endpoints/0/select/all/1/not/any/1/plane": 2,
    }

    for pointer, value in expected_values.items():
        assert any(
            isinstance(control, BuilderScalarControl)
            and control.json_pointer == pointer
            and control.present
            and control.value == value
            for control in controls
        )

    concrete_nested_selector = [
        control
        for control in controls
        if isinstance(control, BuilderObjectControl)
        and control.present
        and control.json_pointer == "/link_rules/0/endpoints/0/select/all/1/not"
    ]
    assert concrete_nested_selector
    assert all(not control.recursive_reference for control in concrete_nested_selector)


def test_unordered_collection_projection_is_stable() -> None:
    assert tuple(_sequence_values(frozenset({"beta", "alpha"}))) == (
        "alpha",
        "beta",
    )


def test_specialized_field_markers_do_not_hide_unclaimed_siblings() -> None:
    built = build_session_control_tree(
        _session(),
        projection_revision=6,
        specialized_fields={(SpaceSegment, "id")},
    )
    controls = tuple(_walk(built.tree.root))

    segment_id = next(
        control
        for control in controls
        if control.json_pointer == "/segments/0/id" and control.present
    )
    display_name = next(
        control for control in controls if control.json_pointer == "/segments/0/display_name"
    )
    assert segment_id.specialized is True
    assert display_name.specialized is False


def test_rich_link_fields_remain_editable_in_complete_session_controls() -> None:
    built = build_session_control_tree(
        _session_with_nested_selector(),
        projection_revision=11,
        specialized_fields=BUILDER_VISUAL_SPECIALIZED_FIELDS,
    )
    controls = {control.json_pointer: control for control in _walk(built.tree.root)}

    for pointer in (
        "/link_rules/0/id",
        "/link_rules/0/enabled",
        "/link_rules/0/endpoints/0/select/all/0/segment",
        "/link_rules/0/endpoints/0/select/all/1/not/any/0/tag",
        "/link_rules/0/endpoints/0/terminal/medium",
        "/link_rules/0/topology/mode",
    ):
        assert controls[pointer].specialized is False
