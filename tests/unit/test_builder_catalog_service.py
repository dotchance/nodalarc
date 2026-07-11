from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import CATALOG_FAMILY_REGISTRY
from nodalarc.catalog_repository import CatalogNotFoundError, CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_catalog_api import (
    CatalogClosureImportRequest,
    CatalogDeleteRequest,
    CatalogDependentsRequest,
    CatalogDocumentWriteRequest,
    CatalogForkRequest,
    CatalogGetRequest,
    CatalogImportEntry,
    CatalogListRequest,
    CatalogSessionExportRequest,
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


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


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


def _import_request(export, *, commit: bool) -> CatalogClosureImportRequest:
    return CatalogClosureImportRequest(
        contract_version=export.contract_version,
        root_ref=export.session_ref,
        root_yaml=export.root.exact_yaml,
        document_digest=export.document_digest,
        closure_digest=export.closure_digest,
        entries=tuple(
            CatalogImportEntry(
                ref=entry.ref,
                exact_yaml=entry.exact_yaml,
                document_digest=entry.document_digest,
            )
            for entry in export.entries
        ),
        commit=commit,
    )


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
        CatalogImportEntry.model_validate(
            {
                "ref": "user:bodies/earth.yaml",
                "exact_yaml": "body: {}\n",
                "document_digest": f"sha256:{'a' * 64}",
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
    exported = source.service.export_session(CatalogSessionExportRequest(session_ref=session_ref))
    assert exported.contract_version == 1
    unversioned_export = exported.model_dump(mode="json")
    unversioned_export.pop("contract_version")
    with pytest.raises(ValidationError, match="Field required"):
        type(exported).model_validate(unversioned_export)
    unsupported_export = exported.model_dump(mode="json")
    unsupported_export["contract_version"] = 2
    with pytest.raises(ValidationError, match="Input should be 1"):
        type(exported).model_validate(unsupported_export)

    import_request = _import_request(exported, commit=False)
    unversioned_import = import_request.model_dump(mode="json")
    unversioned_import.pop("contract_version")
    with pytest.raises(ValidationError, match="Field required"):
        CatalogClosureImportRequest.model_validate(unversioned_import)
    unsupported_import = import_request.model_dump(mode="json")
    unsupported_import["contract_version"] = 2
    with pytest.raises(ValidationError, match="Input should be 1"):
        CatalogClosureImportRequest.model_validate(unsupported_import)

    assert (
        exported.root.exact_yaml.encode("utf-8")
        == source.context.repository.snapshot(source.context.scope).get(session_ref).content
    )
    assert all(entry.preserved_path.startswith("catalog/") for entry in exported.entries)

    proposed = target.service.import_closure(_import_request(exported, commit=False))
    assert proposed.outcome == "proposed"
    assert [item.ref for item in proposed.proposed_writes] == [session_ref]
    committed = target.service.import_closure(_import_request(exported, commit=True))
    assert committed.outcome == "committed"
    assert target.context.repository.snapshot(target.context.scope).get(
        session_ref
    ).content == exported.root.exact_yaml.encode("utf-8")
    repeated = target.service.import_closure(_import_request(exported, commit=True))
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
    collision = target.service.import_closure(_import_request(exported, commit=True))
    assert collision.outcome == "blocked"
    assert collision.collisions[0].reason == "user_content_mismatch"
    assert collision.collisions[0].ref == session_ref


def test_import_verifies_transport_then_persists_and_reports_canonical_user_bytes(
    tmp_path: Path,
) -> None:
    source = _service(tmp_path, "canonical-import-source")
    target = _service(tmp_path, "canonical-import-target")
    session_ref = _seed_session(source, "canonical-import")
    exported = source.service.export_session(CatalogSessionExportRequest(session_ref=session_ref))
    root_document = yaml.safe_load(exported.root.exact_yaml)
    noncanonical_root = yaml.safe_dump(
        root_document,
        default_flow_style=True,
        sort_keys=True,
        width=10_000,
    )
    assert noncanonical_root != exported.root.exact_yaml
    request = _import_request(exported, commit=True).model_copy(
        update={
            "root_yaml": noncanonical_root,
            "document_digest": _sha256(noncanonical_root.encode("utf-8")),
        }
    )

    result = target.service.import_closure(request)

    assert result.outcome == "committed"
    assert result.document_digest == exported.document_digest
    write = next(item for item in result.proposed_writes if item.ref == session_ref)
    assert write.exact_yaml == exported.root.exact_yaml
    assert write.document_digest == exported.document_digest
    stored = target.context.repository.snapshot(target.context.scope).get(session_ref)
    assert stored.content == exported.root.exact_yaml.encode("utf-8")
    reopened = target.service.get_catalog(CatalogGetRequest(ref=session_ref))
    assert reopened.canonical_yaml.encode("utf-8") == stored.content
    reexported = target.service.export_session(CatalogSessionExportRequest(session_ref=session_ref))
    assert reexported.root.exact_yaml.encode("utf-8") == stored.content


def test_export_import_preserves_a_transitive_user_reference_closure(tmp_path: Path) -> None:
    source = _service(tmp_path, "nested-export-source")
    target = _service(tmp_path, "nested-export-target")
    node_ref = CatalogRef("user:nodes/portable-node.yaml")
    node = _canonical_json(source, _first_shipped_ref("nodes"))
    node["node"]["id"] = node_ref.relative_path.stem
    source.service.save_component(CatalogDocumentWriteRequest(ref=node_ref, document=node))

    constellation_ref = CatalogRef("user:constellations/portable-constellation.yaml")
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

    exported = source.service.export_session(CatalogSessionExportRequest(session_ref=session_ref))
    exported_by_ref = {entry.ref: entry for entry in exported.entries}
    assert {node_ref, constellation_ref}.issubset(exported_by_ref)

    committed = target.service.import_closure(_import_request(exported, commit=True))

    assert committed.outcome == "committed"
    snapshot = target.context.repository.snapshot(target.context.scope)
    assert snapshot.get(session_ref).content == exported.root.exact_yaml.encode("utf-8")
    for ref in (node_ref, constellation_ref):
        assert snapshot.get(ref).content == exported_by_ref[ref].exact_yaml.encode("utf-8")
    imported_root = yaml.safe_load(snapshot.get(session_ref).content)
    assert imported_root["segments"][0]["source"] == str(constellation_ref)


def test_import_rejects_digest_extra_dangling_and_shipped_mutation(tmp_path: Path) -> None:
    fixture = _service(tmp_path, "invalid")
    exported = fixture.service.export_session(
        CatalogSessionExportRequest(session_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    request = _import_request(exported, commit=False)
    with pytest.raises(CatalogAuthoringError) as digest:
        fixture.service.import_closure(
            request.model_copy(update={"document_digest": f"sha256:{'0' * 64}"})
        )
    assert digest.value.code == "catalog_authoring.import_digest_mismatch"

    extra = CatalogImportEntry(
        ref="nodalarc:bodies/luna.yaml",
        exact_yaml=(SHIPPED_ROOT / "bodies/luna.yaml").read_text(),
        document_digest=_sha256((SHIPPED_ROOT / "bodies/luna.yaml").read_bytes()),
    )
    with pytest.raises(CatalogAuthoringError) as incomplete:
        fixture.service.import_closure(
            request.model_copy(update={"entries": (*request.entries, extra)})
        )
    assert incomplete.value.code == "catalog_authoring.import_incomplete"

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
