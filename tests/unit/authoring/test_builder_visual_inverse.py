"""Focused contracts for the stateless canonical-to-visual inverse."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_visual_api import (
    BuilderVisualCustomizeChainRequest,
    BuilderVisualDraftApplyWorkspaceRequest,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftOpenRequest,
    BuilderVisualWorkspace,
)
from nodalarc.models.link_rules import LinkRule
from nodalarc.models.segment_session import RoutingBoundary, RoutingDomain, SegmentSessionConfig
from pydantic import BaseModel, ValidationError
from vs_api.builder_compiler import canonicalize_persisted_configuration
from vs_api.builder_session_service import save_builder_session
from vs_api.builder_visual_draft import (
    BuilderVisualDraftCommandError,
    BuilderVisualDraftService,
    _applied_control_tree,
    _assemble_structured,
    _is_builder_owned_component_ref,
    _link_projection,
    _link_rule_document,
    _routing_boundary_documents,
    _routing_boundary_projection,
    _routing_domain_document,
    _routing_domain_projection,
    _workspace_from_applied_session,
)
from vs_api.catalog_context import CatalogContext

from tests.builder_world_fixtures import preview_from_resolution

ROOT = Path(__file__).resolve().parents[3]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SHIPPED_SESSIONS = tuple(sorted((SHIPPED_ROOT / "sessions").glob("*.yaml")))


def test_stored_component_ownership_requires_supported_family_and_session_prefix() -> None:
    assert _is_builder_owned_component_ref(
        CatalogRef("user:constellations/alpha/generated.yaml"),
        owner="alpha",
    )
    assert not _is_builder_owned_component_ref(
        CatalogRef("user:terminals/alpha/generated.yaml"),
        owner="alpha",
    )
    assert not _is_builder_owned_component_ref(
        CatalogRef("user:constellations/beta/generated.yaml"),
        owner="alpha",
    )
    assert not _is_builder_owned_component_ref(
        CatalogRef("nodalarc:constellations/alpha/generated.yaml"),
        owner="alpha",
    )


@pytest.fixture()
def context(tmp_path: Path) -> CatalogContext:
    scope = CatalogScope()
    return CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / "user-catalog"},
        ),
        scope=scope,
    )


@pytest.fixture()
def service(context: CatalogContext) -> BuilderVisualDraftService:
    return BuilderVisualDraftService(context)


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_open_projects_and_no_edit_compiles_every_shipped_session_canonically(
    session_path: Path,
    service: BuilderVisualDraftService,
) -> None:
    source_ref = f"nodalarc:sessions/{session_path.name}"
    opened = service.open(BuilderVisualDraftOpenRequest(source_ref=source_ref))

    assert opened.projection_status == "applied"
    assert opened.session_yaml == session_path.read_text(encoding="utf-8")
    assert opened.applied_revision == opened.draft_revision == 0
    assert opened.applied_workspace == opened.authoring_workspace
    assert opened.applied_workspace is not None
    assert opened.control_tree is not None
    assert opened.control_tree.projection_revision == opened.applied_revision

    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )
    expected_document = yaml.safe_load(session_path.read_bytes())
    expected = canonicalize_persisted_configuration(opened.target_ref, expected_document)

    assert compiled.assembly_issues == ()
    assert compiled.compile_result.canonical_session_json == expected.canonical_json


def _canonical(model: BaseModel) -> Any:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_every_shipped_object_offered_a_card_saves_back_unchanged(session_path: Path) -> None:
    canonical: dict[str, Any] = canonicalize_persisted_configuration(
        CatalogRef(f"nodalarc:sessions/{session_path.name}"),
        yaml.safe_load(session_path.read_bytes()),
    ).canonical_json
    session = SegmentSessionConfig.model_validate(canonical)
    routing = canonical.get("routing", {})

    for rule, saved_rule in zip(
        session.link_rules or (), canonical.get("link_rules", []), strict=True
    ):
        if (card := _link_projection(rule)) is not None:
            written, issues = _link_rule_document(card)
            assert issues == ()
            assert _canonical(LinkRule.model_validate(written)) == saved_rule
    for domain, saved_domain in zip(
        session.routing.domains if session.routing is not None else (),
        routing.get("domains", []),
        strict=True,
    ):
        if (domain_card := _routing_domain_projection(domain)) is not None:
            written, issues = _routing_domain_document(domain_card)
            assert issues == ()
            assert _canonical(RoutingDomain.model_validate(written)) == saved_domain
    for index, (boundary, saved_boundary) in enumerate(
        zip(
            session.routing.boundaries or () if session.routing is not None else (),
            routing.get("boundaries", []),
            strict=True,
        )
    ):
        if (boundary_card := _routing_boundary_projection(boundary, index=index)) is not None:
            written_list, issues = _routing_boundary_documents((boundary_card,))
            assert issues == ()
            assert _canonical(RoutingBoundary.model_validate(written_list[0])) == saved_boundary


def _isl_rule(**fields: Any) -> dict[str, Any]:
    endpoint = {
        "select": {"segment": "leo"},
        "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
    }
    return {
        "id": "leo_isl",
        "topology": {"mode": "nearest_n", "n": 2},
        "endpoints": [endpoint, endpoint],
        **fields,
    }


def _boundary(**export_fields: Any) -> dict[str, Any]:
    def export(source: str, target: str) -> dict[str, Any]:
        return {
            "from": source,
            "to": target,
            "prefixes": {"aggregate_of": "originated"},
            **export_fields,
        }

    return {
        "over": "geo_relay",
        "adapter": "static_ip",
        "export": [export("earth_domain", "luna_domain"), export("luna_domain", "earth_domain")],
    }


_LEO_ISL_REORDERED = _isl_rule(
    endpoints=[
        {
            "select": {"segment": "leo"},
            "terminal": {"all": [{"medium": "optical"}, {"role": "isl"}]},
        },
        _isl_rule()["endpoints"][1],
    ]
)
_DOMAIN = {"id": "earth_domain", "protocol": "isis", "area_assignment": {"strategy": "flat"}}
_PROJECTIONS: dict[str, Callable[[dict[str, Any]], BaseModel | None]] = {
    "rule": lambda document: _link_projection(LinkRule.model_validate(document)),
    "domain": lambda document: _routing_domain_projection(RoutingDomain.model_validate(document)),
    "boundary": lambda document: _routing_boundary_projection(
        RoutingBoundary.model_validate(document), index=0
    ),
}


@pytest.mark.parametrize(
    ("kind", "card_shape", "rewritten_shape"),
    [
        pytest.param("rule", _isl_rule(), _LEO_ISL_REORDERED, id="reordered_terminal_predicates"),
        pytest.param("rule", _isl_rule(), _isl_rule(constraints={}), id="empty_constraints"),
        pytest.param("rule", _isl_rule(), _isl_rule(tags=[]), id="empty_tags"),
        pytest.param(
            "domain",
            {**_DOMAIN, "selectors": [{"any": [{"segment": "leo"}, {"segment": "geo"}]}]},
            {**_DOMAIN, "selectors": [{"segment": "leo"}, {"segment": "geo"}]},
            id="two_top_level_domain_selectors",
        ),
        pytest.param(
            "boundary",
            _boundary(export_node_loopbacks=True),
            _boundary(),
            id="boundary_export_without_export_node_loopbacks",
        ),
    ],
)
def test_a_card_is_offered_only_for_the_shape_it_writes(
    kind: str,
    card_shape: dict[str, Any],
    rewritten_shape: dict[str, Any],
) -> None:
    projection = _PROJECTIONS[kind]

    assert projection(card_shape) is not None
    assert projection(rewritten_shape) is None


def test_a_workspace_preview_compiles_the_edit_and_accepts_nothing(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.authoring_workspace is not None
    edited = opened.authoring_workspace.model_copy(
        update={"display_name": "Previewed name", "max_pairs_per_rule": 600}
    )

    preview = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened, workspace=edited),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    compiled_session = preview.compile_result.canonical_session_json
    assert compiled_session is not None
    assert compiled_session["session"]["display_name"] == "Previewed name"
    assert preview.compile_result.resolved_preview is not None
    assert preview.visual_draft == opened


def test_a_workspace_preview_needs_a_graphical_projection() -> None:
    unprojected = BuilderVisualDraftEnvelope(
        draft_revision=2,
        projection_status="no_valid_projection",
        target_ref="user:sessions/unprojected.yaml",
        session_name_is_placeholder=False,
        reserved_authoring_ids=(),
        session_yaml="session: [",
    )
    with pytest.raises(ValidationError, match="graphical projection"):
        BuilderVisualDraftCompileRequest(draft=unprojected, workspace=BuilderVisualWorkspace())


def test_overlay_changes_owned_metadata_without_touching_rich_unprojected_rule(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.applied_session is not None
    rich = deepcopy(opened.applied_session)
    rich_rule = rich["link_rules"][0]
    rich_rule["tags"] = ["preserve-me"]
    constraints = rich_rule.setdefault("constraints", {})
    constraints["max_links_per_node"] = {"leo": 4, "ground": 1}
    constraints["require_mutual_visibility"] = True
    model = SegmentSessionConfig.model_validate(rich)
    workspace = _workspace_from_applied_session(model)
    assert all(rule.rule_id != rich_rule["id"] for rule in workspace.links)
    draft = opened.model_copy(
        update={
            "applied_session": rich,
            "applied_workspace": workspace,
            "authoring_workspace": workspace.model_copy(
                update={"display_name": "Changed graphically"}
            ),
        }
    )

    assembled, _proposals, issues = _assemble_structured(
        draft,
        allow_workspace_overlay=True,
    )

    assert issues == ()
    assert assembled["session"]["display_name"] == "Changed graphically"
    assert assembled["link_rules"][0] == rich_rule


def test_runtime_unsupported_valid_session_opens_and_remains_saveable(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    document = yaml.safe_load((SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml").read_bytes())
    document["session"]["name"] = "affine-authoring"
    document["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    content = yaml.safe_dump(document, sort_keys=False).encode("utf-8")
    snapshot = context.repository.snapshot(context.scope)
    with context.repository.begin(context.scope, base_generation=snapshot.generation) as unit:
        unit.write_bytes(
            "user:sessions/affine-authoring.yaml",
            content,
            expected_revision=None,
        )
        unit.commit()

    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="user:sessions/affine-authoring.yaml")
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    assert opened.projection_status == "applied"
    assert opened.applied_session["segments"][0]["clock"] == {
        "model": "affine",
        "rate": 2.0,
    }
    assert compiled.compile_result.save_verdict.allowed is True
    assert compiled.compile_result.deploy_eligibility_after_save.allowed is False
    assert any(issue.stage == "runtime_support" for issue in compiled.compile_result.issues)


def test_yaml_apply_advances_once_and_preserves_exact_buffer(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    canonical_yaml = canonicalize_persisted_configuration(
        opened.target_ref,
        yaml.safe_load(opened.session_yaml),
    ).yaml_bytes.decode("utf-8")

    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=17,
            yaml_text=canonical_yaml,
        )
    )

    assert applied.applied is True
    assert applied.buffer_generation == 17
    assert applied.yaml_text == canonical_yaml
    assert applied.canonicalization_required is False
    assert applied.draft.draft_revision == opened.draft_revision + 1
    assert applied.draft.applied_revision == applied.draft.draft_revision
    assert applied.draft.session_yaml == canonical_yaml
    assert applied.draft.authoring_workspace == applied.draft.applied_workspace


def test_yaml_apply_refusal_retains_last_valid_projection_and_source_location(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    invalid = "session:\n  name: earth-leo-simple\nsegments: [unterminated"

    refused = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=3,
            yaml_text=invalid,
        )
    )

    assert refused.applied is False
    assert refused.buffer_generation == 3
    assert refused.yaml_text == invalid
    assert refused.draft.draft_revision == opened.draft_revision
    assert refused.draft.projection_status == "pending_authoring"
    assert refused.draft.applied_workspace == opened.applied_workspace
    assert refused.draft.applied_session == opened.applied_session
    assert refused.draft.authoring_workspace == opened.applied_workspace
    assert refused.draft.control_tree is None
    assert refused.issues[0].code == "builder.draft.yaml.invalid_syntax"
    assert refused.issues[0].source_line == 3
    assert refused.issues[0].source_column is not None


def test_yaml_apply_rejects_unknown_fields_and_fixed_identity_with_source_mapping(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    document = yaml.safe_load(opened.session_yaml)
    document["unknown_builder_field"] = True
    unknown_text = yaml.safe_dump(document, sort_keys=False)
    unknown = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=2,
            yaml_text=unknown_text,
        )
    )
    issue = next(issue for issue in unknown.issues if issue.code.endswith("extra_forbidden"))
    assert unknown.applied is False
    assert unknown.buffer_generation == 2
    assert issue.json_pointer == "/unknown_builder_field"
    assert issue.source_line is not None
    assert issue.source_column == 1

    del document["unknown_builder_field"]
    document["session"]["name"] = "different-session"
    identity_text = yaml.safe_dump(document, sort_keys=False)
    identity = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=4,
            yaml_text=identity_text,
        )
    )
    assert identity.applied is False
    assert identity.issues[0].code == "builder.draft.yaml.fixed_identity"
    assert identity.issues[0].json_pointer == "/session/name"
    assert identity.issues[0].source_line == 2


def test_yaml_apply_is_revision_fenced(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )

    with pytest.raises(BuilderVisualDraftCommandError) as stale:
        service.apply_yaml(
            BuilderVisualDraftApplyYamlRequest(
                draft=opened,
                expected_draft_revision=opened.draft_revision + 1,
                buffer_generation=1,
                yaml_text=opened.session_yaml,
            )
        )

    assert stale.value.code == "catalog_authoring.stale_revision"


def test_no_valid_projection_cannot_customize_catalog_from_source_text(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="no-source-customize"))
    no_projection = draft.model_copy(
        update={
            "projection_status": "no_valid_projection",
            "authoring_workspace": None,
            "session_yaml": "session: [unterminated",
        }
    )

    result = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=no_projection,
            expected_draft_revision=no_projection.draft_revision,
            segment_id="leo",
            leaf_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        )
    )

    assert result.applied is False
    assert result.issues[0].code == "builder.draft.no_valid_projection"
    assert result.draft == no_projection


def test_yaml_apply_accepts_runtime_gated_grammar_and_compile_blocks_only_deploy(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    document = yaml.safe_load(opened.session_yaml)
    document["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    yaml_text = yaml.safe_dump(document, sort_keys=False)

    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=9,
            yaml_text=yaml_text,
        )
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=applied.draft),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    assert applied.applied is True
    assert applied.draft.applied_session["segments"][0]["clock"] == {
        "model": "affine",
        "rate": 2.0,
    }
    assert compiled.compile_result.save_verdict.allowed is True
    assert compiled.compile_result.deploy_eligibility_after_save.allowed is False
    assert any(issue.stage == "runtime_support" for issue in compiled.compile_result.issues)


def test_yaml_apply_accepts_structurally_valid_missing_refs_for_later_compile_refusal(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    document = yaml.safe_load(opened.session_yaml)
    document["segments"][0]["source"] = "user:constellations/missing.yaml"
    yaml_text = yaml.safe_dump(document, sort_keys=False)

    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=11,
            yaml_text=yaml_text,
        )
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=applied.draft),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    assert applied.applied is True
    assert compiled.compile_result.save_verdict.allowed is False
    assert any(issue.stage == "reference" for issue in compiled.compile_result.issues)


def test_workspace_apply_advances_once_and_compiles_the_exact_applied_revision(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.authoring_workspace is not None
    workspace = opened.authoring_workspace.model_copy(
        update={"display_name": "Changed graphically"}
    )

    result = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            workspace=workspace,
        ),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    assert result.visual_draft.draft_revision == opened.draft_revision + 1
    assert result.visual_draft.applied_revision == result.visual_draft.draft_revision
    assert result.visual_draft.projection_status == "applied"
    assert result.visual_draft.authoring_workspace.display_name == "Changed graphically"
    assert result.assembled_draft.draft_revision == result.visual_draft.draft_revision
    assert result.compile_result.save_verdict.allowed is True
    assert result.compile_result.canonical_session_json["session"]["display_name"] == (
        "Changed graphically"
    )


def test_workspace_apply_retains_last_applied_facts_when_graph_is_incomplete(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.authoring_workspace is not None
    placed = opened.authoring_workspace.space_refs[0].model_copy(update={"source_ref": None})
    workspace = opened.authoring_workspace.model_copy(update={"space_refs": (placed,)})

    result = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            workspace=workspace,
        ),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    assert result.visual_draft.draft_revision == opened.draft_revision + 1
    assert result.visual_draft.projection_status == "pending_authoring"
    assert result.visual_draft.applied_revision == opened.applied_revision
    assert result.visual_draft.applied_session == opened.applied_session
    assert result.visual_draft.applied_workspace == opened.applied_workspace
    assert result.visual_draft.control_tree is None
    assert result.compile_result.save_verdict.allowed is False
    assert any(
        issue.code == "builder.draft.space_source_ref_required" for issue in result.assembly_issues
    )


def test_workspace_apply_is_revision_fenced(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.authoring_workspace is not None

    with pytest.raises(BuilderVisualDraftCommandError) as stale:
        service.apply_workspace(
            BuilderVisualDraftApplyWorkspaceRequest(
                draft=opened,
                expected_draft_revision=1,
                workspace=opened.authoring_workspace,
            ),
            available_node_count=1_000_000,
        )

    assert stale.value.code == "catalog_authoring.stale_revision"


def test_workspace_apply_preserves_runtime_gated_fields_and_blocks_only_deploy(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    document = yaml.safe_load(opened.session_yaml)
    document["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    yaml_applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=0,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(document, sort_keys=False),
        )
    )
    assert yaml_applied.draft.authoring_workspace is not None
    workspace = yaml_applied.draft.authoring_workspace.model_copy(
        update={"description": "Still authorable"}
    )

    result = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=yaml_applied.draft,
            expected_draft_revision=yaml_applied.draft.draft_revision,
            workspace=workspace,
        ),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    assert result.visual_draft.projection_status == "applied"
    assert result.visual_draft.applied_session["segments"][0]["clock"] == {
        "model": "affine",
        "rate": 2.0,
    }
    assert result.compile_result.save_verdict.allowed is True
    assert result.compile_result.deploy_eligibility_after_save.allowed is False


def test_open_derives_reference_labels_from_catalog_documents(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )

    assert opened.authoring_workspace is not None
    assert opened.authoring_workspace.space_refs[0].label == ("Earth LEO simple 36-satellite ring")
    assert opened.authoring_workspace.ground_refs[0].label == ("Starlink PoP gateway sites")


def test_authored_ground_scheduling_command_preserves_unowned_ipv6_prefixes(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="ground-overlay"))
    ground = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=draft,
            expected_draft_revision=0,
            command={"operation": "add_ground"},
        ),
        available_node_count=1_000_000,
    ).draft
    minted = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=ground,
            expected_draft_revision=1,
            command={
                "operation": "mint_ground_members",
                "segment_id": "ground-1",
                "sites": [{"name": "Denver", "lat_deg": 39.7, "lon_deg": -104.9}],
            },
        ),
        available_node_count=1_000_000,
    ).draft
    assert minted.applied_session is not None
    assert minted.applied_revision is not None
    rich = deepcopy(minted.applied_session)
    rich["segments"][0]["apply"]["originated_prefixes"] = {"ipv6": ["lan0"]}
    model = SegmentSessionConfig.model_validate(rich)
    workspace = _workspace_from_applied_session(
        model,
        proposals=minted.catalog_documents,
    )
    enriched = minted.model_copy(
        update={
            "applied_session": rich,
            "applied_workspace": workspace,
            "authoring_workspace": workspace,
            "control_tree": _applied_control_tree(model, minted.applied_revision),
        }
    )

    scheduled = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=enriched,
            expected_draft_revision=enriched.draft_revision,
            command={
                "operation": "set_scheduling_preset",
                "segment_id": "ground-1",
                "preset": "geo-longest-pass",
            },
        ),
        available_node_count=1_000_000,
    ).draft

    assert scheduled.applied_session is not None
    apply = scheduled.applied_session["segments"][0]["apply"]
    assert apply["scheduling"]["handover_mode"] == "bbm"
    assert apply["originated_prefixes"]["ipv6"] == ["lan0"]


def test_saved_builder_session_reopens_as_refs_without_reowning_catalog_objects(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="rich-reopen"))

    def apply_command(command: dict[str, object]) -> None:
        nonlocal draft
        draft = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=preview_from_resolution,
        ).draft

    apply_command({"operation": "add_generated_space", "phasing_mode": "walker_delta"})
    apply_command({"operation": "add_ground"})
    apply_command(
        {
            "operation": "mint_ground_members",
            "segment_id": "ground-1",
            "sites": [{"name": "Denver", "lat_deg": 39.7, "lon_deg": -104.9}],
        }
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )
    assert compiled.compile_result.save_verdict.allowed, tuple(
        (issue.code, issue.message) for issue in compiled.compile_result.save_verdict.blockers
    )
    saved = save_builder_session(
        compiled.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    component_ref = next(
        entry.ref
        for entry in saved.dependency_closure.entries
        if entry.ref.namespace == "user" and entry.ref.family == "constellations"
    )
    snapshot = context.repository.snapshot(context.scope)
    stored_component = snapshot.get(component_ref)
    enriched_component = yaml.safe_load(stored_component.content)
    enriched_component["constellation"]["tags"] = ["independently-edited"]
    enriched_component["constellation"]["notes"] = "Edited through the catalog after session save"
    enriched = canonicalize_persisted_configuration(component_ref, enriched_component)
    transaction = context.repository.begin(context.scope)
    transaction.write_bytes(
        component_ref,
        enriched.yaml_bytes,
        expected_revision=stored_component.revision,
    )
    transaction.commit()

    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="user:sessions/rich-reopen.yaml")
    )
    assert opened.projection_status == "applied"
    assert opened.session_yaml == saved.session.canonical_yaml
    assert opened.authoring_workspace == opened.applied_workspace
    assert opened.authoring_workspace is not None
    assert opened.authoring_workspace.space == ()
    assert opened.authoring_workspace.ground == ()
    assert len(opened.authoring_workspace.space_refs) == 1
    assert len(opened.authoring_workspace.ground_refs) == 1
    assert opened.expected_session_revision == saved.session.revision
    assert opened.catalog_documents == ()

    no_op = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )
    assert no_op.assembly_issues == ()
    assert no_op.compile_result.canonical_session_yaml == saved.session.canonical_yaml

    edited = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            workspace=opened.authoring_workspace.model_copy(
                update={"description": "Unrelated session-only GUI edit"}
            ),
        ),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    ).visual_draft
    recompiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=edited),
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )
    resaved = save_builder_session(
        recompiled.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    assert resaved.session.ref == saved.session.ref
    assert {entry.ref for entry in resaved.dependency_closure.entries} == {
        entry.ref for entry in saved.dependency_closure.entries
    }
    assert context.repository.snapshot(context.scope).get(component_ref).content == (
        enriched.yaml_bytes
    )
    assert edited.catalog_documents == ()
    assert edited.authoring_workspace is not None
    assert edited.authoring_workspace.description == "Unrelated session-only GUI edit"
