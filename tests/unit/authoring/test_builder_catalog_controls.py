# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import get_args

import pytest
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import CATALOG_FAMILY_REGISTRY
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_catalog_api import (
    CatalogComponentDraftEnvelope,
    CatalogComponentFamily,
    CatalogDraftControlMutationRequest,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
)
from nodalarc.models.builder_controls_api import (
    BuilderChoiceControl,
    BuilderControl,
    BuilderInsertItemCommand,
    BuilderObjectControl,
    BuilderScalarControl,
    BuilderSelectChoiceCommand,
    BuilderSequenceControl,
    BuilderSetScalarCommand,
)
from pydantic import ValidationError
from vs_api.builder_catalog_draft import BuilderCatalogDraftService
from vs_api.builder_catalog_service import CatalogAuthoringError
from vs_api.builder_control_tree import build_model_control_tree
from vs_api.catalog_context import CatalogContext

from tests.support.builder_controls import as_browser_sends, control_at, walk_controls
from tests.support.builder_model_coverage import (
    BuilderGraphicalCoverageRecorder,
    assert_complete_builder_graphical_coverage,
    discover_builder_model_graph,
)

ROOT = Path(__file__).resolve().parents[3]
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


def test_every_catalog_family_model_graph_has_exact_graphical_coverage() -> None:
    counts: dict[str, int] = {}
    for family in get_args(CatalogComponentFamily.__value__):
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

    assert set(counts) == set(get_args(CatalogComponentFamily.__value__))
    assert all(count > 1 for count in counts.values())


