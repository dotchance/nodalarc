"""FastAPI contracts for backend-owned visual Builder drafts."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from vs_api.builder_router import BuilderRouterServices, create_builder_router
from vs_api.catalog_context import CatalogContext

from tests.asgi_client import ASGITestClient as TestClient
from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    scope = CatalogScope()
    context = CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / "user-catalog"},
        ),
        scope=scope,
    )
    application = FastAPI()
    application.include_router(
        create_builder_router(
            BuilderRouterServices(
                context_provider=lambda: context,
                available_node_count_provider=lambda: 1_000_000,
                preview_factory=lambda raw, _roots: builder_world_preview(raw["session"]["name"]),
            )
        )
    )
    return TestClient(application)


def test_visual_draft_routes_create_open_and_compile_backend_contracts(
    client: TestClient,
) -> None:
    created_response = client.post(
        "/api/v1/builder/draft/new",
        json={"session_name": "API Visual Draft"},
    )
    assert created_response.status_code == 200
    created = created_response.json()
    assert created["mode"] == "structured"
    assert created["target_ref"] == "user:sessions/api-visual-draft.yaml"

    incomplete_response = client.post(
        "/api/v1/builder/draft/compile",
        json={"draft": created},
    )
    assert incomplete_response.status_code == 200
    incomplete = incomplete_response.json()
    assert incomplete["compile_result"]["save_verdict"]["allowed"] is False
    assert incomplete["save_request"]["target_ref"] == created["target_ref"]

    opened_response = client.post(
        "/api/v1/builder/draft/open",
        json={"source_ref": "nodalarc:sessions/earth-leo-simple.yaml"},
    )
    assert opened_response.status_code == 200
    opened = opened_response.json()
    assert opened["mode"] == "opaque_yaml"
    assert opened["target_ref"] == "user:sessions/earth-leo-simple.yaml"
    assert "earth-leo-ring-36.yaml" in opened["session_yaml"]

    customized_response = client.post(
        "/api/v1/builder/draft/customize-chain",
        json={
            "draft": opened,
            "segment_id": "leo",
            "leaf_ref": "nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        },
    )
    assert customized_response.status_code == 200, customized_response.text
    customized = customized_response.json()
    assert customized["applied"] is True
    assert [
        entry["source_ref"].split(":", 1)[1].split("/", 1)[0]
        for entry in customized["forked_chain"]
    ] == [
        "constellations",
        "nodes",
        "terminals",
    ]
    opened = customized["draft"]

    compiled_response = client.post(
        "/api/v1/builder/draft/compile",
        json={"draft": opened},
    )
    assert compiled_response.status_code == 200, compiled_response.text
    compiled = compiled_response.json()
    assert compiled["assembly_issues"] == []
    assert compiled["compile_result"]["save_verdict"]["allowed"] is True
    assert compiled["save_request"]["draft"] == compiled["assembled_draft"]


def test_visual_draft_command_route_returns_next_revision_and_typed_stale_refusal(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/builder/draft/new",
        json={"session_name": "API Command Draft"},
    ).json()
    applied_response = client.post(
        "/api/v1/builder/draft/command",
        json={
            "draft": created,
            "expected_draft_revision": 0,
            "command": {
                "operation": "add_generated_space",
                "phasing_mode": "walker_delta",
            },
        },
    )

    assert applied_response.status_code == 200, applied_response.text
    applied = applied_response.json()
    assert applied["operation"] == "add_generated_space"
    assert applied["base_draft_revision"] == 0
    assert applied["draft"]["draft_revision"] == 1
    assert applied["affected_kind"] == "space"
    assert applied["affected_id"] == "space-1"
    space = applied["draft"]["workspace"]["space"][0]
    assert (
        space["planes"],
        space["raan_spacing_deg"],
        space["phasing_mode"],
        space["phase_offset_deg"],
    ) == (
        3,
        120.0,
        "walker_delta",
        15.0,
    )

    stale_response = client.post(
        "/api/v1/builder/draft/command",
        json={
            "draft": applied["draft"],
            "expected_draft_revision": 0,
            "command": {"operation": "add_ground"},
        },
    )
    assert stale_response.status_code == 409
    assert stale_response.json() == {
        "code": "catalog_authoring.stale_revision",
        "message": "Visual draft revision changed before the command was applied",
        "ref": "user:sessions/api-command-draft.yaml",
        "expected_revision": "0",
        "current_revision": "1",
        "collisions": [],
        "cause_type": "BuilderVisualDraftCommandError",
    }


def test_walker_layout_route_returns_backend_derived_angles(client: TestClient) -> None:
    response = client.post(
        "/api/v1/builder/defaults/walker-layout",
        json={"pattern": "walker_star", "planes": 6, "slots_per_plane": 20},
    )

    assert response.status_code == 200
    assert response.json() == {
        "raan_spacing_deg": 30.0,
        "phase_offset_deg": 3.0,
    }

    invalid = client.post(
        "/api/v1/builder/defaults/walker-layout",
        json={"pattern": "walker_delta", "planes": 1, "slots_per_plane": 20},
    )
    assert invalid.status_code == 422


def test_visual_open_missing_source_is_a_typed_path_free_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/builder/draft/open",
        json={"source_ref": "user:sessions/missing.yaml"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "code": "catalog_authoring.not_found",
        "message": "Catalog document user:sessions/missing.yaml was not found",
        "ref": "user:sessions/missing.yaml",
        "collisions": [],
        "cause_type": "CatalogNotFoundError",
    }


def test_visual_session_target_collisions_are_typed_409(client: TestClient) -> None:
    opened = client.post(
        "/api/v1/builder/draft/open",
        json={"source_ref": "nodalarc:sessions/earth-leo-simple.yaml"},
    )
    assert opened.status_code == 200
    compiled = client.post(
        "/api/v1/builder/draft/compile",
        json={"draft": opened.json()},
    )
    assert compiled.status_code == 200
    saved = client.post(
        "/api/v1/builder/session/save",
        json=compiled.json()["save_request"],
    )
    assert saved.status_code == 200

    reopened = client.post(
        "/api/v1/builder/draft/open",
        json={"source_ref": "nodalarc:sessions/earth-leo-simple.yaml"},
    )
    assert reopened.status_code == 409
    assert reopened.json()["code"] == "catalog_authoring.conflict"
    assert reopened.json()["ref"] == "user:sessions/earth-leo-simple.yaml"

    created = client.post(
        "/api/v1/builder/draft/new",
        json={"session_name": "earth-leo-simple"},
    )
    assert created.status_code == 409
    assert created.json()["code"] == "catalog_authoring.conflict"
    assert created.json()["ref"] == "user:sessions/earth-leo-simple.yaml"
