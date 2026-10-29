"""Backend-owned visual draft assembly and stateless projection contracts."""

from __future__ import annotations

import json
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogConflictError, CatalogNotFoundError, CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import BuilderIssue
from nodalarc.models.builder_visual_api import (
    BuilderVisualCustomizeChainRequest,
    BuilderVisualDraftAcceptStateRequest,
    BuilderVisualDraftApplyEditsRequest,
    BuilderVisualDraftApplyEditsResult,
    BuilderVisualDraftApplyWorkspaceRequest,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCommandResult,
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
    BuilderVisualObjectEditRequest,
    BuilderVisualObjectEditResult,
    BuilderVisualOrbit,
    BuilderVisualRoutingBoundariesWidget,
    BuilderVisualRoutingBoundary,
    BuilderVisualRoutingDomain,
    BuilderVisualSite,
    BuilderVisualSiteNode,
    BuilderVisualSpaceBoresight,
    BuilderVisualSpaceDraft,
    BuilderVisualTerminalMount,
    BuilderVisualWorkspace,
)
from nodalarc.models.builder_world import (
    BuilderSegmentCapability,
    BuilderWorld,
    BuilderWorldSegment,
)
from nodalarc.models.link_rules import LinkRule
from nodalarc.models.segment_session import RoutingBoundary, RoutingDomain
from nodalarc.resolve_session import SessionResolution
from pydantic import BaseModel, TypeAdapter, ValidationError
from vs_api.builder_compiler import PreviewFactory, canonicalize_persisted_configuration
from vs_api.builder_session_service import save_builder_session
from vs_api.builder_visual_draft import (
    BuilderVisualDraftCommandError,
    BuilderVisualDraftService,
    _link_rule_document,
    _routing_boundary_documents,
    _routing_domain_document,
)
from vs_api.builder_world import world_from_resolution
from vs_api.catalog_context import CatalogContext

from tests.builder_world_fixtures import builder_world_preview

ROOT = Path(__file__).resolve().parents[3]
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


def _preview(resolution: SessionResolution) -> BuilderWorld:
    return builder_world_preview(resolution.resolved.session.name)


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

    assert draft.projection_status == "incomplete_authoring"
    assert draft.target_ref == "user:sessions/my_visual_session.yaml"
    assert draft.authoring_workspace is not None
    assert draft.authoring_workspace.session_name == "my_visual_session"
    assert draft.authoring_workspace.display_name == "My visual session"
    assert draft.authoring_workspace.start_time == "2026-07-10T12:34:00Z"
    assert draft.control_tree is None
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


def _command(
    service: BuilderVisualDraftService,
    draft: BuilderVisualDraftEnvelope,
    command: dict[str, Any],
    *,
    pending_edits: tuple[dict[str, Any], ...] = (),
) -> BuilderVisualDraftCommandResult:
    """One command sent as the page sends it: JSON validated into the request."""
    body = {
        "draft": json.loads(draft.model_dump_json()),
        "expected_draft_revision": draft.draft_revision,
        "command": command,
        "pending_edits": list(pending_edits),
    }
    return service.apply_command(
        BuilderVisualDraftCommandRequest.model_validate_json(json.dumps(body)),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )


def _relay_session(name: str) -> dict[str, Any]:
    return {
        "session": {"name": name},
        "segments": [
            {
                "id": "relay",
                "source": "nodalarc:constellations/earth/leo/earth-leo-polar-36.yaml",
            }
        ],
        "time": {"start_time": "2026-07-10T00:00:00Z", "step_seconds": 1, "compression": 1},
    }


def _applied_yaml(
    service: BuilderVisualDraftService,
    session: dict[str, Any],
) -> BuilderVisualDraftEnvelope:
    """A new draft whose accepted session is ``session``, applied from the YAML pane."""
    created = service.create(
        BuilderVisualDraftCreateRequest(session_name=session["session"]["name"])
    )
    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=created,
            expected_draft_revision=created.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(session, sort_keys=False),
        )
    )
    assert applied.applied is True
    return applied.draft


_ID_GESTURES: tuple[dict[str, Any], ...] = (
    {
        "operation": "place_space_reference",
        "source_ref": "nodalarc:constellations/luna/elfo/luna-elfo-relay-2.yaml",
    },
    {"operation": "add_generated_space", "phasing_mode": "walker_delta"},
    {
        "operation": "place_ground_reference",
        "site_set_ref": "nodalarc:site-sets/luna/luna-surface-sites.yaml",
    },
    {"operation": "add_ground"},
    {"operation": "add_routing_domain"},
    {"operation": "connect_segments", "from_segment_id": "relay", "to_segment_id": "relay"},
)


def test_backend_gestures_never_mint_an_id_the_session_or_a_pending_edit_holds(
    service: BuilderVisualDraftService,
) -> None:
    # The rule, the loopback pool and the domain name segments the session does not
    # declare. Such a selector matches no node, so a new segment that took one of
    # these ids would silently join or leave the selection.
    crosslink = {"all": [{"role": "crosslink"}, {"medium": "optical"}]}
    session = {
        **_relay_session("reserved-identities"),
        "link_rules": [
            {
                "id": "relay_isl",
                "endpoints": [
                    {"select": {"segment": "relay"}, "terminal": crosslink},
                    {
                        "select": {"all": [{"segment": "relay"}, {"not": {"segment": "space-1"}}]},
                        "terminal": crosslink,
                    },
                ],
                "topology": {"mode": "nearest_n", "n": 1},
                "constraints": {"max_links_per_node": {"relay": 1, "ground-1": 1}},
            }
        ],
        "addressing": {
            "loopbacks": [
                {
                    "id": "loopbacks",
                    "applies_to": {"any": [{"segment": "relay"}, {"segment": "space-2"}]},
                    "ipv4_pool": "10.240.0.0/16",
                    "prefix_length": 32,
                }
            ]
        },
        "routing": {
            "domains": [
                {
                    "id": "relay_domain",
                    "protocol": "isis",
                    "selectors": [{"segment": "relay"}, {"segment": "ground-2"}],
                }
            ]
        },
    }
    # With the allocation history emptied, only the accepted session holds these ids.
    accepted = _applied_yaml(service, session).model_copy(update={"reserved_authoring_ids": ()})
    assert accepted.applied_session is not None

    assert [_command(service, accepted, gesture).affected_id for gesture in _ID_GESTURES] == [
        "space-3",
        "space-3",
        "ground-3",
        "ground-3",
        "domain-1",
        "link-1",
    ]

    # Pending edits hold the next ids: a new rule that is not complete yet, a
    # changed domain and a new boundary list. None of them is in the session.
    domain = accepted.applied_session["routing"]["domains"][0]
    pending_edits = (
        _content_edit(
            {**_RULE_ADDRESS, "id": "link-1"},
            None,
            {
                "id": "link-1",
                "endpoints": [
                    {"select": {"segment": "space-3"}, "terminal": crosslink},
                    {"select": {"segment": "relay"}, "terminal": crosslink},
                ],
            },
        ),
        _content_edit(
            {**_DOMAIN_ADDRESS, "id": "relay_domain"},
            domain,
            {**domain, "selectors": [*domain["selectors"], {"not": {"segment": "ground-3"}}]},
        ),
        _content_edit(
            _BOUNDARIES_ADDRESS,
            None,
            [
                {
                    "over": "link-2",
                    "adapter": "static_ip",
                    "export": [{"from": "relay_domain", "to": "domain-1"}],
                }
            ],
        ),
    )
    assert [
        _command(service, accepted, gesture, pending_edits=pending_edits).affected_id
        for gesture in _ID_GESTURES
    ] == ["space-4", "space-4", "ground-4", "ground-4", "domain-2", "link-3"]


def test_backend_authoring_allocation_history_survives_segment_deletion(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="allocation-history"))
    for gesture in (
        {"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        {"operation": "add_ground"},
    ):
        draft = _command(service, draft, gesture).draft
    assert draft.reserved_authoring_ids == ("space-1", "ground-1")

    # The YAML pane replaces the session, so neither the session nor the twin holds
    # either segment. Only the allocation history does.
    replaced = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(_relay_session("allocation-history"), sort_keys=False),
        )
    )
    assert replaced.applied is True
    deleted = replaced.draft
    assert deleted.applied_session is not None
    assert [segment["id"] for segment in deleted.applied_session["segments"]] == ["relay"]
    assert deleted.reserved_authoring_ids == ("space-1", "ground-1", "relay")

    for gesture, expected_id in zip(
        _ID_GESTURES[:4],
        ("space-2", "space-2", "ground-2", "ground-2"),
        strict=True,
    ):
        result = _command(service, deleted, gesture)
        assert result.affected_id == expected_id
        assert result.draft.reserved_authoring_ids == (
            "space-1",
            "ground-1",
            "relay",
            expected_id,
        )


def test_a_command_result_is_one_accepted_revision_or_edits_at_the_same_revision(
    service: BuilderVisualDraftService,
) -> None:
    applied = _applied_yaml(service, _relay_session("command-results"))
    accepted = _command(service, applied, {"operation": "add_routing_domain"})
    assert accepted.draft.draft_revision == applied.draft_revision + 1
    assert accepted.created_edits == ()
    answer = json.loads(accepted.model_dump_json())
    unchanged = json.loads(applied.model_dump_json())
    created_edit = _content_edit(
        {**_DOMAIN_ADDRESS, "id": "domain-1"},
        None,
        {"id": "domain-1", "protocol": "isis"},
    )

    kept = BuilderVisualDraftCommandResult.model_validate_json(
        json.dumps({**answer, "draft": unchanged, "created_edits": [created_edit]})
    )
    assert kept.draft == applied
    assert [edit.working for edit in kept.created_edits] == [created_edit["working"]]
    with pytest.raises(ValidationError, match="must leave draft_revision unchanged"):
        BuilderVisualDraftCommandResult.model_validate_json(
            json.dumps({**answer, "created_edits": [created_edit]})
        )
    with pytest.raises(ValidationError, match="advance draft_revision exactly once"):
        BuilderVisualDraftCommandResult.model_validate_json(
            json.dumps({**answer, "draft": unchanged})
        )


