from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef, SessionRef
from nodalarc.catalog_registry import (
    CATALOG_FAMILY_REGISTRY,
    validate_referenced_configuration_document,
)
from nodalarc.catalog_repository import CatalogNotFoundError, CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_catalog_api import (
    CatalogDeleteRequest,
    CatalogDependentsRequest,
    CatalogDocumentWriteRequest,
    CatalogForkRequest,
    CatalogGetRequest,
    CatalogListRequest,
    CatalogSessionYamlExportRequest,
    CatalogSessionYamlImportRequest,
    CatalogYamlImportFile,
)
from pydantic import ValidationError
from vs_api import builder_catalog_service as service_module
from vs_api.builder_catalog_service import (
    BuilderCatalogAuthoringService,
    CatalogAuthoringError,
)
from vs_api.builder_compiler import canonicalize_persisted_configuration
from vs_api.catalog_context import CatalogContext

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"


@dataclass(frozen=True)
class ServiceFixture:
    service: BuilderCatalogAuthoringService
    context: CatalogContext


def _service(tmp_path: Path, name: str) -> ServiceFixture:
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: tmp_path / name},
    )
    context = CatalogContext(repository=repository, scope=scope)
    return ServiceFixture(
        service=BuilderCatalogAuthoringService(
            context,
            page_token_secret=b"builder-catalog-test-page-key-0001",
        ),
        context=context,
    )


def _first_shipped_ref(family: str) -> CatalogRef:
    path = sorted((SHIPPED_ROOT / family).rglob("*.yaml"))[0]
    return CatalogRef(f"nodalarc:{path.relative_to(SHIPPED_ROOT).as_posix()}")


def _canonical_json(fixture: ServiceFixture, ref: str | CatalogRef) -> dict[str, Any]:
    return fixture.service.get_catalog(CatalogGetRequest(ref=CatalogRef(str(ref)))).canonical_json


def test_exact_document_validation_uses_shared_reference_identity_contract() -> None:
    ref = CatalogRef("user:nodes/wrong-node.yaml")
    document = yaml.safe_load(
        (SHIPPED_ROOT / "nodes/space/starlink-v2-mesh.yaml").read_text(encoding="utf-8")
    )
    content = yaml.safe_dump(document).encode("utf-8")

    with pytest.raises(ValueError) as authority_error:
        validate_referenced_configuration_document(ref, document)

    with pytest.raises(CatalogAuthoringError) as service_error:
        service_module._validated_exact_document(ref, content)

    assert service_error.value.refusal.cause_type == "ValueError"
    assert str(authority_error.value) in service_error.value.refusal.message


def _seed_session(
    fixture: ServiceFixture,
    name: str,
    *,
    source_ref: str | None = None,
    display_suffix: str = "",
) -> CatalogRef:
    source = _canonical_json(fixture, "nodalarc:sessions/earth-leo-simple.yaml")
    source["session"]["name"] = name
    source["session"]["display_name"] = f"Imported {name}{display_suffix}"
    if source_ref is not None:
        source["segments"][0]["source"] = source_ref
    target = CatalogRef(f"user:sessions/{name}.yaml")
    canonical = canonicalize_persisted_configuration(target, source)
    snapshot = fixture.context.repository.snapshot(fixture.context.scope)
    transaction = fixture.context.repository.begin(
        fixture.context.scope,
        base_generation=snapshot.generation,
    )
    transaction.write_bytes(target, canonical.yaml_bytes, expected_revision=None)
    transaction.commit()
    return target


def _import_request(
    export,
    *,
    commit: bool,
    proposal_token: str | None = None,
) -> CatalogSessionYamlImportRequest:
    return CatalogSessionYamlImportRequest(
        yaml_files=tuple(
            CatalogYamlImportFile(
                yaml_text=file.yaml_text,
                logical_path_hint=file.logical_path,
            )
            for file in export.files
        ),
        commit=commit,
        proposal_token=proposal_token,
    )


def _yaml_files(*yaml_texts: str) -> tuple[CatalogYamlImportFile, ...]:
    return tuple(CatalogYamlImportFile(yaml_text=text) for text in yaml_texts)


def _exported_yaml_by_ref(export) -> dict[CatalogRef, str]:
    result: dict[CatalogRef, str] = {}
    for file in export.files[1:]:
        namespace, relative = file.logical_path.removeprefix("catalog/").split("/", 1)
        result[CatalogRef(f"{namespace}:{relative}")] = file.yaml_text
    return result


