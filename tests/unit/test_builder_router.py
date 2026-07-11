from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import BuilderSessionDeployRefusal
from nodalarc.models.builder_catalog_api import CatalogOperationRefusal
from vs_api.builder_catalog_service import (
    BuilderCatalogAuthoringService,
    CatalogAuthoringError,
)
from vs_api.builder_router import (
    BuilderRouterServices,
    BuilderSessionDeployError,
    create_builder_router,
)
from vs_api.builder_session_service import (
    BuilderSessionSaveBlockedError,
    BuilderSessionSaveErrorCode,
    BuilderSessionSaveErrorEvidence,
    BuilderSessionSavePersistenceError,
    BuilderSessionSaveStaleError,
)
from vs_api.catalog_context import CatalogContext

from tests.asgi_client import ASGITestClient as TestClient
from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions/earth-leo-simple.yaml"


@pytest.fixture()
def catalog_context(tmp_path: Path) -> CatalogContext:
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: tmp_path / "user-catalog"},
    )
    return CatalogContext(repository=repository, scope=scope)


def _catalog_service(context: CatalogContext) -> BuilderCatalogAuthoringService:
    return BuilderCatalogAuthoringService(
        context,
        page_token_secret=b"builder-router-test-page-secret-001",
    )


def _application(
    context: CatalogContext,
    *,
    catalog_service_factory=_catalog_service,
    session_service=None,
    deploy_callback=None,
) -> FastAPI:
    kwargs: dict[str, Any] = {
        "context_provider": lambda: context,
        "available_node_count_provider": lambda: 1_000_000,
        "catalog_service_factory": catalog_service_factory,
        "preview_factory": lambda raw, _roots: builder_world_preview(raw["session"]["name"]),
    }
    if session_service is not None:
        kwargs["session_service"] = session_service
    if deploy_callback is not None:
        kwargs["deploy_callback"] = deploy_callback
    app = FastAPI()
    app.include_router(create_builder_router(BuilderRouterServices(**kwargs)))
    return app


def _first_shipped_ref(family: str) -> str:
    path = sorted((SHIPPED_ROOT / family).rglob("*.yaml"))[0]
    return f"nodalarc:{path.relative_to(SHIPPED_ROOT).as_posix()}"


def _session_save_request(name: str) -> dict[str, Any]:
    session = yaml.safe_load(SIMPLE_SESSION.read_bytes())
    session["session"]["name"] = name
    return {
        "draft": {
            "contract_version": 1,
            "draft_revision": 3,
            "state": {"session": session, "catalog_documents": []},
        },
        "target_ref": f"user:sessions/{name}.yaml",
    }


def _walk_request_property_names(
    schema: dict[str, Any],
    components: dict[str, Any],
    *,
    seen: set[str] | None = None,
) -> Iterator[str]:
    visited = set() if seen is None else seen
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in visited:
            return
        visited.add(reference)
        yield from _walk_request_property_names(
            components[reference.rsplit("/", 1)[-1]],
            components,
            seen=visited,
        )
        return
    for name, property_schema in schema.get("properties", {}).items():
        yield name
        yield from _walk_request_property_names(property_schema, components, seen=visited)
    for keyword in ("items", "additionalProperties"):
        child = schema.get(keyword)
        if isinstance(child, dict):
            yield from _walk_request_property_names(child, components, seen=visited)
    for keyword in ("allOf", "anyOf", "oneOf"):
        for child in schema.get(keyword, ()):
            yield from _walk_request_property_names(child, components, seen=visited)


