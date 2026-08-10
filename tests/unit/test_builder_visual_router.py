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


def _walk_controls(control: dict):
    yield control
    if control["kind"] == "object":
        for field in control["fields"]:
            yield from _walk_controls(field["control"])
    elif control["kind"] == "choice":
        for branch in control["branches"]:
            if branch.get("control") is not None:
                yield from _walk_controls(branch["control"])
    elif control["kind"] == "sequence":
        for item in control["items"]:
            yield from _walk_controls(item["control"])
        if control.get("add_item_control") is not None:
            yield from _walk_controls(control["add_item_control"])
    elif control["kind"] == "map":
        for entry in control["entries"]:
            yield from _walk_controls(entry["key"])
            yield from _walk_controls(entry["value"])
        yield from _walk_controls(control["add_key_control"])
        yield from _walk_controls(control["add_value_control"])


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
    assert created["contract_version"] == 2
    assert created["projection_status"] == "incomplete_authoring"
    assert created["applied_revision"] is None
    assert created["applied_session"] is None
    assert created["applied_workspace"] is None
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
    assert opened["projection_status"] == "applied"
    assert opened["applied_revision"] == opened["draft_revision"] == 0
    assert opened["authoring_workspace"] == opened["applied_workspace"]
    assert opened["applied_session"] is not None
    assert opened["target_ref"] == "user:sessions/earth-leo-simple.yaml"
    assert "earth-leo-ring-36.yaml" in opened["session_yaml"]

    customized_response = client.post(
        "/api/v1/builder/draft/customize-chain",
        json={
            "draft": opened,
            "expected_draft_revision": opened["draft_revision"],
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


def test_visual_yaml_apply_route_echoes_buffer_and_returns_typed_stale_refusal(
    client: TestClient,
) -> None:
    opened_response = client.post(
        "/api/v1/builder/draft/open",
        json={"source_ref": "nodalarc:sessions/earth-leo-simple.yaml"},
    )
    assert opened_response.status_code == 200
    opened = opened_response.json()
    invalid = "session:\n  name: earth-leo-simple\nsegments: [unterminated"

    refused_response = client.post(
        "/api/v1/builder/draft/apply-yaml",
        json={
            "draft": opened,
            "expected_draft_revision": 0,
            "buffer_generation": 41,
            "yaml_text": invalid,
        },
    )
    assert refused_response.status_code == 200, refused_response.text
    refused = refused_response.json()
    assert refused["applied"] is False
    assert refused["buffer_generation"] == 41
    assert refused["yaml_text"] == invalid
    assert refused["draft"]["draft_revision"] == 0
    assert refused["draft"]["projection_status"] == "pending_authoring"
    assert refused["issues"][0]["source_line"] == 3

    stale_response = client.post(
        "/api/v1/builder/draft/apply-yaml",
        json={
            "draft": opened,
            "expected_draft_revision": 1,
            "buffer_generation": 40,
            "yaml_text": opened["session_yaml"],
        },
    )
    assert stale_response.status_code == 409, stale_response.text
    assert stale_response.json()["code"] == "catalog_authoring.stale_revision"
    assert stale_response.json()["expected_revision"] == "1"
    assert stale_response.json()["current_revision"] == "0"


def test_visual_workspace_apply_route_returns_compiled_revision_and_stale_refusal(
    client: TestClient,
) -> None:
    opened_response = client.post(
        "/api/v1/builder/draft/open",
        json={"source_ref": "nodalarc:sessions/earth-leo-simple.yaml"},
    )
    assert opened_response.status_code == 200
    opened = opened_response.json()
    workspace = dict(opened["authoring_workspace"])
    workspace["display_name"] = "Changed through workspace route"

    applied_response = client.post(
        "/api/v1/builder/draft/apply-workspace",
        json={
            "draft": opened,
            "expected_draft_revision": 0,
            "workspace": workspace,
        },
    )
    assert applied_response.status_code == 200, applied_response.text
    applied = applied_response.json()
    assert applied["visual_draft"]["draft_revision"] == 1
    assert applied["visual_draft"]["projection_status"] == "applied"
    assert applied["assembled_draft"]["draft_revision"] == 1
    assert applied["compile_result"]["save_verdict"]["allowed"] is True
    assert (
        applied["compile_result"]["canonical_session_json"]["session"]["display_name"]
        == "Changed through workspace route"
    )

    stale_response = client.post(
        "/api/v1/builder/draft/apply-workspace",
        json={
            "draft": opened,
            "expected_draft_revision": 1,
            "workspace": workspace,
        },
    )
    assert stale_response.status_code == 409, stale_response.text
    assert stale_response.json()["code"] == "catalog_authoring.stale_revision"


def test_visual_control_mutation_route_is_revision_fenced_and_typed(
    client: TestClient,
) -> None:
    opened = client.post(
        "/api/v1/builder/draft/open",
        json={"source_ref": "nodalarc:sessions/earth-leo-simple.yaml"},
    ).json()
    controls = tuple(_walk_controls(opened["applied_workspace"]["control_tree"]["root"]))
    display_name = next(
        control
        for control in controls
        if control["kind"] == "scalar" and control["json_pointer"] == "/session/display_name"
    )

    applied_response = client.post(
        "/api/v1/builder/draft/control-mutate",
        json={
            "draft": opened,
            "expected_draft_revision": 0,
            "commands": [
                {
                    "operation": "set_scalar",
                    "control_id": display_name["control_id"],
                    "value": "Changed through control route",
                }
            ],
        },
    )
    assert applied_response.status_code == 200, applied_response.text
    applied = applied_response.json()
    assert applied["visual_draft"]["draft_revision"] == 1
    assert (
        applied["compile_result"]["canonical_session_json"]["session"]["display_name"]
        == "Changed through control route"
    )

    stale_response = client.post(
        "/api/v1/builder/draft/control-mutate",
        json={
            "draft": applied["visual_draft"],
            "expected_draft_revision": 0,
            "commands": [
                {
                    "operation": "set_scalar",
                    "control_id": display_name["control_id"],
                    "value": "stale",
                }
            ],
        },
    )
    assert stale_response.status_code == 409
    assert stale_response.json()["code"] == "catalog_authoring.stale_revision"

    unknown_response = client.post(
        "/api/v1/builder/draft/control-mutate",
        json={
            "draft": applied["visual_draft"],
            "expected_draft_revision": 1,
            "commands": [
                {
                    "operation": "set_scalar",
                    "control_id": "ctl_00000000000000000000000000000000",
                    "value": "unknown",
                }
            ],
        },
    )
    assert unknown_response.status_code == 422
    assert unknown_response.json()["code"] == "catalog_authoring.invalid_patch"


def test_customize_chain_requires_the_current_draft_revision(client: TestClient) -> None:
    opened = client.post(
        "/api/v1/builder/draft/open",
        json={"source_ref": "nodalarc:sessions/earth-leo-simple.yaml"},
    ).json()

    missing = client.post(
        "/api/v1/builder/draft/customize-chain",
        json={
            "draft": opened,
            "segment_id": "leo",
            "leaf_ref": "nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        },
    )
    assert missing.status_code == 422

    stale = client.post(
        "/api/v1/builder/draft/customize-chain",
        json={
            "draft": opened,
            "expected_draft_revision": 1,
            "segment_id": "leo",
            "leaf_ref": "nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "catalog_authoring.stale_revision"


def test_visual_draft_route_generates_name_and_places_catalog_reference(
    client: TestClient,
) -> None:
    created_response = client.post("/api/v1/builder/draft/new", json={})
    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    generated_name = created["authoring_workspace"]["session_name"]
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
    assert applied["draft"]["authoring_workspace"]["space_refs"] == [
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
    envelope_names = sorted(
        name for name in schemas if name.startswith("BuilderVisualDraftEnvelope")
    )
    assert envelope_names, sorted(schemas)
    envelope_schema = schemas[envelope_names[0]]
    assert "mode" not in envelope_schema["properties"]
    assert "workspace" not in envelope_schema["properties"]
    assert "projection_status" in envelope_schema.get("required", [])
    assert "session_yaml" in envelope_schema.get("required", [])
    assert "authoring_workspace" in envelope_schema["properties"]
    assert "applied_workspace" in envelope_schema["properties"]
    assert "applied_revision" in envelope_schema["properties"]
    assert "applied_session" in envelope_schema["properties"]
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


def test_visual_draft_json_materializes_all_authoring_workspace_defaults(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/builder/draft/new",
        json={"session_name": "materialized-workspace"},
    ).json()
    assert created["reserved_authoring_ids"] == []
    assert created["projection_status"] == "incomplete_authoring"
    workspace = created["authoring_workspace"]
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
    visual_draft = response.json()["visual_draft"]
    assert visual_draft["projection_status"] == "incomplete_authoring"
    assert visual_draft["applied_revision"] is None
    materialized = visual_draft["authoring_workspace"]

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
        "projection_revision",
        "control_tree",
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
        "node_ref",
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
    assert applied["draft"]["projection_status"] == "applied"
    assert applied["draft"]["applied_revision"] == 1
    assert applied["draft"]["authoring_workspace"] == applied["draft"]["applied_workspace"]
    assert applied["affected_kind"] == "space"
    assert applied["affected_id"] == "space-1"
    space = applied["draft"]["authoring_workspace"]["space"][0]
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

    explicit_collision = client.post(
        "/api/v1/builder/draft/open",
        json={
            "source_ref": "nodalarc:sessions/earth-leo-simple.yaml",
            "target_ref": "user:sessions/earth-leo-simple.yaml",
        },
    )
    assert explicit_collision.status_code == 409
    assert explicit_collision.json()["code"] == "catalog_authoring.conflict"
    assert explicit_collision.json()["ref"] == "user:sessions/earth-leo-simple.yaml"

    created = client.post(
        "/api/v1/builder/draft/new",
        json={"session_name": "earth-leo-simple"},
    )
    assert created.status_code == 409
    assert created.json()["code"] == "catalog_authoring.conflict"
    assert created.json()["ref"] == "user:sessions/earth-leo-simple.yaml"