def test_bootstrap_is_complete_scope_free_and_truthful(tmp_path: Path) -> None:
    fixture = _service(tmp_path, "bootstrap")
    bootstrap = fixture.service.bootstrap()

    assert {item.family for item in bootstrap.families} == set(CATALOG_FAMILY_REGISTRY)
    session = next(item for item in bootstrap.families if item.family == "sessions")
    assert session.wrapper is None
    assert session.session_draft_save is True
    assert session.direct_user_write is False
    assert session.suggested_object_id is None
    assert all(
        item.suggested_object_id is not None
        for item in bootstrap.families
        if item.family != "sessions"
    )
    assert bootstrap.capabilities.user_catalog_write is True
    assert bootstrap.capabilities.deploy_yaml_closure is True
    assert bootstrap.authoring_context_binding == fixture.context.scope_binding
    assert [(item.id, item.label) for item in bootstrap.scheduling_presets] == [
        ("leo-fast-handover", "LEO fast handover — make-before-break"),
        ("geo-longest-pass", "GEO longest pass — break-before-make"),
    ]
    assert bootstrap.public_grammar_href == "/docs/ops/configuration-grammar.md"
    assert bootstrap.authoring.default_body_ref == "nodalarc:bodies/earth.yaml"
    assert bootstrap.authoring.default_phasing_mode == "walker_delta"
    assert bootstrap.authoring.single_plane_phasing_mode == "evenly_spaced_mean_anomaly"
    assert bootstrap.authoring.default_scheduling_preset == "leo-fast-handover"
    assert {item.id for item in bootstrap.authoring.mount_roles} == {
        "access",
        "isl",
        "crosslink",
        "backbone",
    }
    assert {item.id for item in bootstrap.authoring.link_media} == {"rf", "optical"}
    assert (
        next(
            item for item in bootstrap.authoring.routing_protocols if item.id == "bgp"
        ).runtime_supported
        is False
    )
    assert (
        next(
            item for item in bootstrap.authoring.boundary_adapters if item.id == "static_ip"
        ).runtime_supported
        is True
    )
    assert "scope" not in bootstrap.model_dump(mode="json")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CatalogGetRequest.model_validate({"ref": "nodalarc:bodies/earth.yaml", "scope": "other"})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CatalogSessionYamlImportRequest.model_validate(
            {
                "yaml_files": [{"yaml_text": "body: {}\n"}],
                "path": "/tmp/escape.yaml",
            }
        )


def test_every_component_family_noop_fork_preserves_all_fields(tmp_path: Path) -> None:
    fixture = _service(tmp_path, "forks")

    for family, spec in CATALOG_FAMILY_REGISTRY.items():
        if family == "sessions":
            continue
        if family == "payloads":
            source_ref = CatalogRef("user:payloads/source-payload.yaml")
            fixture.service.save_component(
                CatalogDocumentWriteRequest(
                    ref=source_ref,
                    document={
                        "payload": {
                            "id": "source-payload",
                            "display_name": "Complete payload fixture",
                            "terminal_slots": [
                                {
                                    "id": "access",
                                    "terminal": str(_first_shipped_ref("terminals")),
                                    "tags": ["primary"],
                                }
                            ],
                            "resource_groups": [
                                {
                                    "id": "shared-power",
                                    "slots": ["access"],
                                    "simultaneous_active": 1,
                                }
                            ],
                            "reference": "urn:nodalarc:test",
                            "notes": "Preserve every payload field",
                        }
                    },
                )
            )
        else:
            source_ref = _first_shipped_ref(family)
        target_ref = CatalogRef(f"user:{family}/fork-{family.replace('-', '_')}.yaml")
        source = _canonical_json(fixture, source_ref)
        expected = copy.deepcopy(source)
        expected[spec.wrapper]["id"] = target_ref.relative_path.stem

        forked = fixture.service.fork_component(
            CatalogForkRequest(source_ref=source_ref, target_ref=target_ref)
        )

        assert forked.source_ref == source_ref
        assert forked.result.document.canonical_json == expected
        assert fixture.context.repository.snapshot(fixture.context.scope).get(
            target_ref
        ).content == forked.result.document.canonical_yaml.encode("utf-8")

    with pytest.raises(ValidationError, match="cannot be forked"):
        CatalogForkRequest(
            source_ref="nodalarc:sessions/earth-leo-simple.yaml",
            target_ref="user:sessions/copy.yaml",
        )


