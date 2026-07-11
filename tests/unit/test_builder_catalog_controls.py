from __future__ import annotations

import copy
from pathlib import Path
from typing import get_args

import pytest
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import CATALOG_FAMILY_REGISTRY
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_catalog_api import (
    CatalogComponentFamily,
    CatalogDraftControlMutationRequest,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
)
from nodalarc.models.builder_controls_api import (
    BuilderChoiceControl,
    BuilderControl,
    BuilderInsertItemCommand,
    BuilderMapControl,
    BuilderObjectControl,
    BuilderScalarControl,
    BuilderSelectChoiceCommand,
    BuilderSequenceControl,
    BuilderSetScalarCommand,
)
from vs_api.builder_catalog_draft import BuilderCatalogDraftService
from vs_api.builder_catalog_service import CatalogAuthoringError
from vs_api.builder_control_tree import build_model_control_tree
from vs_api.catalog_context import CatalogContext

from tests.support.builder_model_coverage import (
    BuilderGraphicalCoverageRecorder,
    assert_complete_builder_graphical_coverage,
    discover_builder_model_graph,
)

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"


def _service(tmp_path: Path) -> BuilderCatalogDraftService:
    scope = CatalogScope()
    return BuilderCatalogDraftService(
        CatalogContext(
            repository=FilesystemCatalogRepository(
                shipped_root=SHIPPED_ROOT,
                scope_roots={scope: tmp_path / "user-catalog"},
            ),
            scope=scope,
        )
    )


def _first_shipped_ref(family: str) -> CatalogRef:
    source = sorted((SHIPPED_ROOT / family).rglob("*.yaml"))[0]
    return CatalogRef(f"nodalarc:{source.relative_to(SHIPPED_ROOT).as_posix()}")


def _walk_controls(control: BuilderControl):
    yield control
    if isinstance(control, BuilderObjectControl):
        for field in control.fields:
            yield from _walk_controls(field.control)
    elif isinstance(control, BuilderChoiceControl):
        for branch in control.branches:
            if branch.control is not None:
                yield from _walk_controls(branch.control)
    elif isinstance(control, BuilderSequenceControl):
        for item in control.items:
            yield from _walk_controls(item.control)
        if control.add_item_control is not None:
            yield from _walk_controls(control.add_item_control)
    elif isinstance(control, BuilderMapControl):
        for entry in control.entries:
            yield from _walk_controls(entry.key)
            yield from _walk_controls(entry.value)
        yield from _walk_controls(control.add_key_control)
        yield from _walk_controls(control.add_value_control)


def _control_at(root: BuilderControl, pointer: str) -> BuilderControl:
    return next(control for control in _walk_controls(root) if control.json_pointer == pointer)


def test_every_catalog_family_model_graph_has_exact_graphical_coverage() -> None:
    counts: dict[str, int] = {}
    for family in get_args(CatalogComponentFamily):
        spec = CATALOG_FAMILY_REGISTRY[family]
        assert spec.wrapper is not None
        graph = discover_builder_model_graph(spec.document_model_type)
        recorder = BuilderGraphicalCoverageRecorder()
        build_model_control_tree(
            spec.document_model_type,
            {spec.wrapper: {"id": f"coverage-{family.replace('-', '_')}"}},
            projection_revision=0,
            root_label=f"{family} coverage",
            instrument=recorder.record,
        )
        assert_complete_builder_graphical_coverage(
            graph,
            recorder.obligation_keys,
            registry_name=f"{family} catalog controls",
        )
        counts[family] = len(graph.obligation_keys)

    assert set(counts) == set(get_args(CatalogComponentFamily))
    assert all(count > 1 for count in counts.values())


def test_component_controls_mutate_one_field_and_preserve_all_siblings(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    opened = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("bodies")))
    original = copy.deepcopy(opened.document)
    display_name = _control_at(opened.control_tree.root, "/body/display_name")
    assert isinstance(display_name, BuilderScalarControl)

    updated = service.mutate_controls(
        CatalogDraftControlMutationRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            commands=(
                BuilderSetScalarCommand(
                    operation="set_scalar",
                    control_id=display_name.control_id,
                    value="Graphically edited body",
                ),
            ),
        )
    )

    expected = copy.deepcopy(original)
    expected["body"]["display_name"] = "Graphically edited body"
    assert updated.document == expected
    assert updated.draft_revision == opened.draft_revision + 1
    assert updated.control_tree.projection_revision == updated.draft_revision

    with pytest.raises(CatalogAuthoringError) as stale_control:
        service.mutate_controls(
            CatalogDraftControlMutationRequest(
                draft=updated,
                expected_draft_revision=updated.draft_revision,
                commands=(
                    BuilderSetScalarCommand(
                        operation="set_scalar",
                        control_id=display_name.control_id,
                        value="Stale control must fail",
                    ),
                ),
            )
        )
    assert stale_control.value.code == "catalog_authoring.invalid_patch"

    tampered_tree = updated.control_tree.model_copy(
        update={
            "root": updated.control_tree.root.model_copy(
                update={"label": "Client-authored control tree"}
            )
        }
    )
    with pytest.raises(CatalogAuthoringError) as tampered:
        service.mutate_controls(
            CatalogDraftControlMutationRequest(
                draft=updated.model_copy(update={"control_tree": tampered_tree}),
                expected_draft_revision=updated.draft_revision,
                commands=(
                    BuilderSetScalarCommand(
                        operation="set_scalar",
                        control_id=_control_at(
                            updated.control_tree.root,
                            "/body/display_name",
                        ).control_id,
                        value="Tampered tree must fail",
                    ),
                ),
            )
        )
    assert tampered.value.code == "catalog_authoring.invalid_graph"


