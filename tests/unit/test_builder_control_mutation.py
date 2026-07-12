"""Revision-fenced graphical control mutation transactions."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_controls_api import (
    BuilderInsertItemCommand,
    BuilderInsertMapEntryCommand,
    BuilderMoveItemCommand,
    BuilderRemoveItemCommand,
    BuilderRemoveMapEntryCommand,
    BuilderRenameMapKeyCommand,
    BuilderSelectChoiceCommand,
    BuilderSetPresentCommand,
    BuilderSetScalarCommand,
)
from nodalarc.models.builder_visual_api import (
    BuilderVisualControlMutationRequest,
    BuilderVisualDraftApplyWorkspaceRequest,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftOpenRequest,
)
from nodalarc.models.catalog import SiteNode, TerminalInstallation
from nodalarc.models.link_rules import NodeSelector
from nodalarc.models.segment_session import (
    SegmentSessionConfig,
    SessionMeta,
    Simulation,
    TimeConfig,
)
from nodalarc.models.segments import SegmentClock, SpaceSegment
from pydantic import TypeAdapter
from vs_api.builder_control_mutation import apply_builder_control_mutations
from vs_api.builder_control_tree import BuilderControlBinding, build_session_control_tree
from vs_api.builder_visual_draft import (
    BuilderVisualDraftCommandError,
    BuilderVisualDraftService,
)
from vs_api.catalog_context import CatalogContext

from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"


@pytest.fixture()
def service(tmp_path: Path) -> BuilderVisualDraftService:
    scope = CatalogScope()
    context = CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / "user-catalog"},
        ),
        scope=scope,
    )
    return BuilderVisualDraftService(context)


def _open(service: BuilderVisualDraftService):
    return service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )


def _build(draft):
    assert draft.applied_session is not None
    assert draft.applied_revision is not None
    return build_session_control_tree(
        SegmentSessionConfig.model_validate(draft.applied_session),
        projection_revision=draft.applied_revision,
    )


def _id(
    build,
    *,
    pointer: str,
    role: str,
    owner: type | None = None,
    field: str | None = None,
    choice_value: object = ...,
    trail_fragment: tuple[str, ...] | None = None,
) -> str:
    matches = [
        control_id
        for control_id, binding in build.bindings.items()
        if binding.json_pointer == pointer
        and binding.role == role
        and (owner is None or binding.owner_model is owner)
        and (field is None or binding.field_name == field)
        and (choice_value is ... or binding.choice_value == choice_value)
        and (
            trail_fragment is None
            or any(
                binding.trail[index : index + len(trail_fragment)] == trail_fragment
                for index in range(len(binding.trail) - len(trail_fragment) + 1)
            )
        )
    ]
    assert len(matches) == 1, (pointer, role, owner, field, choice_value, matches)
    return matches[0]


def _mutate(service, draft, *commands):
    return service.mutate_controls(
        BuilderVisualControlMutationRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            commands=commands,
        ),
        available_node_count=1_000_000,
        preview_factory=lambda raw, _roots: builder_world_preview(raw["session"]["name"]),
    )


def test_scalar_presence_and_literal_choice_advance_one_compiled_revision(
    service: BuilderVisualDraftService,
) -> None:
    opened = _open(service)
    build = _build(opened)
    description_choice = _id(
        build,
        pointer="/session/description",
        role="choice",
        owner=SessionMeta,
        field="description",
    )
    description_scalar = _id(
        build,
        pointer="/session/description",
        role="scalar",
        owner=SessionMeta,
        field="description",
    )
    geometry_choice = _id(
        build,
        pointer="/simulation/ground_link_model",
        role="choice",
        owner=Simulation,
        field="ground_link_model",
    )
    geometry_branch = _id(
        build,
        pointer="/simulation/ground_link_model",
        role="literal_branch",
        owner=Simulation,
        field="ground_link_model",
        choice_value="geometry_only",
    )
    acknowledged = _id(
        build,
        pointer="/simulation/acknowledge_geometry_only",
        role="scalar",
        owner=Simulation,
        field="acknowledge_geometry_only",
    )

    result = _mutate(
        service,
        opened,
        BuilderSetPresentCommand(
            operation="set_present",
            control_id=description_choice,
            present=True,
        ),
        BuilderSetScalarCommand(
            operation="set_scalar",
            control_id=description_scalar,
            value="Edited graphically",
        ),
        BuilderSelectChoiceCommand(
            operation="select_choice",
            control_id=geometry_choice,
            branch_id=geometry_branch,
        ),
        BuilderSetScalarCommand(
            operation="set_scalar",
            control_id=acknowledged,
            value=True,
        ),
    )

    assert result.visual_draft.draft_revision == opened.draft_revision + 1
    assert result.visual_draft.applied_session is not None
    assert result.visual_draft.applied_session["session"]["description"] == "Edited graphically"
    assert result.visual_draft.applied_session["simulation"]["ground_link_model"] == "geometry_only"
    assert result.visual_draft.applied_session["simulation"]["acknowledge_geometry_only"] is True
    assert result.visual_draft.applied_session["simulation"]["candidate_limits"] == {
        "max_pairs_per_rule": 500,
        "max_pairs_per_tick": 2000,
    }
    assert result.assembled_draft.draft_revision == result.visual_draft.draft_revision


def test_complex_sequence_item_insert_uses_virtual_branch_and_child_controls(
    service: BuilderVisualDraftService,
) -> None:
    opened = _open(service)
    build = _build(opened)
    segments = _id(build, pointer="/segments", role="sequence")
    segment_choice = _id(
        build,
        pointer="/segments/-",
        role="choice",
        owner=SegmentSessionConfig,
        field="segments",
    )
    space_branch = _id(
        build,
        pointer="/segments/-",
        role="choice_branch",
        owner=SegmentSessionConfig,
        field="segments",
        choice_value=SpaceSegment,
    )
    segment_id = _id(
        build,
        pointer="/segments/-/id",
        role="scalar",
        owner=SpaceSegment,
        field="id",
    )
    source = _id(
        build,
        pointer="/segments/-/source",
        role="scalar",
        owner=SpaceSegment,
        field="source",
    )
    assert opened.applied_session is not None
    source_ref = opened.applied_session["segments"][0]["source"]
    insert_at = len(opened.applied_session["segments"])

    result = _mutate(
        service,
        opened,
        BuilderInsertItemCommand(
            operation="insert_item",
            control_id=segments,
            index=insert_at,
        ),
        BuilderSelectChoiceCommand(
            operation="select_choice",
            control_id=segment_choice,
            branch_id=space_branch,
        ),
        BuilderSetScalarCommand(
            operation="set_scalar",
            control_id=segment_id,
            value="extra-space",
        ),
        BuilderSetScalarCommand(
            operation="set_scalar",
            control_id=source,
            value=source_ref,
        ),
    )

    assert result.visual_draft.applied_session is not None
    inserted = result.visual_draft.applied_session["segments"][insert_at]
    assert inserted == {"id": "extra-space", "source": source_ref}
    assert result.visual_draft.applied_workspace is not None
    assert result.visual_draft.applied_workspace.control_tree is not None


def test_runtime_gated_choice_remains_saveable_and_deploy_blocked(
    service: BuilderVisualDraftService,
) -> None:
    opened = _open(service)
    build = _build(opened)
    model_choice = _id(
        build,
        pointer="/segments/0/clock/model",
        role="choice",
        owner=SegmentClock,
        field="model",
        trail_fragment=("item", "0", "union", "0", "control"),
    )
    affine_branch = _id(
        build,
        pointer="/segments/0/clock/model",
        role="literal_branch",
        owner=SegmentClock,
        field="model",
        choice_value="affine",
        trail_fragment=("item", "0", "union", "0", "control"),
    )
    rate = _id(
        build,
        pointer="/segments/0/clock/rate",
        role="scalar",
        owner=SegmentClock,
        field="rate",
        trail_fragment=("item", "0", "union", "0", "control"),
    )

    result = _mutate(
        service,
        opened,
        BuilderSelectChoiceCommand(
            operation="select_choice",
            control_id=model_choice,
            branch_id=affine_branch,
        ),
        BuilderSetScalarCommand(
            operation="set_scalar",
            control_id=rate,
            value=2.0,
        ),
    )

    assert result.compile_result.save_verdict.allowed is True
    assert result.compile_result.deploy_eligibility_after_save.allowed is False
    assert any(issue.stage == "runtime_support" for issue in result.compile_result.issues)


def test_deep_selector_is_created_and_then_edited_through_recursive_controls(
    service: BuilderVisualDraftService,
) -> None:
    opened = _open(service)
    assert opened.applied_session is not None
    document = deepcopy(opened.applied_session)
    document["link_rules"][0]["endpoints"][0]["select"] = {"segment": "ground"}
    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(document, sort_keys=False),
        )
    ).draft
    build = _build(applied)
    segment = _id(
        build,
        pointer="/link_rules/0/endpoints/0/select/segment",
        role="choice",
        owner=NodeSelector,
        field="segment",
    )
    nested_plane = _id(
        build,
        pointer="/link_rules/0/endpoints/0/select/not/plane",
        role="scalar",
        owner=NodeSelector,
        field="plane",
    )

    created = _mutate(
        service,
        applied,
        BuilderSetPresentCommand(
            operation="set_present",
            control_id=segment,
            present=False,
        ),
        BuilderSetScalarCommand(
            operation="set_scalar",
            control_id=nested_plane,
            value=2,
        ),
    )
    assert created.visual_draft.applied_session is not None
    selector = created.visual_draft.applied_session["link_rules"][0]["endpoints"][0]["select"]
    assert selector == {"not": {"plane": 2}}

    next_build = _build(created.visual_draft)
    concrete_plane = _id(
        next_build,
        pointer="/link_rules/0/endpoints/0/select/not/plane",
        role="scalar",
        owner=NodeSelector,
        field="plane",
    )
    edited = _mutate(
        service,
        created.visual_draft,
        BuilderSetScalarCommand(
            operation="set_scalar",
            control_id=concrete_plane,
            value=3,
        ),
    )
    assert edited.visual_draft.applied_session is not None
    assert edited.visual_draft.applied_session["link_rules"][0]["endpoints"][0]["select"] == {
        "not": {"plane": 3}
    }


def test_invalid_stale_unknown_and_pending_batches_advance_nothing(
    service: BuilderVisualDraftService,
) -> None:
    opened = _open(service)
    build = _build(opened)
    step = _id(
        build,
        pointer="/time/step_seconds",
        role="scalar",
        owner=TimeConfig,
        field="step_seconds",
    )

    with pytest.raises(BuilderVisualDraftCommandError) as stale:
        service.mutate_controls(
            BuilderVisualControlMutationRequest(
                draft=opened,
                expected_draft_revision=opened.draft_revision + 1,
                commands=(
                    BuilderSetScalarCommand(
                        operation="set_scalar",
                        control_id=step,
                        value=2,
                    ),
                ),
            ),
            available_node_count=1_000_000,
        )
    assert stale.value.code == "catalog_authoring.stale_revision"

    with pytest.raises(BuilderVisualDraftCommandError) as unknown:
        _mutate(
            service,
            opened,
            BuilderSetScalarCommand(
                operation="set_scalar",
                control_id="ctl_00000000000000000000000000000000",
                value=2,
            ),
        )
    assert unknown.value.code == "catalog_authoring.invalid_patch"

    with pytest.raises(BuilderVisualDraftCommandError) as invalid:
        _mutate(
            service,
            opened,
            BuilderSetScalarCommand(
                operation="set_scalar",
                control_id=step,
                value=0,
            ),
        )
    assert invalid.value.code == "catalog_authoring.invalid_graph"
    assert opened.draft_revision == 0

    dirty_yaml = "session:\n  name: earth-leo-simple\nsegments: [unterminated"
    pending = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=0,
            buffer_generation=1,
            yaml_text=dirty_yaml,
        )
    ).draft
    with pytest.raises(BuilderVisualDraftCommandError) as dirty:
        _mutate(
            service,
            pending,
            BuilderSetScalarCommand(
                operation="set_scalar",
                control_id=step,
                value=2,
            ),
        )
    assert dirty.value.code == "catalog_authoring.invalid_graph"
    assert pending.session_yaml == dirty_yaml
    assert pending.draft_revision == opened.draft_revision


def test_pending_workspace_can_be_completed_without_residual_tree_deadlock(
    service: BuilderVisualDraftService,
) -> None:
    opened = _open(service)
    invalid = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=0,
            buffer_generation=2,
            yaml_text="session: [unterminated",
        )
    ).draft
    assert invalid.authoring_workspace is not None
    assert invalid.authoring_workspace.control_tree is None

    completed = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=invalid,
            expected_draft_revision=invalid.draft_revision,
            workspace=invalid.authoring_workspace,
        ),
        available_node_count=1_000_000,
        preview_factory=lambda raw, _roots: builder_world_preview(raw["session"]["name"]),
    )

    assert completed.visual_draft.projection_status == "applied"
    assert completed.visual_draft.applied_workspace is not None
    assert completed.visual_draft.applied_workspace.control_tree is not None


def test_all_structural_operations_are_ordered_and_atomic() -> None:
    sequence_binding = BuilderControlBinding(
        projection_revision=1,
        json_pointer="/items",
        role="sequence",
        annotation=tuple[str, ...],
        owner_model=None,
        field_name=None,
        annotation_path=(),
        trail=("root", "field", "items"),
    )
    map_binding = BuilderControlBinding(
        projection_revision=1,
        json_pointer="/mapping",
        role="mapping",
        annotation=dict[str, int],
        owner_model=None,
        field_name=None,
        annotation_path=(),
        trail=("root", "field", "mapping"),
    )
    bindings = {
        "ctl_11111111111111111111111111111111": sequence_binding,
        "ctl_22222222222222222222222222222222": map_binding,
    }
    document = {"items": ["alpha", "beta"], "mapping": {"one": 1, "two": 2}}

    moved = apply_builder_control_mutations(
        document,
        bindings,
        (
            BuilderMoveItemCommand(
                operation="move_item",
                control_id="ctl_11111111111111111111111111111111",
                from_index=0,
                to_index=1,
            ),
        ),
    )
    assert moved["items"] == ["beta", "alpha"]
    removed = apply_builder_control_mutations(
        moved,
        bindings,
        (
            BuilderRemoveItemCommand(
                operation="remove_item",
                control_id="ctl_11111111111111111111111111111111",
                index=0,
            ),
        ),
    )
    assert removed["items"] == ["alpha"]
    inserted = apply_builder_control_mutations(
        removed,
        bindings,
        (
            BuilderInsertMapEntryCommand(
                operation="insert_map_entry",
                control_id="ctl_22222222222222222222222222222222",
                key="three",
                value=3,
            ),
        ),
    )
    renamed = apply_builder_control_mutations(
        inserted,
        bindings,
        (
            BuilderRenameMapKeyCommand(
                operation="rename_map_key",
                control_id="ctl_22222222222222222222222222222222",
                index=2,
                key="third",
            ),
        ),
    )
    assert renamed["mapping"] == {"one": 1, "two": 2, "third": 3}
    map_removed = apply_builder_control_mutations(
        renamed,
        bindings,
        (
            BuilderRemoveMapEntryCommand(
                operation="remove_map_entry",
                control_id="ctl_22222222222222222222222222222222",
                index=1,
            ),
        ),
    )
    assert map_removed["mapping"] == {"one": 1, "third": 3}


def test_structured_canonical_map_values_use_virtual_child_controls() -> None:
    map_annotation = SiteNode.model_fields["terminals"].annotation
    map_trail = ("root", "field", "terminals")
    bindings = {
        "ctl_33333333333333333333333333333333": BuilderControlBinding(
            projection_revision=1,
            json_pointer="/terminals",
            role="mapping",
            annotation=map_annotation,
            owner_model=SiteNode,
            field_name="terminals",
            annotation_path=(),
            trail=map_trail,
        ),
        "ctl_44444444444444444444444444444444": BuilderControlBinding(
            projection_revision=1,
            json_pointer="/terminals/-/installed_count",
            role="scalar",
            annotation=int,
            owner_model=TerminalInstallation,
            field_name="installed_count",
            annotation_path=(),
            trail=(*map_trail, "add-value", "field", "installed_count"),
        ),
    }

    mutated = apply_builder_control_mutations(
        {"terminals": {}},
        bindings,
        (
            BuilderInsertMapEntryCommand(
                operation="insert_map_entry",
                control_id="ctl_33333333333333333333333333333333",
                key="access",
                value=None,
            ),
            BuilderSetScalarCommand(
                operation="set_scalar",
                control_id="ctl_44444444444444444444444444444444",
                value=2,
            ),
        ),
    )

    validated = TypeAdapter(map_annotation).validate_python(mutated["terminals"])
    assert validated["access"].installed_count == 2