def test_fork_is_generation_fenced_to_the_exact_source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _service(tmp_path, "fork-source-race")
    source_ref = CatalogRef("user:terminals/source-race.yaml")
    source = fixture.service.fork_component(
        CatalogForkRequest(source_ref=_first_shipped_ref("terminals"), target_ref=source_ref)
    ).result.document
    target_ref = CatalogRef("user:terminals/source-race-copy.yaml")
    replacement = copy.deepcopy(source.canonical_json)
    replacement["terminal"]["notes"] = "Changed while the fork was being committed"
    original_canonical_document = service_module._canonical_document
    raced = False

    def race_after_source_read(document):
        nonlocal raced
        canonical = original_canonical_document(document)
        if document.ref == source_ref and not raced:
            raced = True
            fixture.service.save_component(
                CatalogDocumentWriteRequest(
                    ref=source_ref,
                    document=replacement,
                    expected_revision=source.revision,
                )
            )
        return canonical

    monkeypatch.setattr(service_module, "_canonical_document", race_after_source_read)

    with pytest.raises(CatalogAuthoringError) as stale:
        fixture.service.fork_component(
            CatalogForkRequest(
                source_ref=source_ref,
                target_ref=target_ref,
                expected_source_revision=source.revision,
            )
        )

    assert stale.value.code == "catalog_authoring.stale_revision"
    current = fixture.context.repository.snapshot(fixture.context.scope)
    assert current.get(source_ref).revision != source.revision
    with pytest.raises(CatalogNotFoundError):
        current.get(target_ref)


def test_pagination_is_deterministic_snapshot_bound_and_scope_bound(tmp_path: Path) -> None:
    first = _service(tmp_path, "page-a")
    second = _service(tmp_path, "page-b")
    request = CatalogListRequest(page_size=7)
    page = first.service.list_catalog(request)
    refs = [str(item.ref) for item in page.items]
    assert all(item.display_name for item in page.items)
    assert any(item.summary for item in page.items)
    token = page.next_page_token
    assert token is not None

    while token is not None:
        page = first.service.list_catalog(CatalogListRequest(page_size=7, page_token=token))
        refs.extend(str(item.ref) for item in page.items)
        token = page.next_page_token
    assert refs == sorted(refs)
    assert len(refs) == len(set(refs))

    with pytest.raises(CatalogAuthoringError) as cross_scope:
        second.service.list_catalog(
            CatalogListRequest(
                page_size=7, page_token=first.service.list_catalog(request).next_page_token
            )
        )
    assert cross_scope.value.code == "catalog_authoring.invalid_page_token"

    first_page = first.service.list_catalog(request)
    first.service.fork_component(
        CatalogForkRequest(
            source_ref=_first_shipped_ref("terminals"),
            target_ref="user:terminals/page-change.yaml",
        )
    )
    with pytest.raises(CatalogAuthoringError) as stale:
        first.service.list_catalog(
            CatalogListRequest(page_size=7, page_token=first_page.next_page_token)
        )
    assert stale.value.code == "catalog_authoring.stale_page_token"


def test_cas_and_two_scopes_never_fallback_or_overwrite(tmp_path: Path) -> None:
    first = _service(tmp_path, "scope-a")
    second = _service(tmp_path, "scope-b")
    target = CatalogRef("user:terminals/shared.yaml")
    one = first.service.fork_component(
        CatalogForkRequest(source_ref=_first_shipped_ref("terminals"), target_ref=target)
    ).result.document
    alternative = sorted((SHIPPED_ROOT / "terminals").rglob("*.yaml"))[1]
    alternative_ref = CatalogRef(f"nodalarc:{alternative.relative_to(SHIPPED_ROOT).as_posix()}")
    two = second.service.fork_component(
        CatalogForkRequest(source_ref=alternative_ref, target_ref=target)
    ).result.document
    assert one.canonical_json != two.canonical_json
    assert (
        first.service.get_catalog(CatalogGetRequest(ref=target)).canonical_json
        == one.canonical_json
    )
    assert (
        second.service.get_catalog(CatalogGetRequest(ref=target)).canonical_json
        == two.canonical_json
    )

    replacement = copy.deepcopy(one.canonical_json)
    replacement["terminal"]["notes"] = "scope A replacement"
    updated = first.service.save_component(
        CatalogDocumentWriteRequest(
            ref=target,
            document=replacement,
            expected_revision=one.revision,
        )
    )
    assert updated.document.revision != one.revision
    with pytest.raises(CatalogAuthoringError) as stale:
        first.service.save_component(
            CatalogDocumentWriteRequest(
                ref=target,
                document=replacement,
                expected_revision=one.revision,
            )
        )
    assert stale.value.code == "catalog_authoring.stale_revision"

    with pytest.raises(ValueError, match="server-selected scope"):
        second.service.save_component_at_snapshot(
            CatalogDocumentWriteRequest(
                ref="user:terminals/cross-scope.yaml",
                document=one.canonical_json,
            ),
            first.context.repository.snapshot(first.context.scope),
        )
    with pytest.raises(CatalogAuthoringError) as absent:
        second.service.get_catalog(CatalogGetRequest(ref="user:terminals/cross-scope.yaml"))
    assert absent.value.code == "catalog_authoring.not_found"


