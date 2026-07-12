"""Coverage discovery for the canonical Builder model graph."""

from __future__ import annotations

from typing import Literal

import pytest
from nodalarc.models.link_rules import NearestVisibleTopology, NodeSelector
from nodalarc.models.segment_session import ExportRule, RoutingBoundary, SegmentSessionConfig
from nodalarc.models.segments import (
    GroundSegment,
    LagrangeSegment,
    SegmentClock,
    SpaceSegment,
)
from nodalarc.runtime_support import RuntimeSupport
from pydantic import BaseModel, ConfigDict, Field

from tests.support.builder_model_coverage import (
    BuilderFieldObligation,
    BuilderGraphicalCoverageRecorder,
    BuilderLiteralObligation,
    BuilderUnionBranchObligation,
    assert_complete_builder_graphical_coverage,
    compare_builder_coverage,
    discover_builder_model_graph,
    field_obligation_key,
    obligation_key,
    obligation_label,
)


class _RecursiveProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_: _RecursiveProbe | None = Field(default=None, alias="next")
    flags: tuple[Literal["red", "blue"], ...] = ()


class _AlphaProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["alpha"]


class _BetaProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["beta"]


class _RootProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: tuple[_AlphaProbe | _BetaProbe, ...]
    recursive: _RecursiveProbe


def test_model_graph_walk_is_recursive_alias_aware_and_branch_complete() -> None:
    graph = discover_builder_model_graph(_RootProbe)

    assert graph.models == frozenset({_RootProbe, _AlphaProbe, _BetaProbe, _RecursiveProbe})
    assert (
        BuilderFieldObligation(
            model=_RecursiveProbe,
            field_name="next_",
            wire_alias="next",
            required=False,
        )
        in graph.fields
    )

    entries = next(
        field
        for field in graph.fields
        if field.model is _RootProbe and field.field_name == "entries"
    )
    assert {
        obligation.branch
        for obligation in graph.union_branches
        if obligation.field == entries and obligation.annotation_path == ("sequence-item",)
    } == {_AlphaProbe, _BetaProbe}

    flags = next(
        field
        for field in graph.fields
        if field.model is _RecursiveProbe and field.field_name == "flags"
    )
    assert {
        obligation.value
        for obligation in graph.literals
        if obligation.field == flags and obligation.annotation_path == ("sequence-item",)
    } == {"red", "blue"}
    assert len(graph.obligation_keys) == len(graph.obligations)
    assert field_obligation_key(_RecursiveProbe, "next_") == (
        "field:tests.unit.test_builder_model_coverage._RecursiveProbe.next_"
    )

    alpha_branch = next(
        obligation
        for obligation in graph.union_branches
        if obligation.field == entries and obligation.branch is _AlphaProbe
    )
    assert obligation_key(alpha_branch) == (
        "union:tests.unit.test_builder_model_coverage._RootProbe.entries@"
        '["sequence-item"]='
        "tests.unit.test_builder_model_coverage._AlphaProbe"
    )

    red_literal = next(obligation for obligation in graph.literals if obligation.value == "red")
    assert obligation_key(red_literal) == (
        "literal:tests.unit.test_builder_model_coverage._RecursiveProbe.flags@"
        '["sequence-item"]=str:"red"'
    )


def test_coverage_comparison_fails_missing_and_stale_obligations_exactly() -> None:
    graph = discover_builder_model_graph(_RootProbe)
    missing = next(iter(graph.literals))
    missing_key = obligation_key(missing)
    stale_key = "field:tests.unit.test_builder_model_coverage._RootProbe.retired"

    difference = compare_builder_coverage(
        graph,
        (graph.obligation_keys - {missing_key}) | {stale_key},
    )

    assert difference.complete is False
    assert difference.missing == frozenset({missing_key})
    assert difference.stale == frozenset({stale_key})
    assert obligation_label(missing)

    with pytest.raises(AssertionError) as raised:
        assert_complete_builder_graphical_coverage(
            graph,
            (graph.obligation_keys - {missing_key}) | {stale_key},
            registry_name="Probe editor",
        )

    message = str(raised.value)
    assert message.startswith(
        "Probe editor does not cover the canonical _RootProbe grammar.\n"
        "Only editable, round-tripping graphical controls count as representation coverage."
    )
    assert f"{missing_key} :: {obligation_label(missing)}" in message
    assert stale_key in message