def test_backend_never_reuses_topology_ids_or_repairs_dangling_boundaries(
    service: BuilderVisualDraftService,
) -> None:
    """A boundary left naming a removed rule and domain is never repaired.

    A new domain skips the id the boundary still names. "+ boundary" refuses where
    it used to pick. Connect refuses the draft, since the boundary keeps it from
    resolving.
    """
    crosslink = {"all": [{"role": "crosslink"}, {"medium": "optical"}]}

    def exchange(source: str, target: str) -> dict[str, Any]:
        return {
            "from": source,
            "to": target,
            "prefixes": {"aggregate_of": "originated"},
            "export_node_loopbacks": True,
        }

    boundary = {
        "over": "link-1",
        "adapter": "static_ip",
        "export": [exchange("domain-1", "domain-2"), exchange("domain-2", "domain-1")],
    }
    session = {
        "session": {"name": "topology-history"},
        "segments": [
            {"id": "polar", "source": "nodalarc:constellations/earth/leo/earth-leo-polar-36.yaml"},
            {"id": "meo", "source": "nodalarc:constellations/earth/meo/earth-meo-gps-24.yaml"},
        ],
        "link_rules": [
            {
                "id": "link-1",
                "endpoints": [
                    {"select": {"segment": "polar"}, "terminal": crosslink},
                    {"select": {"segment": "meo"}, "terminal": crosslink},
                ],
                "topology": {"mode": "nearest_n", "n": 1},
            }
        ],
        "routing": {
            "domains": [
                {"id": "domain-1", "protocol": "static", "selectors": [{"segment": "polar"}]},
                {"id": "domain-2", "protocol": "static", "selectors": [{"segment": "meo"}]},
            ],
            "boundaries": [boundary],
        },
        "time": {"start_time": "2026-07-10T00:00:00Z", "step_seconds": 1, "compression": 1},
    }
    removed = _apply_edits(
        service,
        _applied_yaml(service, session),
        [
            {
                "kind": "list",
                "list_pointer": "/link_rules",
                "ids_before": ["link-1"],
                "ids_wanted": [],
            },
            {
                "kind": "list",
                "list_pointer": "/routing/domains",
                "ids_before": ["domain-1", "domain-2"],
                "ids_wanted": ["domain-1"],
            },
        ],
    )
    assert removed.applied is True and removed.assembly is not None
    # With the allocation history emptied, only the dangling boundary holds link-1 and domain-2.
    draft = removed.assembly.visual_draft.model_copy(update={"reserved_authoring_ids": ()})
    assert draft.applied_session is not None
    assert "link_rules" not in draft.applied_session
    assert draft.applied_session["routing"]["boundaries"] == [boundary]

    with pytest.raises(BuilderVisualDraftCommandError) as no_rule_or_domain:
        _command(service, draft, {"operation": "add_boundary"})
    assert no_rule_or_domain.value.code == "catalog_authoring.invalid_graph"
    assert str(no_rule_or_domain.value) == (
        "A routing boundary needs an enabled link rule that is not an access rule. "
        "The session has none. A routing boundary needs two routing domains. "
        "The session declares 1."
    )

    domain_result = _command(service, draft, {"operation": "add_routing_domain"})
    assert domain_result.affected_id == "domain-3"
    draft = domain_result.draft
    assert draft.applied_session is not None
    assert [domain["id"] for domain in draft.applied_session["routing"]["domains"]] == [
        "domain-1",
        "domain-3",
    ]
    assert draft.applied_session["routing"]["domains"][1]["selectors"] == [{"segment": "meo"}]

    # Connect cannot learn terminal facts from a draft that does not resolve.
    with pytest.raises(BuilderVisualDraftCommandError, match="names no declared link rule"):
        _command(
            service,
            draft,
            {"operation": "connect_segments", "from_segment_id": "polar", "to_segment_id": "meo"},
        )
    with pytest.raises(BuilderVisualDraftCommandError, match="needs an enabled link rule"):
        _command(service, draft, {"operation": "add_boundary"})
    assert draft.applied_session["routing"]["boundaries"] == [boundary]

    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert compiled.compile_result.save_verdict.allowed is False
    assert any(
        "names no declared link rule" in issue.message for issue in compiled.compile_result.issues
    )


def test_a_new_boundary_runs_over_the_first_enabled_rule_that_is_not_access(
    service: BuilderVisualDraftService,
) -> None:
    """The resolver's own rule decides access: the role an endpoint's terminal names
    outside every `not`. An endpoint that names no single role leaves its rule without
    one."""
    session = _boundary_overlay_session()
    polar_to_meo, meo_to_heo, heo_to_geo = session["link_rules"]
    polar_to_meo["enabled"] = False
    meo_to_heo["endpoints"][1]["terminal"] = {"medium": "optical"}
    heo_to_geo["endpoints"][0]["terminal"] = {"all": [{"role": "access"}, {"medium": "optical"}]}
    not_access = {"all": [{"not": {"role": "access"}}, {"role": "crosslink"}]}
    session["link_rules"].append(
        {
            "id": "geo_to_polar",
            "topology": {"mode": "nearest_n", "n": 1},
            "endpoints": [
                {"select": {"segment": "geo"}, "terminal": not_access},
                {"select": {"segment": "polar"}, "terminal": not_access},
            ],
        }
    )
    draft = _applied_yaml(service, session)
    assert draft.applied_session is not None
    declared = draft.applied_session["routing"]["boundaries"]

    result = _command(service, draft, {"operation": "add_boundary"})
    assert (result.affected_kind, result.affected_id) == ("boundary", "/routing/boundaries")
    boundaries = result.draft.applied_session["routing"]["boundaries"]
    assert boundaries[:3] == declared
    assert boundaries[3]["over"] == "geo_to_polar"
    assert [(item["from"], item["to"]) for item in boundaries[3]["export"]] == [
        ("polar_domain", "meo_domain"),
        ("meo_domain", "polar_domain"),
    ]


def test_a_new_boundary_joins_the_list_as_its_pending_edit_holds_it(
    service: BuilderVisualDraftService,
) -> None:
    """A boundary card's pending edit goes into the revision "+ boundary" makes, so the
    command never leaves the user's change behind to go stale."""
    draft = _applied_yaml(service, _boundary_overlay_session())
    assert draft.applied_session is not None
    declared = draft.applied_session["routing"]["boundaries"]
    working = deepcopy(declared)
    for export in working[1]["export"]:
        export["export_node_loopbacks"] = False
    pending = {
        "kind": "content",
        "address": _BOUNDARIES_ADDRESS,
        "base": declared,
        "working": working,
    }

    result = _command(service, draft, {"operation": "add_boundary"}, pending_edits=(pending,))

    assert result.draft.draft_revision == draft.draft_revision + 1
    assert result.created_edits == ()
    boundaries = result.draft.applied_session["routing"]["boundaries"]
    assert boundaries[:3] == working
    assert len(boundaries) == 4

    stale = {**pending, "base": working}
    with pytest.raises(BuilderVisualDraftCommandError, match="pending edit") as refused:
        _command(service, draft, {"operation": "add_boundary"}, pending_edits=(stale,))
    assert refused.value.code == "catalog_authoring.invalid_graph"


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


def test_a_session_built_from_placed_references_keeps_its_candidate_limits(
    service: BuilderVisualDraftService,
) -> None:
    """The limits a new draft states survive the commands before its first link rule,
    so a session built only from Builder commands compiles and saves."""
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="placed-references"))
    assert draft.authoring_workspace is not None
    seeded = {
        "max_pairs_per_rule": draft.authoring_workspace.max_pairs_per_rule,
        "max_pairs_per_tick": draft.authoring_workspace.max_pairs_per_tick,
    }

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

    space = apply(
        {
            "operation": "place_space_reference",
            "source_ref": "nodalarc:constellations/earth/heo/earth-heo-molniya-3.yaml",
        }
    ).affected_id
    assert draft.applied_session["simulation"]["candidate_limits"] == seeded
    ground = apply(
        {
            "operation": "place_ground_reference",
            "site_set_ref": "nodalarc:site-sets/earth/heo/earth-heo-gateway-sites.yaml",
        }
    ).affected_id
    apply({"operation": "connect_segments", "from_segment_id": space, "to_segment_id": ground})
    apply({"operation": "add_routing_domain"})
    assert draft.applied_session["simulation"]["candidate_limits"] == seeded

    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert compiled.compile_result.save_verdict.allowed, compiled.compile_result.issues