def test_typed_dependency_impact_delete_fencing_and_graph_race(tmp_path: Path) -> None:
    fixture = _service(tmp_path, "impact")
    terminal_ref = CatalogRef("user:terminals/impact-terminal.yaml")
    fixture.service.fork_component(
        CatalogForkRequest(source_ref=_first_shipped_ref("terminals"), target_ref=terminal_ref)
    )
    initial = fixture.service.dependents(CatalogDependentsRequest(ref=terminal_ref))
    terminal_revision = initial.target_revision
    assert initial.delete_allowed is True

    node_ref = CatalogRef("user:nodes/impact-node.yaml")
    node = _canonical_json(fixture, "nodalarc:nodes/ground/geo-gateway.yaml")
    node["node"]["id"] = node_ref.relative_path.stem
    node["node"]["terminals"][0]["terminal"] = terminal_ref
    fixture.service.save_component(CatalogDocumentWriteRequest(ref=node_ref, document=node))

    constellation_ref = CatalogRef("user:constellations/impact-constellation.yaml")
    constellation = _canonical_json(
        fixture, "nodalarc:constellations/earth/geo/earth-geo-ring-8.yaml"
    )
    constellation["constellation"]["id"] = constellation_ref.relative_path.stem
    constellation["constellation"]["node"] = node_ref
    fixture.service.save_component(
        CatalogDocumentWriteRequest(ref=constellation_ref, document=constellation)
    )
    session_ref = _seed_session(
        fixture,
        "impact-session",
        source_ref=str(constellation_ref),
    )

    impact = fixture.service.dependents(CatalogDependentsRequest(ref=terminal_ref))
    assert {item.ref: item.minimum_depth for item in impact.transitive_dependents} == {
        node_ref: 1,
        constellation_ref: 2,
        session_ref: 3,
    }
    with pytest.raises(CatalogAuthoringError) as raced:
        fixture.service.delete_catalog(
            CatalogDeleteRequest(
                ref=terminal_ref,
                expected_revision=terminal_revision,
                impact_acknowledgement=initial.acknowledgement,
            )
        )
    assert raced.value.code == "catalog_authoring.impact_mismatch"
    assert raced.value.refusal.impact == impact

    with pytest.raises(CatalogAuthoringError) as blocked:
        fixture.service.delete_catalog(
            CatalogDeleteRequest(
                ref=terminal_ref,
                expected_revision=terminal_revision,
                impact_acknowledgement=impact.acknowledgement,
            )
        )
    assert blocked.value.code == "catalog_authoring.dependents_exist"


def test_unreferenced_delete_requires_exact_revision_and_impact_ack(tmp_path: Path) -> None:
    fixture = _service(tmp_path, "delete")
    target = CatalogRef("user:terminals/delete-me.yaml")
    saved = fixture.service.fork_component(
        CatalogForkRequest(source_ref=_first_shipped_ref("terminals"), target_ref=target)
    ).result.document
    impact = fixture.service.dependents(CatalogDependentsRequest(ref=target))

    with pytest.raises(CatalogAuthoringError) as mismatch:
        fixture.service.delete_catalog(
            CatalogDeleteRequest(
                ref=target,
                expected_revision=saved.revision,
                impact_acknowledgement=f"sha256:{'0' * 64}",
            )
        )
    assert mismatch.value.code == "catalog_authoring.impact_mismatch"

    result = fixture.service.delete_catalog(
        CatalogDeleteRequest(
            ref=target,
            expected_revision=saved.revision,
            impact_acknowledgement=impact.acknowledgement,
        )
    )
    assert result.deleted_ref == target
    with pytest.raises(CatalogAuthoringError) as missing:
        fixture.service.get_catalog(CatalogGetRequest(ref=target))
    assert missing.value.code == "catalog_authoring.not_found"


def test_export_import_is_exact_idempotent_and_collision_safe(tmp_path: Path) -> None:
    source = _service(tmp_path, "export-source")
    target = _service(tmp_path, "export-target")
    session_ref = _seed_session(source, "portable-session")
    exported = source.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref=session_ref)
    )

    assert (
        exported.files[0].yaml_text.encode("utf-8")
        == source.context.repository.snapshot(source.context.scope).get(session_ref).content
    )
    assert exported.files[0].logical_path == "catalog/user/sessions/portable-session.yaml"
    assert all(file.logical_path.startswith("catalog/") for file in exported.files[1:])
    assert "contract_version" not in exported.model_dump(mode="json")
    assert "digest" not in str(exported.model_dump(mode="json"))
    assert set(_import_request(exported, commit=False).model_dump(mode="json")) == {
        "yaml_files",
        "commit",
        "proposal_token",
    }

    proposed = target.service.import_session_yaml(_import_request(exported, commit=False))
    assert proposed.outcome == "proposed"
    assert [item.ref for item in proposed.proposed_writes] == [session_ref]
    assert proposed.proposed_writes[0].canonicalization_changed is False
    assert proposed.proposal_token is not None
    committed = target.service.import_session_yaml(
        _import_request(
            exported,
            commit=True,
            proposal_token=proposed.proposal_token,
        )
    )
    assert committed.outcome == "committed"
    assert target.context.repository.snapshot(target.context.scope).get(
        session_ref
    ).content == exported.files[0].yaml_text.encode("utf-8")
    repeated = target.service.import_session_yaml(_import_request(exported, commit=False))
    assert repeated.outcome == "unchanged"

    stored = target.context.repository.snapshot(target.context.scope).get(session_ref)
    different = _canonical_json(target, session_ref)
    different["session"]["display_name"] = "Different exact content"
    canonical = canonicalize_persisted_configuration(session_ref, different)
    transaction = target.context.repository.begin(target.context.scope)
    transaction.write_bytes(
        session_ref,
        canonical.yaml_bytes,
        expected_revision=stored.revision,
    )
    transaction.commit()
    collision = target.service.import_session_yaml(_import_request(exported, commit=False))
    assert collision.outcome == "blocked"
    assert collision.collisions[0].reason == "user_content_mismatch"
    assert collision.collisions[0].ref == session_ref


