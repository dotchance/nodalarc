"""Backend-owned visual draft assembly and lossless opaque editing contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_repository import CatalogConflictError, CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_visual_api import (
    BuilderVisualCustomizeChainRequest,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftOpenRequest,
    BuilderVisualGroundBoresight,
    BuilderVisualGroundDraft,
    BuilderVisualGroundMember,
    BuilderVisualGroundStamp,
    BuilderVisualLinkEndpoint,
    BuilderVisualLinkRule,
    BuilderVisualNode,
    BuilderVisualOrbit,
    BuilderVisualRoutingBoundary,
    BuilderVisualRoutingDomain,
    BuilderVisualSite,
    BuilderVisualSiteNode,
    BuilderVisualSpaceBoresight,
    BuilderVisualSpaceDraft,
    BuilderVisualTerminalMount,
    BuilderVisualWorkspace,
)
from nodalarc.models.builder_world import BuilderWorld, BuilderWorldNode
from nodalarc.models.resolved_session import ResolvedTerminalBlock
from pydantic import ValidationError
from vs_api.builder_compiler import canonicalize_persisted_configuration
from vs_api.builder_session_service import save_builder_session
from vs_api.builder_visual_draft import (
    BuilderVisualDraftCommandError,
    BuilderVisualDraftService,
)
from vs_api.catalog_context import CatalogContext

from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"
SHIPPED_SESSIONS = tuple(sorted((SHIPPED_ROOT / "sessions").glob("*.yaml")))


@pytest.fixture()
def context(tmp_path: Path) -> CatalogContext:
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: tmp_path / "user-catalog"},
    )
    return CatalogContext(repository=repository, scope=scope)


@pytest.fixture()
def service(context: CatalogContext) -> BuilderVisualDraftService:
    return BuilderVisualDraftService(
        context,
        clock=lambda: datetime(2026, 7, 10, 12, 34, 56, tzinfo=UTC),
    )


def _preview(raw: dict[str, Any], _roots: object) -> BuilderWorld:
    return builder_world_preview(raw["session"]["name"])


def _orbit() -> BuilderVisualOrbit:
    return BuilderVisualOrbit(
        central_body="nodalarc:bodies/earth.yaml",
        shape_kind="circular",
        altitude_km=550,
        inclination_deg=53,
        raan_deg=0,
        argument_of_perigee_deg=0,
        mean_anomaly_deg=0,
        propagator="j2_mean_elements",
    )


def test_backend_creates_the_new_structured_visual_draft(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(
        BuilderVisualDraftCreateRequest(
            session_name="My Visual Session",
            display_name="My visual session",
        )
    )

    assert draft.mode == "structured"
    assert draft.target_ref == "user:sessions/my-visual-session.yaml"
    assert draft.workspace is not None
    assert draft.workspace.session_name == "my-visual-session"
    assert draft.workspace.display_name == "My visual session"
    assert draft.workspace.start_time == "2026-07-10T12:34:00Z"
    assert draft.session_yaml is None

    reposted = BuilderVisualDraftEnvelope.model_validate_json(draft.model_dump_json())
    assert reposted == draft


def test_backend_visual_commands_own_seeds_and_advance_one_fenced_revision(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="command-owned"))

    def apply(command: dict[str, Any]):
        nonlocal draft
        result = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        )
        assert result.base_draft_revision + 1 == result.draft.draft_revision
        draft = result.draft
        return result

    space_result = apply({"operation": "add_generated_space", "phasing_mode": "walker_delta"})
    assert (space_result.affected_kind, space_result.affected_id) == ("space", "space-1")
    assert draft.workspace is not None
    space = draft.workspace.space[0]
    assert str(space.node_ref).startswith("nodalarc:nodes/space/")
    assert space.model_dump(mode="json") == {
        "segment_id": "space-1",
        "display_name": "Constellation 1",
        "node_ref": str(space.node_ref),
        "node_draft": None,
        "orbit": {
            "central_body": "nodalarc:bodies/earth.yaml",
            "shape_kind": "circular",
            "altitude_km": 550.0,
            "perigee_altitude_km": 550.0,
            "apogee_altitude_km": 550.0,
            "inclination_deg": 53.0,
            "raan_deg": 0.0,
            "argument_of_perigee_deg": 0.0,
            "mean_anomaly_deg": 0.0,
            "propagator": "j2_mean_elements",
        },
        "planes": 3,
        "raan_spacing_deg": 120.0,
        "slots_per_plane": 8,
        "phasing_mode": "walker_delta",
        "phase_offset_deg": 15.0,
    }

    compiled_space = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert compiled_space.assembly_issues == ()
    assert compiled_space.compile_result.save_verdict.allowed is True
    assert compiled_space.compile_result.resolved_preview is not None

    ground_result = apply({"operation": "add_ground"})
    assert (ground_result.affected_kind, ground_result.affected_id) == (
        "ground",
        "ground-1",
    )
    ground = draft.workspace.ground[0]
    assert str(ground.stamp.node_ref).startswith("nodalarc:nodes/ground/")
    assert ground.stamp.installed
    assert ground.stamp.boresights
    assert {boresight.mode for boresight in ground.stamp.boresights.values()} == {"local_vertical"}
    assert ground.stamp.lan_base == "172.20"
    assert ground.stamp.loopback_base == "10.200"
    assert ground.scheduling["handover_mode"] == "mbb"
    assert ground.scheduling["ranking_order"] == [
        "service_priority",
        "per_gs_rank",
        "satellite_ground_terminal_capacity",
        "lex_pair",
    ]

    domain_result = apply({"operation": "add_routing_domain"})
    assert domain_result.affected_id == "domain-1"
    assert draft.workspace.routing_domains[0].member_segment_ids == (
        "space-1",
        "ground-1",
    )

    link_result = apply(
        {
            "operation": "connect_segments",
            "from_segment_id": "space-1",
            "to_segment_id": "ground-1",
        }
    )
    assert link_result.affected_id == "link-1"
    link = draft.workspace.links[0]
    assert link.label == "Ground segment 1 to Constellation 1"
    assert (link.a.segment_id, link.b.segment_id) == ("ground-1", "space-1")
    assert (link.a.role, link.a.medium, link.a.min_elevation_deg) == (
        "access",
        "rf",
        25.0,
    )
    assert (link.topology_mode, link.topology_n) == ("visible_candidates", 1)

    boundary_result = apply({"operation": "add_boundary"})
    assert boundary_result.affected_id == "boundary-1"
    boundary = draft.workspace.boundaries[0]
    assert boundary.over_rule_id == "link-1"
    assert boundary.from_domain_id == boundary.to_domain_id == "domain-1"

    scheduling_result = apply(
        {
            "operation": "set_scheduling_preset",
            "segment_id": "ground-1",
            "preset": "geo-longest-pass",
        }
    )
    assert scheduling_result.affected_kind == "ground"
    assert scheduling_result.scheduling_preset == "geo-longest-pass"
    assert draft.workspace.ground[0].scheduling["handover_mode"] == "bbm"

    rederived = apply(
        {
            "operation": "rederive_link",
            "rule_id": "link-1",
            "side": "a",
            "segment_id": "space-1",
        }
    )
    assert rederived.notice == (
        "re-derived: isl · optical · nearest-2 — WARNING: neither side has matching terminals"
    )
    link = draft.workspace.links[0]
    assert link.a.segment_id == link.b.segment_id == "space-1"
    assert (link.a.role, link.a.medium, link.topology_mode, link.topology_n) == (
        "isl",
        "optical",
        "nearest_n",
        2,
    )
    assert draft.draft_revision == 7

    independent = service.create(
        BuilderVisualDraftCreateRequest(session_name="independent-command-owned")
    )
    independent_result = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=independent,
            expected_draft_revision=0,
            command={"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert independent_result.affected_id == "space-1"


def test_backend_command_mints_ground_sites_and_allocates_all_addresses(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="backend-mint"))
    ground_result = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=draft,
            expected_draft_revision=0,
            command={"operation": "add_ground"},
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    minted = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=ground_result.draft,
            expected_draft_revision=1,
            command={
                "operation": "mint_ground_members",
                "segment_id": "ground-1",
                "sites": [
                    {"name": "Denver", "lat_deg": 39.7, "lon_deg": -104.9},
                    {"name": "Perth", "lat_deg": -31.9, "lon_deg": 115.8, "alt_m": 12},
                ],
            },
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert minted.notice == "minted 2 sites"
    assert minted.affected_kind == "ground"
    assert minted.affected_id == "ground-1"
    assert minted.draft.workspace is not None
    members = minted.draft.workspace.ground[0].members
    assert [(member.member_id, member.site_id, member.label) for member in members] == [
        ("member-1", "site-1", "Denver"),
        ("member-2", "site-2", "Perth"),
    ]
    assert [
        (
            member.site.lan_ipv4,
            member.site.nodes[0].terr0_ipv4,
            member.site.nodes[0].lo0_ipv4,
        )
        for member in members
        if member.site is not None
    ] == [
        ("172.20.0.0/24", "172.20.0.1/24", "10.200.0.1/32"),
        ("172.20.1.0/24", "172.20.1.1/24", "10.200.0.2/32"),
    ]

    workspace = minted.draft.workspace
    ground = workspace.ground[0]
    without_first = ground.model_copy(update={"members": (ground.members[1],)})
    revised = minted.draft.model_copy(
        update={
            "workspace": workspace.model_copy(update={"ground": (without_first,)}),
        }
    )
    third = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=revised,
            expected_draft_revision=2,
            command={
                "operation": "mint_ground_members",
                "segment_id": "ground-1",
                "sites": [{"name": "Ames", "lat_deg": 42, "lon_deg": -93}],
            },
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert third.draft.workspace is not None
    ames = third.draft.workspace.ground[0].members[-1]
    assert ames.site is not None
    assert ames.site.lan_ipv4 == "172.20.2.0/24"
    assert ames.site.nodes[0].terr0_ipv4 == "172.20.2.1/24"
    assert ames.site.nodes[0].lo0_ipv4 == "10.200.0.3/32"


def test_backend_commands_derive_space_transitions_and_ground_installations(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="backend-derivation"))

    def apply(command: dict[str, Any]) -> None:
        nonlocal draft
        result = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        )
        draft = result.draft

    apply({"operation": "add_generated_space", "phasing_mode": "walker_delta"})
    apply(
        {
            "operation": "set_space_population",
            "segment_id": "space-1",
            "phasing_mode": "evenly_spaced_mean_anomaly",
        }
    )
    assert draft.workspace is not None
    space = draft.workspace.space[0]
    assert (space.phasing_mode, space.planes, space.raan_spacing_deg, space.phase_offset_deg) == (
        "evenly_spaced_mean_anomaly",
        1,
        360.0,
        0.0,
    )

    apply({"operation": "set_space_population", "segment_id": "space-1", "planes": 4})
    space = draft.workspace.space[0]
    assert (space.phasing_mode, space.planes, space.raan_spacing_deg, space.phase_offset_deg) == (
        "walker_delta",
        4,
        90.0,
        11.25,
    )
    apply(
        {
            "operation": "set_space_population",
            "segment_id": "space-1",
            "phasing_mode": "walker_star",
        }
    )
    apply(
        {
            "operation": "set_space_population",
            "segment_id": "space-1",
            "slots_per_plane": 10,
        }
    )
    space = draft.workspace.space[0]
    assert (space.phasing_mode, space.planes, space.slots_per_plane) == ("walker_star", 4, 10)
    assert (space.raan_spacing_deg, space.phase_offset_deg) == (45.0, 9.0)

    apply({"operation": "add_ground"})
    apply(
        {
            "operation": "set_ground_stamp_node_model",
            "segment_id": "ground-1",
            "node_ref": "nodalarc:nodes/ground/leo-gateway.yaml",
        }
    )
    ground = draft.workspace.ground[0]
    assert ground.stamp.installed == {"access_ka": 8}
    assert ground.stamp.boresights == {
        "access_ka": BuilderVisualGroundBoresight(mode="local_vertical")
    }

    apply(
        {
            "operation": "mint_ground_members",
            "segment_id": "ground-1",
            "sites": [{"name": "Denver", "lat_deg": 39.7, "lon_deg": -104.9}],
        }
    )
    apply(
        {
            "operation": "set_ground_site_node_model",
            "segment_id": "ground-1",
            "member_id": "member-1",
            "node_id": "gw1",
            "node_ref": "nodalarc:nodes/ground/starlink-gateway.yaml",
        }
    )
    member = draft.workspace.ground[0].members[0]
    assert member.site is not None
    first_node = member.site.nodes[0]
    assert first_node.installed == {"access_ka": 64}
    assert first_node.lo0_ipv4 == "10.200.0.1/32"
    assert first_node.terr0_ipv4 == "172.20.0.1/24"

    site_with_gap = member.site.model_copy(
        update={"nodes": (*member.site.nodes, first_node.model_copy(update={"node_id": "gw3"}))}
    )
    ground = draft.workspace.ground[0]
    ground_with_gap = ground.model_copy(
        update={
            "members": (
                ground.members[0].model_copy(update={"site": site_with_gap}),
                *ground.members[1:],
            )
        }
    )
    draft = draft.model_copy(
        update={"workspace": draft.workspace.model_copy(update={"ground": (ground_with_gap,)})}
    )
    apply(
        {
            "operation": "add_ground_site_node",
            "segment_id": "ground-1",
            "member_id": "member-1",
        }
    )
    member = draft.workspace.ground[0].members[0]
    assert member.site is not None
    added = member.site.nodes[-1]
    assert added.node_id == "gw2"
    assert added.model_ref == "nodalarc:nodes/ground/starlink-gateway.yaml"
    assert added.installed == {"access_ka": 64}
    assert added.boresights == {"access_ka": BuilderVisualGroundBoresight(mode="local_vertical")}


def test_backend_visual_node_commands_derive_identity_shape_and_counts(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="node-command-owned"))

    def apply(command: dict[str, Any]) -> None:
        nonlocal draft
        draft = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        ).draft

    apply({"operation": "add_generated_space", "phasing_mode": "walker_delta"})
    apply({"operation": "author_inline_space_node", "segment_id": "space-1"})
    for _ in range(2):
        apply(
            {
                "operation": "add_or_increment_node_terminal",
                "segment_id": "space-1",
                "terminal_ref": ("nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml"),
                "role": "access",
            }
        )
    apply({"operation": "add_node_ethernet_port", "segment_id": "space-1"})
    apply({"operation": "add_node_ethernet_port", "segment_id": "space-1"})

    assert draft.workspace is not None
    node = draft.workspace.space[0].node_draft
    assert node is not None
    assert (node.id, node.display_name, node.forwarding) == (
        "space-1-node",
        "Constellation 1 node",
        None,
    )
    assert node.ethernet == ("terr0", "terr1")
    assert node.terminals == (
        BuilderVisualTerminalMount(
            mount_id="access_0",
            role="access",
            terminal_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
            count=2,
            boresight=BuilderVisualSpaceBoresight(mode="nadir"),
        ),
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BuilderVisualDraftCommandRequest.model_validate(
            {
                "draft": draft.model_dump(mode="json"),
                "expected_draft_revision": draft.draft_revision,
                "command": {
                    "operation": "add_node_ethernet_port",
                    "segment_id": "space-1",
                    "port_id": "browser-owned",
                },
            }
        )


def test_structured_labels_do_not_redefine_backend_issued_identifiers(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="stable-identities"))
    for command in (
        {"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        {"operation": "add_routing_domain"},
        {
            "operation": "connect_segments",
            "from_segment_id": "space-1",
            "to_segment_id": "space-1",
        },
    ):
        draft = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        ).draft

    assert draft.workspace is not None
    workspace = draft.workspace.model_copy(
        update={
            "links": (draft.workspace.links[0].model_copy(update={"label": "Human link label"}),),
            "routing_domains": (
                draft.workspace.routing_domains[0].model_copy(
                    update={"label": "Human domain label"}
                ),
            ),
        }
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft.model_copy(update={"workspace": workspace})),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert compiled.assembly_issues == ()
    session = compiled.assembled_draft.state.session
    assert [rule["id"] for rule in session["link_rules"]] == ["link-1"]
    assert [domain["id"] for domain in session["routing"]["domains"]] == ["domain-1"]


def test_backend_visual_command_revision_and_mode_refusals_are_typed(
    service: BuilderVisualDraftService,
) -> None:
    structured = service.create(BuilderVisualDraftCreateRequest(session_name="fenced"))
    with pytest.raises(BuilderVisualDraftCommandError) as stale:
        service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=structured,
                expected_draft_revision=7,
                command={"operation": "add_routing_domain"},
            ),
            available_node_count=100,
            preview_factory=_preview,
        )
    assert stale.value.code == "catalog_authoring.stale_revision"
    assert stale.value.expected_revision == 7
    assert stale.value.current_revision == 0
    assert structured.draft_revision == 0

    opaque = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    with pytest.raises(BuilderVisualDraftCommandError) as wrong_mode:
        service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=opaque,
                expected_draft_revision=0,
                command={"operation": "add_ground"},
            ),
            available_node_count=100,
            preview_factory=_preview,
        )
    assert wrong_mode.value.code == "catalog_authoring.invalid_patch"


def test_visual_phasing_and_space_access_boresight_are_explicit_application_state() -> None:
    with pytest.raises(ValidationError, match="exactly one orbital plane"):
        BuilderVisualSpaceDraft(
            segment_id="invalid",
            orbit=_orbit(),
            planes=3,
            raan_spacing_deg=120,
            slots_per_plane=2,
            phasing_mode="evenly_spaced_mean_anomaly",
            phase_offset_deg=0,
        )

    with pytest.raises(ValidationError, match="explicit nadir boresight"):
        BuilderVisualTerminalMount(
            mount_id="access",
            role="access",
            terminal_ref="nodalarc:terminals/rf/rf-ka-leo-access.yaml",
            count=1,
        )

    mount = BuilderVisualTerminalMount(
        mount_id="access",
        role="access",
        terminal_ref="nodalarc:terminals/rf/rf-ka-leo-access.yaml",
        count=1,
        boresight=BuilderVisualSpaceBoresight(mode="nadir"),
    )
    assert mount.boresight == BuilderVisualSpaceBoresight(mode="nadir")


def test_evenly_spaced_command_seeds_one_plane_and_compiles(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="single-plane"))
    result = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=draft,
            expected_draft_revision=0,
            command={
                "operation": "add_generated_space",
                "phasing_mode": "evenly_spaced_mean_anomaly",
            },
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert result.draft.workspace is not None
    space = result.draft.workspace.space[0]
    assert (space.planes, space.phasing_mode, space.phase_offset_deg) == (
        1,
        "evenly_spaced_mean_anomaly",
        0,
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=result.draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert compiled.assembly_issues == ()
    assert compiled.compile_result.save_verdict.allowed is True
    assert compiled.compile_result.resolved_preview is not None


def test_connect_command_uses_backend_resolved_terminal_facts(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="resolved-connect"))
    for _ in range(2):
        result = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command={
                    "operation": "add_generated_space",
                    "phasing_mode": "walker_delta",
                },
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        )
        draft = result.draft

    def resolved_preview(raw: dict[str, Any], _roots: object) -> BuilderWorld:
        base = builder_world_preview(raw["session"]["name"])
        nodes = tuple(
            BuilderWorldNode(
                node_id=f"{segment_id}-node",
                local_node_id="node",
                segment_id=segment_id,
                kind="satellite",
                terminal_inventory=(
                    ResolvedTerminalBlock(
                        terminal_id=f"{segment_id}-crosslink",
                        owner_node_id=f"{segment_id}-node",
                        endpoint_role="crosslink",
                        medium="rf",
                        count=1,
                        source_ref="test:resolved-terminal-facts",
                    ),
                ),
            )
            for segment_id in ("space-1", "space-2")
        )
        return base.model_copy(update={"nodes": nodes})

    connected = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            command={
                "operation": "connect_segments",
                "from_segment_id": "space-1",
                "to_segment_id": "space-2",
            },
        ),
        available_node_count=1_000_000,
        preview_factory=resolved_preview,
    )

    assert connected.draft.workspace is not None
    rule = connected.draft.workspace.links[0]
    assert (rule.a.role, rule.a.medium, rule.topology_mode, rule.topology_n) == (
        "crosslink",
        "rf",
        "nearest_n",
        1,
    )


def test_structured_assembly_creates_ref_composed_component_proposals(
    service: BuilderVisualDraftService,
) -> None:
    workspace = BuilderVisualWorkspace(
        session_name="visual-components",
        start_time="2026-07-10T00:00:00Z",
        space=(
            BuilderVisualSpaceDraft(
                segment_id="space",
                display_name="Authored shell",
                node_draft=BuilderVisualNode(
                    id="authored-node",
                    display_name="Authored node",
                    forwarding="routed",
                    ethernet=(),
                    terminals=(
                        BuilderVisualTerminalMount(
                            mount_id="access",
                            role="access",
                            terminal_ref="nodalarc:terminals/rf/rf-ka-leo-access.yaml",
                            count=1,
                            boresight=BuilderVisualSpaceBoresight(mode="nadir"),
                        ),
                    ),
                ),
                orbit=_orbit(),
                planes=1,
                raan_spacing_deg=360,
                slots_per_plane=1,
                phasing_mode="evenly_spaced_mean_anomaly",
                phase_offset_deg=0,
            ),
        ),
    )
    draft = BuilderVisualDraftEnvelope(
        draft_revision=4,
        mode="structured",
        target_ref="user:sessions/visual-components.yaml",
        workspace=workspace,
    )

    result = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=100,
        preview_factory=_preview,
    )

    assert result.compile_result.save_verdict.allowed is True
    assert result.assembly_issues == ()
    session = result.assembled_draft.state.session
    assert session["segments"][0]["source"] == (
        "user:constellations/visual-components/visual-components-space.yaml"
    )
    proposals = {
        str(proposal.ref): proposal.document
        for proposal in result.assembled_draft.state.catalog_documents
    }
    assert set(proposals) == {
        "user:nodes/visual-components/authored-node.yaml",
        "user:orbits/visual-components/space-orbit.yaml",
        "user:constellations/visual-components/visual-components-space.yaml",
    }
    constellation = proposals["user:constellations/visual-components/visual-components-space.yaml"][
        "constellation"
    ]
    assert constellation["node"] == "user:nodes/visual-components/authored-node.yaml"
    assert constellation["orbit"] == "user:orbits/visual-components/space-orbit.yaml"
    authored_node = proposals["user:nodes/visual-components/authored-node.yaml"]["node"]
    assert authored_node["terminals"][0]["boresight"] == {"mode": "nadir"}
    assert result.save_request.draft == result.assembled_draft


def test_structured_ground_site_emits_explicit_installation_boresight(
    service: BuilderVisualDraftService,
) -> None:
    boresight = BuilderVisualGroundBoresight(mode="local_vertical")
    workspace = BuilderVisualWorkspace(
        session_name="visual-ground",
        start_time="2026-07-10T00:00:00Z",
        ground=(
            BuilderVisualGroundDraft(
                segment_id="ground",
                display_name="Authored ground",
                members=(
                    BuilderVisualGroundMember(
                        member_id="member-1",
                        kind="draft",
                        site_id="authored-site",
                        label="Authored site",
                        site=BuilderVisualSite(
                            site_id="authored-site",
                            display_name="Authored site",
                            body="nodalarc:bodies/earth.yaml",
                            lat_deg=39.7,
                            lon_deg=-104.9,
                            alt_m=1600,
                            lan_ipv4="172.20.1.0/24",
                            nodes=(
                                BuilderVisualSiteNode(
                                    node_id="gw1",
                                    model_ref="nodalarc:nodes/ground/leo-gateway.yaml",
                                    installed={"access_ka": 1},
                                    boresights={"access_ka": boresight},
                                    lo0_ipv4="10.200.0.1/32",
                                    terr0_ipv4="172.20.1.1/24",
                                ),
                            ),
                        ),
                    ),
                ),
                stamp=BuilderVisualGroundStamp(
                    node_ref="nodalarc:nodes/ground/leo-gateway.yaml",
                    installed={"access_ka": 1},
                    boresights={"access_ka": boresight},
                    body="nodalarc:bodies/earth.yaml",
                    lan_base="172.20",
                    loopback_base="10.200",
                ),
            ),
        ),
    )
    draft = BuilderVisualDraftEnvelope(
        draft_revision=1,
        mode="structured",
        target_ref="user:sessions/visual-ground.yaml",
        workspace=workspace,
    )

    result = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=100,
        preview_factory=_preview,
    )

    assert result.assembly_issues == ()
    assert result.compile_result.save_verdict.allowed is True
    proposals = {
        str(item.ref): item.document for item in result.save_request.draft.state.catalog_documents
    }
    installation = proposals["user:sites/visual-ground/authored-site.yaml"]["site"]["nodes"][0][
        "terminals"
    ]["access_ka"]
    assert installation == {
        "installed_count": 1,
        "capabilities": {"boresight": {"mode": "local_vertical"}},
    }


def test_incomplete_authored_content_is_reported_and_never_filtered(
    service: BuilderVisualDraftService,
) -> None:
    workspace = BuilderVisualWorkspace(
        session_name="incomplete-visual",
        start_time="2026-07-10T00:00:00Z",
        ground=(
            BuilderVisualGroundDraft(
                segment_id="empty-ground",
                display_name="Incomplete ground",
                stamp=BuilderVisualGroundStamp(boresights={}),
            ),
        ),
        links=(
            BuilderVisualLinkRule(
                rule_id="unfinished-rule",
                a=BuilderVisualLinkEndpoint(segment_id="empty-ground"),
                b=BuilderVisualLinkEndpoint(),
            ),
        ),
        routing_domains=(BuilderVisualRoutingDomain(domain_id="unfinished-domain"),),
        boundaries=(BuilderVisualRoutingBoundary(boundary_id="unfinished-boundary"),),
    )
    draft = BuilderVisualDraftEnvelope(
        draft_revision=1,
        mode="structured",
        target_ref="user:sessions/incomplete-visual.yaml",
        workspace=workspace,
    )

    result = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=100,
        preview_factory=_preview,
    )

    session = result.assembled_draft.state.session
    assert [segment["id"] for segment in session["segments"]] == ["empty-ground"]
    assert len(session["link_rules"]) == 1
    assert len(session["routing"]["domains"]) == 1
    assert len(session["routing"]["boundaries"]) == 1
    site_set = next(
        proposal
        for proposal in result.assembled_draft.state.catalog_documents
        if proposal.ref.family == "site-sets"
    )
    assert site_set.document["site_set"]["sites"] == []
    codes = {issue.code for issue in result.assembly_issues}
    assert "builder.draft.ground_members_required" in codes
    assert "builder.draft.link_endpoint_role_required" in codes
    assert "builder.draft.routing_members_required" in codes
    assert "builder.draft.routing_adapter_required" in codes
    assert result.compile_result.save_verdict.allowed is False
    assert all({"save", "deploy"}.issubset(issue.blocks) for issue in result.assembly_issues)


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_opaque_mode_edits_and_saves_every_shipped_session_without_reassembly_loss(
    session_path: Path,
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    source_ref = f"nodalarc:sessions/{session_path.name}"
    opened = service.open(BuilderVisualDraftOpenRequest(source_ref=source_ref))
    assert opened.mode == "opaque_yaml"
    assert opened.session_yaml == session_path.read_text(encoding="utf-8")
    assert opened.target_ref == f"user:sessions/{session_path.name}"

    edited_document = yaml.safe_load(opened.session_yaml)
    edited_document["session"]["description"] = (
        f"Edited losslessly through opaque mode: {session_path.stem}"
    )
    edited = opened.model_copy(
        update={"draft_revision": 1, "session_yaml": yaml.safe_dump(edited_document)}
    )
    assembled = service.compile(
        BuilderVisualDraftCompileRequest(draft=edited),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    expected = canonicalize_persisted_configuration(
        edited.target_ref,
        edited_document,
    )
    assert assembled.assembly_issues == ()
    assert assembled.compile_result.save_verdict.allowed is True
    assert assembled.compile_result.canonical_session_json == expected.canonical_json

    saved = save_builder_session(
        assembled.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert saved.session.ref == edited.target_ref
    assert saved.session.canonical_json == expected.canonical_json
    assert saved.session.canonical_json["session"]["description"].startswith("Edited losslessly")


def test_explicit_session_save_as_changes_only_identity_and_is_immediately_saveable(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    source_ref = "nodalarc:sessions/earth-leo-simple.yaml"
    target_ref = "user:sessions/renamed-earth-leo.yaml"
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref=source_ref, target_ref=target_ref)
    )
    expected = yaml.safe_load((SHIPPED_ROOT / "sessions/earth-leo-simple.yaml").read_bytes())
    source_canonical = canonicalize_persisted_configuration(opened.source_ref, expected)
    expected["session"]["name"] = "renamed-earth-leo"
    canonical = canonicalize_persisted_configuration(opened.target_ref, expected)
    source_canonical.canonical_json["session"]["name"] = "renamed-earth-leo"

    assert canonical.canonical_json == source_canonical.canonical_json
    assert yaml.safe_load(opened.session_yaml) == canonical.canonical_json
    assert opened.session_yaml == canonical.yaml_bytes.decode("utf-8")
    assembled = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert assembled.assembly_issues == ()
    assert assembled.compile_result.save_verdict.allowed is True
    assert assembled.compile_result.canonical_session_json == canonical.canonical_json

    saved = save_builder_session(
        assembled.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert saved.session.ref == target_ref
    assert saved.session.canonical_json == canonical.canonical_json


def test_new_and_default_customization_never_overwrite_user_session(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    source_ref = "nodalarc:sessions/earth-leo-simple.yaml"
    first = service.open(BuilderVisualDraftOpenRequest(source_ref=source_ref))
    assembled = service.compile(
        BuilderVisualDraftCompileRequest(draft=first),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    save_builder_session(
        assembled.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    with pytest.raises(CatalogConflictError, match="explicit new user"):
        service.open(BuilderVisualDraftOpenRequest(source_ref=source_ref))
    with pytest.raises(CatalogConflictError, match="open it to edit"):
        service.create(BuilderVisualDraftCreateRequest(session_name="earth-leo-simple"))


def test_opaque_mode_preserves_structurally_supported_specialized_fields(
    service: BuilderVisualDraftService,
) -> None:
    session = yaml.safe_load(
        (SHIPPED_ROOT / "sessions/earth-leo-simple.yaml").read_text(encoding="utf-8")
    )
    session["session"]["name"] = "opaque-specialized"
    session["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    draft = BuilderVisualDraftEnvelope(
        draft_revision=0,
        mode="opaque_yaml",
        target_ref="user:sessions/opaque-specialized.yaml",
        session_yaml=yaml.safe_dump(session),
    )

    result = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert result.compile_result.save_verdict.allowed is True
    assert result.compile_result.deploy_eligibility_after_save.allowed is False
    assert result.compile_result.canonical_session_json["segments"][0]["clock"] == {
        "model": "affine",
        "rate": 2.0,
    }
    assert any(issue.stage == "runtime_support" for issue in result.compile_result.issues)


def test_nested_customization_forks_only_the_minimal_ancestor_chain(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    original = yaml.safe_load(opened.session_yaml)

    customized = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=opened,
            segment_id="leo",
            leaf_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        )
    )

    assert customized.applied is True
    assert [str(entry.source_ref) for entry in customized.forked_chain] == [
        "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
        "nodalarc:nodes/space/starlink-v2-mesh.yaml",
        "nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
    ]
    assert [entry.target_ref.family for entry in customized.forked_chain] == [
        "constellations",
        "nodes",
        "terminals",
    ]
    assert all(entry.target_ref.namespace == "user" for entry in customized.forked_chain)
    assert all(entry.source_ref.family != "orbits" for entry in customized.forked_chain)

    updated_session = yaml.safe_load(customized.draft.session_yaml)
    assert [segment["id"] for segment in updated_session["segments"]] == [
        segment["id"] for segment in original["segments"]
    ]
    assert [rule["id"] for rule in updated_session["link_rules"]] == [
        rule["id"] for rule in original["link_rules"]
    ]
    assert updated_session["segments"][0]["source"] == str(customized.root_target_ref)

    proposals = {
        proposal.ref.family: proposal.document for proposal in customized.draft.catalog_documents
    }
    target_by_family = {
        entry.target_ref.family: str(entry.target_ref) for entry in customized.forked_chain
    }
    assert proposals["constellations"]["constellation"]["node"] == target_by_family["nodes"]
    terminal_refs = [mount["terminal"] for mount in proposals["nodes"]["node"]["terminals"]]
    assert target_by_family["terminals"] in terminal_refs
    assert "nodalarc:terminals/optical/optical-starlink-space-isl.yaml" in terminal_refs

    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=customized.draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert compiled.compile_result.save_verdict.allowed is True
    assert (
        compiled.compile_result.canonical_session_json["segments"][0]["source"]
        == (target_by_family["constellations"])
    )
    saved = save_builder_session(
        compiled.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    saved_refs = {str(entry.ref) for entry in saved.dependency_closure.entries}
    assert set(target_by_family.values()).issubset(saved_refs)


def test_invalid_opaque_yaml_returns_typed_issues_instead_of_transport_rejection(
    service: BuilderVisualDraftService,
) -> None:
    draft = BuilderVisualDraftEnvelope(
        draft_revision=0,
        mode="opaque_yaml",
        target_ref="user:sessions/broken-yaml.yaml",
        session_yaml="session: [unterminated",
    )

    result = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1,
        preview_factory=_preview,
    )

    assert result.compile_result.save_verdict.allowed is False
    issue = next(
        issue
        for issue in result.assembly_issues
        if issue.code == "builder.draft.opaque_yaml_invalid"
    )
    assert issue.draft_path == "session_yaml"
    assert issue.blocks == ("save", "deploy")
