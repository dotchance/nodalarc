from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi import FastAPI
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_registry import CATALOG_FAMILY_REGISTRY
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_catalog_api import (
    CatalogComponentFamily,
    CatalogDocumentWriteRequest,
    CatalogDraftCompileRequest,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
    CatalogDraftPatchCommand,
    CatalogDraftPatchRequest,
    CatalogDraftReplaceObjectRequest,
    CatalogDraftSaveRequest,
    CatalogGetRequest,
)
from pydantic import ValidationError
from vs_api import builder_catalog_draft as draft_module
from vs_api.builder_catalog_draft import BuilderCatalogDraftService
from vs_api.builder_catalog_service import BuilderCatalogAuthoringService, CatalogAuthoringError
from vs_api.builder_router import BuilderRouterServices, create_builder_router
from vs_api.catalog_context import CatalogContext

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"


def _context(tmp_path: Path, name: str = "user-catalog") -> CatalogContext:
    scope = CatalogScope()
    return CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / name},
        ),
        scope=scope,
    )


def _services(tmp_path: Path) -> tuple[BuilderCatalogDraftService, BuilderCatalogAuthoringService]:
    context = _context(tmp_path)
    return BuilderCatalogDraftService(context), BuilderCatalogAuthoringService(
        context,
        page_token_secret=b"catalog-draft-test-page-key-0001",
    )


def _first_shipped_ref(family: str) -> CatalogRef:
    source = sorted((SHIPPED_ROOT / family).rglob("*.yaml"))[0]
    return CatalogRef(f"nodalarc:{source.relative_to(SHIPPED_ROOT).as_posix()}")


def _payload_source(authoring: BuilderCatalogAuthoringService) -> CatalogRef:
    source_ref = CatalogRef("user:payloads/component-draft-source.yaml")
    authoring.save_component(
        CatalogDocumentWriteRequest(
            ref=source_ref,
            document={
                "payload": {
                    "id": "component-draft-source",
                    "display_name": "Advanced payload source",
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
                    "reference": "urn:nodalarc:component-draft-test",
                    "notes": "Every payload field must survive",
                }
            },
        )
    )
    return source_ref


def _schema_property_names(
    schema: dict[str, Any],
    components: dict[str, Any],
    *,
    seen: set[str] | None = None,
) -> set[str]:
    visited = set() if seen is None else seen
    reference = schema.get("$ref")
    if isinstance(reference, str):
        name = reference.rsplit("/", 1)[-1]
        if name in visited:
            return set()
        visited.add(name)
        return _schema_property_names(components[name], components, seen=visited)
    names = set(schema.get("properties", ()))
    for child in schema.get("properties", {}).values():
        names.update(_schema_property_names(child, components, seen=visited))
    for keyword in ("items", "additionalProperties"):
        child = schema.get(keyword)
        if isinstance(child, dict):
            names.update(_schema_property_names(child, components, seen=visited))
    for keyword in ("allOf", "anyOf", "oneOf"):
        for child in schema.get(keyword, ()):
            names.update(_schema_property_names(child, components, seen=visited))
    return names


def test_new_drafts_cover_every_component_family_and_remain_truthfully_incomplete(
    tmp_path: Path,
) -> None:
    drafts, _ = _services(tmp_path)

    for family in get_args(CatalogComponentFamily):
        object_id = f"new-{family.replace('-', '_')}"
        draft = drafts.new(CatalogDraftNewRequest(family=family, object_id=object_id))

        assert draft.target_ref == f"user:{family}/{object_id}.yaml"
        assert draft.family == family
        assert draft.source_ref is None
        assert draft.expected_target_revision is None
        assert draft.document == {CATALOG_FAMILY_REGISTRY[family].wrapper: {"id": object_id}}
        assert draft.issues
        assert all(issue.stage == "structural" for issue in draft.issues)
        assert all(set(issue.blocks) == {"save", "deploy"} for issue in draft.issues)

    with pytest.raises(ValidationError):
        CatalogDraftNewRequest(family="sessions", object_id="not-a-component")