def test_import_commit_requires_the_exact_reviewed_proposal(tmp_path: Path) -> None:
    source = _service(tmp_path, "proposal-source")
    target = _service(tmp_path, "proposal-target")
    session_ref = _seed_session(source, "proposal-fence")
    exported = source.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref=session_ref)
    )
    yaml_texts = tuple(file.yaml_text for file in exported.files)
    yaml_files = _yaml_files(*yaml_texts)

    with pytest.raises(ValidationError, match="commit requires a proposal token"):
        CatalogSessionYamlImportRequest(yaml_files=yaml_files, commit=True)
    with pytest.raises(ValidationError, match="proposal requests cannot carry one"):
        CatalogSessionYamlImportRequest(
            yaml_files=yaml_files,
            commit=False,
            proposal_token="naip1.unreviewed",
        )

    proposed = target.service.import_session_yaml(
        CatalogSessionYamlImportRequest(yaml_files=yaml_files)
    )
    assert proposed.proposal_token is not None

    changed_yaml = yaml.safe_load(yaml_texts[0])
    changed_yaml["session"]["display_name"] = "Changed after review"
    changed_files = (
        yaml.safe_dump(changed_yaml, sort_keys=False),
        *yaml_texts[1:],
    )
    with pytest.raises(CatalogAuthoringError) as changed:
        target.service.import_session_yaml(
            CatalogSessionYamlImportRequest(
                yaml_files=_yaml_files(*changed_files),
                commit=True,
                proposal_token=proposed.proposal_token,
            )
        )
    assert changed.value.code == "catalog_authoring.stale_import_proposal"

    target.service.fork_component(
        CatalogForkRequest(
            source_ref=_first_shipped_ref("terminals"),
            target_ref="user:terminals/unrelated-after-review.yaml",
        )
    )
    with pytest.raises(CatalogAuthoringError) as stale:
        target.service.import_session_yaml(
            CatalogSessionYamlImportRequest(
                yaml_files=yaml_files,
                commit=True,
                proposal_token=proposed.proposal_token,
            )
        )
    assert stale.value.code == "catalog_authoring.stale_import_proposal"
    with pytest.raises(CatalogNotFoundError):
        target.context.repository.snapshot(target.context.scope).get(session_ref)

    current = target.service.import_session_yaml(
        CatalogSessionYamlImportRequest(yaml_files=yaml_files)
    )
    committed = target.service.import_session_yaml(
        CatalogSessionYamlImportRequest(
            yaml_files=yaml_files,
            commit=True,
            proposal_token=current.proposal_token,
        )
    )
    assert committed.outcome == "committed"


def test_import_canonicalizes_then_persists_and_reports_ordinary_yaml(
    tmp_path: Path,
) -> None:
    source = _service(tmp_path, "canonical-import-source")
    target = _service(tmp_path, "canonical-import-target")
    session_ref = _seed_session(source, "canonical-import")
    exported = source.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref=session_ref)
    )
    root_document = yaml.safe_load(exported.files[0].yaml_text)
    noncanonical_root = yaml.safe_dump(
        root_document,
        default_flow_style=True,
        sort_keys=True,
        width=10_000,
    )
    assert noncanonical_root != exported.files[0].yaml_text
    proposed_request = CatalogSessionYamlImportRequest(
        yaml_files=(
            CatalogYamlImportFile(
                yaml_text=noncanonical_root,
                logical_path_hint="catalog/user/sessions/canonical-import.yaml",
            ),
            *(
                CatalogYamlImportFile(
                    yaml_text=file.yaml_text,
                    logical_path_hint=file.logical_path,
                )
                for file in exported.files[1:]
            ),
        ),
        commit=False,
    )
    proposed = target.service.import_session_yaml(proposed_request)
    request = proposed_request.model_copy(
        update={"commit": True, "proposal_token": proposed.proposal_token}
    )

    result = target.service.import_session_yaml(request)

    assert result.outcome == "committed"
    write = next(item for item in result.proposed_writes if item.ref == session_ref)
    assert write.canonical_yaml == exported.files[0].yaml_text
    assert write.canonicalization_changed is True
    assert write.logical_path == f"catalog/user/sessions/{session_ref.relative_path.name}"
    stored = target.context.repository.snapshot(target.context.scope).get(session_ref)
    assert stored.content == exported.files[0].yaml_text.encode("utf-8")
    reopened = target.service.get_catalog(CatalogGetRequest(ref=session_ref))
    assert reopened.canonical_yaml.encode("utf-8") == stored.content
    reexported = target.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref=session_ref)
    )
    assert reexported.files[0].yaml_text.encode("utf-8") == stored.content