def test_backend_visual_commands_own_seeds_and_advance_one_fenced_revision(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="command-owned"))

    def apply(command: dict[str, Any]) -> BuilderVisualDraftCommandResult:
        nonlocal draft
        result = _command(service, draft, command)
        assert result.created_edits == ()
        assert result.base_draft_revision + 1 == result.draft.draft_revision
        draft = result.draft
        return result

    def refused(command: dict[str, Any]) -> str:
        with pytest.raises(BuilderVisualDraftCommandError) as refusal:
            _command(service, draft, command)
        assert refusal.value.code == "catalog_authoring.invalid_graph"
        return str(refusal.value)

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
    assert ground.scheduling["handover_mode"] == "mbb"
    assert ground.scheduling["ranking_order"] == [
        "service_priority",
        "per_gs_rank",
        "satellite_ground_terminal_capacity",
        "lex_pair",
    ]

    # A ground segment with no site leaves the session pending. Link rule and
    # routing commands run on an accepted session only.
    assert draft.projection_status == "pending_authoring"
    assert "fully applied" in refused({"operation": "add_routing_domain"})
    apply(
        {
            "operation": "mint_ground_members",
            "segment_id": "ground-1",
            "sites": [{"name": "Denver", "lat_deg": 39.7, "lon_deg": -104.9}],
        }
    )
    assert draft.projection_status == "applied"
    assert draft.applied_session is not None

    domain_result = apply({"operation": "add_routing_domain"})
    assert (domain_result.affected_kind, domain_result.affected_id) == (
        "routing_domain",
        "domain-1",
    )
    assert draft.applied_session["routing"] == {
        "domains": [
            {
                "id": "domain-1",
                "protocol": "isis",
                "selectors": [{"any": [{"segment": "space-1"}, {"segment": "ground-1"}]}],
                "area_assignment": {"strategy": "flat"},
            }
        ]
    }
    assert refused({"operation": "add_boundary"}) == (
        "A routing boundary needs an enabled link rule that is not an access rule. "
        "The session has none. A routing boundary needs two routing domains. "
        "The session declares 1."
    )
    assert (
        refused(
            {
                "operation": "connect_segments",
                "from_segment_id": "space-1",
                "to_segment_id": "nowhere",
            }
        )
        == "The session declares no segment 'nowhere'"
    )

    link_result = apply(
        {
            "operation": "connect_segments",
            "from_segment_id": "space-1",
            "to_segment_id": "ground-1",
        }
    )
    assert (link_result.affected_kind, link_result.affected_id) == ("link", "link-1")
    # The stub world declares no terminals, so the role and medium are a guess.
    # Connect writes no elevation floor on either endpoint.
    access = {"all": [{"role": "access"}, {"medium": "rf"}]}
    assert draft.applied_session["link_rules"] == [
        {
            "id": "link-1",
            "enabled": True,
            "endpoints": [
                {"select": {"segment": "ground-1"}, "terminal": access},
                {"select": {"segment": "space-1"}, "terminal": access},
            ],
            "topology": {"mode": "visible_candidates"},
        }
    ]
    connected = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert connected.compile_result.save_verdict.allowed, connected.compile_result.issues

    # Every segment is in a domain, so the new domain has no member. It is returned
    # as a pending edit with no selectors. The draft keeps its revision.
    uncovered = _command(service, draft, {"operation": "add_routing_domain"})
    assert (uncovered.affected_kind, uncovered.affected_id) == ("routing_domain", "domain-2")
    assert uncovered.draft == draft
    assert [edit.model_dump(mode="json") for edit in uncovered.created_edits] == [
        _content_edit(
            {**_DOMAIN_ADDRESS, "id": "domain-2"},
            None,
            {"id": "domain-2", "protocol": "isis", "area_assignment": {"strategy": "flat"}},
        )
    ]
    domain_one = draft.applied_session["routing"]["domains"][0]
    split = _apply_edits(
        service,
        draft,
        [
            _content_edit(
                {**_DOMAIN_ADDRESS, "id": "domain-1"},
                domain_one,
                {**domain_one, "selectors": [{"segment": "space-1"}]},
            ),
            _content_edit(
                {**_DOMAIN_ADDRESS, "id": "domain-2"},
                None,
                {
                    **uncovered.created_edits[0].working,
                    "selectors": [{"segment": "ground-1"}],
                },
            ),
        ],
    )
    assert split.applied is True and split.assembly is not None
    draft = split.assembly.visual_draft
    assert draft.applied_session is not None
    # An access rule cannot carry a boundary.
    assert refused({"operation": "add_boundary"}) == (
        "A routing boundary needs an enabled link rule that is not an access rule. "
        "The session has none."
    )

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
    isl = {"all": [{"role": "isl"}, {"medium": "optical"}]}
    assert draft.applied_session["link_rules"] == [
        {
            "id": "link-1",
            "enabled": True,
            "endpoints": [{"select": {"segment": "space-1"}, "terminal": isl}] * 2,
            "topology": {"mode": "nearest_n", "n": 2},
        }
    ]

    # The seed runs over the first rule that is not access, between the first two
    # domains. The user changes it on the boundary card.
    boundary_result = apply({"operation": "add_boundary"})
    assert (boundary_result.affected_kind, boundary_result.affected_id) == (
        "boundary",
        "/routing/boundaries",
    )
    exchange = {"prefixes": {"aggregate_of": "originated"}, "export_node_loopbacks": True}
    assert draft.applied_session["routing"]["boundaries"] == [
        {
            "over": "link-1",
            "adapter": "static_ip",
            "export": [
                {"from": "domain-1", "to": "domain-2", **exchange},
                {"from": "domain-2", "to": "domain-1", **exchange},
            ],
        }
    ]
    # A boundary has no grammar id, so the allocation history holds none.
    assert draft.reserved_authoring_ids == ("space-1", "ground-1", "domain-1", "link-1", "domain-2")
    assert draft.draft_revision == 9

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
    # Addresses are resolver-allocated at deploy; minted draft sites carry
    # composition only.
    assert all(
        member.site is not None and member.site.nodes[0].node_ref is not None for member in members
    )

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
    assert ames.site.nodes[0].node_ref is not None


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
            "operation": "set_ground_stamp_node",
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
            "operation": "set_ground_site_node",
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
    assert first_node.node_ref is not None

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
    assert added.node_ref == "nodalarc:nodes/ground/starlink-gateway.yaml"
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
                update={"space": (incomplete_space,)}
            ),
            "control_tree": None,
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
            "authoring_workspace": draft.authoring_workspace.model_copy(update={"space": (reset,)}),
            "control_tree": None,
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
            "authoring_workspace": draft.authoring_workspace.model_copy(update={"space": (space,)}),
            "control_tree": None,
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

    def resolved_preview(resolution: SessionResolution) -> BuilderWorld:
        base = builder_world_preview(resolution.resolved.session.name)
        segments = tuple(
            BuilderWorldSegment(
                segment_id=segment_id,
                display_name=segment_id,
                capabilities=(BuilderSegmentCapability(endpoint_role="crosslink", medium="rf"),),
            )
            for segment_id in ("space-1", "space-2")
        )
        return base.model_copy(update={"segments": segments})

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

    assert connected.draft.applied_session is not None
    rule = connected.draft.applied_session["link_rules"][-1]
    crosslink = {"all": [{"role": "crosslink"}, {"medium": "rf"}]}
    assert [endpoint["terminal"] for endpoint in rule["endpoints"]] == [crosslink, crosslink]
    assert rule["topology"] == {"mode": "nearest_n", "n": 1}


def test_connect_refuses_a_draft_that_does_not_resolve(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="unresolved-connect"))
    for _ in range(2):
        draft = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command={"operation": "add_generated_space", "phasing_mode": "walker_delta"},
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        ).draft

    def failing_preview(resolution: SessionResolution) -> BuilderWorld:
        raise ValueError("preview could not be built")

    # Without a resolved world the terminals are unknown; connect does not guess them.
    with pytest.raises(BuilderVisualDraftCommandError, match="does not resolve"):
        service.apply_command(
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
            preview_factory=failing_preview,
        )


def _connected_heo_access_draft(
    service: BuilderVisualDraftService,
    session_name: str,
    preview_factory: PreviewFactory,
) -> BuilderVisualDraftEnvelope:
    """A Molniya constellation and the HEO gateway sites, joined by the connect gesture."""
    draft = service.create(BuilderVisualDraftCreateRequest(session_name=session_name))
    for command in (
        {
            "operation": "place_space_reference",
            "source_ref": "nodalarc:constellations/earth/heo/earth-heo-molniya-3.yaml",
        },
        {
            "operation": "place_ground_reference",
            "site_set_ref": "nodalarc:site-sets/earth/heo/earth-heo-gateway-sites.yaml",
        },
        {
            "operation": "connect_segments",
            "from_segment_id": "space-1",
            "to_segment_id": "ground-1",
        },
    ):
        draft = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=preview_factory,
        ).draft
    return draft


def test_connect_leaves_each_ground_station_on_its_own_elevation_floor(
    service: BuilderVisualDraftService,
) -> None:
    """Connect writes no floor on the ground endpoint, so the resolver keeps each
    station on the floor its own access terminals declare."""
    effective_floors: dict[str, float] = {}

    def resolved_world(resolution: SessionResolution) -> BuilderWorld:
        effective_floors.clear()
        effective_floors.update(resolution.resolved.effective_ground_min_elevation_by_gs())
        return world_from_resolution(resolution)

    draft = _connected_heo_access_draft(service, "own-floors", resolved_world)
    ground_endpoint, space_endpoint = draft.applied_session["link_rules"][0]["endpoints"]
    assert ground_endpoint["select"] == {"segment": "ground-1"}
    assert "min_elevation_deg" not in ground_endpoint
    assert "min_elevation_deg" not in space_endpoint

    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
        preview_factory=resolved_world,
    )
    assert compiled.compile_result.save_verdict.allowed, compiled.compile_result.issues
    world = compiled.compile_result.resolved_preview
    assert world is not None
    declared = {
        node.node_id: max(
            block.min_elevation_deg
            for block in node.terminal_inventory
            if block.endpoint_role == "access"
        )
        for node in world.nodes
        if node.segment_id == "ground-1"
    }
    # The stations declare different floors, so a copy of the strictest one
    # on the endpoint would raise the others to it.
    assert len(set(declared.values())) > 1
    assert effective_floors == declared