def test_router_executes_every_typed_authoring_operation(catalog_context: CatalogContext) -> None:
    client = TestClient(_application(catalog_context))

    bootstrap = client.get("/api/v1/builder/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["capabilities"] == {
        "user_catalog_write": True,
        "deploy_yaml_closure": True,
    }

    save_request = _session_save_request("router-session")
    compile_response = client.post(
        "/api/v1/builder/compile",
        json={"draft": save_request["draft"], "target_ref": save_request["target_ref"]},
    )
    assert compile_response.status_code == 200
    assert compile_response.json()["save_verdict"]["allowed"] is True

    saved = client.post("/api/v1/builder/session/save", json=save_request)
    assert saved.status_code == 200
    saved_document = saved.json()["session"]
    assert saved_document["ref"] == save_request["target_ref"]
    assert "nodalarc:" in saved_document["canonical_yaml"]

    listed = client.post(
        "/api/v1/builder/catalog/list",
        json={"family": "sessions", "namespace": "user", "page_size": 10},
    )
    assert listed.status_code == 200
    assert [item["ref"] for item in listed.json()["items"]] == [save_request["target_ref"]]

    fetched = client.post(
        "/api/v1/builder/catalog/get",
        json={"ref": save_request["target_ref"]},
    )
    assert fetched.status_code == 200
    assert fetched.json() == saved_document

    session_impact = client.post(
        "/api/v1/builder/catalog/dependents",
        json={"ref": save_request["target_ref"]},
    )
    assert session_impact.status_code == 200
    assert session_impact.json()["delete_allowed"] is True

    exported = client.post(
        "/api/v1/builder/session/export",
        json={
            "session_ref": save_request["target_ref"],
            "expected_session_revision": saved_document["revision"],
        },
    )
    assert exported.status_code == 200
    export = exported.json()
    assert export["root"]["exact_yaml"] == saved_document["canonical_yaml"]

    imported = client.post(
        "/api/v1/builder/session/import",
        json={
            "contract_version": 1,
            "root_ref": export["session_ref"],
            "root_yaml": export["root"]["exact_yaml"],
            "document_digest": export["document_digest"],
            "closure_digest": export["closure_digest"],
            "entries": [
                {
                    "ref": entry["ref"],
                    "exact_yaml": entry["exact_yaml"],
                    "document_digest": entry["document_digest"],
                }
                for entry in export["entries"]
            ],
            "commit": False,
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["outcome"] == "unchanged"

    source_ref = _first_shipped_ref("terminals")
    forked = client.post(
        "/api/v1/builder/catalog/fork",
        json={
            "source_ref": source_ref,
            "target_ref": "user:terminals/router-terminal.yaml",
        },
    )
    assert forked.status_code == 200
    fork_document = forked.json()["result"]["document"]

    incomplete_draft = client.post(
        "/api/v1/builder/catalog/draft/new",
        json={"family": "terminals", "object_id": "router-new-terminal"},
    )
    assert incomplete_draft.status_code == 200, incomplete_draft.text
    assert incomplete_draft.json()["issues"]

    opened_draft = client.post(
        "/api/v1/builder/catalog/draft/open",
        json={
            "source_ref": source_ref,
            "target_ref": "user:terminals/router-draft-terminal.yaml",
        },
    )
    assert opened_draft.status_code == 200, opened_draft.text
    opened = opened_draft.json()
    patched_draft = client.post(
        "/api/v1/builder/catalog/draft/patch",
        json={
            "draft": opened,
            "expected_draft_revision": opened["draft_revision"],
            "commands": [
                {
                    "operation": "replace",
                    "pointer": "/terminal/notes",
                    "value": "Patched by the backend",
                }
            ],
        },
    )
    assert patched_draft.status_code == 200, patched_draft.text
    patched = patched_draft.json()
    compiled_draft = client.post(
        "/api/v1/builder/catalog/draft/compile",
        json={
            "draft": patched,
            "expected_draft_revision": patched["draft_revision"],
        },
    )
    assert compiled_draft.status_code == 200, compiled_draft.text
    assert compiled_draft.json()["save_allowed"] is True
    saved_draft = client.post(
        "/api/v1/builder/catalog/draft/save",
        json={
            "draft": patched,
            "expected_draft_revision": patched["draft_revision"],
        },
    )
    assert saved_draft.status_code == 200, saved_draft.text
    assert (
        saved_draft.json()["result"]["document"]["canonical_json"]["terminal"]["notes"]
        == "Patched by the backend"
    )

    replacement = copy.deepcopy(fork_document["canonical_json"])
    replacement["terminal"]["notes"] = "Updated through the typed router"
    written = client.post(
        "/api/v1/builder/catalog/write",
        json={
            "ref": fork_document["ref"],
            "document": replacement,
            "expected_revision": fork_document["revision"],
        },
    )
    assert written.status_code == 200
    updated = written.json()
    assert updated["document"]["canonical_json"]["terminal"]["notes"].startswith("Updated")

    impact = client.post(
        "/api/v1/builder/catalog/dependents",
        json={"ref": fork_document["ref"]},
    ).json()
    deleted = client.post(
        "/api/v1/builder/catalog/delete",
        json={
            "ref": fork_document["ref"],
            "expected_revision": updated["document"]["revision"],
            "impact_acknowledgement": impact["acknowledgement"],
        },
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_ref"] == fork_document["ref"]


def test_router_compiles_wizard_intent_into_builder_contract(
    catalog_context: CatalogContext,
) -> None:
    client = TestClient(_application(catalog_context))

    response = client.post(
        "/api/v1/builder/wizard/compile",
        json={
            "draft_revision": 2,
            "intent": {
                "constellation_ref": ("nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml"),
                "satellite_node_ref": "nodalarc:nodes/space/leo-relay.yaml",
                "ground_site_set_ref": (
                    "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml"
                ),
                "protocol": "isis",
                "extensions": ["te", "mpls"],
                "orbit_propagator": "j2_mean_elements",
                "area_strategy": "per_plane",
                "routing_timers": {
                    "bfd": False,
                    "bfd_detect_multiplier": 3,
                    "bfd_rx_interval": 300,
                    "bfd_tx_interval": 300,
                    "isis_hello_interval": 1,
                    "isis_hello_multiplier": 3,
                    "spf_init_delay": 50,
                    "spf_short_delay": 200,
                    "spf_long_delay": 1000,
                    "spf_holddown": 2000,
                    "spf_time_to_learn": 500,
                    "ospf_hello_interval": 1,
                    "ospf_dead_interval": 3,
                    "ospf_spf_delay": 50,
                    "ospf_spf_initial_hold": 200,
                    "ospf_spf_max_hold": 1000,
                },
            },
        },
    )

    assert response.status_code == 200, response.text
    compiled = response.json()
    assert compiled["target_ref"].startswith("user:sessions/wizard/")
    assert compiled["save_verdict"]["allowed"] is True
    assert compiled["deploy_eligibility_after_save"]["allowed"] is True
    session_source = compiled["draft"]["state"]["session"]["segments"][0]["source"]
    assert session_source.startswith("user:constellations/wizard/")
    assert compiled["draft"]["state"]["catalog_documents"][0]["ref"] == session_source


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("catalog_authoring.not_found", 404),
        ("catalog_authoring.read_only", 403),
        ("catalog_authoring.invalid_document", 422),
        ("catalog_authoring.invalid_patch", 422),
        ("catalog_authoring.invalid_graph", 422),
        ("catalog_authoring.conflict", 409),
        ("catalog_authoring.stale_revision", 409),
        ("catalog_authoring.invalid_page_token", 400),
        ("catalog_authoring.stale_page_token", 409),
        ("catalog_authoring.impact_mismatch", 409),
        ("catalog_authoring.dependents_exist", 409),
        ("catalog_authoring.import_limit", 413),
        ("catalog_authoring.import_digest_mismatch", 422),
        ("catalog_authoring.import_incomplete", 422),
        ("catalog_authoring.import_collision", 409),
        ("catalog_authoring.persistence_failed", 503),
    ],
)
def test_catalog_refusals_have_stable_http_statuses(
    catalog_context: CatalogContext,
    code: str,
    expected_status: int,
) -> None:
    refusal = CatalogOperationRefusal(
        code=code,
        message="typed refusal",
        ref="user:terminals/refused.yaml",
    )

    class RefusingCatalogService:
        def get_catalog(self, _request):
            raise CatalogAuthoringError(refusal)

    client = TestClient(
        _application(
            catalog_context,
            catalog_service_factory=lambda _context: RefusingCatalogService(),
        )
    )
    response = client.post(
        "/api/v1/builder/catalog/get",
        json={"ref": "user:terminals/refused.yaml"},
    )

    assert response.status_code == expected_status
    assert response.json()["code"] == code
    assert response.json()["message"] == "typed refusal"


def test_compile_maps_catalog_snapshot_outage_to_typed_503(
    catalog_context: CatalogContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_snapshot(_scope):
        raise OSError("repository unavailable")

    monkeypatch.setattr(catalog_context.repository, "snapshot", unavailable_snapshot)
    response = TestClient(_application(catalog_context)).post(
        "/api/v1/builder/compile",
        json={
            "draft": {
                "contract_version": 1,
                "draft_revision": 0,
                "state": {"session": {}},
            },
            "target_ref": "user:sessions/unavailable.yaml",
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "catalog_authoring.persistence_failed",
        "message": "Catalog storage is unavailable",
        "collisions": [],
        "cause_type": "OSError",
    }


@pytest.mark.parametrize(
    ("code", "error_type", "repository_committed", "expected_status"),
    [
        (
            BuilderSessionSaveErrorCode.SAVE_BLOCKED,
            BuilderSessionSaveBlockedError,
            False,
            422,
        ),
        (
            BuilderSessionSaveErrorCode.STALE_WRITE,
            BuilderSessionSaveStaleError,
            False,
            409,
        ),
        (
            BuilderSessionSaveErrorCode.GRAPH_INVALID,
            BuilderSessionSavePersistenceError,
            False,
            422,
        ),
        (
            BuilderSessionSaveErrorCode.PERSISTENCE_FAILED,
            BuilderSessionSavePersistenceError,
            False,
            503,
        ),
        (
            BuilderSessionSaveErrorCode.STORAGE_VERIFICATION_FAILED,
            BuilderSessionSavePersistenceError,
            True,
            500,
        ),
    ],
)
def test_session_save_refusals_preserve_commit_truth(
    catalog_context: CatalogContext,
    code: BuilderSessionSaveErrorCode,
    error_type,
    repository_committed: bool,
    expected_status: int,
) -> None:
    def refuse_save(*_args, **_kwargs):
        raise error_type(
            BuilderSessionSaveErrorEvidence(
                code=code,
                message="session save was refused",
                target_ref="user:sessions/refused.yaml",
                base_generation="generation-1",
                repository_committed=repository_committed,
                cause_type="SyntheticFailure",
            )
        )

    response = TestClient(_application(catalog_context, session_service=refuse_save)).post(
        "/api/v1/builder/session/save",
        json={
            "draft": {
                "contract_version": 1,
                "draft_revision": 0,
                "state": {"session": {}},
            },
            "target_ref": "user:sessions/refused.yaml",
        },
    )

    assert response.status_code == expected_status
    body = response.json()
    assert body["code"] == code.value
    assert body["repository_committed"] is repository_committed
    assert "rollback" not in str(body).lower()


def test_request_contracts_expose_no_client_selected_authority(
    catalog_context: CatalogContext,
) -> None:
    async def accept_deploy(request, _context):
        return {
            "operation_id": "authority-test-operation",
            "status": "accepted",
            "source": request,
        }

    app = _application(catalog_context, deploy_callback=accept_deploy)
    client = TestClient(app)
    response = client.post(
        "/api/v1/builder/compile",
        json={
            "draft": {
                "contract_version": 1,
                "draft_revision": 0,
                "state": {"session": {}},
            },
            "target_ref": "user:sessions/authority-test.yaml",
            "scope": "attacker-selected",
            "tenant_id": "other-tenant",
            "filesystem_path": "/tmp/escape",
            "upload_id": "arbitrary-upload",
        },
    )
    assert response.status_code == 422
    assert {tuple(item["loc"])[-1] for item in response.json()["detail"]} >= {
        "scope",
        "tenant_id",
        "filesystem_path",
        "upload_id",
    }

    forbidden = {
        "scope",
        "scope_id",
        "tenant_id",
        "principal_id",
        "catalog_scope_id",
        "filesystem_path",
        "path",
        "upload_id",
        "upload_name",
        "config_map_name",
    }
    openapi = app.openapi()
    components = openapi["components"]["schemas"]
    observed: set[str] = set()
    for path in openapi["paths"].values():
        for operation in path.values():
            request_body = operation.get("requestBody")
            if request_body is None:
                continue
            schema = request_body["content"]["application/json"]["schema"]
            observed.update(_walk_request_property_names(schema, components))
    assert forbidden.isdisjoint(observed)


def test_deploy_callback_receives_only_exact_saved_source_and_server_context(
    catalog_context: CatalogContext,
) -> None:
    calls: list[tuple[Any, CatalogContext]] = []

    async def accept_deploy(request, context):
        calls.append((request, context))
        return {
            "operation_id": "switch-operation-42",
            "status": "accepted",
            "source": request,
        }

    client = TestClient(_application(catalog_context, deploy_callback=accept_deploy))
    request = {
        "session_ref": "user:sessions/deploy-me.yaml",
        "expected_session_revision": "session-revision-7",
        "expected_document_digest": f"sha256:{'a' * 64}",
        "expected_dependency_digest": f"sha256:{'b' * 64}",
    }
    accepted = client.post("/api/v1/builder/session/deploy", json=request)

    assert accepted.status_code == 202
    assert accepted.json() == {
        "operation_id": "switch-operation-42",
        "status": "accepted",
        "source": request,
    }
    assert len(calls) == 1
    assert calls[0][0].model_dump(mode="json") == request
    assert calls[0][1] is catalog_context

    refused = client.post(
        "/api/v1/builder/session/deploy",
        json={**request, "upload_id": "browser-selected-upload"},
    )
    assert refused.status_code == 422
    assert len(calls) == 1


def test_deploy_refusal_is_typed_and_path_free(catalog_context: CatalogContext) -> None:
    async def refuse_deploy(request, _context):
        raise BuilderSessionDeployError(
            BuilderSessionDeployRefusal(
                code="builder_session_deploy.stale_source",
                message="Saved session changed after review",
                session_ref=request.session_ref,
                expected="sha256:" + "a" * 64,
                observed="sha256:" + "b" * 64,
                cause_type="CatalogConflictError",
            )
        )

    client = TestClient(_application(catalog_context, deploy_callback=refuse_deploy))
    response = client.post(
        "/api/v1/builder/session/deploy",
        json={
            "session_ref": "user:sessions/deploy-me.yaml",
            "expected_session_revision": "session-revision-7",
            "expected_document_digest": f"sha256:{'a' * 64}",
            "expected_dependency_digest": f"sha256:{'b' * 64}",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "builder_session_deploy.stale_source",
        "message": "Saved session changed after review",
        "session_ref": "user:sessions/deploy-me.yaml",
        "expected": "sha256:" + "a" * 64,
        "observed": "sha256:" + "b" * 64,
        "cause_type": "CatalogConflictError",
    }
    assert "/tmp/" not in response.text


def test_router_is_auth_neutral_and_root_can_apply_a_guard(
    catalog_context: CatalogContext,
) -> None:
    unguarded = TestClient(_application(catalog_context))
    assert unguarded.get("/api/v1/builder/bootstrap").status_code == 200

    async def require_token(request: Request) -> None:
        if request.headers.get("authorization") != "Bearer test-token":
            raise HTTPException(status_code=401, detail="missing test token")

    guarded = FastAPI()
    guarded.include_router(
        create_builder_router(
            BuilderRouterServices(
                context_provider=lambda: catalog_context,
                catalog_service_factory=_catalog_service,
            )
        ),
        dependencies=[Depends(require_token)],
    )
    client = TestClient(guarded)
    assert client.get("/api/v1/builder/bootstrap").status_code == 401
    assert (
        client.get(
            "/api/v1/builder/bootstrap",
            headers={"authorization": "Bearer test-token"},
        ).status_code
        == 200
    )