def test_all_families_open_compile_and_save_without_losing_advanced_fields(
    tmp_path: Path,
) -> None:
    drafts, authoring = _services(tmp_path)
    payload_ref = _payload_source(authoring)

    for family in get_args(CatalogComponentFamily):
        source_ref = payload_ref if family == "payloads" else _first_shipped_ref(family)
        source = authoring.get_catalog(CatalogGetRequest(ref=source_ref))
        target_id = f"draft-{family.replace('-', '_')}"
        target_ref = CatalogRef(f"user:{family}/{target_id}.yaml")
        expected = copy.deepcopy(source.canonical_json)
        wrapper = CATALOG_FAMILY_REGISTRY[family].wrapper
        assert wrapper is not None
        expected[wrapper]["id"] = target_id

        opened = drafts.open(CatalogDraftOpenRequest(source_ref=source_ref, target_ref=target_ref))
        compiled = drafts.compile(
            CatalogDraftCompileRequest(
                draft=opened,
                expected_draft_revision=opened.draft_revision,
            )
        )
        saved = drafts.save(
            CatalogDraftSaveRequest(
                draft=opened,
                expected_draft_revision=opened.draft_revision,
            )
        )

        assert compiled.save_allowed is True
        assert compiled.canonical_json == expected
        assert saved.result.document.canonical_json == expected
        assert saved.result.document.canonical_yaml == compiled.canonical_yaml
        assert saved.draft.expected_target_revision == saved.result.document.revision
        assert (
            authoring.get_catalog(CatalogGetRequest(ref=source_ref)).canonical_json
            == source.canonical_json
        )


def test_specialized_patch_changes_only_the_selected_field_on_an_advanced_site(
    tmp_path: Path,
) -> None:
    drafts, authoring = _services(tmp_path)
    source_ref = CatalogRef("nodalarc:sites/earth/fj/earth-fj-suva.yaml")
    source = authoring.get_catalog(CatalogGetRequest(ref=source_ref)).canonical_json
    opened = drafts.open(CatalogDraftOpenRequest(source_ref=source_ref))
    expected = copy.deepcopy(source)
    expected["site"]["display_name"] = "Suva authoring copy"

    patched = drafts.patch(
        CatalogDraftPatchRequest(
            draft=opened,
            expected_draft_revision=0,
            commands=(
                CatalogDraftPatchCommand(
                    operation="replace",
                    pointer="/site/display_name",
                    value="Suva authoring copy",
                ),
            ),
        )
    )
    saved = drafts.save(
        CatalogDraftSaveRequest(
            draft=patched,
            expected_draft_revision=patched.draft_revision,
        )
    )

    assert saved.result.document.canonical_json == expected
    advanced = saved.result.document.canonical_json["site"]
    assert advanced["verified"] == source["site"]["verified"]
    assert advanced["lan"]["ipv6"] == source["site"]["lan"]["ipv6"]
    assert advanced["nodes"][0]["terminals"] == source["site"]["nodes"][0]["terminals"]
    assert (
        advanced["nodes"][0]["originated_prefixes"]
        == source["site"]["nodes"][0]["originated_prefixes"]
    )


def test_default_shipped_customization_never_overwrites_an_existing_user_object(
    tmp_path: Path,
) -> None:
    drafts, _ = _services(tmp_path)
    source_ref = _first_shipped_ref("terminals")
    first = drafts.open(CatalogDraftOpenRequest(source_ref=source_ref))
    drafts.save(
        CatalogDraftSaveRequest(
            draft=first,
            expected_draft_revision=first.draft_revision,
        )
    )

    with pytest.raises(CatalogAuthoringError) as raised:
        drafts.open(CatalogDraftOpenRequest(source_ref=source_ref))

    assert raised.value.code == "catalog_authoring.conflict"


@pytest.mark.parametrize(
    "pointer",
    [
        "/terminal/id",
        "/terminal/__proto__/polluted",
        "/terminal/../notes",
        "/terminal/constructor/prototype",
        "/other/notes",
    ],
)
def test_patch_rejects_identity_family_prototype_and_traversal_changes(
    tmp_path: Path,
    pointer: str,
) -> None:
    drafts, _ = _services(tmp_path)
    opened = drafts.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("terminals")))

    with pytest.raises(CatalogAuthoringError) as raised:
        drafts.patch(
            CatalogDraftPatchRequest(
                draft=opened,
                expected_draft_revision=0,
                commands=(
                    CatalogDraftPatchCommand(
                        operation="add" if pointer != "/terminal/id" else "replace",
                        pointer=pointer,
                        value="forbidden",
                    ),
                ),
            )
        )

    assert raised.value.code == "catalog_authoring.invalid_patch"