def test_rederive_keeps_an_authored_floor_only_on_the_ground_side_of_an_access_rule(
    service: BuilderVisualDraftService,
) -> None:
    draft = _connected_heo_access_draft(service, "authored-floor", world_from_resolution)
    assert draft.applied_session is not None
    rule = draft.applied_session["link_rules"][0]
    floored = deepcopy(rule)
    floored["endpoints"][0]["min_elevation_deg"] = 20.0
    applied = _apply_edits(
        service,
        draft,
        [_content_edit({**_RULE_ADDRESS, "id": "link-1"}, rule, floored)],
    )
    assert applied.assembly is not None, applied.outcomes
    authored = applied.assembly.visual_draft

    def rederive_side_a(
        draft: BuilderVisualDraftEnvelope,
        segment_id: str,
    ) -> BuilderVisualDraftCommandResult:
        return service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command={
                    "operation": "rederive_link",
                    "rule_id": "link-1",
                    "side": "a",
                    "segment_id": segment_id,
                },
            ),
            available_node_count=1_000_000,
            preview_factory=world_from_resolution,
        )

    # Side a stays on the ground segment, so the authored 20 degrees stay.
    kept = rederive_side_a(authored, "ground-1")
    assert kept.notice == "re-derived: access · rf · all visible pairs"
    ground_endpoint = kept.draft.applied_session["link_rules"][0]["endpoints"][0]
    assert ground_endpoint["select"] == {"segment": "ground-1"}
    assert ground_endpoint["min_elevation_deg"] == 20.0

    # With both sides in space the rule is no longer access. The floor is removed.
    cleared = rederive_side_a(kept.draft, "space-1")
    assert cleared.notice == "re-derived: isl · optical · nearest-2"
    endpoints = cleared.draft.applied_session["link_rules"][0]["endpoints"]
    assert [endpoint["select"] for endpoint in endpoints] == [{"segment": "space-1"}] * 2
    assert all("min_elevation_deg" not in endpoint for endpoint in endpoints)


def test_rederive_accepts_the_rules_own_pending_edit_with_it(
    service: BuilderVisualDraftService,
) -> None:
    """A rederive started in a link card carries the card's edit. One revision holds both."""
    draft = _connected_heo_access_draft(service, "rederive-pending", _preview)
    assert draft.applied_session is not None
    rule = draft.applied_session["link_rules"][0]
    address = {**_RULE_ADDRESS, "id": "link-1"}

    def rederive(segment_id: str, working: dict[str, Any]) -> BuilderVisualDraftCommandResult:
        return _command(
            service,
            draft,
            {
                "operation": "rederive_link",
                "rule_id": "link-1",
                "side": "a",
                "segment_id": segment_id,
            },
            pending_edits=(_content_edit(address, rule, working),),
        )

    rederived = rederive("space-1", _ranged(rule))
    assert rederived.created_edits == ()
    assert rederived.draft.draft_revision == draft.draft_revision + 1
    isl = {"all": [{"role": "isl"}, {"medium": "optical"}]}
    assert rederived.draft.applied_session["link_rules"] == [
        {
            "id": "link-1",
            "enabled": True,
            "endpoints": [{"select": {"segment": "space-1"}, "terminal": isl}] * 2,
            "topology": {"mode": "nearest_n", "n": 2},
            "constraints": {"max_range_km": 12345.0},
        }
    ]

    with pytest.raises(BuilderVisualDraftCommandError, match="no segment 'nowhere'"):
        rederive("nowhere", _ranged(rule))
    with pytest.raises(BuilderVisualDraftCommandError, match="cannot be applied"):
        rederive("space-1", {key: value for key, value in rule.items() if key != "topology"})
    # A rule in a form the card does not write back is never rewritten in the card's form.
    with pytest.raises(BuilderVisualDraftCommandError, match="does not write back"):
        rederive("space-1", {**rule, "tags": ["relay"]})
    # After the accepted rederive, an edit begun from the earlier rule is stale.
    with pytest.raises(BuilderVisualDraftCommandError, match="changed since the edit began"):
        _command(
            service,
            rederived.draft,
            {
                "operation": "rederive_link",
                "rule_id": "link-1",
                "side": "a",
                "segment_id": "ground-1",
            },
            pending_edits=(_content_edit(address, rule, _ranged(rule)),),
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


@pytest.mark.parametrize(
    "session_path",
    sorted((SHIPPED_ROOT / "sessions").glob("*.yaml")),
    ids=lambda path: path.stem,
)
def test_save_under_a_new_name_keeps_the_whole_shipped_session(
    context: CatalogContext,
    service: BuilderVisualDraftService,
    session_path: Path,
) -> None:
    # Saving an opened session under another name changes its name and nothing
    # else: earth-leo-walker keeps leo_isl, a rule the graphical editors cannot hold.
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref=f"nodalarc:sessions/{session_path.name}")
    )
    original = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).compile_result.canonical_session_json
    assert original is not None
    new_name = f"{session_path.stem}-copy"
    retargeted = service.retarget(
        BuilderVisualDraftRetargetRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            target_ref=f"user:sessions/{new_name}.yaml",
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    saved = save_builder_session(
        retargeted.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )

    assert saved.session.canonical_json == {
        **original,
        "session": {**original["session"], "name": new_name},
    }


def _generated_space_draft(
    service: BuilderVisualDraftService,
    session_name: str,
) -> BuilderVisualDraftEnvelope:
    created = service.create(BuilderVisualDraftCreateRequest(session_name=session_name))
    return service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=created,
            expected_draft_revision=created.draft_revision,
            command={"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).draft


def _with_space_edit(
    draft: BuilderVisualDraftEnvelope,
    service: BuilderVisualDraftService,
    **changes: Any,
) -> BuilderVisualDraftEnvelope:
    workspace = draft.authoring_workspace
    assert workspace is not None
    space = workspace.space[0]
    return service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            workspace=workspace.model_copy(update={"space": (space.model_copy(update=changes),)}),
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).visual_draft


def test_save_under_a_new_name_names_components_as_the_assembler_does(
    service: BuilderVisualDraftService,
) -> None:
    # A generated constellation or site set is named after the session and its
    # segment. After a save under a new name, the next graphical edit runs the
    # assembler, which must generate exactly the refs the rename gave them.
    authored = _generated_space_draft(service, "first-name")
    for command in (
        {"operation": "add_ground"},
        {
            "operation": "mint_ground_members",
            "segment_id": "ground-1",
            "sites": [{"name": "Denver", "lat_deg": 39.7, "lon_deg": -104.9}],
        },
    ):
        authored = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=authored,
                expected_draft_revision=authored.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        ).draft
    retargeted = service.retarget(
        BuilderVisualDraftRetargetRequest(
            draft=authored,
            expected_draft_revision=authored.draft_revision,
            target_ref="user:sessions/second-name.yaml",
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).visual_draft
    renamed = {str(proposal.ref) for proposal in retargeted.catalog_documents}
    assert {CatalogRef(ref).family for ref in renamed} >= {
        "constellations",
        "orbits",
        "site-sets",
        "sites",
    }
    assert all("/second-name/" in ref and "first-name" not in ref for ref in renamed)

    edited = _with_space_edit(retargeted, service, display_name="Edited constellation")

    assert edited.projection_status == "applied"
    assert {str(proposal.ref) for proposal in edited.catalog_documents} == renamed


def test_save_under_a_new_name_refuses_a_draft_with_pending_edits(
    service: BuilderVisualDraftService,
) -> None:
    # A save under a new name copies the accepted session; a draft whose last
    # graphical edit was refused must not be saved without that edit. The
    # refused edit here leaves every component valid.
    authored = _generated_space_draft(service, "pending-source")
    workspace = authored.authoring_workspace
    assert workspace is not None
    pending = service.apply_workspace(
        BuilderVisualDraftApplyWorkspaceRequest(
            draft=authored,
            expected_draft_revision=authored.draft_revision,
            workspace=workspace.model_copy(update={"session_name": "another-name"}),
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).visual_draft
    assert pending.projection_status == "pending_authoring"
    assert pending.applied_session is not None

    with pytest.raises(BuilderVisualDraftCommandError) as refused:
        service.retarget(
            BuilderVisualDraftRetargetRequest(
                draft=pending,
                expected_draft_revision=pending.draft_revision,
                target_ref="user:sessions/pending-copy.yaml",
            ),
            available_node_count=1_000_000,
        )
    assert refused.value.code == "catalog_authoring.invalid_graph"
    assert "pending edits" in str(refused.value)


def test_save_under_a_new_name_refuses_an_invalid_generated_component(
    service: BuilderVisualDraftService,
) -> None:
    # The draft is accepted while its generated orbit is invalid (the compile
    # reports the orbit); a save under a new name cannot copy that component
    # and says so with a typed refusal.
    authored = _generated_space_draft(service, "invalid-source")
    workspace = authored.authoring_workspace
    assert workspace is not None
    invalid = _with_space_edit(
        authored,
        service,
        orbit=workspace.space[0].orbit.model_copy(update={"altitude_km": -5000}),
    )
    assert invalid.projection_status == "applied"

    with pytest.raises(BuilderVisualDraftCommandError) as refused:
        service.retarget(
            BuilderVisualDraftRetargetRequest(
                draft=invalid,
                expected_draft_revision=invalid.draft_revision,
                target_ref="user:sessions/invalid-copy.yaml",
            ),
            available_node_count=1_000_000,
        )
    assert refused.value.code == "catalog_authoring.invalid_graph"
    assert "altitude_km" in str(refused.value)


@pytest.mark.parametrize(
    ("target_name", "refusal"),
    [
        ("my--copy", "must be written as 'my-copy'"),
        ("copy_", "must be written as 'copy'"),
        ("a" * 49, "must be written as"),
        ("a-session-name-that-is-forty-four-chars-long", "the same id"),
    ],
)
def test_save_under_a_new_name_refuses_names_the_builder_cannot_keep(
    service: BuilderVisualDraftService,
    target_name: str,
    refusal: str,
) -> None:
    # The graphical editor requires the session name to be its own canonical
    # identifier, and it derives one id per generated orbit, constellation and
    # site set; a name that breaks either rule is refused before anything is
    # renamed.
    authored = _generated_space_draft(service, "two-segments")
    authored = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=authored,
            expected_draft_revision=authored.draft_revision,
            command={"operation": "add_generated_space", "phasing_mode": "walker_delta"},
        ),
        available_node_count=1_000_000,
        preview_factory=_preview,
    ).draft

    with pytest.raises(BuilderVisualDraftCommandError) as refused:
        service.retarget(
            BuilderVisualDraftRetargetRequest(
                draft=authored,
                expected_draft_revision=authored.draft_revision,
                target_ref=f"user:sessions/{target_name}.yaml",
            ),
            available_node_count=1_000_000,
        )
    assert refused.value.code == "catalog_authoring.invalid_patch"
    assert refusal in str(refused.value)


