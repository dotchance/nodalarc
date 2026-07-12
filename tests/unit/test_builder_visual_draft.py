"""Backend-owned visual draft assembly and stateless projection contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogConflictError, CatalogNotFoundError, CatalogScope
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
    BuilderVisualDraftRetargetRequest,
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


def _incomplete_draft(
    target_ref: str,
    workspace: BuilderVisualWorkspace,
    *,
    draft_revision: int,
) -> BuilderVisualDraftEnvelope:
    return BuilderVisualDraftEnvelope(
        draft_revision=draft_revision,
        projection_status="incomplete_authoring",
        target_ref=target_ref,
        session_name_is_placeholder=False,
        reserved_authoring_ids=(),
        session_yaml=yaml.safe_dump(
            {
                "session": {"name": workspace.session_name},
                "segments": [],
                "time": {
                    "start_time": workspace.start_time,
                    "step_seconds": workspace.step_seconds,
                    "compression": workspace.compression,
                },
            },
            sort_keys=False,
        ),
        authoring_workspace=workspace,
    )


def _boundary_overlay_session() -> dict[str, Any]:
    segment_sources = {
        "polar": "nodalarc:constellations/earth/leo/earth-leo-polar-36.yaml",
        "meo": "nodalarc:constellations/earth/meo/earth-meo-gps-24.yaml",
        "heo": "nodalarc:constellations/earth/heo/earth-heo-molniya-3.yaml",
        "geo": "nodalarc:constellations/earth/geo/earth-geo-ring-8.yaml",
    }

    def crosslink(rule_id: str, left: str, right: str) -> dict[str, Any]:
        terminal = {"all": [{"role": "crosslink"}, {"medium": "optical"}]}
        return {
            "id": rule_id,
            "topology": {"mode": "nearest_n", "n": 1},
            "endpoints": [
                {"select": {"segment": left}, "terminal": terminal},
                {"select": {"segment": right}, "terminal": terminal},
            ],
        }

    def exchange(source: str, target: str, install_via: str | None = None) -> dict[str, Any]:
        export: dict[str, Any] = {
            "from": source,
            "to": target,
            "prefixes": {"aggregate_of": "originated"},
            "export_node_loopbacks": True,
        }
        if install_via is not None:
            export["install_via"] = install_via
        return export

    def boundary(
        over: str,
        source: str,
        target: str,
        install_via: str | None = None,
    ) -> dict[str, Any]:
        return {
            "over": over,
            "adapter": "static_ip",
            "export": [
                exchange(source, target, install_via),
                exchange(target, source, install_via),
            ],
        }

    return {
        "session": {"name": "boundary-overlay"},
        "segments": [
            {"id": segment_id, "source": source} for segment_id, source in segment_sources.items()
        ],
        "link_rules": [
            crosslink("polar_to_meo", "polar", "meo"),
            crosslink("meo_to_heo", "meo", "heo"),
            crosslink("heo_to_geo", "heo", "geo"),
        ],
        "addressing": {
            "loopbacks": [
                {
                    "id": "node_loopbacks",
                    "applies_to": {
                        "any": [{"segment": segment_id} for segment_id in segment_sources]
                    },
                    "ipv4_pool": "10.240.0.0/16",
                    "prefix_length": 32,
                    "allocation": "by_node_order",
                }
            ]
        },
        "routing": {
            "domains": [
                {
                    "id": f"{segment_id}_domain",
                    "protocol": "static",
                    "selectors": [{"segment": segment_id}],
                }
                for segment_id in segment_sources
            ],
            "boundaries": [
                boundary(
                    "polar_to_meo",
                    "polar_domain",
                    "meo_domain",
                    "peer_loopback",
                ),
                boundary("meo_to_heo", "meo_domain", "heo_domain"),
                boundary("heo_to_geo", "heo_domain", "geo_domain"),
            ],
        },
        "simulation": {"candidate_limits": {"max_pairs_per_rule": 100, "max_pairs_per_tick": 300}},
        "time": {
            "start_time": "2026-07-10T00:00:00Z",
            "step_seconds": 1,
            "compression": 1,
        },
    }


def test_backend_creates_an_incomplete_authoring_visual_draft(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(
        BuilderVisualDraftCreateRequest(
            session_name="my_visual_session",
            display_name="My visual session",
        )
    )

    assert draft.contract_version == 2
    assert draft.projection_status == "incomplete_authoring"
    assert draft.target_ref == "user:sessions/my_visual_session.yaml"
    assert draft.authoring_workspace is not None
    assert draft.authoring_workspace.session_name == "my_visual_session"
    assert draft.authoring_workspace.display_name == "My visual session"
    assert draft.authoring_workspace.start_time == "2026-07-10T12:34:00Z"
    assert draft.authoring_workspace.projection_revision is None
    assert draft.applied_workspace is None
    assert draft.applied_revision is None
    assert draft.applied_session is None
    assert draft.session_name_is_placeholder is False
    assert yaml.safe_load(draft.session_yaml)["session"]["name"] == "my_visual_session"

    reposted = BuilderVisualDraftEnvelope.model_validate_json(draft.model_dump_json())
    assert reposted == draft


@pytest.mark.parametrize("session_name", ["", "My Visual Session", "_leading"])
def test_explicit_visual_draft_names_must_be_canonical_identifiers(
    session_name: str,
) -> None:
    with pytest.raises(ValidationError):
        BuilderVisualDraftCreateRequest(session_name=session_name)


def test_backend_generates_unique_names_for_unnamed_visual_drafts(
    service: BuilderVisualDraftService,
) -> None:
    first = service.create(BuilderVisualDraftCreateRequest())
    second = service.create(BuilderVisualDraftCreateRequest())

    assert first.target_ref != second.target_ref
    for draft in (first, second):
        assert draft.authoring_workspace is not None
        name = draft.target_ref.relative_path.stem
        assert name.startswith("untitled-session-")
        assert len(name.removeprefix("untitled-session-")) == 12
        assert draft.authoring_workspace.session_name == name
        assert draft.session_name_is_placeholder is True


def test_backend_places_catalog_references_and_derives_catalog_identity(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="catalog-placement"))

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
        draft = result.draft
        return result

    apply(
        {
            "operation": "place_space_reference",
            "source_ref": "nodalarc:constellations/luna/elfo/luna-elfo-relay-2.yaml",
        }
    )
    apply(
        {
            "operation": "place_space_reference",
            "source_ref": "nodalarc:space-node-sets/earth/geo/earth-geo-tdrs-6.yaml",
        }
    )
    ground_ref_result = apply(
        {
            "operation": "place_ground_reference",
            "site_set_ref": "nodalarc:site-sets/luna/luna-surface-sites.yaml",
        }
    )
    authored_ground_result = apply({"operation": "add_ground"})
    site_result = apply(
        {
            "operation": "add_ground_site_reference",
            "segment_id": authored_ground_result.affected_id,
            "site_ref": "nodalarc:sites/luna/luna-artemis-base.yaml",
        }
    )

    assert draft.authoring_workspace is not None
    assert [placed.model_dump(mode="json") for placed in draft.authoring_workspace.space_refs] == [
        {
            "segment_id": "space-1",
            "source_ref": "nodalarc:constellations/luna/elfo/luna-elfo-relay-2.yaml",
            "label": "Two-node lunar ELFO relay constellation",
        },
        {
            "segment_id": "space-2",
            "source_ref": "nodalarc:space-node-sets/earth/geo/earth-geo-tdrs-6.yaml",
            "label": "earth-geo-tdrs-6",
        },
    ]
    ground_ref = draft.authoring_workspace.ground_refs[0]
    assert (ground_ref_result.affected_id, ground_ref.segment_id, ground_ref.label) == (
        "ground-1",
        "ground-1",
        "Luna surface sites",
    )
    assert ground_ref.scheduling["handover_mode"] == "mbb"
    assert ground_ref_result.scheduling_preset == "leo-fast-handover"
    assert authored_ground_result.affected_id == "ground-2"
    assert site_result.affected_kind == "ground_member"
    assert site_result.affected_id == "member-1"
    member = draft.authoring_workspace.ground[0].members[0]
    assert (member.member_id, member.site_id, member.label) == (
        "member-1",
        "luna-artemis-base",
        "Artemis lunar surface base",
    )
    assert member.ref == "nodalarc:sites/luna/luna-artemis-base.yaml"
    assert member.summary == (
        "Lunar south-pole surface site for cislunar reachability experiments."
    )

    with pytest.raises(BuilderVisualDraftCommandError, match="already placed") as duplicate:
        apply(
            {
                "operation": "add_ground_site_reference",
                "segment_id": "ground-2",
                "site_ref": "nodalarc:sites/luna/luna-artemis-base.yaml",
            }
        )
    assert duplicate.value.code == "catalog_authoring.invalid_graph"

    with pytest.raises(BuilderVisualDraftCommandError, match="invalid") as missing:
        apply(
            {
                "operation": "place_space_reference",
                "source_ref": "user:constellations/missing.yaml",
            }
        )
    assert missing.value.code == "catalog_authoring.invalid_graph"

    with pytest.raises(ValidationError, match="constellations or space-node-sets"):
        BuilderVisualDraftCommandRequest.model_validate(
            {
                "draft": draft.model_dump(mode="json"),
                "expected_draft_revision": draft.draft_revision,
                "command": {
                    "operation": "place_space_reference",
                    "source_ref": "nodalarc:site-sets/luna/luna-surface-sites.yaml",
                },
            }
        )


def test_backend_segment_creation_never_reuses_ids_held_by_dangling_intent(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="reserved-identities"))

    def apply(command: dict[str, Any], *, source: BuilderVisualDraftEnvelope | None = None):
        nonlocal draft
        active = source or draft
        result = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=active,
                expected_draft_revision=active.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        )
        if source is None:
            draft = result.draft
        return result

    assert (
        apply({"operation": "add_generated_space", "phasing_mode": "walker_delta"}).affected_id
        == "space-1"
    )
    assert apply({"operation": "add_ground"}).affected_id == "ground-1"
    assert draft.authoring_workspace is not None
    stripped = draft.model_copy(
        update={
            "authoring_workspace": draft.authoring_workspace.model_copy(
                update={
                    "space": (),
                    "ground": (),
                    "links": (
                        BuilderVisualLinkRule(
                            rule_id="dangling-space-link",
                            label="Dangling space link",
                            a=BuilderVisualLinkEndpoint(
                                segment_id="space-1",
                                role="isl",
                                medium="optical",
                            ),
                            b=BuilderVisualLinkEndpoint(
                                segment_id="space-1",
                                role="isl",
                                medium="optical",
                            ),
                            topology_mode="nearest_n",
                            topology_n=1,
                        ),
                    ),
                    "routing_domains": (
                        BuilderVisualRoutingDomain(
                            domain_id="dangling-ground-domain",
                            label="Dangling ground domain",
                            protocol="isis",
                            member_segment_ids=("ground-1",),
                        ),
                    ),
                }
            )
        }
    )

    commands = (
        (
            {
                "operation": "place_space_reference",
                "source_ref": "nodalarc:constellations/luna/elfo/luna-elfo-relay-2.yaml",
            },
            "space-2",
        ),
        (
            {"operation": "add_generated_space", "phasing_mode": "walker_delta"},
            "space-2",
        ),
        (
            {
                "operation": "place_ground_reference",
                "site_set_ref": "nodalarc:site-sets/luna/luna-surface-sites.yaml",
            },
            "ground-2",
        ),
        ({"operation": "add_ground"}, "ground-2"),
    )
    for command, expected_id in commands:
        assert apply(command, source=stripped).affected_id == expected_id


def test_backend_authoring_allocation_history_survives_local_segment_deletion(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="allocation-history"))

    def apply(command: dict[str, Any], *, source: BuilderVisualDraftEnvelope | None = None):
        nonlocal draft
        active = source or draft
        result = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=active,
                expected_draft_revision=active.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        )
        if source is None:
            draft = result.draft
        return result

    apply({"operation": "add_generated_space", "phasing_mode": "walker_delta"})
    apply({"operation": "add_ground"})
    assert draft.reserved_authoring_ids == ("space-1", "ground-1")
    assert draft.authoring_workspace is not None
    locally_deleted = draft.model_copy(
        update={
            "authoring_workspace": draft.authoring_workspace.model_copy(
                update={"space": (), "ground": (), "links": (), "routing_domains": ()}
            )
        }
    )

    commands = (
        (
            {
                "operation": "place_space_reference",
                "source_ref": "nodalarc:constellations/luna/elfo/luna-elfo-relay-2.yaml",
            },
            "space-2",
        ),
        (
            {"operation": "add_generated_space", "phasing_mode": "walker_delta"},
            "space-2",
        ),
        (
            {
                "operation": "place_ground_reference",
                "site_set_ref": "nodalarc:site-sets/luna/luna-surface-sites.yaml",
            },
            "ground-2",
        ),
        ({"operation": "add_ground"}, "ground-2"),
    )
    for command, expected_id in commands:
        result = apply(command, source=locally_deleted)
        assert result.affected_id == expected_id
        assert result.draft.reserved_authoring_ids == (
            "space-1",
            "ground-1",
            expected_id,
        )


def test_backend_never_reuses_topology_ids_or_repairs_dangling_boundaries(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="topology-history"))

    def apply(command: dict[str, Any], *, source: BuilderVisualDraftEnvelope | None = None):
        nonlocal draft
        active = source or draft
        result = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=active,
                expected_draft_revision=active.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        )
        draft = result.draft
        return result

    apply({"operation": "add_generated_space", "phasing_mode": "walker_delta"})
    apply({"operation": "add_ground"})
    apply({"operation": "add_routing_domain"})
    apply(
        {
            "operation": "connect_segments",
            "from_segment_id": "space-1",
            "to_segment_id": "ground-1",
        }
    )
    apply({"operation": "add_boundary"})
    assert draft.reserved_authoring_ids == (
        "space-1",
        "ground-1",
        "domain-1",
        "link-1",
        "boundary-1",
    )
    assert draft.authoring_workspace is not None

    deleted_topology = draft.model_copy(
        update={
            "reserved_authoring_ids": ("space-1", "ground-1", "boundary-1"),
            "authoring_workspace": draft.authoring_workspace.model_copy(
                update={"links": (), "routing_domains": ()}
            ),
        }
    )
    link_result = apply(
        {
            "operation": "connect_segments",
            "from_segment_id": "space-1",
            "to_segment_id": "ground-1",
        },
        source=deleted_topology,
    )
    assert link_result.affected_id == "link-2"
    assert "link-1" in link_result.draft.reserved_authoring_ids
    assert "domain-1" in link_result.draft.reserved_authoring_ids
    domain_result = apply({"operation": "add_routing_domain"})
    assert domain_result.affected_id == "domain-2"
    assert draft.authoring_workspace is not None
    dangling_boundary = draft.authoring_workspace.boundaries[0]
    assert dangling_boundary.over_rule_id == "link-1"
    assert dangling_boundary.from_domain_id == "domain-1"
    assert dangling_boundary.to_domain_id == "domain-1"

    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert compiled.compile_result.save_verdict.allowed is False

    without_boundaries = draft.model_copy(
        update={
            "authoring_workspace": draft.authoring_workspace.model_copy(update={"boundaries": ()})
        }
    )
    boundary_result = apply({"operation": "add_boundary"}, source=without_boundaries)
    assert boundary_result.affected_id == "boundary-2"


def test_backend_atomically_creates_a_ground_for_a_site_reference(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="atomic-site"))
    result = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            command={
                "operation": "add_ground_site_reference",
                "site_ref": "nodalarc:sites/luna/luna-artemis-base.yaml",
            },
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert result.affected_kind == "ground_member"
    assert result.affected_id == "member-1"
    assert result.draft.reserved_authoring_ids == ("ground-1",)
    assert result.scheduling_preset == "leo-fast-handover"
    assert result.draft.authoring_workspace is not None
    assert len(result.draft.authoring_workspace.ground) == 1
    ground = result.draft.authoring_workspace.ground[0]
    assert ground.segment_id == "ground-1"
    assert ground.display_name == "Ground segment 1"
    assert ground.stamp.body == "nodalarc:bodies/earth.yaml"
    assert ground.stamp.lan_base == "172.20"
    assert ground.stamp.loopback_base == "10.200"
    assert ground.scheduling["handover_mode"] == "mbb"
    assert ground.members[0].ref == "nodalarc:sites/luna/luna-artemis-base.yaml"

    refused = service.create(BuilderVisualDraftCreateRequest(session_name="atomic-site-refused"))
    with pytest.raises(BuilderVisualDraftCommandError, match="invalid"):
        service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=refused,
                expected_draft_revision=refused.draft_revision,
                command={
                    "operation": "add_ground_site_reference",
                    "site_ref": "user:sites/missing.yaml",
                },
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        )
    assert refused.authoring_workspace is not None
    assert refused.authoring_workspace.ground == ()


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
    assert draft.authoring_workspace is not None
    space = draft.authoring_workspace.space[0]
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
    ground = draft.authoring_workspace.ground[0]
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
    assert draft.authoring_workspace.routing_domains[0].member_segment_ids == (
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
    link = draft.authoring_workspace.links[0]
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
    boundary = draft.authoring_workspace.boundaries[0]
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
    assert draft.authoring_workspace.ground[0].scheduling["handover_mode"] == "bbm"

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
    link = draft.authoring_workspace.links[0]
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
    assert minted.draft.authoring_workspace is not None
    members = minted.draft.authoring_workspace.ground[0].members
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

    workspace = minted.draft.authoring_workspace
    ground = workspace.ground[0]
    without_first = ground.model_copy(update={"members": (ground.members[1],)})
    revised = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=minted.draft,
            expected_draft_revision=minted.draft.draft_revision,
            workspace=workspace.model_copy(update={"ground": (without_first,)}),
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).visual_draft
    third = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=revised,
            expected_draft_revision=revised.draft_revision,
            command={
                "operation": "mint_ground_members",
                "segment_id": "ground-1",
                "sites": [{"name": "Ames", "lat_deg": 42, "lon_deg": -93}],
            },
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert third.draft.authoring_workspace is not None
    ames = third.draft.authoring_workspace.ground[0].members[-1]
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
    assert draft.authoring_workspace is not None
    space = draft.authoring_workspace.space[0]
    assert (space.phasing_mode, space.planes, space.raan_spacing_deg, space.phase_offset_deg) == (
        "evenly_spaced_mean_anomaly",
        1,
        360.0,
        0.0,
    )

    apply({"operation": "set_space_population", "segment_id": "space-1", "planes": 4})
    space = draft.authoring_workspace.space[0]
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
    space = draft.authoring_workspace.space[0]
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
    ground = draft.authoring_workspace.ground[0]
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
    member = draft.authoring_workspace.ground[0].members[0]
    assert member.site is not None
    first_node = member.site.nodes[0]
    assert first_node.installed == {"access_ka": 64}
    assert first_node.lo0_ipv4 == "10.200.0.1/32"
    assert first_node.terr0_ipv4 == "172.20.0.1/24"

    site_with_gap = member.site.model_copy(
        update={"nodes": (*member.site.nodes, first_node.model_copy(update={"node_id": "gw3"}))}
    )
    ground = draft.authoring_workspace.ground[0]
    ground_with_gap = ground.model_copy(
        update={
            "members": (
                ground.members[0].model_copy(update={"site": site_with_gap}),
                *ground.members[1:],
            )
        }
    )
    draft = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            workspace=draft.authoring_workspace.model_copy(update={"ground": (ground_with_gap,)}),
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).visual_draft
    apply(
        {
            "operation": "add_ground_site_node",
            "segment_id": "ground-1",
            "member_id": "member-1",
        }
    )
    member = draft.authoring_workspace.ground[0].members[0]
    assert member.site is not None
    added = member.site.nodes[-1]
    assert added.node_id == "gw2"
    assert added.model_ref == "nodalarc:nodes/ground/starlink-gateway.yaml"
    assert added.installed == {"access_ka": 64}
    assert added.boresights == {"access_ka": BuilderVisualGroundBoresight(mode="local_vertical")}


def test_backend_repairs_nullable_space_population_one_field_at_a_time(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="population-repair"))
    created = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            command={"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).draft
    assert created.authoring_workspace is not None
    incomplete_space = created.authoring_workspace.space[0].model_copy(
        update={
            "planes": None,
            "slots_per_plane": None,
            "raan_spacing_deg": None,
            "phase_offset_deg": None,
        }
    )
    pending_revision = created.draft_revision + 1
    draft = created.model_copy(
        update={
            "draft_revision": pending_revision,
            "projection_status": "pending_authoring",
            "authoring_workspace": created.authoring_workspace.model_copy(
                update={"space": (incomplete_space,), "projection_revision": None}
            ),
        }
    )
    assert draft.projection_status == "pending_authoring"
    assert draft.applied_revision == created.applied_revision

    def apply(command: dict[str, Any]) -> BuilderVisualSpaceDraft:
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
        assert draft.authoring_workspace is not None
        return draft.authoring_workspace.space[0]

    space = apply({"operation": "set_space_population", "segment_id": "space-1", "planes": 4})
    assert (space.planes, space.slots_per_plane) == (4, None)
    assert (space.raan_spacing_deg, space.phase_offset_deg) == (None, None)

    space = apply(
        {
            "operation": "set_space_population",
            "segment_id": "space-1",
            "slots_per_plane": 10,
        }
    )
    assert (space.planes, space.slots_per_plane) == (4, 10)
    assert (space.raan_spacing_deg, space.phase_offset_deg) == (90.0, 9.0)

    reset = space.model_copy(
        update={
            "planes": None,
            "slots_per_plane": None,
            "raan_spacing_deg": None,
            "phase_offset_deg": None,
        }
    )
    assert draft.authoring_workspace is not None
    pending_revision = draft.draft_revision + 1
    draft = draft.model_copy(
        update={
            "draft_revision": pending_revision,
            "projection_status": "pending_authoring",
            "authoring_workspace": draft.authoring_workspace.model_copy(
                update={"space": (reset,), "projection_revision": None}
            ),
        }
    )
    assert draft.projection_status == "pending_authoring"
    space = apply(
        {
            "operation": "set_space_population",
            "segment_id": "space-1",
            "phasing_mode": "evenly_spaced_mean_anomaly",
        }
    )
    assert (space.phasing_mode, space.planes, space.slots_per_plane) == (
        "evenly_spaced_mean_anomaly",
        1,
        None,
    )
    assert (space.raan_spacing_deg, space.phase_offset_deg) == (None, None)

    space = apply(
        {
            "operation": "set_space_population",
            "segment_id": "space-1",
            "slots_per_plane": 8,
        }
    )
    assert (space.raan_spacing_deg, space.phase_offset_deg) == (360.0, 0.0)


def test_backend_reports_direct_missing_phase_edit_as_pending_authoring(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="missing-phase"))
    draft = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            command={"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).draft
    assert draft.authoring_workspace is not None
    space = draft.authoring_workspace.space[0].model_copy(update={"phase_offset_deg": None})
    pending_revision = draft.draft_revision + 1
    draft = draft.model_copy(
        update={
            "draft_revision": pending_revision,
            "projection_status": "pending_authoring",
            "authoring_workspace": draft.authoring_workspace.model_copy(
                update={"space": (space,), "projection_revision": None}
            ),
        }
    )
    assert draft.projection_status == "pending_authoring"
    assert draft.applied_revision is not None
    assert draft.applied_revision < draft.draft_revision

    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert [issue.code for issue in compiled.assembly_issues] == [
        "builder.draft.unapplied_workspace_changes"
    ]
    assert compiled.visual_draft.projection_status == "pending_authoring"
    assert compiled.compile_result.save_verdict.allowed is False


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

    assert draft.authoring_workspace is not None
    node = draft.authoring_workspace.space[0].node_draft
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

    apply(
        {
            "operation": "set_node_terminal_role",
            "segment_id": "space-1",
            "mount_id": "access_0",
            "role": "isl",
        }
    )
    node = draft.authoring_workspace.space[0].node_draft
    assert node is not None
    assert (node.terminals[0].role, node.terminals[0].boresight) == ("isl", None)

    apply(
        {
            "operation": "set_node_terminal_role",
            "segment_id": "space-1",
            "mount_id": "access_0",
            "role": "access",
        }
    )
    node = draft.authoring_workspace.space[0].node_draft
    assert node is not None
    assert node.terminals[0].role == "access"
    assert node.terminals[0].boresight == BuilderVisualSpaceBoresight(mode="nadir")

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


def test_pending_visual_labels_do_not_redefine_backend_issued_identifiers(
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

    assert draft.authoring_workspace is not None
    workspace = draft.authoring_workspace.model_copy(
        update={
            "links": (
                draft.authoring_workspace.links[0].model_copy(update={"label": "Human link label"}),
            ),
            "routing_domains": (
                draft.authoring_workspace.routing_domains[0].model_copy(
                    update={"label": "Human domain label"}
                ),
            ),
        }
    )
    pending = draft.model_copy(
        update={
            "draft_revision": draft.draft_revision + 1,
            "projection_status": "pending_authoring",
            "authoring_workspace": workspace.model_copy(update={"projection_revision": None}),
        }
    )
    assert pending.applied_revision == draft.applied_revision
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=pending),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert [issue.code for issue in compiled.assembly_issues] == [
        "builder.draft.unapplied_workspace_changes"
    ]
    assert compiled.compile_result.save_verdict.allowed is False
    session = compiled.assembled_draft.state.session
    assert [rule["id"] for rule in session["link_rules"]] == ["link-1"]
    assert [domain["id"] for domain in session["routing"]["domains"]] == ["domain-1"]


def test_backend_visual_command_revision_and_missing_projection_refusals_are_typed(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="fenced"))
    with pytest.raises(BuilderVisualDraftCommandError) as stale:
        service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=7,
                command={"operation": "add_routing_domain"},
            ),
            available_node_count=100,
            preview_factory=_preview,
        )
    assert stale.value.code == "catalog_authoring.stale_revision"
    assert stale.value.expected_revision == 7
    assert stale.value.current_revision == 0
    assert draft.draft_revision == 0

    no_valid_projection = BuilderVisualDraftEnvelope(
        draft_revision=0,
        projection_status="no_valid_projection",
        target_ref="user:sessions/no-valid-projection.yaml",
        session_name_is_placeholder=False,
        reserved_authoring_ids=(),
        session_yaml="session: [unterminated",
    )
    with pytest.raises(BuilderVisualDraftCommandError) as missing_projection:
        service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=no_valid_projection,
                expected_draft_revision=0,
                command={"operation": "add_ground"},
            ),
            available_node_count=100,
            preview_factory=_preview,
        )
    assert missing_projection.value.code == "catalog_authoring.invalid_patch"


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
    assert result.draft.authoring_workspace is not None
    space = result.draft.authoring_workspace.space[0]
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

    assert connected.draft.authoring_workspace is not None
    rule = connected.draft.authoring_workspace.links[0]
    assert (rule.a.role, rule.a.medium, rule.topology_mode, rule.topology_n) == (
        "crosslink",
        "rf",
        "nearest_n",
        1,
    )


def test_visual_authoring_assembly_creates_ref_composed_component_proposals(
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
    draft = _incomplete_draft(
        "user:sessions/visual-components.yaml",
        workspace,
        draft_revision=4,
    )
    assert draft.projection_status == "incomplete_authoring"

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


def test_visual_save_as_reallocates_generated_components_under_the_new_owner(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    original = service.create(BuilderVisualDraftCreateRequest(session_name="original-owner"))
    authored = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=original,
            expected_draft_revision=original.draft_revision,
            command={"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).draft
    retargeted = service.retarget(
        BuilderVisualDraftRetargetRequest(
            draft=authored,
            expected_draft_revision=authored.draft_revision,
            target_ref="user:sessions/renamed-owner.yaml",
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert retargeted.visual_draft.target_ref == "user:sessions/renamed-owner.yaml"
    assert retargeted.visual_draft.draft_revision == authored.draft_revision + 1
    assert retargeted.visual_draft.expected_session_revision is None
    assert authored.target_ref == "user:sessions/original-owner.yaml"
    assert retargeted.compile_result.save_verdict.allowed is True
    proposal_refs = {
        str(proposal.ref) for proposal in retargeted.assembled_draft.state.catalog_documents
    }
    assert proposal_refs
    assert all("/renamed-owner/" in ref for ref in proposal_refs)
    saved_renamed = save_builder_session(
        retargeted.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert saved_renamed.session.ref == "user:sessions/renamed-owner.yaml"


def test_visual_retarget_retains_reachable_customize_proposals_and_reowns_generated_ones(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    customized = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            segment_id="leo",
            leaf_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        )
    )
    assert customized.applied is True
    customized_refs = {str(proposal.ref) for proposal in customized.draft.catalog_documents}
    mixed = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=customized.draft,
            expected_draft_revision=customized.draft.draft_revision,
            command={"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).draft

    retargeted = service.retarget(
        BuilderVisualDraftRetargetRequest(
            draft=mixed,
            expected_draft_revision=mixed.draft_revision,
            target_ref="user:sessions/customized-copy.yaml",
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    proposal_refs = {str(proposal.ref) for proposal in retargeted.visual_draft.catalog_documents}
    assert customized_refs.issubset(proposal_refs)
    regenerated_refs = proposal_refs.difference(customized_refs)
    assert regenerated_refs
    assert all("/customized-copy/" in ref for ref in regenerated_refs)
    assert retargeted.compile_result.canonical_session_json["session"]["name"] == (
        "customized-copy"
    )
    snapshot = context.repository.snapshot(context.scope)
    with pytest.raises(CatalogNotFoundError):
        snapshot.get("user:sessions/customized-copy.yaml")
    for ref in proposal_refs:
        with pytest.raises(CatalogNotFoundError):
            snapshot.get(ref)


def test_reopened_saved_components_stay_refs_through_customize_edit_and_retarget(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    created = service.create(BuilderVisualDraftCreateRequest(session_name="origin-session"))
    authored = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=created,
            expected_draft_revision=created.draft_revision,
            command={
                "operation": "add_generated_space",
                "node_ref": "nodalarc:nodes/space/starlink-v2-mesh.yaml",
                "phasing_mode": "walker_delta",
            },
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).draft
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=authored),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert compiled.compile_result.save_verdict.allowed, compiled.compile_result.issues
    saved = save_builder_session(
        compiled.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    constellation_ref = next(
        entry.ref
        for entry in saved.dependency_closure.entries
        if entry.ref.namespace == "user" and entry.ref.family == "constellations"
    )
    before_enrichment = context.repository.snapshot(context.scope)
    stored_constellation = before_enrichment.get(constellation_ref)
    enriched_document = yaml.safe_load(stored_constellation.content)
    enriched_document["constellation"].update(
        {
            "tags": ["preserved-tag"],
            "reference": "urn:nodalarc:session-builder-draft",
            "notes": "Preserve fields owned by the catalog component editor.",
        }
    )
    enriched = canonicalize_persisted_configuration(constellation_ref, enriched_document)
    transaction = context.repository.begin(
        context.scope,
        base_generation=before_enrichment.generation,
    )
    transaction.write_bytes(
        constellation_ref,
        enriched.yaml_bytes,
        expected_revision=stored_constellation.revision,
    )
    transaction.commit()

    reopened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="user:sessions/origin-session.yaml")
    )
    assert reopened.catalog_documents == ()
    assert reopened.authoring_workspace is not None
    assert reopened.authoring_workspace.space == ()
    assert [str(item.source_ref) for item in reopened.authoring_workspace.space_refs] == [
        str(constellation_ref)
    ]

    customized = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=reopened,
            expected_draft_revision=reopened.draft_revision,
            segment_id="space-1",
            leaf_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        )
    )
    assert customized.applied is True
    assert customized.draft.catalog_documents
    assert {proposal.origin for proposal in customized.draft.catalog_documents} == {"customized"}
    assert customized.draft.authoring_workspace is not None
    assert customized.draft.authoring_workspace.space == ()
    assert customized.draft.authoring_workspace.space_refs

    edited_workspace = customized.draft.authoring_workspace.model_copy(
        update={"description": "Unrelated session edit after catalog customization"}
    )
    edited = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=customized.draft,
            expected_draft_revision=customized.draft.draft_revision,
            workspace=edited_workspace,
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    retargeted = service.retarget(
        BuilderVisualDraftRetargetRequest(
            draft=edited.visual_draft,
            expected_draft_revision=edited.visual_draft.draft_revision,
            target_ref="user:sessions/customized-copy.yaml",
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert {proposal.origin for proposal in retargeted.visual_draft.catalog_documents} == {
        "customized"
    }
    forked_root = next(
        proposal
        for proposal in retargeted.visual_draft.catalog_documents
        if proposal.ref == customized.root_target_ref
    )
    assert forked_root.document["constellation"]["tags"] == ["preserved-tag"]
    assert forked_root.document["constellation"]["reference"] == (
        "urn:nodalarc:session-builder-draft"
    )
    assert forked_root.document["constellation"]["notes"] == (
        "Preserve fields owned by the catalog component editor."
    )

    save_builder_session(
        retargeted.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    committed = context.repository.snapshot(context.scope)
    assert committed.get(constellation_ref).content == enriched.yaml_bytes
    saved_fork = yaml.safe_load(committed.get(customized.root_target_ref).content)
    assert saved_fork["constellation"]["tags"] == ["preserved-tag"]
    assert saved_fork["constellation"]["reference"] == ("urn:nodalarc:session-builder-draft")
    assert saved_fork["constellation"]["notes"] == (
        "Preserve fields owned by the catalog component editor."
    )


def test_visual_retarget_is_revision_fenced_and_create_only(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="retarget-source"))
    with pytest.raises(BuilderVisualDraftCommandError) as stale_draft:
        service.retarget(
            BuilderVisualDraftRetargetRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision + 1,
                target_ref="user:sessions/fresh-target.yaml",
            ),
            available_node_count=1_000_000,
        )
    assert stale_draft.value.code == "catalog_authoring.stale_revision"

    occupied = service.open(
        BuilderVisualDraftOpenRequest(
            source_ref="nodalarc:sessions/earth-leo-simple.yaml",
            target_ref="user:sessions/occupied-target.yaml",
        )
    )
    occupied_compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=occupied),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    occupied_saved = save_builder_session(
        occupied_compiled.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    with pytest.raises(BuilderVisualDraftCommandError) as occupied_target:
        service.retarget(
            BuilderVisualDraftRetargetRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                target_ref="user:sessions/occupied-target.yaml",
            ),
            available_node_count=1_000_000,
        )
    assert occupied_target.value.code == "catalog_authoring.conflict"
    assert occupied_target.value.current_revision == occupied_saved.session.revision

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BuilderVisualDraftRetargetRequest.model_validate(
            {
                "draft": draft.model_dump(mode="json"),
                "expected_draft_revision": draft.draft_revision,
                "target_ref": "user:sessions/occupied-target.yaml",
                "expected_target_revision": occupied_saved.session.revision,
            }
        )

    opened_occupied = service.open(
        BuilderVisualDraftOpenRequest(source_ref="user:sessions/occupied-target.yaml")
    )
    forged_fields = {
        **opened_occupied.model_dump(mode="json"),
        "source_ref": "nodalarc:sessions/earth-leo-simple.yaml",
    }
    with pytest.raises(ValidationError, match="cannot replace an existing session target"):
        BuilderVisualDraftEnvelope.model_validate(forged_fields)

    forged = opened_occupied.model_copy(
        update={"source_ref": "nodalarc:sessions/earth-leo-simple.yaml"}
    )
    before = context.repository.snapshot(context.scope).get("user:sessions/occupied-target.yaml")
    with pytest.raises(BuilderVisualDraftCommandError) as recovery_bypass:
        service.compile(
            BuilderVisualDraftCompileRequest.model_construct(draft=forged),
            available_node_count=1_000_000,
        )
    assert recovery_bypass.value.code == "catalog_authoring.conflict"
    assert (
        context.repository.snapshot(context.scope).get("user:sessions/occupied-target.yaml").content
        == before.content
    )


def test_visual_ground_site_emits_explicit_installation_boresight(
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
    draft = _incomplete_draft(
        "user:sessions/visual-ground.yaml",
        workspace,
        draft_revision=1,
    )
    assert draft.projection_status == "incomplete_authoring"

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
    draft = _incomplete_draft(
        "user:sessions/incomplete-visual.yaml",
        workspace,
        draft_revision=1,
    )
    assert draft.projection_status == "incomplete_authoring"

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


def test_graphical_boundary_edit_preserves_unprojected_and_sibling_boundaries(
    service: BuilderVisualDraftService,
) -> None:
    created = service.create(BuilderVisualDraftCreateRequest(session_name="boundary-overlay"))
    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=created,
            expected_draft_revision=created.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(_boundary_overlay_session(), sort_keys=False),
        )
    )

    assert applied.applied is True
    workspace = applied.draft.authoring_workspace
    assert workspace is not None
    assert [boundary.boundary_id for boundary in workspace.boundaries] == [
        "boundary-2",
        "boundary-3",
    ]
    edited_boundaries = tuple(
        boundary.model_copy(update={"export_node_loopbacks": False})
        if boundary.boundary_id == "boundary-2"
        else boundary
        for boundary in workspace.boundaries
    )
    revised = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=applied.draft,
            expected_draft_revision=applied.draft.draft_revision,
            workspace=workspace.model_copy(update={"boundaries": edited_boundaries}),
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert revised.assembly_issues == ()
    assert revised.compile_result.save_verdict.allowed is True, revised.compile_result.issues
    boundaries = revised.assembled_draft.state.session["routing"]["boundaries"]
    assert [(boundary["over"], boundary["adapter"]) for boundary in boundaries] == [
        ("polar_to_meo", "static_ip"),
        ("meo_to_heo", "static_ip"),
        ("heo_to_geo", "static_ip"),
    ]
    assert boundaries[0]["export"][0]["install_via"] == "peer_loopback"
    assert [export["export_node_loopbacks"] for export in boundaries[1]["export"]] == [
        False,
        False,
    ]
    assert [export["export_node_loopbacks"] for export in boundaries[2]["export"]] == [
        True,
        True,
    ]


@pytest.mark.parametrize("over_rule_id", ("meo_to_heo", "polar_to_meo"))
def test_graphical_boundary_edit_refuses_duplicate_boundary_identity(
    service: BuilderVisualDraftService,
    over_rule_id: str,
) -> None:
    created = service.create(BuilderVisualDraftCreateRequest(session_name="boundary-overlay"))
    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=created,
            expected_draft_revision=created.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(_boundary_overlay_session(), sort_keys=False),
        )
    )

    assert applied.applied is True
    workspace = applied.draft.authoring_workspace
    assert workspace is not None
    assert applied.draft.applied_session is not None
    original_boundaries = applied.draft.applied_session["routing"]["boundaries"]
    edited_boundaries = tuple(
        boundary.model_copy(update={"over_rule_id": over_rule_id})
        if boundary.boundary_id == "boundary-3"
        else boundary
        for boundary in workspace.boundaries
    )
    revised = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=applied.draft,
            expected_draft_revision=applied.draft.draft_revision,
            workspace=workspace.model_copy(update={"boundaries": edited_boundaries}),
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert [issue.code for issue in revised.assembly_issues] == [
        "builder.draft.routing_boundary_identity_ambiguous"
    ]
    assert revised.compile_result.save_verdict.allowed is False
    assert revised.assembled_draft.state.session["routing"]["boundaries"] == original_boundaries


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_applied_yaml_revision_saves_every_shipped_session_without_reassembly_loss(
    session_path: Path,
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    source_ref = f"nodalarc:sessions/{session_path.name}"
    opened = service.open(BuilderVisualDraftOpenRequest(source_ref=source_ref))
    assert opened.projection_status == "applied"
    assert opened.session_yaml == session_path.read_text(encoding="utf-8")
    assert opened.target_ref == f"user:sessions/{session_path.name}"
    assert opened.applied_revision == opened.draft_revision == 0
    assert opened.authoring_workspace == opened.applied_workspace

    edited_document = yaml.safe_load(opened.session_yaml)
    edited_document["session"]["description"] = (
        f"Edited through the stateless YAML projection: {session_path.stem}"
    )
    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(edited_document, sort_keys=False),
        )
    )
    assert applied.applied is True
    edited = applied.draft
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
    assert saved.session.canonical_json["session"]["description"].startswith("Edited through")


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

    with pytest.raises(CatalogConflictError, match="customization target already exists"):
        service.open(BuilderVisualDraftOpenRequest(source_ref=source_ref))
    with pytest.raises(CatalogConflictError, match="open it to edit"):
        service.create(BuilderVisualDraftCreateRequest(session_name="earth-leo-simple"))


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
            expected_draft_revision=opened.draft_revision,
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


def test_orphaned_customize_proposals_are_visible_but_not_fenced_or_saved(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    customized = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            segment_id="leo",
            leaf_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        )
    )
    assert customized.applied is True
    proposed_refs = tuple(str(item.ref) for item in customized.draft.catalog_documents)
    original_session = yaml.safe_load(opened.session_yaml)
    orphaned_session = yaml.safe_load(customized.draft.session_yaml)
    orphaned_session["segments"][0]["source"] = original_session["segments"][0]["source"]
    applied_orphan = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=customized.draft,
            expected_draft_revision=customized.draft.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(orphaned_session, sort_keys=False),
        )
    )
    assert applied_orphan.applied is True
    orphaned = applied_orphan.draft

    compiled_orphaned = service.compile(
        BuilderVisualDraftCompileRequest(draft=orphaned),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert orphaned.catalog_documents == ()
    assert compiled_orphaned.visual_draft.catalog_documents == ()
    assert compiled_orphaned.assembled_draft.state.catalog_documents == ()
    assert compiled_orphaned.save_request.draft == compiled_orphaned.assembled_draft
    assert compiled_orphaned.compile_result.draft == compiled_orphaned.assembled_draft
    assert compiled_orphaned.compile_result.save_verdict.allowed is True
    assert "builder.draft.unreferenced_catalog_documents" not in {
        issue.code for issue in compiled_orphaned.compile_result.issues
    }

    orphaned_save = save_builder_session(
        compiled_orphaned.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    after_orphaned_save = context.repository.snapshot(context.scope)
    for ref in proposed_refs:
        with pytest.raises(CatalogNotFoundError):
            after_orphaned_save.get(ref)

    rereference_base = customized.draft.model_copy(
        update={
            "source_ref": orphaned_save.session.ref,
            "expected_session_revision": orphaned_save.session.revision,
        }
    )
    rereferenced_result = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=rereference_base,
            expected_draft_revision=rereference_base.draft_revision,
            buffer_generation=2,
            yaml_text=customized.draft.session_yaml,
        )
    )
    assert rereferenced_result.applied is True
    rereferenced = rereferenced_result.draft
    compiled_referenced = service.compile(
        BuilderVisualDraftCompileRequest(draft=rereferenced),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert compiled_referenced.compile_result.save_verdict.allowed is True
    assert {
        str(item.ref) for item in compiled_referenced.assembled_draft.state.catalog_documents
    } == set(proposed_refs)

    referenced_save = save_builder_session(
        compiled_referenced.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    committed = context.repository.snapshot(context.scope)
    assert {str(entry.ref) for entry in referenced_save.dependency_closure.entries}.issuperset(
        proposed_refs
    )
    for ref in proposed_refs:
        assert committed.get(ref).family == CatalogRef(ref).family


def test_no_valid_projection_is_typed_and_never_parsed_as_a_save_candidate(
    service: BuilderVisualDraftService,
) -> None:
    draft = BuilderVisualDraftEnvelope(
        draft_revision=0,
        projection_status="no_valid_projection",
        target_ref="user:sessions/broken-yaml.yaml",
        session_name_is_placeholder=False,
        reserved_authoring_ids=(),
        session_yaml="session: [unterminated",
    )

    result = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1,
        preview_factory=_preview,
    )

    assert result.compile_result.save_verdict.allowed is False
    assert result.compile_result.deploy_eligibility_after_save.allowed is False
    assert result.assembled_draft.state.session == {}
    issue = next(
        issue
        for issue in result.assembly_issues
        if issue.code == "builder.draft.no_valid_projection"
    )
    assert issue.draft_path == "session_yaml"
    assert issue.blocks == ("save", "deploy")
