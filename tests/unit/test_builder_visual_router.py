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
        json={"session_name": "api-visual-draft"},
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


def test_visual_draft_route_generates_name_and_places_catalog_reference(
    client: TestClient,
) -> None:
    created_response = client.post("/api/v1/builder/draft/new", json={})
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    generated_name = created["workspace"]["session_name"]
    assert generated_name.startswith("untitled-session-")
    assert created["target_ref"] == f"user:sessions/{generated_name}.yaml"
    assert created["session_name_is_placeholder"] is True
    assert created["reserved_authoring_ids"] == []

    applied_response = client.post(
        "/api/v1/builder/draft/command",
        json={
            "draft": created,
            "expected_draft_revision": 0,
            "command": {
                "operation": "place_space_reference",
                "source_ref": "nodalarc:constellations/luna/elfo/luna-elfo-relay-2.yaml",
            },
        },
    )
    assert applied_response.status_code == 200, applied_response.text
    applied = applied_response.json()
    assert applied["operation"] == "place_space_reference"
    assert applied["affected_id"] == "space-1"
    assert applied["draft"]["reserved_authoring_ids"] == ["space-1"]
    assert applied["draft"]["workspace"]["space_refs"] == [
        {
            "segment_id": "space-1",
            "source_ref": "nodalarc:constellations/luna/elfo/luna-elfo-relay-2.yaml",
            "label": "Two-node lunar ELFO relay constellation",
        }
    ]


@pytest.mark.parametrize("session_name", ["", "API Visual Draft", "_leading"])
def test_visual_draft_route_rejects_explicit_noncanonical_names(
    client: TestClient,
    session_name: str,
) -> None:
    response = client.post(
        "/api/v1/builder/draft/new",
        json={"session_name": session_name},
    )

    assert response.status_code == 422, response.text


def test_visual_draft_openapi_exposes_intent_only_command_contracts(
    client: TestClient,
) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200, response.text
    schemas = response.json()["components"]["schemas"]

    create_schema = schemas["BuilderVisualDraftCreateRequest"]
    assert "session_name" not in create_schema.get("required", [])
    session_name_schema = create_schema["properties"]["session_name"]
    identifier_schema = next(
        choice for choice in session_name_schema["anyOf"] if choice.get("type") == "string"
    )
    assert identifier_schema["pattern"] == "^[a-z0-9][a-z0-9_-]*$"
    envelope_schema = schemas["BuilderVisualDraftEnvelope"]
    assert "session_name_is_placeholder" in envelope_schema.get("required", [])
    assert "reserved_authoring_ids" in envelope_schema["properties"]
    assert "reserved_authoring_ids" in envelope_schema.get("required", [])
    space_schema = schemas["BuilderVisualSpaceDraft"]
    assert "phase_offset_deg" in space_schema["properties"]
    assert "phase_offset_deg" not in space_schema.get("required", [])
    expected_fields = {
        "BuilderVisualPlaceSpaceReferenceCommand": (
            {"operation", "source_ref"},
            {"operation", "source_ref"},
        ),
        "BuilderVisualPlaceGroundReferenceCommand": (
            {"operation", "site_set_ref"},
            {"operation", "site_set_ref"},
        ),
        "BuilderVisualAddGroundSiteReferenceCommand": (
            {"operation", "segment_id", "site_ref"},
            {"operation", "site_ref"},
        ),
        "BuilderVisualSetNodeTerminalRoleCommand": (
            {"operation", "segment_id", "mount_id", "role"},
            {"operation", "segment_id", "mount_id", "role"},
        ),
    }
    for schema_name, (properties, required) in expected_fields.items():
        schema = schemas[schema_name]
        assert set(schema["properties"]) == properties
        assert set(schema["required"]) == required
        assert schema["additionalProperties"] is False

    command_schema = schemas["BuilderVisualDraftCommandRequest"]["properties"]["command"]
    mapping = command_schema["discriminator"]["mapping"]
    assert {
        "place_space_reference",
        "place_ground_reference",
        "add_ground_site_reference",
        "set_node_terminal_role",
    }.issubset(mapping)