def test_patch_compile_and_save_reject_stale_draft_or_catalog_revisions(
    tmp_path: Path,
) -> None:
    drafts, authoring = _services(tmp_path)
    opened = drafts.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("terminals")))

    with pytest.raises(CatalogAuthoringError) as stale_draft:
        drafts.patch(
            CatalogDraftPatchRequest(
                draft=opened,
                expected_draft_revision=1,
                commands=(
                    CatalogDraftPatchCommand(
                        operation="add",
                        pointer="/terminal/notes",
                        value="stale",
                    ),
                ),
            )
        )
    assert stale_draft.value.code == "catalog_authoring.stale_revision"

    authoring.save_component(
        CatalogDocumentWriteRequest(
            ref=opened.target_ref,
            document=opened.document,
            expected_revision=None,
        )
    )
    for operation in (
        lambda: drafts.compile(CatalogDraftCompileRequest(draft=opened, expected_draft_revision=0)),
        lambda: drafts.save(CatalogDraftSaveRequest(draft=opened, expected_draft_revision=0)),
    ):
        with pytest.raises(CatalogAuthoringError) as stale_catalog:
            operation()
        assert stale_catalog.value.code == "catalog_authoring.stale_revision"


def test_draft_save_is_generation_fenced_to_its_compile_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drafts, authoring = _services(tmp_path)
    source_ref = CatalogRef("user:terminals/draft-race-source.yaml")
    source_document = copy.deepcopy(
        authoring.get_catalog(CatalogGetRequest(ref=_first_shipped_ref("terminals"))).canonical_json
    )
    source_document["terminal"]["id"] = source_ref.relative_path.stem
    source = authoring.save_component(
        CatalogDocumentWriteRequest(
            ref=source_ref,
            document=source_document,
        )
    ).document
    opened = drafts.open(
        CatalogDraftOpenRequest(
            source_ref=source_ref,
            target_ref="user:terminals/draft-race-target.yaml",
        )
    )
    replacement = copy.deepcopy(source.canonical_json)
    replacement["terminal"]["notes"] = "Changed during component draft compilation"
    original_reference_issues = draft_module._reference_issues
    raced = False

    def race_after_reference_check(*args, **kwargs):
        nonlocal raced
        issues = original_reference_issues(*args, **kwargs)
        if not raced:
            raced = True
            authoring.save_component(
                CatalogDocumentWriteRequest(
                    ref=source_ref,
                    document=replacement,
                    expected_revision=source.revision,
                )
            )
        return issues

    monkeypatch.setattr(draft_module, "_reference_issues", race_after_reference_check)

    with pytest.raises(CatalogAuthoringError) as stale:
        drafts.save(
            CatalogDraftSaveRequest(
                draft=opened,
                expected_draft_revision=opened.draft_revision,
            )
        )

    assert stale.value.code == "catalog_authoring.stale_revision"
    with pytest.raises(CatalogAuthoringError) as missing:
        authoring.get_catalog(CatalogGetRequest(ref=opened.target_ref))
    assert missing.value.code == "catalog_authoring.not_found"


def test_compile_separates_structural_saveability_from_runtime_support(
    tmp_path: Path,
) -> None:
    drafts, _ = _services(tmp_path)
    opened = drafts.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("orbits")))
    patched = drafts.patch(
        CatalogDraftPatchRequest(
            draft=opened,
            expected_draft_revision=0,
            commands=(
                CatalogDraftPatchCommand(
                    operation="replace",
                    pointer="/orbit/propagator",
                    value="crtbp",
                ),
            ),
        )
    )
    compiled = drafts.compile(
        CatalogDraftCompileRequest(
            draft=patched,
            expected_draft_revision=patched.draft_revision,
        )
    )

    assert compiled.save_allowed is True
    assert compiled.runtime_supported is False
    assert compiled.canonical_yaml is not None
    assert compiled.issues[0].stage == "runtime_support"
    assert compiled.issues[0].blocks == ("deploy",)


def test_compile_blocks_dangling_component_reference_before_save(
    tmp_path: Path,
) -> None:
    drafts, _ = _services(tmp_path)
    opened = drafts.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("nodes")))
    assert opened.document["node"]["terminals"]
    patched = drafts.patch(
        CatalogDraftPatchRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            commands=(
                CatalogDraftPatchCommand(
                    operation="replace",
                    pointer="/node/terminals/0/terminal",
                    value="user:terminals/does-not-exist.yaml",
                ),
            ),
        )
    )

    compiled = drafts.compile(
        CatalogDraftCompileRequest(
            draft=patched,
            expected_draft_revision=patched.draft_revision,
        )
    )

    assert compiled.save_allowed is False
    assert compiled.runtime_supported is False
    assert [issue.stage for issue in compiled.issues] == ["reference"]
    assert compiled.issues[0].code.endswith("dangling_reference")