def test_customizing_an_unsaved_root_again_is_an_in_band_refusal(
    service: BuilderVisualDraftService,
) -> None:
    # After one customize the segment's root is an unsaved proposal; the page
    # offers that root as the next leaf, and the fork cannot read it from the
    # catalog. The refusal comes back as an issue on the result.
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    first = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            segment_id="leo",
            leaf_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        )
    )
    assert first.applied is True
    assert first.draft.authoring_workspace is not None
    root = first.draft.authoring_workspace.space_refs[0].source_ref
    assert root is not None

    again = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=first.draft,
            expected_draft_revision=first.draft.draft_revision,
            segment_id="leo",
            leaf_ref=root,
        )
    )

    assert again.applied is False
    assert [issue.code for issue in again.issues] == ["builder.draft.customize_fork_invalid"]


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
                            nodes=(
                                BuilderVisualSiteNode(
                                    node_id="gw1",
                                    node_ref="nodalarc:nodes/ground/leo-gateway.yaml",
                                    installed={"access_ka": 1},
                                    boresights={"access_ka": boresight},
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
    site_set = next(
        proposal
        for proposal in result.assembled_draft.state.catalog_documents
        if proposal.ref.family == "site-sets"
    )
    assert site_set.document["site_set"]["sites"] == []
    codes = {issue.code for issue in result.assembly_issues}
    assert "builder.draft.ground_members_required" in codes
    assert result.compile_result.save_verdict.allowed is False
    assert all({"save", "deploy"}.issubset(issue.blocks) for issue in result.assembly_issues)


def _issue_pointers(issues: tuple[BuilderIssue, ...]) -> set[str | None]:
    assert all(issue.blocks == ("save", "deploy") for issue in issues)
    return {issue.json_pointer for issue in issues}


def test_complete_card_values_build_objects_that_validate_on_their_own() -> None:
    rule, rule_issues = _link_rule_document(
        BuilderVisualLinkRule(
            rule_id="leo_ground",
            enabled=False,
            a=BuilderVisualLinkEndpoint(
                segment_id="ground",
                tag="gateways",
                role="access",
                medium="rf",
                min_elevation_deg=10.0,
            ),
            b=BuilderVisualLinkEndpoint(segment_id="leo", role="access", medium="rf"),
            topology_mode="nearest_n",
            topology_n=2,
            max_range_km=3000.0,
        )
    )
    assert rule_issues == ()
    assert rule == {
        "id": "leo_ground",
        "enabled": False,
        "topology": {"mode": "nearest_n", "n": 2},
        "endpoints": [
            {
                "select": {"all": [{"segment": "ground"}, {"tag": "gateways"}]},
                "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
                "min_elevation_deg": 10.0,
            },
            {
                "select": {"segment": "leo"},
                "terminal": {"all": [{"role": "access"}, {"medium": "rf"}]},
            },
        ],
        "constraints": {"max_range_km": 3000.0},
    }
    LinkRule.model_validate(rule)

    domain, domain_issues = _routing_domain_document(
        BuilderVisualRoutingDomain(
            domain_id="leo_domain",
            protocol="isis",
            member_segment_ids=("leo", "ground"),
            hello_interval_s=2,
            hold_interval_s=7,
        )
    )
    assert domain_issues == ()
    assert domain == {
        "id": "leo_domain",
        "protocol": "isis",
        "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
        "area_assignment": {"strategy": "flat"},
        "timers": {"hello_interval_s": 2, "hold_interval_s": 7},
    }
    RoutingDomain.model_validate(domain)

    boundaries, boundary_issues = _routing_boundary_documents(
        (
            BuilderVisualRoutingBoundary(
                boundary_id="boundary-1",
                over_rule_id="leo_meo",
                adapter="static_ip",
                from_domain_id="leo_domain",
                to_domain_id="meo_domain",
                export_node_loopbacks=False,
            ),
        )
    )
    assert boundary_issues == ()
    assert boundaries == [
        {
            "over": "leo_meo",
            "adapter": "static_ip",
            "export": [
                {
                    "from": "leo_domain",
                    "to": "meo_domain",
                    "prefixes": {"aggregate_of": "originated"},
                    "export_node_loopbacks": False,
                },
                {
                    "from": "meo_domain",
                    "to": "leo_domain",
                    "prefixes": {"aggregate_of": "originated"},
                    "export_node_loopbacks": False,
                },
            ],
        }
    ]
    TypeAdapter(tuple[RoutingBoundary, ...]).validate_python(boundaries)


def test_missing_card_values_stay_absent_and_each_is_named_in_an_issue() -> None:
    rule, rule_issues = _link_rule_document(
        BuilderVisualLinkRule(
            a=BuilderVisualLinkEndpoint(tag="gateways"),
            b=BuilderVisualLinkEndpoint(segment_id="leo", medium="optical"),
        )
    )
    assert rule == {
        "topology": {},
        "endpoints": [
            {
                "select": {"all": [{}, {"tag": "gateways"}]},
                "terminal": {"all": [{}, {}]},
            },
            {
                "select": {"segment": "leo"},
                "terminal": {"all": [{}, {"medium": "optical"}]},
            },
        ],
    }
    assert _issue_pointers(rule_issues) == {
        "/id",
        "/topology/mode",
        "/endpoints/0/select/all/0/segment",
        "/endpoints/0/terminal/all/0/role",
        "/endpoints/0/terminal/all/1/medium",
        "/endpoints/1/terminal/all/0/role",
    }

    nearest, nearest_issues = _link_rule_document(
        BuilderVisualLinkRule(
            rule_id="leo_isl",
            a=BuilderVisualLinkEndpoint(role="isl", medium="optical"),
            b=BuilderVisualLinkEndpoint(segment_id="leo", role="isl", medium="optical"),
            topology_mode="nearest_n",
        )
    )
    assert nearest["topology"] == {"mode": "nearest_n"}
    assert nearest["endpoints"][0]["select"] == {}
    assert _issue_pointers(nearest_issues) == {"/topology/n", "/endpoints/0/select/segment"}

    domain, domain_issues = _routing_domain_document(
        BuilderVisualRoutingDomain(member_segment_ids=("leo", ""), hello_interval_s=5)
    )
    assert domain == {
        "selectors": [{"any": [{"segment": "leo"}, {}]}],
        "timers": {"hello_interval_s": 5},
    }
    assert _issue_pointers(domain_issues) == {
        "/id",
        "/protocol",
        "/selectors/0/any/1/segment",
        "/timers/hold_interval_s",
    }

    lone, lone_issues = _routing_domain_document(
        BuilderVisualRoutingDomain(
            domain_id="ospf_domain", protocol="ospf", member_segment_ids=("",)
        )
    )
    assert lone["selectors"] == [{}]
    assert _issue_pointers(lone_issues) == {"/selectors/0/segment"}

    empty, empty_issues = _routing_domain_document(
        BuilderVisualRoutingDomain(domain_id="ospf_domain", protocol="ospf")
    )
    assert empty == {
        "id": "ospf_domain",
        "protocol": "ospf",
        "area_assignment": {"strategy": "flat"},
    }
    assert _issue_pointers(empty_issues) == {"/selectors"}

    boundaries, boundary_issues = _routing_boundary_documents(
        (
            BuilderVisualRoutingBoundary(
                over_rule_id="leo_meo",
                adapter="static_ip",
                from_domain_id="leo_domain",
                to_domain_id="meo_domain",
            ),
            BuilderVisualRoutingBoundary(to_domain_id="meo_domain"),
        )
    )
    assert boundaries[1] == {
        "export": [
            {
                "to": "meo_domain",
                "prefixes": {"aggregate_of": "originated"},
                "export_node_loopbacks": True,
            },
            {
                "from": "meo_domain",
                "prefixes": {"aggregate_of": "originated"},
                "export_node_loopbacks": True,
            },
        ]
    }
    assert _issue_pointers(boundary_issues) == {
        "/1/over",
        "/1/adapter",
        "/1/export/0/from",
        "/1/export/1/to",
    }


def _object_edit(
    service: BuilderVisualDraftService,
    *,
    target_ref: str,
    applied_revision: int,
    address: dict[str, Any],
    working: Any,
    change: dict[str, Any] | None = None,
) -> BuilderVisualObjectEditResult:
    """One object edit sent as the page sends it: JSON validated into the request."""
    body: dict[str, Any] = {
        "target_ref": target_ref,
        "applied_revision": applied_revision,
        "address": address,
        "working": working,
    }
    if change is not None:
        body["change"] = change
    return service.edit_object(BuilderVisualObjectEditRequest.model_validate_json(json.dumps(body)))


def _tree_controls(control: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield control
    for field in control.get("fields", ()):
        yield from _tree_controls(field["control"])
    for branch in control.get("branches", ()):
        if branch.get("control") is not None:
            yield from _tree_controls(branch["control"])
    for item in control.get("items", ()):
        yield from _tree_controls(item["control"])


def _edited_control(
    result: BuilderVisualObjectEditResult,
    pointer: str,
    kind: str,
) -> dict[str, Any]:
    root = result.control_tree.model_dump(mode="json")["root"]
    return next(
        control
        for control in _tree_controls(root)
        if control["json_pointer"] == pointer and control["kind"] == kind
    )


def _present_branch(choice: dict[str, Any]) -> str:
    """The branch of an optional field's choice that makes the field present."""
    return next(branch["branch_id"] for branch in choice["branches"] if branch["control"])


def _saved_form(model: type[BaseModel], value: Any) -> dict[str, Any]:
    return model.model_validate(value).model_dump(mode="json", by_alias=True, exclude_none=True)


_RULE_ADDRESS = {"kind": "member", "list_pointer": "/link_rules"}
_DOMAIN_ADDRESS = {"kind": "member", "list_pointer": "/routing/domains"}
_BOUNDARIES_ADDRESS = {"kind": "whole_list", "pointer": "/routing/boundaries"}


def _accepted(
    service: BuilderVisualDraftService,
    session: str,
) -> tuple[str, int, dict[str, Any]]:
    """A shipped session's target, accepted revision and accepted session JSON."""
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref=f"nodalarc:sessions/{session}.yaml")
    )
    assert opened.applied_session is not None and opened.applied_revision is not None
    return opened.target_ref, opened.applied_revision, opened.applied_session


def _member(session: dict[str, Any], list_pointer: str, member_id: str) -> dict[str, Any]:
    if list_pointer == "/link_rules":
        members = session["link_rules"]
    else:
        members = session["routing"]["domains"]
    return next(member for member in members if member["id"] == member_id)


def test_object_edit_writes_a_card_value_as_the_whole_object(
    service: BuilderVisualDraftService,
) -> None:
    target_ref, revision, session = _accepted(service, "earth-leo-simple")
    rule = _member(session, "/link_rules", "leo_access")
    address = {**_RULE_ADDRESS, "id": "leo_access"}
    described = _object_edit(
        service, target_ref=target_ref, applied_revision=revision, address=address, working=rule
    )
    assert described.working == rule
    assert described.issues == ()
    assert described.widget is not None and described.widget.kind == "link_rule"
    # The field editor is bound to the accepted revision and shows the id read-only.
    assert described.control_tree.projection_revision == revision
    assert _edited_control(described, "/id", "scalar")["specialized"] is True

    card = described.widget.model_dump(mode="json")
    card["rule"]["max_range_km"] = 12345.0
    edited = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=address,
        working=described.working,
        change=card,
    )
    assert edited.issues == ()
    assert _saved_form(LinkRule, edited.working) == _saved_form(
        LinkRule, {**rule, "constraints": {"max_range_km": 12345.0}}
    )
    assert edited.widget is not None and edited.widget.kind == "link_rule"
    assert edited.widget.rule.max_range_km == 12345.0

    target_ref, revision, session = _accepted(service, "earth-leo-heo-geo-luna-reachability")
    domain = _member(session, "/routing/domains", "luna_domain")
    domain_address = {**_DOMAIN_ADDRESS, "id": "luna_domain"}
    described = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=domain_address,
        working=domain,
    )
    assert described.widget is not None and described.widget.kind == "routing_domain"
    card = described.widget.model_dump(mode="json")
    card["domain"]["hello_interval_s"] = 2
    card["domain"]["hold_interval_s"] = 7
    timed = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=domain_address,
        working=described.working,
        change=card,
    )
    assert timed.issues == ()
    assert timed.working == {**domain, "timers": {"hello_interval_s": 2, "hold_interval_s": 7}}
    RoutingDomain.model_validate(timed.working)
    assert timed.widget is not None and timed.widget.kind == "routing_domain"
    assert (timed.widget.domain.hello_interval_s, timed.widget.domain.hold_interval_s) == (2, 7)