def test_structured_visual_draft_json_materializes_all_workspace_defaults(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/builder/draft/new",
        json={"session_name": "materialized-workspace"},
    ).json()
    assert created["reserved_authoring_ids"] == []
    workspace = created["workspace"]
    assert workspace["session_name"] == "materialized-workspace"
    assert workspace["display_name"] is None
    assert workspace["description"] is None
    assert workspace["space"] == []
    assert workspace["space_refs"] == []
    assert workspace["ground"] == []
    assert workspace["ground_refs"] == []
    assert workspace["links"] == []
    assert workspace["routing_domains"] == []
    assert workspace["boundaries"] == []
    assert workspace["max_pairs_per_rule"] == 2_000
    assert workspace["max_pairs_per_tick"] == 10_000
    assert workspace["start_time"].endswith("Z")
    assert workspace["step_seconds"] == 1.0
    assert workspace["compression"] == 1.0
    workspace.update(
        {
            "space": [
                {
                    "phasing_mode": "walker_delta",
                    "phase_offset_deg": 0,
                    "node_draft": {"terminals": [{}]},
                }
            ],
            "space_refs": [{}],
            "ground": [
                {
                    "stamp": {"boresights": {}},
                    "members": [
                        {
                            "kind": "draft",
                            "site": {"nodes": [{"boresights": {}}]},
                        }
                    ],
                }
            ],
            "ground_refs": [{}],
            "links": [{}],
            "routing_domains": [{}],
            "boundaries": [{}],
        }
    )

    response = client.post(
        "/api/v1/builder/draft/compile",
        json={"draft": created},
    )
    assert response.status_code == 200, response.text
    materialized = response.json()["visual_draft"]["workspace"]

    assert set(materialized) == {
        "session_name",
        "display_name",
        "description",
        "space",
        "space_refs",
        "ground",
        "ground_refs",
        "links",
        "routing_domains",
        "boundaries",
        "max_pairs_per_rule",
        "max_pairs_per_tick",
        "start_time",
        "step_seconds",
        "compression",
    }
    assert set(materialized["space"][0]) == {
        "segment_id",
        "display_name",
        "node_ref",
        "node_draft",
        "orbit",
        "planes",
        "raan_spacing_deg",
        "slots_per_plane",
        "phasing_mode",
        "phase_offset_deg",
    }
    assert set(materialized["space"][0]["orbit"]) == {
        "central_body",
        "shape_kind",
        "altitude_km",
        "perigee_altitude_km",
        "apogee_altitude_km",
        "inclination_deg",
        "raan_deg",
        "argument_of_perigee_deg",
        "mean_anomaly_deg",
        "propagator",
    }
    assert set(materialized["space"][0]["node_draft"]) == {
        "id",
        "display_name",
        "forwarding",
        "ethernet",
        "terminals",
    }
    assert set(materialized["space"][0]["node_draft"]["terminals"][0]) == {
        "mount_id",
        "role",
        "terminal_ref",
        "count",
        "boresight",
    }
    assert set(materialized["space_refs"][0]) == {
        "segment_id",
        "source_ref",
        "label",
    }
    assert set(materialized["ground"][0]) == {
        "segment_id",
        "display_name",
        "members",
        "stamp",
        "scheduling",
        "originated_ipv4",
        "tags",
    }
    assert set(materialized["ground"][0]["stamp"]) == {
        "node_ref",
        "installed",
        "boresights",
        "body",
        "lan_base",
        "loopback_base",
    }
    member = materialized["ground"][0]["members"][0]
    assert set(member) == {
        "member_id",
        "kind",
        "ref",
        "site_id",
        "label",
        "summary",
        "site",
        "scheduling_override",
    }
    assert set(member["site"]) == {
        "site_id",
        "display_name",
        "body",
        "lat_deg",
        "lon_deg",
        "alt_m",
        "lan_ipv4",
        "tags",
        "nodes",
    }
    assert set(member["site"]["nodes"][0]) == {
        "node_id",
        "model_ref",
        "installed",
        "boresights",
        "lo0_ipv4",
        "terr0_ipv4",
    }
    assert set(materialized["ground_refs"][0]) == {
        "segment_id",
        "site_set_ref",
        "label",
        "scheduling",
    }
    assert set(materialized["links"][0]) == {
        "rule_id",
        "label",
        "enabled",
        "a",
        "b",
        "topology_mode",
        "topology_n",
        "max_range_km",
    }
    assert set(materialized["links"][0]["a"]) == {
        "segment_id",
        "tag",
        "role",
        "medium",
        "min_elevation_deg",
    }
    assert set(materialized["routing_domains"][0]) == {
        "domain_id",
        "label",
        "protocol",
        "member_segment_ids",
        "hello_interval_s",
        "hold_interval_s",
    }
    assert set(materialized["boundaries"][0]) == {
        "boundary_id",
        "over_rule_id",
        "adapter",
        "from_domain_id",
        "to_domain_id",
        "export_node_loopbacks",
    }


def test_visual_draft_command_route_returns_next_revision_and_typed_stale_refusal(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/builder/draft/new",
        json={"session_name": "api-command-draft"},
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