def test_graphical_coverage_recorder_accepts_factory_and_dedicated_control_hooks() -> None:
    graph = discover_builder_model_graph(_RootProbe)
    recorder = BuilderGraphicalCoverageRecorder()
    entries_key = field_obligation_key(_RootProbe, "entries")
    one_literal = next(iter(graph.literals))

    recorder.record(entries_key)
    recorder.record(entries_key)
    recorder.record_obligation(one_literal)
    recorder.record_many(graph.obligation_keys - {entries_key, obligation_key(one_literal)})

    assert recorder.obligation_keys == graph.obligation_keys
    assert_complete_builder_graphical_coverage(graph, recorder.obligation_keys)

    with pytest.raises(KeyError, match="has no canonical field 'retired'"):
        recorder.record_field(_RootProbe, "retired")


def test_segment_session_walk_uses_wire_aliases_and_all_segment_branches() -> None:
    graph = discover_builder_model_graph(SegmentSessionConfig)

    assert {
        SegmentSessionConfig,
        SpaceSegment,
        GroundSegment,
        LagrangeSegment,
        NodeSelector,
    } <= graph.models
    assert (
        BuilderFieldObligation(
            model=ExportRule,
            field_name="from_",
            wire_alias="from",
            required=True,
        )
        in graph.fields
    )
    assert (
        BuilderFieldObligation(
            model=NodeSelector,
            field_name="not_",
            wire_alias="not",
            required=False,
        )
        in graph.fields
    )

    segments = next(
        field
        for field in graph.fields
        if field.model is SegmentSessionConfig and field.field_name == "segments"
    )
    assert {
        obligation.branch
        for obligation in graph.union_branches
        if obligation.field == segments and obligation.annotation_path == ("sequence-item",)
    } == {SpaceSegment, GroundSegment, LagrangeSegment}


def test_runtime_gates_do_not_remove_graphical_representation_obligations() -> None:
    graph = discover_builder_model_graph(SegmentSessionConfig)
    runtime = RuntimeSupport.earth_luna()

    assert runtime.check_segment_kind("lagrange") is not None
    assert runtime.check_link_topology("nearest_visible") is not None
    assert runtime.check_protocol_adapter("dtn_bundle") is not None

    segments = next(
        field
        for field in graph.fields
        if field.model is SegmentSessionConfig and field.field_name == "segments"
    )
    assert (
        BuilderUnionBranchObligation(
            field=segments,
            annotation_path=("sequence-item",),
            branch=LagrangeSegment,
        )
        in graph.union_branches
    )

    nearest_mode = next(
        field
        for field in graph.fields
        if field.model is NearestVisibleTopology and field.field_name == "mode"
    )
    assert (
        BuilderLiteralObligation(
            field=nearest_mode,
            annotation_path=(),
            value="nearest_visible",
        )
        in graph.literals
    )

    adapter = next(
        field
        for field in graph.fields
        if field.model is RoutingBoundary and field.field_name == "adapter"
    )
    assert (
        BuilderLiteralObligation(
            field=adapter,
            annotation_path=(),
            value="dtn_bundle",
        )
        in graph.literals
    )

    clock_model = next(
        field
        for field in graph.fields
        if field.model is SegmentClock and field.field_name == "model"
    )
    assert (
        BuilderLiteralObligation(
            field=clock_model,
            annotation_path=(),
            value="affine",
        )
        in graph.literals
    )

    unsupported_representation_keys = {
        obligation_key(obligation)
        for obligation in graph.obligations
        if obligation
        in {
            BuilderUnionBranchObligation(
                field=segments,
                annotation_path=("sequence-item",),
                branch=LagrangeSegment,
            ),
            BuilderLiteralObligation(
                field=nearest_mode,
                annotation_path=(),
                value="nearest_visible",
            ),
            BuilderLiteralObligation(
                field=adapter,
                annotation_path=(),
                value="dtn_bundle",
            ),
            BuilderLiteralObligation(
                field=clock_model,
                annotation_path=(),
                value="affine",
            ),
        }
    }
    assert len(unsupported_representation_keys) == 4
    assert unsupported_representation_keys <= graph.obligation_keys