def test_object_edit_field_controls_edit_objects_no_card_writes_back(
    service: BuilderVisualDraftService,
) -> None:
    target_ref, revision, session = _accepted(service, "earth-luna-dtn")

    rule = _member(session, "/link_rules", "relay_geo_access")
    rule_address = {**_RULE_ADDRESS, "id": "relay_geo_access"}
    described = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=rule_address,
        working=rule,
    )
    # The mount predicate has no place on the link card.
    assert described.widget is None
    assert described.issues == ()
    constraints = _edited_control(described, "/constraints", "choice")
    ranged = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=rule_address,
        working=described.working,
        change={
            "kind": "controls",
            "commands": [
                {
                    "operation": "select_choice",
                    "control_id": constraints["control_id"],
                    "branch_id": _present_branch(constraints),
                },
                {
                    "operation": "set_scalar",
                    "control_id": _edited_control(described, "/constraints/max_range_km", "scalar")[
                        "control_id"
                    ],
                    "value": 12345.0,
                },
            ],
        },
    )
    assert ranged.issues == ()
    assert ranged.widget is None
    assert ranged.working == {**rule, "constraints": {"max_range_km": 12345.0}}

    domain = _member(session, "/routing/domains", "earth_domain")
    domain_address = {**_DOMAIN_ADDRESS, "id": "earth_domain"}
    described = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=domain_address,
        working=domain,
    )
    assert described.widget is None
    timers = _edited_control(described, "/timers", "choice")
    timed = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=domain_address,
        working=described.working,
        change={
            "kind": "controls",
            "commands": [
                {
                    "operation": "select_choice",
                    "control_id": timers["control_id"],
                    "branch_id": _present_branch(timers),
                },
                *(
                    {
                        "operation": "set_scalar",
                        "control_id": _edited_control(described, f"/timers/{name}", "scalar")[
                            "control_id"
                        ],
                        "value": value,
                    }
                    for name, value in (("hello_interval_s", 2), ("hold_interval_s", 7))
                ),
            ],
        },
    )
    assert timed.issues == ()
    assert timed.widget is None
    assert timed.working == {**domain, "timers": {"hello_interval_s": 2, "hold_interval_s": 7}}

    boundaries = session["routing"]["boundaries"]
    described = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=_BOUNDARIES_ADDRESS,
        working=boundaries,
    )
    # The boundary installs routes through the peer loopback, which no card states.
    assert described.widget == BuilderVisualRoutingBoundariesWidget(
        kind="routing_boundaries", boundaries=(None,)
    )
    unexported = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=_BOUNDARIES_ADDRESS,
        working=described.working,
        change={
            "kind": "controls",
            "commands": [
                {
                    "operation": "set_scalar",
                    "control_id": _edited_control(
                        described, f"/boundaries/0/export/{export}/export_node_loopbacks", "scalar"
                    )["control_id"],
                    "value": False,
                }
                for export in (0, 1)
            ],
        },
    )
    assert unexported.issues == ()
    expected = json.loads(json.dumps(boundaries))
    for export in expected[0]["export"]:
        export["export_node_loopbacks"] = False
    assert unexported.working == expected


def test_object_edit_writes_only_the_boundaries_that_have_a_card(
    service: BuilderVisualDraftService,
) -> None:
    boundaries = _boundary_overlay_session()["routing"]["boundaries"]
    target_ref = "user:sessions/boundary-overlay.yaml"
    described = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=0,
        address=_BOUNDARIES_ADDRESS,
        working=boundaries,
    )
    assert described.widget is not None and described.widget.kind == "routing_boundaries"
    first, second, third = described.widget.boundaries
    assert first is None
    assert second is not None and third is not None

    cards = described.widget.model_dump(mode="json")
    cards["boundaries"][1]["export_node_loopbacks"] = False
    edited = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=0,
        address=_BOUNDARIES_ADDRESS,
        working=described.working,
        change=cards,
    )
    assert edited.issues == ()
    # A boundary with no card stays as the working list holds it.
    assert edited.working[0] == boundaries[0]
    changed = json.loads(json.dumps(boundaries[1]))
    for export in changed["export"]:
        export["export_node_loopbacks"] = False
    assert edited.working[1] == changed
    assert edited.working[2] == boundaries[2]

    # Removing one boundary is an item command on the list, which stays open.
    removed = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=0,
        address=_BOUNDARIES_ADDRESS,
        working=boundaries,
        change={
            "kind": "controls",
            "commands": [
                {
                    "operation": "remove_item",
                    "control_id": _edited_control(described, "/boundaries", "sequence")[
                        "control_id"
                    ],
                    "index": 0,
                }
            ],
        },
    )
    assert removed.working == boundaries[1:]