def test_export_import_preserves_a_transitive_user_reference_closure(tmp_path: Path) -> None:
    source = _service(tmp_path, "nested-export-source")
    target = _service(tmp_path, "nested-export-target")
    node_ref = CatalogRef("user:nodes/imported/portable-node.yaml")
    node = _canonical_json(source, _first_shipped_ref("nodes"))
    node["node"]["id"] = node_ref.relative_path.stem
    source.service.save_component(CatalogDocumentWriteRequest(ref=node_ref, document=node))

    constellation_ref = CatalogRef("user:constellations/imported/portable-constellation.yaml")
    constellation = _canonical_json(source, _first_shipped_ref("constellations"))
    constellation["constellation"]["id"] = constellation_ref.relative_path.stem
    constellation["constellation"]["node"] = node_ref
    source.service.save_component(
        CatalogDocumentWriteRequest(ref=constellation_ref, document=constellation)
    )
    session_ref = _seed_session(
        source,
        "portable-user-closure",
        source_ref=str(constellation_ref),
    )

    exported = source.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref=session_ref)
    )
    exported_by_ref = _exported_yaml_by_ref(exported)
    assert {node_ref, constellation_ref}.issubset(exported_by_ref)

    proposed = target.service.import_session_yaml(_import_request(exported, commit=False))
    committed = target.service.import_session_yaml(
        _import_request(
            exported,
            commit=True,
            proposal_token=proposed.proposal_token,
        )
    )

    assert committed.outcome == "committed"
    snapshot = target.context.repository.snapshot(target.context.scope)
    assert snapshot.get(session_ref).content == exported.files[0].yaml_text.encode("utf-8")
    for ref in (node_ref, constellation_ref):
        assert snapshot.get(ref).content == exported_by_ref[ref].encode("utf-8")
    imported_root = yaml.safe_load(snapshot.get(session_ref).content)
    assert imported_root["segments"][0]["source"] == str(constellation_ref)


def test_export_import_disambiguates_nested_refs_with_the_same_document_id(
    tmp_path: Path,
) -> None:
    source = _service(tmp_path, "same-id-source")
    target = _service(tmp_path, "same-id-target")
    node_refs = (
        CatalogRef("user:nodes/session-a/shared-node.yaml"),
        CatalogRef("user:nodes/session-b/shared-node.yaml"),
    )
    constellation_refs = (
        CatalogRef("user:constellations/session-a/ring-a.yaml"),
        CatalogRef("user:constellations/session-b/ring-b.yaml"),
    )
    for position, node_ref in enumerate(node_refs, start=1):
        node = _canonical_json(source, _first_shipped_ref("nodes"))
        node["node"]["id"] = "shared-node"
        node["node"]["notes"] = f"Distinct nested node {position}"
        source.service.save_component(CatalogDocumentWriteRequest(ref=node_ref, document=node))
    for position, (constellation_ref, node_ref) in enumerate(
        zip(constellation_refs, node_refs, strict=True),
        start=1,
    ):
        constellation = _canonical_json(source, _first_shipped_ref("constellations"))
        constellation["constellation"]["id"] = constellation_ref.relative_path.stem
        constellation["constellation"]["display_name"] = f"Nested ring {position}"
        constellation["constellation"]["node"] = node_ref
        source.service.save_component(
            CatalogDocumentWriteRequest(ref=constellation_ref, document=constellation)
        )

    session_ref = _seed_session(
        source,
        "same-id-import",
        source_ref=str(constellation_refs[0]),
    )
    session = _canonical_json(source, session_ref)
    second_segment = copy.deepcopy(session["segments"][0])
    second_segment["id"] = "leo-second"
    second_segment["source"] = str(constellation_refs[1])
    session["segments"].append(second_segment)
    current = source.context.repository.snapshot(source.context.scope).get(session_ref)
    canonical = canonicalize_persisted_configuration(session_ref, session)
    transaction = source.context.repository.begin(source.context.scope)
    transaction.write_bytes(
        session_ref,
        canonical.yaml_bytes,
        expected_revision=current.revision,
    )
    transaction.commit()

    exported = source.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref=session_ref)
    )
    flat_files = tuple(CatalogYamlImportFile(yaml_text=file.yaml_text) for file in exported.files)
    with pytest.raises(CatalogAuthoringError) as ambiguous:
        target.service.import_session_yaml(CatalogSessionYamlImportRequest(yaml_files=flat_files))
    assert ambiguous.value.code == "catalog_authoring.import_incomplete"
    assert "supply their exported catalog paths" in ambiguous.value.refusal.message

    proposed = target.service.import_session_yaml(_import_request(exported, commit=False))
    committed = target.service.import_session_yaml(
        _import_request(
            exported,
            commit=True,
            proposal_token=proposed.proposal_token,
        )
    )

    assert committed.outcome == "committed"
    snapshot = target.context.repository.snapshot(target.context.scope)
    assert yaml.safe_load(snapshot.get(node_refs[0]).content)["node"]["notes"] == (
        "Distinct nested node 1"
    )
    assert yaml.safe_load(snapshot.get(node_refs[1]).content)["node"]["notes"] == (
        "Distinct nested node 2"
    )