def test_specialized_markers_hide_only_fields_fully_owned_by_specialized_forms(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    terminal = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("terminals")))
    assert _control_at(terminal.control_tree.root, "/terminal/id").specialized is True
    assert (
        _control_at(
            terminal.control_tree.root,
            "/terminal/display_name",
        ).specialized
        is True
    )
    assert (
        _control_at(
            terminal.control_tree.root,
            "/terminal/limits/elevation_deg",
        ).specialized
        is True
    )
    assert (
        _control_at(
            terminal.control_tree.root,
            "/terminal/limits/azimuth_deg",
        ).specialized
        is False
    )
    assert _control_at(terminal.control_tree.root, "/terminal/notes").specialized is False

    node = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("nodes")))
    assert (
        _control_at(
            node.control_tree.root,
            "/node/terminals/0/role",
        ).specialized
        is True
    )
    assert (
        _control_at(
            node.control_tree.root,
            "/node/terminals/0/id",
        ).specialized
        is False
    )
    assert (
        _control_at(
            node.control_tree.root,
            "/node/ethernet/0/tags",
        ).specialized
        is False
    )

    site = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("sites")))
    assert _control_at(site.control_tree.root, "/site/location").specialized is False
    assert _control_at(site.control_tree.root, "/site/location/lat_deg").specialized is True


def test_component_controls_protect_identity_and_accept_incomplete_sequence_edits(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    body = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("bodies")))
    body_id = _control_at(body.control_tree.root, "/body/id")
    assert isinstance(body_id, BuilderScalarControl)
    with pytest.raises(CatalogAuthoringError) as identity:
        service.mutate_controls(
            CatalogDraftControlMutationRequest(
                draft=body,
                expected_draft_revision=body.draft_revision,
                commands=(
                    BuilderSetScalarCommand(
                        operation="set_scalar",
                        control_id=body_id.control_id,
                        value="renamed",
                    ),
                ),
            )
        )
    assert identity.value.code == "catalog_authoring.invalid_patch"

    payload = service.new(CatalogDraftNewRequest(family="payloads", object_id="visual-payload"))
    slots = _control_at(payload.control_tree.root, "/payload/terminal_slots")
    assert isinstance(slots, BuilderSequenceControl)
    inserted = service.mutate_controls(
        CatalogDraftControlMutationRequest(
            draft=payload,
            expected_draft_revision=payload.draft_revision,
            commands=(
                BuilderInsertItemCommand(
                    operation="insert_item",
                    control_id=slots.control_id,
                    index=0,
                ),
            ),
        )
    )
    assert inserted.document == {"payload": {"id": "visual-payload", "terminal_slots": [{}]}}
    assert inserted.issues

    site = service.new(CatalogDraftNewRequest(family="sites", object_id="visual-site"))
    frame = _control_at(site.control_tree.root, "/site/frame")
    assert isinstance(frame, BuilderChoiceControl)
    body_fixed = next(
        branch
        for branch in frame.branches
        if isinstance(branch.control, BuilderObjectControl)
        and branch.control.model_name is not None
        and branch.control.model_name.endswith(".BodyFixedFrameWrapper")
    )
    framed = service.mutate_controls(
        CatalogDraftControlMutationRequest(
            draft=site,
            expected_draft_revision=site.draft_revision,
            commands=(
                BuilderSelectChoiceCommand(
                    operation="select_choice",
                    control_id=frame.control_id,
                    branch_id=body_fixed.branch_id,
                ),
            ),
        )
    )
    assert framed.document["site"]["frame"] == {"body_fixed": {"body": ""}}
    selected_frame = _control_at(framed.control_tree.root, "/site/frame")
    assert isinstance(selected_frame, BuilderChoiceControl)
    assert next(branch for branch in selected_frame.branches if branch.selected).branch_id != ""

    node = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("nodes")))
    forwarding = _control_at(node.control_tree.root, "/node/forwarding")
    assert isinstance(forwarding, BuilderChoiceControl)
    branch = next(branch for branch in forwarding.branches if not branch.selected)
    changed = service.mutate_controls(
        CatalogDraftControlMutationRequest(
            draft=node,
            expected_draft_revision=node.draft_revision,
            commands=(
                BuilderSelectChoiceCommand(
                    operation="select_choice",
                    control_id=forwarding.control_id,
                    branch_id=branch.branch_id,
                ),
            ),
        )
    )
    assert changed.document["node"]["forwarding"] == branch.literal_value

    orbit = service.new(CatalogDraftNewRequest(family="orbits", object_id="future-orbit"))
    propagator = _control_at(orbit.control_tree.root, "/orbit/propagator")
    assert isinstance(propagator, BuilderChoiceControl)
    assert {branch.literal_value for branch in propagator.branches} == {
        "two_body",
        "j2_mean_elements",
        "crtbp",
    }