def test_object_edit_names_a_removed_required_field_in_an_issue(
    service: BuilderVisualDraftService,
) -> None:
    target_ref, revision, session = _accepted(service, "earth-leo-simple")
    rule = _member(session, "/link_rules", "leo_access")
    address = {**_RULE_ADDRESS, "id": "leo_access"}
    described = _object_edit(
        service, target_ref=target_ref, applied_revision=revision, address=address, working=rule
    )
    removed = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=address,
        working=described.working,
        change={
            "kind": "controls",
            "commands": [
                {
                    "operation": "set_present",
                    "control_id": _edited_control(described, "/topology", "choice")["control_id"],
                    "present": False,
                }
            ],
        },
    )
    assert "topology" not in removed.working
    assert removed.widget is None
    assert [(issue.code, issue.json_pointer) for issue in removed.issues] == [
        ("builder.structural.missing", "/topology")
    ]
    assert all(
        issue.source_ref == target_ref and {"save", "deploy"} <= set(issue.blocks)
        for issue in removed.issues
    )

    # A card left without a topology mode writes the rule without one and says so.
    assert described.widget is not None
    card = described.widget.model_dump(mode="json")
    card["rule"]["topology_mode"] = None
    unset = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=address,
        working=described.working,
        change=card,
    )
    assert unset.working["topology"] == {}
    assert unset.widget is None
    assert ("builder.draft.link_topology_required", "/topology/mode") in {
        (issue.code, issue.json_pointer) for issue in unset.issues
    }


def test_object_edit_refuses_id_changes_and_unknown_controls(
    service: BuilderVisualDraftService,
) -> None:
    target_ref, revision, session = _accepted(service, "earth-luna-dtn")
    rule = _member(session, "/link_rules", "relay_geo_access")
    address = {**_RULE_ADDRESS, "id": "relay_geo_access"}
    described = _object_edit(
        service, target_ref=target_ref, applied_revision=revision, address=address, working=rule
    )

    def controls(*commands: dict[str, Any], applied_revision: int = revision) -> None:
        _object_edit(
            service,
            target_ref=target_ref,
            applied_revision=applied_revision,
            address=address,
            working=described.working,
            change={"kind": "controls", "commands": list(commands)},
        )

    rename = {
        "operation": "set_scalar",
        "control_id": _edited_control(described, "/id", "scalar")["control_id"],
        "value": "renamed",
    }
    with pytest.raises(
        BuilderVisualDraftCommandError, match="rename is an edit of the session YAML"
    ) as refused:
        controls(rename)
    assert refused.value.code == "catalog_authoring.invalid_patch"
    with pytest.raises(BuilderVisualDraftCommandError, match="cannot remove a link rule"):
        controls(
            {
                "operation": "set_present",
                "control_id": _edited_control(described, "", "object")["control_id"],
                "present": False,
            }
        )
    with pytest.raises(BuilderVisualDraftCommandError, match="unknown or stale control id"):
        controls(
            {
                "operation": "set_scalar",
                "control_id": "ctl_00000000000000000000000000000000",
                "value": 7.5,
            }
        )
    # Control ids belong to the accepted revision the tree was built at.
    elevation = {
        "operation": "set_scalar",
        "control_id": _edited_control(described, "/endpoints/0/min_elevation_deg", "scalar")[
            "control_id"
        ],
        "value": 7.5,
    }
    with pytest.raises(BuilderVisualDraftCommandError, match="unknown or stale control id"):
        controls(elevation, applied_revision=revision + 1)
    controls(elevation)

    card = {
        "kind": "link_rule",
        "rule": {"rule_id": "renamed", "topology_mode": "visible_candidates"},
    }
    for working, change in (
        ({**rule, "id": "renamed"}, None),
        (rule, card),
        (rule, {"kind": "routing_domain", "domain": {"domain_id": "relay_geo_access"}}),
    ):
        with pytest.raises(ValidationError):
            _object_edit(
                service,
                target_ref=target_ref,
                applied_revision=revision,
                address=address,
                working=working,
                change=change,
            )

    boundaries = _object_edit(
        service,
        target_ref=target_ref,
        applied_revision=revision,
        address=_BOUNDARIES_ADDRESS,
        working=session["routing"]["boundaries"],
    )
    with pytest.raises(BuilderVisualDraftCommandError, match="cannot remove the routing boundary"):
        _object_edit(
            service,
            target_ref=target_ref,
            applied_revision=revision,
            address=_BOUNDARIES_ADDRESS,
            working=boundaries.working,
            change={
                "kind": "controls",
                "commands": [
                    {
                        "operation": "set_present",
                        "control_id": _edited_control(boundaries, "/boundaries", "sequence")[
                            "control_id"
                        ],
                        "present": False,
                    }
                ],
            },
        )


def _apply_edits(
    service: BuilderVisualDraftService,
    draft: BuilderVisualDraftEnvelope,
    edits: list[dict[str, Any]],
    *,
    expected_draft_revision: int | None = None,
) -> BuilderVisualDraftApplyEditsResult:
    """One apply sent as the page sends it: JSON validated into the request."""
    body = {
        "draft": json.loads(draft.model_dump_json()),
        "expected_draft_revision": (
            draft.draft_revision if expected_draft_revision is None else expected_draft_revision
        ),
        "edits": edits,
    }
    return service.apply_edits(
        BuilderVisualDraftApplyEditsRequest.model_validate_json(json.dumps(body)),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )


def _content_edit(address: dict[str, Any], base: Any, working: Any) -> dict[str, Any]:
    return {"kind": "content", "address": address, "base": base, "working": working}


def _ranged(rule: dict[str, Any]) -> dict[str, Any]:
    return {**rule, "constraints": {**rule.get("constraints", {}), "max_range_km": 12345.0}}


def test_apply_edits_accepts_a_batch_as_the_next_revision_and_saves_it(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    session_path = SHIPPED_ROOT / "sessions/earth-leo-heo-geo-luna-reachability.yaml"
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref=f"nodalarc:sessions/{session_path.name}")
    )
    assert opened.applied_session is not None
    session = opened.applied_session
    rule = _member(session, "/link_rules", "leo_a_access")
    domain = _member(session, "/routing/domains", "luna_domain")
    boundaries = session["routing"]["boundaries"]
    unexported = json.loads(json.dumps(boundaries))
    for export in unexported[0]["export"]:
        export["export_node_loopbacks"] = False
    timers = {"hello_interval_s": 2, "hold_interval_s": 7}

    result = _apply_edits(
        service,
        opened,
        [
            _content_edit({**_RULE_ADDRESS, "id": "leo_a_access"}, rule, _ranged(rule)),
            _content_edit(
                {**_DOMAIN_ADDRESS, "id": "luna_domain"}, domain, {**domain, "timers": timers}
            ),
            _content_edit(_BOUNDARIES_ADDRESS, boundaries, unexported),
        ],
    )

    edited = yaml.safe_load(session_path.read_bytes())
    edited_rule = next(item for item in edited["link_rules"] if item["id"] == "leo_a_access")
    edited_rule["constraints"] = {"max_range_km": 12345.0}
    edited_domain = next(
        item for item in edited["routing"]["domains"] if item["id"] == "luna_domain"
    )
    edited_domain["timers"] = timers
    for export in edited["routing"]["boundaries"][0]["export"]:
        export["export_node_loopbacks"] = False
    expected = canonicalize_persisted_configuration(opened.target_ref, edited)

    assert result.applied is True
    assert [outcome.status for outcome in result.outcomes] == ["applied"] * 3
    assert result.assembly is not None
    accepted = result.assembly.visual_draft
    assert accepted.draft_revision == accepted.applied_revision == opened.draft_revision + 1
    assert accepted.projection_status == "applied"
    assert accepted.control_tree is not None
    assert accepted.control_tree.projection_revision == accepted.applied_revision
    assert accepted.applied_session == expected.canonical_json
    assert accepted.session_yaml == expected.yaml_bytes.decode("utf-8")
    assert set(opened.reserved_authoring_ids) <= set(accepted.reserved_authoring_ids)
    listing = result.assembly.listing
    assert listing is not None
    luna = next(item for item in listing.routing_domains if item.domain_id == "luna_domain")
    assert luna.widget is not None
    assert (luna.widget.hello_interval_s, luna.widget.hold_interval_s) == (2, 7)
    compiled = result.assembly.compile_result
    assert compiled.save_verdict.allowed is True, compiled.issues
    assert compiled.canonical_session_json == expected.canonical_json

    saved = save_builder_session(
        result.assembly.save_request,
        context,
        available_node_count=1_000_000,
        preview_factory=_preview,
    )
    assert saved.session.canonical_json == expected.canonical_json