def test_export_import_preserves_a_nested_user_session_ref(tmp_path: Path) -> None:
    source = _service(tmp_path, "nested-session-source")
    target = _service(tmp_path, "nested-session-target")
    session_ref = SessionRef("user:sessions/research/team-a/nested-session.yaml")
    session = _canonical_json(source, "nodalarc:sessions/earth-leo-simple.yaml")
    session["session"]["name"] = session_ref.relative_path.stem
    canonical = canonicalize_persisted_configuration(session_ref, session)
    transaction = source.context.repository.begin(source.context.scope)
    transaction.write_bytes(session_ref, canonical.yaml_bytes, expected_revision=None)
    transaction.commit()

    exported = source.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref=session_ref)
    )
    assert exported.files[0].logical_path == (
        "catalog/user/sessions/research/team-a/nested-session.yaml"
    )

    proposed = target.service.import_session_yaml(_import_request(exported, commit=False))

    assert proposed.root_ref == session_ref
    assert proposed.proposed_writes[0].ref == session_ref


def test_import_accepts_root_only_with_shipped_refs_and_rejects_extra_yaml(
    tmp_path: Path,
) -> None:
    fixture = _service(tmp_path, "invalid")
    exported = fixture.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    root_only = CatalogSessionYamlImportRequest(
        yaml_files=_yaml_files(exported.files[0].yaml_text),
        commit=False,
    )
    proposed = fixture.service.import_session_yaml(root_only)
    assert proposed.outcome == "proposed"
    assert proposed.root_ref == "user:sessions/earth-leo-simple.yaml"

    request = CatalogSessionYamlImportRequest(
        yaml_files=(
            *(
                CatalogYamlImportFile(
                    yaml_text=file.yaml_text,
                    logical_path_hint=file.logical_path,
                )
                for file in exported.files
            ),
            CatalogYamlImportFile(yaml_text=(SHIPPED_ROOT / "bodies/luna.yaml").read_text()),
        ),
        commit=False,
    )
    with pytest.raises(CatalogAuthoringError) as incomplete:
        fixture.service.import_session_yaml(request)
    assert incomplete.value.code == "catalog_authoring.import_incomplete"


