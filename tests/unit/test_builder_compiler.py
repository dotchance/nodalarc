"""Focused contracts for read-only Builder draft compilation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import validate_referenced_configuration_document
from nodalarc.catalog_repository import CatalogReadSnapshot, CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import BuilderCompileRequest, BuilderDraftEnvelope
from nodalarc.models.builder_world import BuilderWorld
from vs_api.builder_compiler import canonicalize_persisted_configuration, compile_builder_draft

from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
SHIPPED_CONSTELLATION = SHIPPED_ROOT / "constellations" / "earth" / "leo" / "earth-leo-ring-36.yaml"
SHIPPED_NODE = SHIPPED_ROOT / "nodes" / "space" / "starlink-v2-mesh.yaml"
SHIPPED_SESSIONS = tuple(sorted((SHIPPED_ROOT / "sessions").glob("*.yaml")))


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory: pytest.TempPathFactory) -> CatalogReadSnapshot:
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: tmp_path_factory.mktemp("builder-compiler-user")},
    )
    return repository.snapshot(scope)


def _request(
    session: dict[str, Any],
    *,
    catalog_documents: list[dict[str, Any]] | None = None,
) -> BuilderCompileRequest:
    name = session.get("session", {}).get("name", "incomplete")
    return BuilderCompileRequest(
        draft=BuilderDraftEnvelope(
            draft_revision=1,
            state={
                "session": session,
                "catalog_documents": catalog_documents or [],
            },
        ),
        target_ref=f"user:sessions/{name}.yaml",
    )


def _compile(
    request: BuilderCompileRequest,
    snapshot: CatalogReadSnapshot,
):
    return compile_builder_draft(
        request,
        snapshot,
        available_node_count=1_000_000,
        preview_factory=lambda raw, _roots: builder_world_preview(raw["session"]["name"]),
    )


def _deep_user_draft(*, node_display_name: str = "User spacecraft"):
    session = deepcopy(_load(SIMPLE_SESSION))
    session["session"]["name"] = "deep-user"
    session["segments"][0]["source"] = "user:constellations/user-ring.yaml"

    constellation = deepcopy(_load(SHIPPED_CONSTELLATION))
    constellation["constellation"]["id"] = "user-ring"
    constellation["constellation"]["node"] = "user:nodes/user-spacecraft.yaml"

    node = deepcopy(_load(SHIPPED_NODE))
    node["node"]["id"] = "user-spacecraft"
    node["node"]["display_name"] = node_display_name
    proposals = [
        {
            "ref": "user:constellations/user-ring.yaml",
            "document": constellation,
        },
        {
            "ref": "user:nodes/user-spacecraft.yaml",
            "document": node,
        },
    ]
    return session, proposals


@pytest.mark.parametrize(
    ("ref", "document"),
    (
        (
            CatalogRef("user:sessions/wrong-session.yaml"),
            _load(SIMPLE_SESSION),
        ),
        (
            CatalogRef("user:nodes/wrong-node.yaml"),
            _load(SHIPPED_NODE),
        ),
    ),
)
def test_canonicalizer_uses_shared_referenced_document_identity_contract(
    ref: CatalogRef,
    document: dict[str, Any],
) -> None:
    with pytest.raises(ValueError) as authority_error:
        validate_referenced_configuration_document(ref, document)

    with pytest.raises(ValueError) as canonicalizer_error:
        canonicalize_persisted_configuration(ref, document)

    assert str(canonicalizer_error.value) == str(authority_error.value)


def test_shipped_simple_no_op_compile_is_saveable_and_deployable(
    snapshot: CatalogReadSnapshot,
) -> None:
    before_generation = snapshot.generation
    before_user_documents = snapshot.list(namespace="user")

    result = _compile(_request(_load(SIMPLE_SESSION)), snapshot)

    assert result.save_verdict.allowed is True
    assert result.deploy_eligibility_after_save.allowed is True
    assert result.canonical_session_yaml is not None
    assert result.canonical_session_json is not None
    assert result.dependency_closure is not None
    assert result.dependency_closure.file_count > 0
    assert all(entry.ref.startswith("nodalarc:") for entry in result.dependency_closure.entries)
    assert all(
        entry.preserved_path
        == f"catalog/{entry.ref.namespace}/{entry.ref.relative_path.as_posix()}"
        for entry in result.dependency_closure.entries
    )
    assert result.digests is not None
    assert result.digests.resolved_semantic is not None
    assert result.resolved_preview == builder_world_preview("earth-leo-simple")
    assert snapshot.generation == before_generation
    assert snapshot.list(namespace="user") == before_user_documents


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_every_shipped_session_no_op_compiles_without_semantic_loss(
    snapshot: CatalogReadSnapshot,
    session_path: Path,
) -> None:
    raw = _load(session_path)

    first = _compile(_request(raw), snapshot)

    assert first.save_verdict.allowed is True
    assert first.deploy_eligibility_after_save.allowed is True
    assert first.canonical_session_json is not None
    assert first.canonical_session_yaml is not None
    assert first.dependency_closure is not None
    assert first.digests is not None

    reopened = _compile(_request(first.canonical_session_json), snapshot)
    assert reopened.save_verdict.allowed is True
    assert reopened.deploy_eligibility_after_save.allowed is True
    assert reopened.canonical_session_json == first.canonical_session_json
    assert reopened.canonical_session_yaml == first.canonical_session_yaml
    assert reopened.digests == first.digests


def test_default_preview_uses_the_existing_ome_builder_world(
    snapshot: CatalogReadSnapshot,
) -> None:
    result = compile_builder_draft(
        _request(_load(SIMPLE_SESSION)),
        snapshot,
        available_node_count=1_000_000,
    )

    assert result.save_verdict.allowed is True
    assert result.deploy_eligibility_after_save.allowed is True
    assert isinstance(result.resolved_preview, BuilderWorld)
    assert result.resolved_preview.session.name == "earth-leo-simple"
    assert result.resolved_preview.nodes


def test_direct_and_deep_user_refs_compile_without_flattening(
    snapshot: CatalogReadSnapshot,
) -> None:
    session, proposals = _deep_user_draft()

    result = _compile(_request(session, catalog_documents=proposals), snapshot)

    assert result.save_verdict.allowed is True
    assert result.canonical_session_json is not None
    assert result.canonical_session_json["segments"][0]["source"] == (
        "user:constellations/user-ring.yaml"
    )
    assert result.dependency_closure is not None
    entries = {str(entry.ref): entry for entry in result.dependency_closure.entries}
    assert "user:constellations/user-ring.yaml" in entries
    assert "user:nodes/user-spacecraft.yaml" in entries
    assert entries["user:constellations/user-ring.yaml"].revision is None
    assert entries["user:nodes/user-spacecraft.yaml"].revision is None


def test_deep_dependency_content_changes_dependency_digest(
    snapshot: CatalogReadSnapshot,
) -> None:
    first_session, first_proposals = _deep_user_draft(node_display_name="First")
    second_session, second_proposals = _deep_user_draft(node_display_name="Second")

    first = _compile(_request(first_session, catalog_documents=first_proposals), snapshot)
    second = _compile(_request(second_session, catalog_documents=second_proposals), snapshot)

    assert first.digests is not None
    assert second.digests is not None
    assert first.digests.document == second.digests.document
    assert first.digests.dependency != second.digests.dependency


def test_literal_user_reference_prose_is_not_a_dependency(
    snapshot: CatalogReadSnapshot,
) -> None:
    body = {
        "body": {
            "id": "prose-body",
            "display_name": "Prose body",
            "gravitational_parameter_km3_s2": 398600.4418,
            "mean_radius_km": 6371.0088,
            "equatorial_radius_km": 6378.137,
            "polar_radius_km": 6356.752,
            "reference": "urn:nodalarc:test",
            "notes": "This prose names user:nodes/missing.yaml but is not a ref slot.",
        }
    }
    result = _compile(
        _request(
            _load(SIMPLE_SESSION),
            catalog_documents=[{"ref": "user:bodies/prose-body.yaml", "document": body}],
        ),
        snapshot,
    )

    assert result.save_verdict.allowed is True
    assert all(issue.stage != "reference" for issue in result.issues)


def test_incomplete_inner_session_returns_structural_issues(
    snapshot: CatalogReadSnapshot,
) -> None:
    request = _request({"session": {"name": "incomplete"}})

    result = _compile(request, snapshot)

    assert result.save_verdict.allowed is False
    assert result.deploy_eligibility_after_save.allowed is False
    assert result.dependency_closure is None
    assert any(issue.stage == "structural" for issue in result.issues)
    assert all("save" in issue.blocks for issue in result.issues)


def test_dangling_reference_and_semantic_failure_have_distinct_stages(
    snapshot: CatalogReadSnapshot,
) -> None:
    dangling = deepcopy(_load(SIMPLE_SESSION))
    dangling["session"]["name"] = "dangling"
    dangling["segments"][0]["source"] = "user:constellations/missing.yaml"
    dangling_result = _compile(_request(dangling), snapshot)
    assert dangling_result.save_verdict.allowed is False
    assert {issue.stage for issue in dangling_result.issues} == {"reference"}

    semantic = deepcopy(_load(SIMPLE_SESSION))
    semantic["session"]["name"] = "semantic-invalid"
    semantic["link_rules"][0]["endpoints"][1]["select"] = {"segment": "missing"}
    semantic_result = _compile(_request(semantic), snapshot)
    assert semantic_result.save_verdict.allowed is False
    assert any(issue.stage == "semantic" for issue in semantic_result.issues)


def test_runtime_unsupported_and_readiness_failures_block_only_deploy(
    snapshot: CatalogReadSnapshot,
) -> None:
    unsupported = deepcopy(_load(SIMPLE_SESSION))
    unsupported["session"]["name"] = "unsupported-affine"
    unsupported["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    unsupported_result = _compile(_request(unsupported), snapshot)
    assert unsupported_result.save_verdict.allowed is True
    assert unsupported_result.deploy_eligibility_after_save.allowed is False
    runtime_issues = [
        issue for issue in unsupported_result.issues if issue.stage == "runtime_support"
    ]
    assert runtime_issues
    assert all(issue.blocks == ("deploy",) for issue in runtime_issues)

    ground_only = deepcopy(_load(SIMPLE_SESSION))
    ground_only["session"]["name"] = "ground-only"
    ground_only["segments"] = [
        segment for segment in ground_only["segments"] if segment["id"] == "ground"
    ]
    ground_only.pop("link_rules", None)
    ground_only.pop("simulation", None)
    readiness_result = _compile(_request(ground_only), snapshot)
    assert readiness_result.save_verdict.allowed is True
    assert readiness_result.deploy_eligibility_after_save.allowed is False
    readiness_issues = [issue for issue in readiness_result.issues if issue.stage == "readiness"]
    assert any(issue.code == "builder.readiness.no_satellites" for issue in readiness_issues)
    assert all("save" not in issue.blocks for issue in readiness_issues)


def test_canonical_compile_is_idempotent_and_does_not_write(
    snapshot: CatalogReadSnapshot,
) -> None:
    session, proposals = _deep_user_draft()
    first = _compile(_request(session, catalog_documents=proposals), snapshot)
    assert first.canonical_session_json is not None

    second = _compile(
        _request(first.canonical_session_json, catalog_documents=proposals),
        snapshot,
    )

    assert first.canonical_session_yaml == second.canonical_session_yaml
    assert first.canonical_session_json == second.canonical_session_json
    assert first.digests == second.digests
    assert snapshot.list(namespace="user") == ()


def test_stale_proposed_revision_blocks_save_without_hiding_compile_facts(
    snapshot: CatalogReadSnapshot,
) -> None:
    body = {
        "body": {
            "id": "missing-revision-body",
            "display_name": "Missing revision body",
            "gravitational_parameter_km3_s2": 398600.4418,
            "mean_radius_km": 6371.0088,
            "equatorial_radius_km": 6378.137,
            "polar_radius_km": 6356.752,
            "reference": "urn:nodalarc:test",
        }
    }
    request = _request(
        _load(SIMPLE_SESSION),
        catalog_documents=[
            {
                "ref": "user:bodies/missing-revision-body.yaml",
                "document": body,
                "expected_revision": f"sha256:{'0' * 64}",
            }
        ],
    )

    result = _compile(request, snapshot)

    assert result.save_verdict.allowed is False
    assert result.digests is not None
    assert result.resolved_preview is not None
    assert any(issue.stage == "staleness" for issue in result.issues)


def test_expected_preview_failure_is_a_typed_deploy_blocker(
    snapshot: CatalogReadSnapshot,
) -> None:
    def unavailable(_raw: dict[str, Any], _roots: object):
        raise ValueError("preview physics unavailable")

    result = compile_builder_draft(
        _request(_load(SIMPLE_SESSION)),
        snapshot,
        available_node_count=1_000_000,
        preview_factory=unavailable,
    )

    assert result.save_verdict.allowed is True
    assert result.deploy_eligibility_after_save.allowed is False
    issue = next(
        issue for issue in result.issues if issue.code == "builder.readiness.preview_unavailable"
    )
    assert issue.stage == "readiness"
    assert issue.blocks == ("deploy",)