def test_apply_edits_removes_members_and_drops_an_emptied_routing_section(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(
            source_ref="nodalarc:sessions/earth-leo-heo-geo-luna-reachability.yaml"
        )
    )
    assert opened.applied_session is not None
    session = opened.applied_session
    rule_ids = [rule["id"] for rule in session["link_rules"]]
    domain_ids = [domain["id"] for domain in session["routing"]["domains"]]
    remove_rule = {
        "kind": "list",
        "list_pointer": "/link_rules",
        "ids_before": rule_ids,
        "ids_wanted": [rule_id for rule_id in rule_ids if rule_id != "santiago_geo_access"],
    }
    remove_domains = {
        "kind": "list",
        "list_pointer": "/routing/domains",
        "ids_before": domain_ids,
        "ids_wanted": [],
    }

    # The last domain cannot go while a boundary remains.
    refused = _apply_edits(service, opened, [remove_rule, remove_domains])
    assert refused.applied is False
    assert refused.assembly is None
    assert [outcome.status for outcome in refused.outcomes] == ["applied", "invalid"]
    assert [issue.code for issue in refused.outcomes[1].issues] == [
        "builder.draft.routing_boundaries_remain"
    ]

    result = _apply_edits(
        service,
        opened,
        [
            remove_rule,
            remove_domains,
            _content_edit(_BOUNDARIES_ADDRESS, session["routing"]["boundaries"], []),
        ],
    )
    assert result.applied is True
    assert result.assembly is not None
    accepted = result.assembly.visual_draft
    assert accepted.draft_revision == opened.draft_revision + 1
    assert accepted.applied_session is not None
    assert "routing" not in accepted.applied_session
    assert [rule["id"] for rule in accepted.applied_session["link_rules"]] == [
        rule_id for rule_id in rule_ids if rule_id != "santiago_geo_access"
    ]
    assert result.assembly.listing is not None
    assert result.assembly.listing.routing_domains == ()
    assert result.assembly.listing.boundaries == ()
    # The allocation history never shrinks, so a removed id it held is not minted again.
    assert "luna_domain" in opened.reserved_authoring_ids
    assert set(opened.reserved_authoring_ids) <= set(accepted.reserved_authoring_ids)


def test_apply_edits_refuses_the_whole_batch_for_one_stale_or_invalid_edit(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.applied_session is not None
    access = _member(opened.applied_session, "/link_rules", "leo_access")
    isl = _member(opened.applied_session, "/link_rules", "leo_isl")
    access_address = {**_RULE_ADDRESS, "id": "leo_access"}
    ranged_isl = _content_edit({**_RULE_ADDRESS, "id": "leo_isl"}, isl, _ranged(isl))

    stale = _apply_edits(
        service,
        opened,
        [
            ranged_isl,
            _content_edit(
                access_address, {**access, "topology": {"mode": "nearest_n", "n": 1}}, access
            ),
        ],
    )
    assert stale.applied is False
    assert stale.assembly is None
    assert [outcome.status for outcome in stale.outcomes] == ["applied", "stale"]
    assert [
        (issue.code, issue.draft_path, issue.json_pointer) for issue in stale.outcomes[1].issues
    ] == [("builder.staleness.pending_edit", "pending_edits.1", "/link_rules/0")]

    untopologized = {key: value for key, value in access.items() if key != "topology"}
    invalid = _apply_edits(
        service,
        opened,
        [ranged_isl, _content_edit(access_address, access, untopologized)],
    )
    assert invalid.applied is False
    assert invalid.assembly is None
    assert [outcome.status for outcome in invalid.outcomes] == ["applied", "invalid"]
    assert [
        (issue.code, issue.draft_path, issue.json_pointer) for issue in invalid.outcomes[1].issues
    ] == [("builder.structural.missing", "pending_edits.1", "/link_rules/0/topology")]

    # Neither refusal minted a revision: the opened revision still takes the good edit.
    applied = _apply_edits(service, opened, [ranged_isl])
    assert applied.applied is True
    assert applied.assembly is not None
    assert applied.assembly.visual_draft.draft_revision == opened.draft_revision + 1
    assert applied.assembly.visual_draft.applied_session is not None
    assert _member(applied.assembly.visual_draft.applied_session, "/link_rules", "leo_isl") == (
        _saved_form(LinkRule, _ranged(isl))
    )


def test_apply_edits_is_revision_fenced_and_needs_an_accepted_valid_session(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.applied_session is not None
    isl = _member(opened.applied_session, "/link_rules", "leo_isl")
    ranged_isl = _content_edit({**_RULE_ADDRESS, "id": "leo_isl"}, isl, _ranged(isl))

    with pytest.raises(BuilderVisualDraftCommandError) as stale:
        _apply_edits(
            service,
            opened,
            [ranged_isl],
            expected_draft_revision=opened.draft_revision + 1,
        )
    assert stale.value.code == "catalog_authoring.stale_revision"

    created = service.create(BuilderVisualDraftCreateRequest(session_name="no-accepted-session"))
    with pytest.raises(BuilderVisualDraftCommandError, match="fully applied draft") as unaccepted:
        _apply_edits(service, created, [ranged_isl])
    assert unaccepted.value.code == "catalog_authoring.invalid_graph"

    # Each boundary is valid on its own, but a routing section needs a domain.
    boundary = {
        "over": "leo_isl",
        "adapter": "static_ip",
        "export": [
            {"from": "a_domain", "to": "b_domain", "prefixes": {"aggregate_of": "originated"}}
        ],
    }
    RoutingBoundary.model_validate(boundary)
    refused = _apply_edits(
        service,
        opened,
        [{"kind": "content", "address": _BOUNDARIES_ADDRESS, "working": [boundary]}],
    )
    # The edit created the routing section, so the session's refusal is the edit's.
    assert refused.applied is False and refused.assembly is None and refused.issues == ()
    (outcome,) = refused.outcomes
    assert outcome.status == "invalid"
    assert [(issue.json_pointer, issue.draft_path) for issue in outcome.issues] == [
        ("/routing/domains", "pending_edits.0")
    ]


def _stored_state(draft: BuilderVisualDraftEnvelope) -> dict[str, Any]:
    """What an undo entry holds: the accepted session and its proposals."""
    return {
        "session": draft.applied_session,
        "catalog_documents": [
            json.loads(proposal.model_dump_json()) for proposal in draft.catalog_documents
        ],
    }


def _accept_state(
    service: BuilderVisualDraftService,
    draft: BuilderVisualDraftEnvelope,
    state: dict[str, Any],
    *,
    expected_draft_revision: int | None = None,
) -> BuilderVisualDraftAssemblyResult:
    """One re-accept sent as the page sends it: JSON validated into the request."""
    body = {
        "draft": json.loads(draft.model_dump_json()),
        "expected_draft_revision": (
            draft.draft_revision if expected_draft_revision is None else expected_draft_revision
        ),
        "state": state,
    }
    return service.accept_state(
        BuilderVisualDraftAcceptStateRequest.model_validate_json(json.dumps(body)),
        available_node_count=1_000_000,
        preview_factory=_preview,
    )


def test_accept_state_re_accepts_a_stored_session_and_its_proposals(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.applied_session is not None
    customized = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            segment_id="leo",
            leaf_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        )
    )
    assert customized.applied is True
    assert customized.draft.catalog_documents
    revision = customized.draft.draft_revision

    # Undo of the customize: the opened state becomes the next revision.
    undone = _accept_state(service, customized.draft, _stored_state(opened))
    reverted = undone.visual_draft
    expected = canonicalize_persisted_configuration(opened.target_ref, opened.applied_session)
    assert reverted.draft_revision == reverted.applied_revision == revision + 1
    assert reverted.projection_status == "applied"
    assert reverted.control_tree is not None
    assert reverted.control_tree.projection_revision == reverted.applied_revision
    assert reverted.applied_session == opened.applied_session
    assert reverted.session_yaml == expected.yaml_bytes.decode("utf-8")
    assert reverted.catalog_documents == ()
    assert undone.compile_result.save_verdict.allowed is True, undone.compile_result.issues
    assert undone.compile_result.canonical_session_json == expected.canonical_json

    # The customized state comes back with its forks, which the draft no longer holds.
    redone = _accept_state(service, reverted, _stored_state(customized.draft))
    restored = redone.visual_draft
    assert restored.draft_revision == revision + 2
    assert restored.applied_session == customized.draft.applied_session
    assert restored.catalog_documents == customized.draft.catalog_documents
    assert redone.compile_result.save_verdict.allowed is True, redone.compile_result.issues


def test_accept_state_never_frees_an_allocated_id(service: BuilderVisualDraftService) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )

    def place(draft: BuilderVisualDraftEnvelope) -> BuilderVisualDraftCommandResult:
        return service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command={
                    "operation": "place_space_reference",
                    "source_ref": "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml",
                },
            ),
            available_node_count=1_000_000,
            preview_factory=_preview,
        )

    placed = place(opened)
    assert placed.affected_id == "space-1"
    assert "space-1" not in opened.reserved_authoring_ids

    undone = _accept_state(service, placed.draft, _stored_state(opened)).visual_draft
    assert undone.applied_session == opened.applied_session
    history = placed.draft.reserved_authoring_ids
    assert undone.reserved_authoring_ids[: len(history)] == history

    # The undone segment's id stays taken, so the next placement mints another.
    assert place(undone).affected_id == "space-2"


def test_accept_state_is_revision_fenced_and_refuses_an_invalid_state(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.applied_session is not None
    state = _stored_state(opened)

    with pytest.raises(BuilderVisualDraftCommandError) as stale:
        _accept_state(
            service,
            opened,
            state,
            expected_draft_revision=opened.draft_revision + 1,
        )
    assert stale.value.code == "catalog_authoring.stale_revision"

    created = service.create(BuilderVisualDraftCreateRequest(session_name="no-accepted-session"))
    with pytest.raises(BuilderVisualDraftCommandError, match="fully applied draft") as unaccepted:
        _accept_state(service, created, state)
    assert unaccepted.value.code == "catalog_authoring.invalid_graph"

    session = opened.applied_session
    renamed = {**session, "session": {**session["session"], "name": "another-session"}}
    untopologized = {
        **session,
        "link_rules": [
            {key: value for key, value in rule.items() if key != "topology"}
            for rule in session["link_rules"]
        ],
    }
    for invalid in (renamed, untopologized):
        with pytest.raises(BuilderVisualDraftCommandError, match="not a valid session") as refused:
            _accept_state(service, opened, {"session": invalid})
        assert refused.value.code == "catalog_authoring.invalid_graph"


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