def test_import_requires_unstored_user_refs_and_refuses_ambiguous_identity(
    tmp_path: Path,
) -> None:
    source = _service(tmp_path, "missing-user-source")
    target = _service(tmp_path, "missing-user-target")
    constellation_ref = CatalogRef("user:constellations/missing/import-ring.yaml")
    constellation = _canonical_json(source, _first_shipped_ref("constellations"))
    constellation["constellation"]["id"] = constellation_ref.relative_path.stem
    source.service.save_component(
        CatalogDocumentWriteRequest(ref=constellation_ref, document=constellation)
    )
    session_ref = _seed_session(
        source,
        "missing-user-session",
        source_ref=str(constellation_ref),
    )
    exported = source.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref=session_ref)
    )

    with pytest.raises(CatalogAuthoringError) as missing:
        target.service.import_session_yaml(
            CatalogSessionYamlImportRequest(
                yaml_files=_yaml_files(exported.files[0].yaml_text),
                commit=False,
            )
        )
    assert missing.value.code == "catalog_authoring.import_incomplete"
    assert str(constellation_ref) in missing.value.refusal.message

    unrelated = _canonical_json(target, _first_shipped_ref("constellations"))
    unrelated["constellation"]["id"] = constellation_ref.relative_path.stem
    unrelated["constellation"]["display_name"] = "Unrelated destination object"
    target.service.save_component(
        CatalogDocumentWriteRequest(ref=constellation_ref, document=unrelated)
    )
    with pytest.raises(CatalogAuthoringError) as destination_binding:
        target.service.import_session_yaml(
            CatalogSessionYamlImportRequest(
                yaml_files=_yaml_files(exported.files[0].yaml_text),
                commit=False,
            )
        )
    assert destination_binding.value.code == "catalog_authoring.import_incomplete"
    assert str(constellation_ref) in destination_binding.value.refusal.message

    root = _canonical_json(source, "nodalarc:sessions/earth-leo-simple.yaml")
    root["session"]["name"] = "ambiguous-import"
    root["segments"][0]["source"] = "user:constellations/first/shared-ring.yaml"
    second_segment = copy.deepcopy(root["segments"][0])
    second_segment["id"] = "leo-second"
    second_segment["source"] = "user:constellations/second/shared-ring.yaml"
    root["segments"].append(second_segment)
    root_yaml = canonicalize_persisted_configuration(
        CatalogRef("user:sessions/ambiguous-import.yaml"),
        root,
    ).yaml_bytes.decode("utf-8")
    shared = _canonical_json(source, _first_shipped_ref("constellations"))
    shared["constellation"]["id"] = "shared-ring"
    shared_yaml = canonicalize_persisted_configuration(
        CatalogRef("user:constellations/first/shared-ring.yaml"),
        shared,
    ).yaml_bytes.decode("utf-8")

    with pytest.raises(CatalogAuthoringError) as ambiguous:
        target.service.import_session_yaml(
            CatalogSessionYamlImportRequest(
                yaml_files=_yaml_files(root_yaml, shared_yaml),
                commit=False,
            )
        )
    assert ambiguous.value.code == "catalog_authoring.import_incomplete"
    assert "placement is ambiguous" in ambiguous.value.refusal.message


def test_import_verifies_supplied_shipped_yaml_against_installed_catalog(
    tmp_path: Path,
) -> None:
    fixture = _service(tmp_path, "shipped-import")
    exported = fixture.service.export_session_yaml(
        CatalogSessionYamlExportRequest(session_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    body_file = next(
        file for file in exported.files if file.logical_path == "catalog/nodalarc/bodies/earth.yaml"
    )
    modified_body = yaml.safe_load(body_file.yaml_text)
    modified_body["body"]["display_name"] = "Changed Earth"
    modified_yaml = yaml.safe_dump(modified_body, sort_keys=False)
    yaml_files = tuple(
        CatalogYamlImportFile(
            yaml_text=modified_yaml if file is body_file else file.yaml_text,
            logical_path_hint=file.logical_path,
        )
        for file in exported.files
    )

    result = fixture.service.import_session_yaml(
        CatalogSessionYamlImportRequest(yaml_files=yaml_files, commit=False)
    )

    assert result.outcome == "blocked"
    collision = next(item for item in result.collisions if item.ref == "nodalarc:bodies/earth.yaml")
    assert collision.reason == "shipped_content_mismatch"


def test_catalog_mutations_still_reject_dangling_and_shipped_writes(tmp_path: Path) -> None:
    fixture = _service(tmp_path, "invalid-mutations")

    node = _canonical_json(fixture, "nodalarc:nodes/ground/geo-gateway.yaml")
    node["node"]["id"] = "dangling-node"
    node["node"]["terminals"][0]["terminal"] = "user:terminals/missing.yaml"
    with pytest.raises(CatalogAuthoringError) as dangling:
        fixture.service.save_component(
            CatalogDocumentWriteRequest(ref="user:nodes/dangling-node.yaml", document=node)
        )
    assert dangling.value.code == "catalog_authoring.invalid_graph"

    shipped_ref = _first_shipped_ref("terminals")
    with pytest.raises(CatalogAuthoringError) as read_only:
        fixture.service.save_component(
            CatalogDocumentWriteRequest(
                ref=shipped_ref,
                document=_canonical_json(fixture, shipped_ref),
            )
        )
    assert read_only.value.code == "catalog_authoring.read_only"

    with pytest.raises(ValidationError, match="path traversal"):
        CatalogGetRequest(ref="user:nodes/../escape.yaml")
    wrong_family = copy.deepcopy(node)
    wrong_family["node"]["terminals"][0]["terminal"] = "user:orbits/not-terminal.yaml"
    with pytest.raises(CatalogAuthoringError) as wrong:
        fixture.service.save_component(
            CatalogDocumentWriteRequest(ref="user:nodes/dangling-node.yaml", document=wrong_family)
        )
    assert wrong.value.code == "catalog_authoring.invalid_document"