def test_patch_commands_round_trip_through_strict_json_transport_models(
    tmp_path: Path,
) -> None:
    drafts, _ = _services(tmp_path)
    opened = drafts.open(CatalogDraftOpenRequest(source_ref=_first_shipped_ref("terminals")))
    request: dict[str, Any] = {
        "draft": opened.model_dump(mode="json"),
        "expected_draft_revision": 0,
        "commands": [{"operation": "add", "pointer": "/terminal/notes", "value": None}],
    }

    validated = CatalogDraftPatchRequest.model_validate(request)
    assert validated.commands[0].value is None

    with pytest.raises(ValidationError, match="remove commands"):
        CatalogDraftPatchCommand(
            operation="remove",
            pointer="/terminal/notes",
            value=None,
        )


def test_advanced_object_json_is_parsed_identity_checked_and_revisioned_by_backend(
    tmp_path: Path,
) -> None:
    drafts, authoring = _services(tmp_path)
    opened = drafts.open(CatalogDraftOpenRequest(source_ref=_payload_source(authoring)))
    object_id = opened.target_ref.relative_path.stem
    replaced = drafts.replace_object(
        CatalogDraftReplaceObjectRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            raw_object_json=(
                '{"id":"' + object_id + '","display_name":"Backend parsed","terminal_slots":[],'
                '"resource_groups":[],"advanced":{"keep":[1,2,3]}}'
            ),
        )
    )

    assert replaced.draft_revision == opened.draft_revision + 1
    assert replaced.expected_source_revision == opened.expected_source_revision
    assert replaced.expected_target_revision == opened.expected_target_revision
    assert replaced.document["payload"]["display_name"] == "Backend parsed"
    assert replaced.document["payload"]["advanced"] == {"keep": [1, 2, 3]}

    for raw in (
        "not-json",
        "[]",
        '{"id":"different"}',
        f'{{"id":"{object_id}","value":NaN}}',
        f'{{"id":"{object_id}","id":"{object_id}"}}',
    ):
        with pytest.raises(CatalogAuthoringError) as invalid:
            drafts.replace_object(
                CatalogDraftReplaceObjectRequest(
                    draft=opened,
                    expected_draft_revision=opened.draft_revision,
                    raw_object_json=raw,
                )
            )
        assert invalid.value.code == "catalog_authoring.invalid_document"


def test_openapi_exposes_typed_scope_free_component_draft_routes(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, "openapi-catalog")
    application = FastAPI()
    application.include_router(
        create_builder_router(
            BuilderRouterServices(
                context_provider=lambda: context,
                available_node_count_provider=lambda: 1_000_000,
            )
        )
    )
    openapi = application.openapi()
    expected = {
        "/api/v1/builder/catalog/draft/new": (
            "CatalogDraftNewRequest",
            "CatalogComponentDraftEnvelope",
        ),
        "/api/v1/builder/catalog/draft/open": (
            "CatalogDraftOpenRequest",
            "CatalogComponentDraftEnvelope",
        ),
        "/api/v1/builder/catalog/draft/patch": (
            "CatalogDraftPatchRequest",
            "CatalogComponentDraftEnvelope",
        ),
        "/api/v1/builder/catalog/draft/replace-object": (
            "CatalogDraftReplaceObjectRequest",
            "CatalogComponentDraftEnvelope",
        ),
        "/api/v1/builder/catalog/draft/compile": (
            "CatalogDraftCompileRequest",
            "CatalogDraftCompileResult",
        ),
        "/api/v1/builder/catalog/draft/save": (
            "CatalogDraftSaveRequest",
            "CatalogDraftSaveResult",
        ),
    }
    components = openapi["components"]["schemas"]
    observed_names: set[str] = set()
    for route, (request_model, response_model) in expected.items():
        operation = openapi["paths"][route]["post"]
        request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert request_schema["$ref"].endswith(f"/{request_model}")
        assert response_schema["$ref"].endswith(f"/{response_model}")
        observed_names.update(_schema_property_names(request_schema, components))

    assert {
        "scope",
        "scope_id",
        "tenant_id",
        "principal_id",
        "catalog_scope_id",
        "filesystem_path",
        "path",
    }.isdisjoint(observed_names)