def test_component_controls_mutate_one_field_and_preserve_all_siblings(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    opened = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("bodies")))
    original = copy.deepcopy(opened.state.document)
    display_name = control_at(opened.control_tree.root, "/body/display_name")
    assert isinstance(display_name, BuilderScalarControl)

    updated = service.mutate_controls(
        CatalogDraftControlMutationRequest(
            draft=opened.state,
            expected_draft_revision=opened.state.draft_revision,
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
    assert updated.state.document == expected
    assert updated.state.draft_revision == opened.state.draft_revision + 1
    assert updated.control_tree.projection_revision == updated.state.draft_revision

    with pytest.raises(CatalogAuthoringError) as stale_control:
        service.mutate_controls(
            CatalogDraftControlMutationRequest(
                draft=updated.state,
                expected_draft_revision=updated.state.draft_revision,
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

    # A request carries the draft state only; the page cannot send the
    # backend's projection (tree, YAML, issues) back as if it were state.
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CatalogDraftControlMutationRequest.model_validate(
            {
                "draft": updated.model_dump(mode="json"),
                "expected_draft_revision": updated.state.draft_revision,
                "commands": [
                    {
                        "operation": "set_scalar",
                        "control_id": display_name.control_id,
                        "value": "Projection sent as state",
                    }
                ],
            }
        )


def _json_pointers(value: object, pointer: str = "") -> list[str]:
    pointers = [pointer]
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            pointers.extend(_json_pointers(item, f"{pointer}/{escaped}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            pointers.extend(_json_pointers(item, f"{pointer}/{index}"))
    return pointers


@pytest.mark.parametrize("family", get_args(CatalogComponentFamily.__value__))
def test_component_controls_render_every_field_of_every_shipped_component(
    tmp_path: Path,
    family: str,
) -> None:
    # The Library edits components through the control tree alone, over the
    # component's JSON document. Every value in a shipped component, those in
    # union-wrapped sequences (tags, selectors) included, has a control, and a
    # union holding a value selects its branch. Only the id is reserved.
    service = _service(tmp_path)
    shipped = sorted((SHIPPED_ROOT / family).rglob("*.yaml"))
    assert shipped, f"no shipped {family}"
    for path in shipped:
        ref = CatalogRef(f"nodalarc:{path.relative_to(SHIPPED_ROOT).as_posix()}")
        opened = service.open(CatalogDraftOpenRequest(source_ref=ref))
        controls = list(walk_controls(opened.control_tree.root))
        by_pointer: dict[str, list[BuilderControl]] = {}
        for control in controls:
            by_pointer.setdefault(control.json_pointer, []).append(control)
        missing = [
            pointer
            for pointer in _json_pointers(opened.state.document)[1:]
            if pointer not in by_pointer
        ]
        assert not missing, f"{ref}: no control for {missing}"
        unselected = [
            control.json_pointer
            for control in controls
            if isinstance(control, BuilderChoiceControl)
            and control.present
            and not any(branch.selected for branch in control.branches)
        ]
        assert not unselected, f"{ref}: no branch selected at {unselected}"
        specialized = {control.json_pointer for control in controls if control.specialized}
        wrapper = next(iter(opened.state.document))
        assert specialized <= {f"/{wrapper}/id"}, f"{ref}: specialized {sorted(specialized)}"


@pytest.mark.parametrize("family", get_args(CatalogComponentFamily.__value__))
def test_a_field_edit_sent_as_the_browser_sends_it_is_accepted(
    tmp_path: Path,
    family: str,
) -> None:
    # The page sends the draft back through browser JSON, which writes 450000.0
    # as 450000. The control ids name the document by value, so the backend's
    # tree still matches and the edit is accepted.
    service = _service(tmp_path)
    for path in sorted((SHIPPED_ROOT / family).rglob("*.yaml")):
        ref = CatalogRef(f"nodalarc:{path.relative_to(SHIPPED_ROOT).as_posix()}")
        opened = service.open(CatalogDraftOpenRequest(source_ref=ref))
        received = CatalogComponentDraftEnvelope.model_validate_json(
            json.dumps(as_browser_sends(opened.model_dump(mode="json")))
        )
        sent = received.state
        field = next(
            control
            for control in walk_controls(received.control_tree.root)
            if isinstance(control, BuilderScalarControl)
            and control.present
            and not control.specialized
            and isinstance(control.value, str)
        )
        updated = service.mutate_controls(
            CatalogDraftControlMutationRequest(
                draft=sent,
                expected_draft_revision=sent.draft_revision,
                commands=(
                    BuilderSetScalarCommand(
                        operation="set_scalar",
                        control_id=field.control_id,
                        value=field.value,
                    ),
                ),
            )
        )
        assert updated.state.draft_revision == sent.draft_revision + 1, ref
        assert updated.state.document == opened.state.document, ref


def test_component_controls_protect_identity_and_accept_incomplete_sequence_edits(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    body = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("bodies")))
    body_id = control_at(body.control_tree.root, "/body/id")
    assert isinstance(body_id, BuilderScalarControl)
    with pytest.raises(CatalogAuthoringError) as identity:
        service.mutate_controls(
            CatalogDraftControlMutationRequest(
                draft=body.state,
                expected_draft_revision=body.state.draft_revision,
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

    node_draft = service.new(CatalogDraftNewRequest(family="nodes", object_id="visual-node"))
    ports = control_at(node_draft.control_tree.root, "/node/ethernet")
    assert isinstance(ports, BuilderSequenceControl)
    inserted = service.mutate_controls(
        CatalogDraftControlMutationRequest(
            draft=node_draft.state,
            expected_draft_revision=node_draft.state.draft_revision,
            commands=(
                BuilderInsertItemCommand(
                    operation="insert_item",
                    control_id=ports.control_id,
                    index=0,
                ),
            ),
        )
    )
    assert inserted.state.document == {"node": {"id": "visual-node", "ethernet": [{}]}}
    assert inserted.issues

    site = service.new(CatalogDraftNewRequest(family="sites", object_id="visual-site"))
    frame = control_at(site.control_tree.root, "/site/frame")
    assert isinstance(frame, BuilderChoiceControl)
    assert [branch.label for branch in frame.branches] == [
        "Body fixed",
        "Lagrange",
        "Ephemeris anchor",
    ]
    body_fixed = frame.branches[0]
    assert isinstance(body_fixed.control, BuilderObjectControl)
    framed = service.mutate_controls(
        CatalogDraftControlMutationRequest(
            draft=site.state,
            expected_draft_revision=site.state.draft_revision,
            commands=(
                BuilderSelectChoiceCommand(
                    operation="select_choice",
                    control_id=frame.control_id,
                    branch_id=body_fixed.branch_id,
                ),
            ),
        )
    )
    assert framed.state.document["site"]["frame"] == {"body_fixed": {"body": ""}}
    selected_frame = control_at(framed.control_tree.root, "/site/frame")
    assert isinstance(selected_frame, BuilderChoiceControl)
    assert next(branch for branch in selected_frame.branches if branch.selected).branch_id != ""

    node = service.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("nodes")))
    forwarding = control_at(node.control_tree.root, "/node/forwarding")
    assert isinstance(forwarding, BuilderChoiceControl)
    branch = next(branch for branch in forwarding.branches if not branch.selected)
    changed = service.mutate_controls(
        CatalogDraftControlMutationRequest(
            draft=node.state,
            expected_draft_revision=node.state.draft_revision,
            commands=(
                BuilderSelectChoiceCommand(
                    operation="select_choice",
                    control_id=forwarding.control_id,
                    branch_id=branch.branch_id,
                ),
            ),
        )
    )
    assert changed.state.document["node"]["forwarding"] == branch.literal_value

    orbit = service.new(CatalogDraftNewRequest(family="orbits", object_id="future-orbit"))
    propagator = control_at(orbit.control_tree.root, "/orbit/propagator")
    assert isinstance(propagator, BuilderChoiceControl)
    assert {branch.literal_value for branch in propagator.branches} == {
        "two_body",
        "j2_mean_elements",
        "crtbp",
    }
