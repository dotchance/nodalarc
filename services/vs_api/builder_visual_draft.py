# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Backend authority for visual Builder draft creation, opening, and assembly."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Collection, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal, cast

import yaml
from nodalarc.catalog_closure import (
    CatalogReadError,
    CatalogReadView,
    catalog_document_references,
    load_catalog_object,
)
from nodalarc.catalog_refs import CatalogFamily, CatalogRef, SessionRef
from nodalarc.catalog_registry import (
    catalog_family_spec,
    validate_referenced_configuration_document,
)
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogReadSnapshot,
)
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.document_errors import document_errors
from nodalarc.models.builder_api import (
    BuilderCompileRequest,
    BuilderContentEdit,
    BuilderDraftEnvelope,
    BuilderIssue,
    BuilderListEdit,
    BuilderMemberAddress,
    BuilderPendingEdits,
    BuilderProposalAddress,
    BuilderProposedCatalogDocument,
    BuilderSectionAddress,
    BuilderWholeListAddress,
    JsonDocument,
)
from nodalarc.models.builder_controls_api import (
    BuilderControlTree,
    BuilderInsertItemCommand,
    BuilderMoveItemCommand,
    BuilderRemoveItemCommand,
)
from nodalarc.models.builder_visual_api import (
    BuilderVisualAddBoundaryCommand,
    BuilderVisualAddGeneratedSpaceCommand,
    BuilderVisualAddGroundSiteReferenceCommand,
    BuilderVisualAddRoutingDomainCommand,
    BuilderVisualConnectSegmentsCommand,
    BuilderVisualControlMutationRequest,
    BuilderVisualCustomizeChainEntry,
    BuilderVisualCustomizeChainRequest,
    BuilderVisualCustomizeChainResult,
    BuilderVisualDraftAcceptStateRequest,
    BuilderVisualDraftAffectedKind,
    BuilderVisualDraftApplyEditsRequest,
    BuilderVisualDraftApplyEditsResult,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftApplyYamlResult,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualDraftCommand,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCommandResult,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftOpenRequest,
    BuilderVisualDraftRetargetRequest,
    BuilderVisualGroundBoresight,
    BuilderVisualLinkEndpoint,
    BuilderVisualLinkRule,
    BuilderVisualLinkRuleListing,
    BuilderVisualLinkRuleWidget,
    BuilderVisualMintGroundMembersCommand,
    BuilderVisualObjectControlEdit,
    BuilderVisualObjectEditRequest,
    BuilderVisualObjectEditResult,
    BuilderVisualObjectWidget,
    BuilderVisualPhasingMode,
    BuilderVisualPlaceGroundReferenceCommand,
    BuilderVisualPlaceSpaceReferenceCommand,
    BuilderVisualRederiveLinkCommand,
    BuilderVisualRoutingBoundariesWidget,
    BuilderVisualRoutingBoundary,
    BuilderVisualRoutingBoundaryListing,
    BuilderVisualRoutingDomain,
    BuilderVisualRoutingDomainListing,
    BuilderVisualRoutingDomainWidget,
    BuilderVisualSchedulingPreset,
    BuilderVisualSessionListing,
    BuilderVisualSetSchedulingPresetCommand,
    BuilderVisualSetSpacePopulationCommand,
    BuilderVisualWalkerLayoutRequest,
    derive_walker_layout,
)
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.catalog import (
    Constellation,
    Node,
    Site,
    SiteSet,
    SpaceNodeSet,
)
from nodalarc.models.link_rules import (
    LinkRule,
    NearestNTopology,
    NodeSelector,
    TerminalSelector,
    VisibleCandidatesTopology,
)
from nodalarc.models.segment_session import (
    LINK_STATE_PROTOCOLS,
    Addressing,
    AggregateOf,
    AreaAssignment,
    Dispatch,
    RoutingBoundary,
    RoutingDomain,
    RoutingTimers,
    SegmentSessionConfig,
    SessionMeta,
    Simulation,
    TimeConfig,
)
from nodalarc.models.segments import (
    GroundSegment,
    LagrangeSegment,
    SpaceSegment,
)
from nodalarc.resolve_session import SessionResolutionError, endpoint_terminal_role
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from .builder_catalog_draft import forked_document, rewrite_catalog_references
from .builder_compiler import (
    OverlayCatalogReadView,
    PreviewFactory,
    apply_pending_edits,
    canonicalize_persisted_configuration,
    compile_builder_draft,
    place_candidate_issues,
)
from .builder_control_mutation import (
    BuilderControlMutationError,
    apply_builder_control_mutations,
)
from .builder_control_tree import (
    BuilderControlTreeBuild,
    build_model_control_tree,
    build_session_control_tree,
)
from .builder_visual_defaults import (
    DEFAULT_BODY_REF,
    DEFAULT_PHASING_MODE,
    DEFAULT_SCHEDULING_PRESET,
    SINGLE_PLANE_PHASING_MODE,
    scheduling_preset_block,
)
from .builder_yaml import parse_yaml_application
from .catalog_context import CatalogContext

Clock = Callable[[], datetime]

_BUILDER_GENERATED_COMPONENT_FAMILIES = frozenset(
    {"constellations", "orbits", "nodes", "site-sets", "sites"}
)
# The session's name is its identity, which only Save under a new name changes.
BUILDER_VISUAL_SPECIALIZED_FIELDS = frozenset({(SessionMeta, "name")})


class BuilderVisualDraftConflictError(CatalogConflictError):
    """A visual authoring gesture would overwrite an existing user session."""

    def __init__(self, message: str, *, ref: SessionRef) -> None:
        super().__init__(message)
        self.ref = ref


class BuilderVisualDraftCommandError(ValueError):
    """Typed refusal for a visual command that cannot mutate its supplied draft."""

    def __init__(
        self,
        message: str,
        *,
        code: Literal[
            "catalog_authoring.conflict",
            "catalog_authoring.invalid_patch",
            "catalog_authoring.invalid_graph",
            "catalog_authoring.stale_revision",
        ],
        ref: CatalogRef,
        expected_revision: int | str | None = None,
        current_revision: int | str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.ref = ref
        self.expected_revision = expected_revision
        self.current_revision = current_revision


def _identifier(value: str) -> str:
    normalized = "".join(
        character.lower()
        if character.isascii() and (character.isalnum() or character in "_-")
        else "-"
        for character in value
    )
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized.strip("-_")[:48]


def _proposal_owner(session_ref: CatalogRef) -> str:
    """The path segment a session draft's generated components are named under."""
    owner = _identifier(session_ref.relative_path.stem)
    if not owner:
        raise ValueError(f"session target {session_ref} has no identifier to own components")
    return owner


def _component_family(ref: CatalogRef) -> str:
    """The catalog family a component ref names; a ref without one is refused."""
    family = ref.family
    if family is None:
        raise ValueError(f"{ref} names no catalog family")
    return family


def _owned_component_ref(family: str, owner: str, object_id: str) -> CatalogRef:
    """The ref of a component a session draft owns: ``user:{family}/{owner}/{id}.yaml``."""
    return CatalogRef(f"user:{family}/{owner}/{object_id}.yaml")


def _mint_component_ref(
    family: str,
    owner: str,
    stem: str,
    *,
    taken: Callable[[CatalogRef], bool],
) -> CatalogRef:
    """Mint a new owned component ref, suffixed when the plain name is taken."""
    candidate = _owned_component_ref(family, owner, stem)
    if not taken(candidate):
        return candidate
    for suffix in range(2, 1_000):
        candidate = _owned_component_ref(family, owner, f"{stem}-{suffix}")
        if not taken(candidate):
            return candidate
    raise ValueError(f"could not allocate a user ref for {family}/{owner}/{stem}")


def _segment_component_id(session_name: str, segment_id: str) -> str:
    """The id the assembler derives for a segment's generated constellation or site set."""
    return f"{session_name}-{segment_id}"


# The families whose generated ids the assembler derives through one shared
# set (``_Assembly.unique_derived_id``): orbits, constellations and site sets.
_DERIVED_ID_FAMILIES = frozenset({"orbits", "constellations", "site-sets"})


def _ref_taken(
    ref: CatalogRef,
    snapshot: CatalogReadSnapshot,
    proposals: Collection[CatalogRef],
) -> bool:
    """Whether ``ref`` already names a catalog object or one of the draft's proposals."""
    if ref in proposals:
        return True
    try:
        snapshot.get(ref)
    except CatalogNotFoundError:
        return False
    return True


def _assert_visual_draft_ownership(draft: BuilderVisualDraftEnvelope) -> None:
    if draft.source_ref is None:
        if draft.expected_session_revision is not None:
            raise BuilderVisualDraftCommandError(
                "New visual drafts cannot replace an existing session target",
                code="catalog_authoring.invalid_graph",
                ref=draft.target_ref,
            )
        return
    if draft.source_ref == draft.target_ref:
        if draft.source_ref.namespace != "user" or draft.expected_session_revision is None:
            raise BuilderVisualDraftCommandError(
                "In-place visual drafts require a pinned user session revision",
                code="catalog_authoring.invalid_graph",
                ref=draft.target_ref,
            )
        return
    if draft.expected_session_revision is not None:
        raise BuilderVisualDraftCommandError(
            "Copied visual drafts cannot replace an existing session target",
            code="catalog_authoring.conflict",
            ref=draft.target_ref,
            current_revision=draft.expected_session_revision,
        )


@dataclass(frozen=True, slots=True)
class _PlacedSegment:
    segment_id: str
    kind: Literal["space", "ground"]


@dataclass(frozen=True, slots=True)
class _DerivedLinkPhysics:
    role: Literal["access", "isl", "crosslink", "backbone"]
    medium: Literal["rf", "optical"]
    topology_mode: Literal["visible_candidates", "nearest_n"]
    topology_n: int
    formable: bool


def _session_segments(session: SegmentSessionConfig) -> dict[str, _PlacedSegment]:
    """Each segment a session declares, by id, with the kind of node it holds.

    A Lagrange-point segment holds a space node.
    """
    return {
        segment.id: _PlacedSegment(
            segment.id,
            "ground" if isinstance(segment, GroundSegment) else "space",
        )
        for segment in session.segments
    }


def _boundary_may_run_over(rule: LinkRule) -> bool:
    """Whether a routing boundary may run over a link rule: it is enabled and not access.

    The resolver's own rule decides access: an endpoint whose terminal selector
    names the access role. An endpoint that names no single role leaves the
    rule without one. The compile reports the resolver's refusal of that rule.
    """
    if not rule.enabled:
        return False
    try:
        roles = {endpoint_terminal_role(endpoint.terminal) for endpoint in rule.endpoints}
    except SessionResolutionError:
        return False
    return "access" not in roles


def _reserved_authoring_ids(
    draft: BuilderVisualDraftEnvelope,
    pending_edits: BuilderPendingEdits,
) -> set[str]:
    """The ids a gesture may not mint.

    These are the allocation history and every id the accepted session and the
    pending edits define or reference.
    """
    return {
        *draft.reserved_authoring_ids,
        *_current_and_referenced_authoring_ids(
            draft.applied_session,
            pending_edits=pending_edits,
        ),
    }


def _current_and_referenced_authoring_ids(
    session: JsonDocument | None,
    *,
    pending_edits: BuilderPendingEdits = (),
) -> tuple[str, ...]:
    """Every segment, link rule and routing domain id defined or referenced, in order.

    The walk covers a session document and the working JSON of pending edits.
    Incomplete JSON contributes the ids it holds. A routing boundary has no id
    of its own.
    """
    found = [
        *_session_authoring_ids(session),
        *(
            authoring_id
            for edit in pending_edits
            for authoring_id in _pending_edit_authoring_ids(edit)
        ),
    ]
    return tuple(dict.fromkeys(item for item in found if item))


def _json_items(value: JsonValue) -> list[JsonValue]:
    """The members of a JSON list; any other value holds none."""
    return value if isinstance(value, list) else []


def _selector_segment_ids(selector: JsonValue) -> Iterator[str]:
    """Every segment a node selector tree names, inside a `not` as well.

    A new segment that took an id named inside a `not` would be excluded from
    the selection.
    """
    if not isinstance(selector, dict):
        return
    segment = selector.get("segment")
    if isinstance(segment, str):
        yield segment
    for operator in ("all", "any"):
        for child in _json_items(selector.get(operator)):
            yield from _selector_segment_ids(child)
    yield from _selector_segment_ids(selector.get("not"))


def _link_rule_authoring_ids(rule: JsonValue) -> Iterator[str]:
    """A link rule's id and the segments its endpoints and per-segment caps name."""
    if not isinstance(rule, dict):
        return
    rule_id = rule.get("id")
    if isinstance(rule_id, str):
        yield rule_id
    for endpoint in _json_items(rule.get("endpoints")):
        if isinstance(endpoint, dict):
            yield from _selector_segment_ids(endpoint.get("select"))
    constraints = rule.get("constraints")
    if isinstance(constraints, dict):
        caps = constraints.get("max_links_per_node")
        if isinstance(caps, dict):
            yield from caps


def _routing_domain_authoring_ids(domain: JsonValue) -> Iterator[str]:
    """A routing domain's id and the segments its selectors name."""
    if not isinstance(domain, dict):
        return
    domain_id = domain.get("id")
    if isinstance(domain_id, str):
        yield domain_id
    for selector in _json_items(domain.get("selectors")):
        yield from _selector_segment_ids(selector)


def _routing_boundary_authoring_ids(boundary: JsonValue) -> Iterator[str]:
    """The link rule a routing boundary runs over and the domains its exports join."""
    if not isinstance(boundary, dict):
        return
    over = boundary.get("over")
    if isinstance(over, str):
        yield over
    for export in _json_items(boundary.get("export")):
        if isinstance(export, dict):
            for field_name in ("from", "to"):
                domain_id = export.get(field_name)
                if isinstance(domain_id, str):
                    yield domain_id


def _session_authoring_ids(session: JsonDocument | None) -> Iterator[str]:
    """Every segment, link rule and routing domain id a session document holds."""
    if session is None:
        return
    for segment in _json_items(session.get("segments")):
        if isinstance(segment, dict):
            segment_id = segment.get("id")
            if isinstance(segment_id, str):
                yield segment_id
    for rule in _json_items(session.get("link_rules")):
        yield from _link_rule_authoring_ids(rule)
    routing = session.get("routing")
    if isinstance(routing, dict):
        for domain in _json_items(routing.get("domains")):
            yield from _routing_domain_authoring_ids(domain)
        for boundary in _json_items(routing.get("boundaries")):
            yield from _routing_boundary_authoring_ids(boundary)
    addressing = session.get("addressing")
    if isinstance(addressing, dict):
        for assignments in addressing.values():
            for assignment in _json_items(assignments):
                if isinstance(assignment, dict):
                    yield from _selector_segment_ids(assignment.get("applies_to"))


def _pending_edit_authoring_ids(edit: BuilderContentEdit | BuilderListEdit) -> Iterator[str]:
    """The ids one pending edit defines or references."""
    if isinstance(edit, BuilderListEdit):
        yield from edit.ids_before
    elif isinstance(edit.address, BuilderWholeListAddress):
        for boundary in _json_items(edit.working):
            yield from _routing_boundary_authoring_ids(boundary)
    elif isinstance(edit.address, BuilderSectionAddress | BuilderProposalAddress):
        # A section names no session object; a proposal's ids are its catalog's.
        return
    elif edit.address.list_pointer == "/segments":
        segment_id = edit.working.get("id") if isinstance(edit.working, dict) else None
        if isinstance(segment_id, str):
            yield segment_id
    elif edit.address.list_pointer == "/link_rules":
        yield from _link_rule_authoring_ids(edit.working)
    else:
        yield from _routing_domain_authoring_ids(edit.working)


def _next_number(prefix: str, values: set[str]) -> int:
    number = 1
    while f"{prefix}-{number}" in values:
        number += 1
    return number


def _derive_link_physics(
    world: BuilderWorld,
    first: _PlacedSegment,
    second: _PlacedSegment,
) -> _DerivedLinkPhysics:
    pairs_by_segment = {
        segment.segment_id: {
            (capability.endpoint_role, capability.medium) for capability in segment.capabilities
        }
        for segment in world.segments
    }
    first_pairs = pairs_by_segment.get(first.segment_id)
    second_pairs = pairs_by_segment.get(second.segment_id)
    preferences: tuple[
        tuple[
            Literal["access", "isl", "crosslink", "backbone"],
            Literal["visible_candidates", "nearest_n"],
            int,
        ],
        ...,
    ]
    if first.segment_id == second.segment_id:
        preferences = (("isl", "nearest_n", 2), ("crosslink", "nearest_n", 2))
    elif first.kind == "space" and second.kind == "space":
        preferences = (("crosslink", "nearest_n", 1), ("isl", "nearest_n", 1))
    else:
        preferences = (("access", "visible_candidates", 1),)
    for role, topology_mode, topology_n in preferences:
        for medium in ("optical", "rf"):
            token = (role, medium)
            if (
                first_pairs is not None
                and second_pairs is not None
                and token in first_pairs
                and token in second_pairs
            ):
                return _DerivedLinkPhysics(
                    role=role,
                    medium=medium,
                    topology_mode=topology_mode,
                    topology_n=topology_n,
                    formable=True,
                )
    role, topology_mode, topology_n = preferences[0]
    return _DerivedLinkPhysics(
        role=role,
        medium="rf" if role == "access" else "optical",
        topology_mode=topology_mode,
        topology_n=topology_n,
        formable=False,
    )


def _physics_notice(physics: _DerivedLinkPhysics) -> str:
    topology = (
        f" · nearest-{physics.topology_n}"
        if physics.topology_mode == "nearest_n"
        else " · all visible pairs"
    )
    warning = "" if physics.formable else " — WARNING: neither side has matching terminals"
    return f"re-derived: {physics.role} · {physics.medium}{topology}{warning}"


def _issue(
    code: str,
    message: str,
    *,
    target_ref: SessionRef,
    draft_path: str,
) -> BuilderIssue:
    return BuilderIssue(
        code=code,
        stage="draft",
        severity="error",
        message=message,
        blocks=("save", "deploy"),
        source_ref=str(target_ref),
        draft_path=draft_path,
    )


def _json_pointer(parts: tuple[object, ...]) -> str:
    if not parts:
        return ""
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _object_issue(code: str, message: str, *pointer: str | int) -> BuilderIssue:
    """A missing field of one grammar object, named by its pointer within that object."""
    return BuilderIssue(
        code=code,
        stage="draft",
        severity="error",
        message=message,
        blocks=("save", "deploy"),
        json_pointer=_json_pointer(pointer),
    )


def _endpoint_document(
    endpoint: BuilderVisualLinkEndpoint,
    issues: list[BuilderIssue],
    *,
    at: tuple[str | int, ...],
) -> JsonDocument:
    segment: JsonDocument = {}
    if endpoint.segment_id:
        segment["segment"] = endpoint.segment_id
    selector = segment
    segment_at: tuple[str | int, ...] = (*at, "select", "segment")
    if endpoint.tag:
        selector = {"all": [segment, {"tag": endpoint.tag}]}
        segment_at = (*at, "select", "all", 0, "segment")
    if not endpoint.segment_id:
        issues.append(
            _object_issue(
                "builder.draft.link_endpoint_segment_required",
                "Link endpoint segment is required",
                *segment_at,
            )
        )
    predicates: list[JsonValue] = []
    for index, (field_name, value) in enumerate(
        (("role", endpoint.role), ("medium", endpoint.medium))
    ):
        if value is None:
            predicates.append({})
            issues.append(
                _object_issue(
                    f"builder.draft.link_endpoint_{field_name}_required",
                    f"Link endpoint {field_name} is required",
                    *at,
                    "terminal",
                    "all",
                    index,
                    field_name,
                )
            )
        else:
            predicates.append({field_name: value})
    document: JsonDocument = {"select": selector, "terminal": {"all": predicates}}
    if endpoint.min_elevation_deg is not None:
        document["min_elevation_deg"] = endpoint.min_elevation_deg
    return document


def _link_rule_document(
    rule: BuilderVisualLinkRule,
) -> tuple[JsonDocument, tuple[BuilderIssue, ...]]:
    """The grammar link rule a link card states, with an issue for each field it leaves absent."""
    issues: list[BuilderIssue] = []
    document: JsonDocument = {}
    if rule.rule_id:
        document["id"] = rule.rule_id
    else:
        issues.append(
            _object_issue("builder.draft.link_rule_id_required", "Link rule id is required", "id")
        )
    if not rule.enabled:
        document["enabled"] = False
    topology: JsonDocument = {}
    if rule.topology_mode is None:
        issues.append(
            _object_issue(
                "builder.draft.link_topology_required",
                "Link topology mode is required",
                "topology",
                "mode",
            )
        )
    else:
        topology["mode"] = rule.topology_mode
        if rule.topology_mode == "nearest_n":
            if rule.topology_n is None:
                issues.append(
                    _object_issue(
                        "builder.draft.link_topology_n_required",
                        "nearest_n topology requires n",
                        "topology",
                        "n",
                    )
                )
            else:
                topology["n"] = rule.topology_n
    document["topology"] = topology
    document["endpoints"] = [
        _endpoint_document(rule.a, issues, at=("endpoints", 0)),
        _endpoint_document(rule.b, issues, at=("endpoints", 1)),
    ]
    if rule.max_range_km is not None:
        document["constraints"] = {"max_range_km": rule.max_range_km}
    return document, tuple(issues)


def _routing_domain_document(
    domain: BuilderVisualRoutingDomain,
) -> tuple[JsonDocument, tuple[BuilderIssue, ...]]:
    """The grammar routing domain a card states, with an issue for each absent required field."""
    issues: list[BuilderIssue] = []
    document: JsonDocument = {}
    if domain.domain_id:
        document["id"] = domain.domain_id
    else:
        issues.append(
            _object_issue(
                "builder.draft.routing_domain_id_required",
                "Routing domain id is required",
                "id",
            )
        )
    if domain.protocol is None:
        issues.append(
            _object_issue(
                "builder.draft.routing_protocol_required",
                "Routing protocol is required",
                "protocol",
            )
        )
    else:
        document["protocol"] = domain.protocol
    members = domain.member_segment_ids
    if not members:
        issues.append(
            _object_issue(
                "builder.draft.routing_members_required",
                "Routing domains require members; the domain was not omitted",
                "selectors",
            )
        )
    else:
        # One member is its own selector; several are the branches of one `any`.
        listed_at: tuple[str | int, ...] = (
            ("selectors",) if len(members) == 1 else ("selectors", 0, "any")
        )
        selectors: list[JsonValue] = []
        for index, member in enumerate(members):
            if member:
                selectors.append({"segment": member})
            else:
                selectors.append({})
                issues.append(
                    _object_issue(
                        "builder.draft.routing_member_segment_required",
                        "Routing domain member segment is required",
                        *listed_at,
                        index,
                        "segment",
                    )
                )
        document["selectors"] = selectors if len(selectors) == 1 else [{"any": selectors}]
    if domain.protocol in LINK_STATE_PROTOCOLS:
        document["area_assignment"] = {"strategy": "flat"}
    # Each interval the card leaves empty takes its grammar default.
    timers: JsonDocument = {
        field_name: value
        for field_name, value in (
            ("hello_interval_s", domain.hello_interval_s),
            ("hold_interval_s", domain.hold_interval_s),
        )
        if value is not None
    }
    if timers:
        document["timers"] = timers
    return document, tuple(issues)


def _boundary_exchange(
    source: str,
    target: str,
    *,
    export_node_loopbacks: bool,
) -> JsonDocument:
    exchange: JsonDocument = {}
    if source:
        exchange["from"] = source
    if target:
        exchange["to"] = target
    exchange["prefixes"] = {"aggregate_of": "originated"}
    exchange["export_node_loopbacks"] = export_node_loopbacks
    return exchange


def _routing_boundary_document(
    boundary: BuilderVisualRoutingBoundary,
    issues: list[BuilderIssue],
    *,
    at: tuple[str | int, ...],
) -> JsonDocument:
    """The grammar boundary one boundary card states, with an issue for each absent field."""
    document: JsonDocument = {}
    if boundary.over_rule_id:
        document["over"] = boundary.over_rule_id
    else:
        issues.append(
            _object_issue(
                "builder.draft.boundary_over_required",
                "Boundary link rule is required",
                *at,
                "over",
            )
        )
    if boundary.adapter is None:
        issues.append(
            _object_issue(
                "builder.draft.routing_adapter_required",
                "Routing boundary adapter is required",
                *at,
                "adapter",
            )
        )
    else:
        document["adapter"] = boundary.adapter
    # A boundary domain is the source of one export and the target of the other.
    # A missing domain therefore leaves one field absent in each export.
    for name, domain_id, reverse in (
        ("from", boundary.from_domain_id, "to"),
        ("to", boundary.to_domain_id, "from"),
    ):
        if domain_id:
            continue
        for export, field_name in ((0, name), (1, reverse)):
            issues.append(
                _object_issue(
                    f"builder.draft.boundary_{name}_domain_required",
                    f"Boundary {name} domain is required",
                    *at,
                    "export",
                    export,
                    field_name,
                )
            )
    document["export"] = [
        _boundary_exchange(
            boundary.from_domain_id,
            boundary.to_domain_id,
            export_node_loopbacks=boundary.export_node_loopbacks,
        ),
        _boundary_exchange(
            boundary.to_domain_id,
            boundary.from_domain_id,
            export_node_loopbacks=boundary.export_node_loopbacks,
        ),
    ]
    return document


def _routing_boundary_documents(
    boundaries: tuple[BuilderVisualRoutingBoundary, ...],
) -> tuple[list[JsonValue], tuple[BuilderIssue, ...]]:
    """The grammar boundary list the boundary cards state, with an issue for each absent field."""
    issues: list[BuilderIssue] = []
    documents: list[JsonValue] = [
        _routing_boundary_document(boundary, issues, at=(index,))
        for index, boundary in enumerate(boundaries)
    ]
    return documents, tuple(issues)


def _is_builder_owned_component_ref(ref: CatalogRef, *, owner: str) -> bool:
    relative_parts = ref.relative_path.parts
    return (
        ref.namespace == "user"
        and ref.family in _BUILDER_GENERATED_COMPONENT_FAMILIES
        and len(relative_parts) >= 3
        and relative_parts[1] == owner
    )


def _builder_generated_proposal_refs(
    proposals: tuple[BuilderProposedCatalogDocument, ...],
    *,
    owner: str,
) -> frozenset[CatalogRef]:
    """Identify the complete generated proposal graph owned by one session draft."""

    by_ref = {proposal.ref: proposal for proposal in proposals}
    pending = [
        proposal.ref
        for proposal in proposals
        if _is_builder_owned_component_ref(proposal.ref, owner=owner)
        and proposal.origin == "generated"
    ]
    generated: set[CatalogRef] = set()
    while pending:
        ref = pending.pop()
        if ref in generated:
            continue
        generated.add(ref)
        proposal = by_ref[ref]
        try:
            model = catalog_family_spec(cast(CatalogFamily, ref.family)).validate_document(
                proposal.document
            )
        except ValidationError, TypeError, ValueError:
            continue
        for dependency in catalog_document_references(model):
            if (
                dependency in by_ref
                and dependency not in generated
                and _is_builder_owned_component_ref(dependency, owner=owner)
                and by_ref[dependency].origin == "generated"
            ):
                pending.append(dependency)
    return frozenset(generated)


def _minted_site(
    site_id: str,
    *,
    display_name: str,
    node_ref: CatalogRef,
    installed: Mapping[str, int],
    boresights: Mapping[str, BuilderVisualGroundBoresight],
    body_ref: CatalogRef,
    lat_deg: float,
    lon_deg: float,
    alt_m: float,
) -> JsonDocument:
    """A complete site document from one typed location and the node it installs."""
    return {
        "site": {
            "id": site_id,
            "display_name": display_name,
            "ethernet": [{"id": "lan0"}],
            "nodes": [
                {
                    "id": "gw1",
                    "node": str(node_ref),
                    "payloads": {},
                    "terminals": {
                        mount: {
                            "installed_count": count,
                            **(
                                {
                                    "capabilities": {
                                        "boresight": boresights[mount].model_dump(mode="json")
                                    }
                                }
                                if mount in boresights
                                else {}
                            ),
                        }
                        for mount, count in installed.items()
                    },
                    "interfaces": {"terr0": "lan0"},
                }
            ],
            "frame": {"body_fixed": {"body": str(body_ref)}},
            "location": {"lat_deg": lat_deg, "lon_deg": lon_deg, "alt_m": alt_m},
        }
    }


def _node_selector_projection(selector: NodeSelector) -> tuple[str, str | None] | None:
    if selector.segment is not None:
        return selector.segment, None
    if selector.all is None or len(selector.all) != 2:
        return None
    segment = next((item.segment for item in selector.all if item.segment is not None), None)
    tag = next((item.tag for item in selector.all if item.tag is not None), None)
    if segment is None or tag is None:
        return None
    if any(item.segment is None and item.tag is None for item in selector.all):
        return None
    return segment, tag


def _terminal_selector_projection(
    selector: TerminalSelector,
) -> tuple[Any, Any] | None:
    if selector.all is None or len(selector.all) != 2:
        return None
    role = next((item.role for item in selector.all if item.role is not None), None)
    medium = next((item.medium for item in selector.all if item.medium is not None), None)
    if role is None or medium is None:
        return None
    if any(item.role is None and item.medium is None for item in selector.all):
        return None
    return role, medium


def _saves_unchanged(
    original: BaseModel,
    document: JsonValue,
    issues: tuple[BuilderIssue, ...],
) -> bool:
    """Whether a card's grammar JSON saves as exactly the object the card was read from.

    A card is offered only for such an object. Any other shape would be rewritten
    in the card's own form by an edit to one of its fields.
    """
    if issues:
        return False
    saved = original.model_dump(mode="json", by_alias=True, exclude_none=True)
    written = type(original).model_validate(document)
    return written.model_dump(mode="json", by_alias=True, exclude_none=True) == saved


def _link_projection(rule: LinkRule) -> BuilderVisualLinkRule | None:
    if not isinstance(rule.topology, VisibleCandidatesTopology | NearestNTopology):
        return None
    if rule.tags:
        return None
    if rule.constraints is not None and (
        rule.constraints.max_links_per_node is not None
        or rule.constraints.require_mutual_visibility is not None
    ):
        return None
    endpoints: list[BuilderVisualLinkEndpoint] = []
    for endpoint in rule.endpoints:
        node = _node_selector_projection(endpoint.select)
        terminal = _terminal_selector_projection(endpoint.terminal)
        if node is None or terminal is None:
            return None
        endpoints.append(
            BuilderVisualLinkEndpoint(
                segment_id=node[0],
                tag=node[1],
                role=terminal[0],
                medium=terminal[1],
                min_elevation_deg=endpoint.min_elevation_deg,
            )
        )
    projected = BuilderVisualLinkRule(
        rule_id=rule.id,
        enabled=rule.enabled,
        a=endpoints[0],
        b=endpoints[1],
        topology_mode=rule.topology.mode,
        topology_n=rule.topology.n if isinstance(rule.topology, NearestNTopology) else None,
        max_range_km=rule.constraints.max_range_km if rule.constraints is not None else None,
    )
    if not _saves_unchanged(rule, *_link_rule_document(projected)):
        return None
    return projected


def _routing_member_segments(domain: RoutingDomain) -> tuple[str, ...] | None:
    if len(domain.selectors) == 1 and domain.selectors[0].any is not None:
        selectors = domain.selectors[0].any
    else:
        selectors = domain.selectors
    if any(selector.segment is None for selector in selectors):
        return None
    return tuple(cast(str, selector.segment) for selector in selectors)


def _routing_domain_projection(domain: RoutingDomain) -> BuilderVisualRoutingDomain | None:
    members = _routing_member_segments(domain)
    if members is None:
        return None
    if domain.capabilities is not None:
        return None
    if domain.protocol in LINK_STATE_PROTOCOLS:
        if domain.area_assignment != AreaAssignment(strategy="flat"):
            return None
    elif domain.area_assignment is not None:
        return None
    if domain.timers is not None and domain.timers != RoutingTimers(
        hello_interval_s=domain.timers.hello_interval_s,
        hold_interval_s=domain.timers.hold_interval_s,
    ):
        return None
    projected = BuilderVisualRoutingDomain(
        domain_id=domain.id,
        protocol=domain.protocol,
        member_segment_ids=members,
        hello_interval_s=domain.timers.hello_interval_s if domain.timers is not None else None,
        hold_interval_s=domain.timers.hold_interval_s if domain.timers is not None else None,
    )
    if not _saves_unchanged(domain, *_routing_domain_document(projected)):
        return None
    return projected


def _routing_boundary_projection(
    boundary: RoutingBoundary,
    *,
    index: int,
) -> BuilderVisualRoutingBoundary | None:
    if len(boundary.export) != 2:
        return None
    first, second = boundary.export
    if first.from_ != second.to or first.to != second.from_:
        return None
    first_loopbacks = first.export_node_loopbacks
    second_loopbacks = second.export_node_loopbacks
    if first_loopbacks != second_loopbacks:
        return None
    if any(
        not isinstance(export.prefixes, AggregateOf)
        or export.prefixes.aggregate_of != "originated"
        or export.install_via is not None
        for export in boundary.export
    ):
        return None
    projected = BuilderVisualRoutingBoundary(
        boundary_id=f"boundary-{index + 1}",
        over_rule_id=boundary.over,
        adapter=boundary.adapter,
        from_domain_id=first.from_,
        to_domain_id=first.to,
        export_node_loopbacks=first_loopbacks is not False,
    )
    documents, issues = _routing_boundary_documents((projected,))
    if not _saves_unchanged(boundary, documents[0], issues):
        return None
    return projected


def _named_segments(selectors: tuple[NodeSelector, ...]) -> tuple[str, ...]:
    """The segments selectors name outside every `not`, in order of first appearance.

    A segment named inside a `not` is excluded from the selection, so it is not listed.
    """
    named: list[str] = []
    for selector in selectors:
        if selector.segment is not None:
            named.append(selector.segment)
        named.extend(_named_segments((*(selector.all or ()), *(selector.any or ()))))
    return tuple(dict.fromkeys(named))


def _accepted_session_listing(session: SegmentSessionConfig) -> BuilderVisualSessionListing:
    """Every rule, domain and boundary of the accepted session, each with its card value."""
    routing = session.routing
    return BuilderVisualSessionListing(
        link_rules=tuple(
            BuilderVisualLinkRuleListing(
                rule_id=rule.id,
                segment_ids=_named_segments(tuple(endpoint.select for endpoint in rule.endpoints)),
                widget=_link_projection(rule),
            )
            for rule in session.link_rules or ()
        ),
        routing_domains=tuple(
            BuilderVisualRoutingDomainListing(
                domain_id=domain.id,
                protocol=domain.protocol,
                segment_ids=_named_segments(domain.selectors),
                widget=_routing_domain_projection(domain),
            )
            for domain in (routing.domains if routing is not None else ())
        ),
        boundaries=tuple(
            BuilderVisualRoutingBoundaryListing(
                index=index,
                over_rule_id=boundary.over,
                domain_ids=tuple(
                    dict.fromkeys(
                        domain_id
                        for export in boundary.export
                        for domain_id in (export.from_, export.to)
                    )
                ),
                widget=_routing_boundary_projection(boundary, index=index),
            )
            for index, boundary in enumerate(
                routing.boundaries or () if routing is not None else ()
            )
        ),
    )


class _RoutingBoundaryList(BaseModel):
    """Every routing boundary of the session, edited as one list."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    boundaries: tuple[RoutingBoundary, ...]


@dataclass(frozen=True, slots=True)
class _ObjectEditor:
    """The field editor of one kind of addressed object."""

    root_model: type[BaseModel]
    root_label: str
    specialized_fields: frozenset[tuple[type[BaseModel], str]]
    # A control at one of these pointers is neither set nor removed: the editor's
    # root, and the object's id or the list itself. Items of a list stay editable.
    protected_pointers: frozenset[str]
    protected_reason: str


_OBJECT_EDITORS: Mapping[str, _ObjectEditor] = MappingProxyType(
    {
        "/link_rules": _ObjectEditor(
            root_model=LinkRule,
            root_label="Link rule",
            specialized_fields=frozenset({(LinkRule, "id")}),
            protected_pointers=frozenset({"", "/id"}),
            protected_reason=(
                "A field edit cannot remove a link rule or change its id; "
                "a rename is an edit of the session YAML"
            ),
        ),
        "/routing/domains": _ObjectEditor(
            root_model=RoutingDomain,
            root_label="Routing domain",
            specialized_fields=frozenset({(RoutingDomain, "id")}),
            protected_pointers=frozenset({"", "/id"}),
            protected_reason=(
                "A field edit cannot remove a routing domain or change its id; "
                "a rename is an edit of the session YAML"
            ),
        ),
        "/routing/boundaries": _ObjectEditor(
            root_model=_RoutingBoundaryList,
            root_label="Routing boundaries",
            specialized_fields=frozenset(),
            protected_pointers=frozenset({"", "/boundaries"}),
            protected_reason=(
                "A field edit cannot remove the routing boundary list; "
                "it removes the boundaries one at a time"
            ),
        ),
    }
)
_LIST_ITEM_COMMANDS = (BuilderInsertItemCommand, BuilderRemoveItemCommand, BuilderMoveItemCommand)


_SECTION_EDITORS: Mapping[str, _ObjectEditor] = MappingProxyType(
    {
        "/session": _ObjectEditor(
            root_model=SessionMeta,
            root_label="Session",
            specialized_fields=frozenset({(SessionMeta, "name")}),
            protected_pointers=frozenset({"", "/name"}),
            protected_reason=(
                "A field edit cannot change the session's name; save under a new name renames it"
            ),
        ),
        **{
            pointer: _ObjectEditor(
                root_model=model,
                root_label=label,
                specialized_fields=frozenset(),
                protected_pointers=frozenset({""}),
                protected_reason=f"A field edit cannot remove the {label.lower()} section",
            )
            for pointer, model, label in (
                ("/time", TimeConfig, "Time"),
                ("/simulation", Simulation, "Simulation"),
                ("/addressing", Addressing, "Addressing"),
                ("/dispatch", Dispatch, "Dispatch"),
            )
        },
    }
)


def _segment_model(working: JsonDocument | list[JsonValue]) -> type[BaseModel]:
    """A segment's grammar model, by the form its JSON states."""
    if isinstance(working, dict) and "placement" in working:
        return GroundSegment
    if isinstance(working, dict) and "frame" in working:
        return LagrangeSegment
    return SpaceSegment


def _object_editor(
    address: BuilderMemberAddress | BuilderWholeListAddress | BuilderSectionAddress,
    working: JsonDocument | list[JsonValue],
) -> _ObjectEditor:
    if isinstance(address, BuilderWholeListAddress):
        return _OBJECT_EDITORS[address.pointer]
    if isinstance(address, BuilderSectionAddress):
        return _SECTION_EDITORS[address.pointer]
    if address.list_pointer == "/segments":
        # A segment's id is an ordinary field: a rename renames the segment.
        return _ObjectEditor(
            root_model=_segment_model(working),
            root_label="Segment",
            specialized_fields=frozenset(),
            protected_pointers=frozenset({""}),
            protected_reason="A field edit cannot remove a segment; Remove does",
        )
    return _OBJECT_EDITORS[address.list_pointer]


def _editor_document(
    address: BuilderMemberAddress | BuilderWholeListAddress | BuilderSectionAddress,
    working: JsonDocument | list[JsonValue],
) -> JsonDocument:
    """The document an object's field editor covers: the object, or the list inside its root."""
    if isinstance(address, BuilderWholeListAddress):
        return {"boundaries": cast(list[JsonValue], working)}
    return cast(JsonDocument, working)


def _object_control_build(
    address: BuilderMemberAddress | BuilderWholeListAddress | BuilderSectionAddress,
    working: JsonDocument | list[JsonValue],
    *,
    applied_revision: int,
) -> BuilderControlTreeBuild:
    editor = _object_editor(address, working)
    return build_model_control_tree(
        editor.root_model,
        _editor_document(address, working),
        projection_revision=applied_revision,
        root_label=editor.root_label,
        specialized_fields=editor.specialized_fields,
    )


def _object_validation_issues(
    error: ValidationError,
    *,
    document: JsonValue,
    at: tuple[str | int, ...],
    source_ref: str,
) -> list[BuilderIssue]:
    """The grammar's refusal of one object ``document``, which sits at ``at`` in the
    working JSON, named by pointers within the working JSON."""
    return [
        BuilderIssue(
            code=f"builder.structural.{item.type}",
            stage="structural",
            severity="error",
            message=item.message,
            blocks=("save", "deploy"),
            source_ref=source_ref,
            json_pointer=_json_pointer(at) + item.pointer or None,
        )
        for item in document_errors(error, document)
    ]


def _classified_object(
    address: BuilderMemberAddress | BuilderWholeListAddress | BuilderSectionAddress,
    working: JsonDocument | list[JsonValue],
    *,
    source_ref: str,
) -> tuple[BuilderVisualObjectWidget | None, list[BuilderIssue]]:
    """Validate working JSON as its standalone grammar model, then offer the card for it.

    A card is offered only for an object that validates and that the card writes
    back unchanged. Each boundary of the list is validated and classified on its own.
    """
    if isinstance(address, BuilderWholeListAddress):
        issues: list[BuilderIssue] = []
        cards: list[BuilderVisualRoutingBoundary | None] = []
        for index, item in enumerate(cast(list[JsonValue], working)):
            try:
                boundary = RoutingBoundary.model_validate(item)
            except ValidationError as error:
                issues.extend(
                    _object_validation_issues(
                        error, document=item, at=(index,), source_ref=source_ref
                    )
                )
                cards.append(None)
            else:
                cards.append(_routing_boundary_projection(boundary, index=index))
        widget = BuilderVisualRoutingBoundariesWidget(
            kind="routing_boundaries", boundaries=tuple(cards)
        )
        return widget, issues
    try:
        validated = _object_editor(address, working).root_model.model_validate(working)
    except ValidationError as error:
        return None, _object_validation_issues(
            error, document=working, at=(), source_ref=source_ref
        )
    if not isinstance(validated, LinkRule | RoutingDomain):
        # Segments and sections are edited in the field editor; no card writes them.
        return None, []
    if isinstance(validated, LinkRule):
        rule_card = _link_projection(validated)
        if rule_card is None:
            return None, []
        return BuilderVisualLinkRuleWidget(kind="link_rule", rule=rule_card), []
    domain_card = _routing_domain_projection(cast(RoutingDomain, validated))
    if domain_card is None:
        return None, []
    return BuilderVisualRoutingDomainWidget(kind="routing_domain", domain=domain_card), []


def _carded_boundaries(
    working: list[JsonValue],
    cards: BuilderVisualRoutingBoundariesWidget,
) -> tuple[list[JsonValue], tuple[BuilderIssue, ...]]:
    """The working boundary list with each boundary that has a card value written by its card."""
    issues: list[BuilderIssue] = []
    written: list[JsonValue] = [
        item if card is None else _routing_boundary_document(card, issues, at=(index,))
        for index, (item, card) in enumerate(zip(working, cards.boundaries, strict=True))
    ]
    return written, tuple(issues)


def _applied_control_tree(session: SegmentSessionConfig, revision: int) -> BuilderControlTree:
    """The control tree of an applied session, bound to its revision."""

    return build_session_control_tree(
        session,
        projection_revision=revision,
        specialized_fields=BUILDER_VISUAL_SPECIALIZED_FIELDS,
    ).tree


def _validated_catalog_model(snapshot: CatalogReadSnapshot, ref: CatalogRef) -> BaseModel:
    document = snapshot.get(ref)
    data = load_configuration_yaml(document.content)
    family = cast(CatalogFamily, ref.family)
    return catalog_family_spec(family).validate_document(data)


def _reachable_proposals(
    session: SegmentSessionConfig,
    proposals: tuple[BuilderProposedCatalogDocument, ...],
    snapshot: CatalogReadSnapshot,
) -> tuple[BuilderProposedCatalogDocument, ...]:
    """The proposals the session reaches through its references, in their order."""
    proposed_by_ref = {proposal.ref: proposal for proposal in proposals}
    reached_proposals: set[CatalogRef] = set()
    visited: set[CatalogRef] = set()

    def visit(ref: CatalogRef) -> None:
        if ref in visited:
            return
        visited.add(ref)
        proposal = proposed_by_ref.get(ref)
        try:
            if proposal is None:
                model = _validated_catalog_model(snapshot, ref)
            else:
                reached_proposals.add(ref)
                model = catalog_family_spec(cast(CatalogFamily, ref.family)).validate_document(
                    proposal.document
                )
        except CatalogNotFoundError, ValidationError, TypeError, ValueError, yaml.YAMLError:
            return
        for dependency in catalog_document_references(model):
            visit(dependency)

    for ref in catalog_document_references(session):
        visit(ref)
    return tuple(proposal for proposal in proposals if proposal.ref in reached_proposals)


def _dependency_paths(
    snapshot: CatalogReadSnapshot,
    root_ref: CatalogRef,
    leaf_ref: CatalogRef,
) -> tuple[tuple[CatalogRef, ...], ...]:
    references: dict[CatalogRef, tuple[CatalogRef, ...]] = {}

    def children(ref: CatalogRef) -> tuple[CatalogRef, ...]:
        if ref not in references:
            model = _validated_catalog_model(snapshot, ref)
            references[ref] = tuple(dict.fromkeys(catalog_document_references(model)))
        return references[ref]

    found: list[tuple[CatalogRef, ...]] = []

    def visit(current: CatalogRef, path: tuple[CatalogRef, ...]) -> None:
        if current == leaf_ref:
            found.append(path)
            return
        if len(path) >= 64:
            return
        for child in children(current):
            if child not in path:
                visit(child, (*path, child))

    visit(root_ref, (root_ref,))
    return tuple(found)


def _catalog_object(view: CatalogReadView, ref: CatalogRef) -> tuple[str, BaseModel]:
    """One catalog component as its wrapper key and validated model."""
    wrapper, source = load_catalog_object(ref, view)
    if wrapper is None:
        raise ValueError("a session document cannot be forked as a component")
    return wrapper, source


def _command_base_session(draft: BuilderVisualDraftEnvelope) -> JsonDocument:
    """The session a command edits: the accepted one, or the skeleton a new draft
    starts from, which its first segment gesture completes."""
    if draft.applied_session is not None:
        return draft.applied_session
    # The envelope guarantees a draft with no accepted session holds a skeleton.
    return cast(JsonDocument, load_configuration_yaml(draft.session_yaml))


def _accepted_draft(
    draft: BuilderVisualDraftEnvelope,
    session: SegmentSessionConfig,
    proposals: tuple[BuilderProposedCatalogDocument, ...],
    snapshot: CatalogReadSnapshot,
) -> BuilderVisualDraftEnvelope:
    """A validated session accepted as the draft's next revision.

    Proposals the session does not reach are dropped. The allocation history
    gains the ids the session holds and never loses one.
    """
    reachable_proposals = _reachable_proposals(session, proposals, snapshot)
    accepted_session = cast(
        JsonDocument,
        session.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    next_revision = draft.draft_revision + 1
    reserved_authoring_ids = tuple(
        dict.fromkeys(
            (
                *draft.reserved_authoring_ids,
                *_current_and_referenced_authoring_ids(accepted_session),
            )
        )
    )
    canonical = canonicalize_persisted_configuration(draft.target_ref, accepted_session)
    return draft.model_copy(
        update={
            "draft_revision": next_revision,
            "catalog_documents": reachable_proposals,
            "reserved_authoring_ids": reserved_authoring_ids,
            "session_yaml": canonical.yaml_bytes.decode("utf-8"),
            "applied_revision": next_revision,
            "applied_session": accepted_session,
            "control_tree": _applied_control_tree(session, next_revision),
        }
    )


def _compile_visual_application(
    visual_draft: BuilderVisualDraftEnvelope,
    snapshot: CatalogReadSnapshot,
    *,
    available_node_count: int,
    preview_factory: PreviewFactory | None,
) -> BuilderVisualDraftAssemblyResult:
    """Compile a draft's accepted session, or a new draft's skeleton, and list its objects."""
    compile_result = compile_builder_draft(
        BuilderCompileRequest(
            draft=BuilderDraftEnvelope(
                draft_revision=visual_draft.draft_revision,
                state={
                    "session": _command_base_session(visual_draft),
                    "catalog_documents": visual_draft.catalog_documents,
                },
            ),
            target_ref=visual_draft.target_ref,
        ),
        snapshot,
        available_node_count=available_node_count,
        preview_factory=preview_factory,
    )
    listing = (
        None
        if visual_draft.applied_session is None
        else _accepted_session_listing(
            SegmentSessionConfig.model_validate(visual_draft.applied_session)
        )
    )
    return BuilderVisualDraftAssemblyResult(
        visual_draft=visual_draft,
        compile_result=compile_result,
        listing=listing,
    )


class BuilderVisualDraftService:
    """Scoped application service over one server-selected catalog context."""

    def __init__(
        self,
        context: CatalogContext,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._context = context
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(self, request: BuilderVisualDraftCreateRequest) -> BuilderVisualDraftEnvelope:
        snapshot = self._context.repository.snapshot(self._context.scope)
        session_name_is_placeholder = request.session_name is None
        if request.session_name is None:
            for _attempt in range(128):
                name = f"untitled-session-{secrets.token_hex(6)}"
                candidate = SessionRef(f"user:sessions/{name}.yaml")
                try:
                    snapshot.get(candidate)
                except CatalogNotFoundError:
                    break
            else:
                raise RuntimeError("Unable to allocate a unique untitled session name")
        else:
            name = request.session_name
        target_ref = SessionRef(f"user:sessions/{name}.yaml")
        try:
            snapshot.get(target_ref)
        except CatalogNotFoundError:
            pass
        else:
            raise BuilderVisualDraftConflictError(
                f"Session draft target already exists: {target_ref}; open it to edit or "
                "choose a new session name",
                ref=target_ref,
            )
        now = self._clock().astimezone(UTC).replace(second=0, microsecond=0)
        session_yaml = yaml.safe_dump(
            {
                "session": {
                    "name": name,
                    **(
                        {"display_name": request.display_name}
                        if request.display_name is not None
                        else {}
                    ),
                    **(
                        {"description": request.description}
                        if request.description is not None
                        else {}
                    ),
                },
                "segments": [],
                "time": {
                    "start_time": now.isoformat().replace("+00:00", "Z"),
                    "step_seconds": 1.0,
                    "compression": 1.0,
                },
            },
            sort_keys=False,
        )
        return BuilderVisualDraftEnvelope(
            draft_revision=0,
            target_ref=target_ref,
            session_name_is_placeholder=session_name_is_placeholder,
            reserved_authoring_ids=(),
            session_yaml=session_yaml,
        )

    def open(self, request: BuilderVisualDraftOpenRequest) -> BuilderVisualDraftEnvelope:
        snapshot = self._context.repository.snapshot(self._context.scope)
        source = snapshot.get(request.source_ref)
        target_ref = request.target_ref
        if target_ref is None:
            target_ref = (
                request.source_ref
                if request.source_ref.namespace == "user"
                else SessionRef(f"user:{request.source_ref.relative_path.as_posix()}")
            )
        expected_revision: str | None = None
        try:
            target = snapshot.get(target_ref)
        except CatalogNotFoundError:
            pass
        else:
            expected_revision = str(target.revision)
        if target_ref == request.source_ref:
            if request.source_ref.namespace != "user":
                raise BuilderVisualDraftConflictError(
                    f"Shipped session {request.source_ref} is read-only",
                    ref=request.source_ref,
                )
        elif expected_revision is not None:
            raise BuilderVisualDraftConflictError(
                f"Session customization target already exists: {target_ref}",
                ref=target_ref,
            )
        session_yaml = source.content.decode("utf-8")
        document = load_configuration_yaml(source.content)
        if not isinstance(document, dict) or not isinstance(document.get("session"), dict):
            raise ValueError(f"Stored session {request.source_ref} has no session identity")
        if request.source_ref.relative_path.stem != target_ref.relative_path.stem:
            document["session"]["name"] = target_ref.relative_path.stem
            session_yaml = canonicalize_persisted_configuration(
                target_ref,
                cast(JsonDocument, document),
            ).yaml_bytes.decode("utf-8")
        applied_model = SegmentSessionConfig.model_validate(document)
        applied_session = cast(
            JsonDocument,
            applied_model.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        return BuilderVisualDraftEnvelope(
            draft_revision=0,
            target_ref=target_ref,
            source_ref=request.source_ref,
            expected_session_revision=expected_revision,
            catalog_documents=(),
            session_name_is_placeholder=False,
            reserved_authoring_ids=_current_and_referenced_authoring_ids(applied_session),
            session_yaml=session_yaml,
            applied_revision=0,
            applied_session=applied_session,
            control_tree=_applied_control_tree(applied_model, 0),
        )

    def apply_yaml(
        self,
        request: BuilderVisualDraftApplyYamlRequest,
    ) -> BuilderVisualDraftApplyYamlResult:
        """Apply one exact YAML buffer without creating a second session loader."""

        if not isinstance(request, BuilderVisualDraftApplyYamlRequest):
            raise TypeError("request must be a BuilderVisualDraftApplyYamlRequest")
        draft = request.draft
        _assert_visual_draft_ownership(draft)
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before the YAML buffer was applied",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )

        application = parse_yaml_application(draft.target_ref, request.yaml_text)
        if application.canonical is None:
            # The page holds the refused text; the draft stays as it was.
            return BuilderVisualDraftApplyYamlResult(
                draft=draft,
                buffer_generation=request.buffer_generation,
                yaml_text=request.yaml_text,
                applied=False,
                canonicalization_required=False,
                issues=application.issues,
            )
        applied_model = cast(SegmentSessionConfig, application.model)
        applied_session = application.canonical.canonical_json
        canonical_yaml = application.canonical.yaml_bytes.decode("utf-8")

        snapshot = self._context.repository.snapshot(self._context.scope)
        reachable_proposals = _reachable_proposals(
            applied_model,
            draft.catalog_documents,
            snapshot,
        )
        next_revision = draft.draft_revision + 1
        reserved_authoring_ids = tuple(
            dict.fromkeys(
                (
                    *draft.reserved_authoring_ids,
                    *_current_and_referenced_authoring_ids(applied_session),
                )
            )
        )
        updated = draft.model_copy(
            update={
                "draft_revision": next_revision,
                "catalog_documents": reachable_proposals,
                "reserved_authoring_ids": reserved_authoring_ids,
                "session_yaml": request.yaml_text,
                "applied_revision": next_revision,
                "applied_session": applied_session,
                "control_tree": _applied_control_tree(applied_model, next_revision),
            }
        )
        return BuilderVisualDraftApplyYamlResult(
            draft=updated,
            buffer_generation=request.buffer_generation,
            yaml_text=request.yaml_text,
            applied=True,
            canonicalization_required=request.yaml_text != canonical_yaml,
            issues=(),
        )

    def mutate_controls(
        self,
        request: BuilderVisualControlMutationRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftAssemblyResult:
        """Apply one canonical control batch and compile its single new revision."""

        if not isinstance(request, BuilderVisualControlMutationRequest):
            raise TypeError("request must be a BuilderVisualControlMutationRequest")
        draft = request.draft
        _assert_visual_draft_ownership(draft)
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before controls were mutated",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )
        if draft.applied_session is None or draft.applied_revision is None:
            raise self._command_error(
                draft,
                "Graphical control mutations require a fully applied draft revision",
                code="catalog_authoring.invalid_graph",
            )

        applied_model = SegmentSessionConfig.model_validate(draft.applied_session)
        control_build = build_session_control_tree(
            applied_model,
            projection_revision=draft.applied_revision,
        )
        try:
            candidate = apply_builder_control_mutations(
                draft.applied_session,
                control_build.bindings,
                request.commands,
            )
        except BuilderControlMutationError as error:
            raise self._command_error(draft, str(error)) from error
        try:
            _wrapper, validated = validate_referenced_configuration_document(
                draft.target_ref,
                candidate,
            )
        except (ValidationError, TypeError, ValueError) as error:
            raise self._command_error(
                draft,
                f"Control mutation batch does not produce a valid session: {error}",
                code="catalog_authoring.invalid_graph",
            ) from error
        return self._accept_session(
            draft,
            cast(SegmentSessionConfig, validated),
            draft.catalog_documents,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )

    def apply_edits(
        self,
        request: BuilderVisualDraftApplyEditsRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftApplyEditsResult:
        """Accept pending link rule, routing domain and boundary edits as one revision.

        The edits are applied to the accepted session with the function the
        compile uses. One stale or invalid edit refuses the whole batch. A refused
        batch mints no revision.
        """

        if not isinstance(request, BuilderVisualDraftApplyEditsRequest):
            raise TypeError("request must be a BuilderVisualDraftApplyEditsRequest")
        draft = request.draft
        _assert_visual_draft_ownership(draft)
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before the pending edits were applied",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )
        if draft.applied_session is None:
            raise self._command_error(
                draft,
                "Pending edits require a fully applied draft revision",
                code="catalog_authoring.invalid_graph",
            )

        applied = apply_pending_edits(
            draft.applied_session,
            request.edits,
            proposals=draft.catalog_documents,
            source_ref=str(draft.target_ref),
        )
        candidate, outcomes = applied.session, applied.outcomes
        if any(outcome.status != "applied" for outcome in outcomes):
            return BuilderVisualDraftApplyEditsResult(
                applied=False,
                outcomes=outcomes,
                assembly=None,
            )
        try:
            _wrapper, validated = validate_referenced_configuration_document(
                draft.target_ref,
                candidate,
            )
        except ValidationError as error:
            # Each edit is valid alone; the session they make together is not.
            placed, unowned = place_candidate_issues(
                error,
                session=draft.applied_session,
                candidate=candidate,
                edits=request.edits,
                outcomes=outcomes,
                source_ref=str(draft.target_ref),
            )
            return BuilderVisualDraftApplyEditsResult(
                applied=False,
                outcomes=placed,
                issues=unowned,
                assembly=None,
            )
        except (TypeError, ValueError) as error:
            raise self._command_error(
                draft,
                f"Pending edits do not produce a valid session: {error}",
                code="catalog_authoring.invalid_graph",
            ) from error
        return BuilderVisualDraftApplyEditsResult(
            applied=True,
            outcomes=outcomes,
            assembly=self._accept_session(
                draft,
                cast(SegmentSessionConfig, validated),
                applied.proposals,
                available_node_count=available_node_count,
                preview_factory=preview_factory,
            ),
        )

    def accept_state(
        self,
        request: BuilderVisualDraftAcceptStateRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftAssemblyResult:
        """Accept a stored session with its proposals as the draft's next revision.

        Undo re-accepts the state an accepted change replaced. The stored
        proposals replace the draft's. The allocation history keeps every id it
        held. Undo therefore never frees an id.
        """

        if not isinstance(request, BuilderVisualDraftAcceptStateRequest):
            raise TypeError("request must be a BuilderVisualDraftAcceptStateRequest")
        draft = request.draft
        _assert_visual_draft_ownership(draft)
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before the stored state was accepted",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )
        if draft.applied_session is None:
            raise self._command_error(
                draft,
                "Accepting a stored state requires a fully applied draft revision",
                code="catalog_authoring.invalid_graph",
            )
        try:
            _wrapper, validated = validate_referenced_configuration_document(
                draft.target_ref,
                request.state.session,
            )
        except (ValidationError, TypeError, ValueError) as error:
            raise self._command_error(
                draft,
                f"The stored state is not a valid session: {error}",
                code="catalog_authoring.invalid_graph",
            ) from error
        return self._accept_session(
            draft,
            cast(SegmentSessionConfig, validated),
            request.state.catalog_documents,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )

    def _accept_session(
        self,
        draft: BuilderVisualDraftEnvelope,
        session: SegmentSessionConfig,
        proposals: tuple[BuilderProposedCatalogDocument, ...],
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None,
    ) -> BuilderVisualDraftAssemblyResult:
        """Accept a validated session as the draft's next revision and compile it."""

        snapshot = self._context.repository.snapshot(self._context.scope)
        updated = _accepted_draft(draft, session, proposals, snapshot)
        return _compile_visual_application(
            updated,
            snapshot,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )

    def edit_object(
        self,
        request: BuilderVisualObjectEditRequest,
    ) -> BuilderVisualObjectEditResult:
        """Apply one card value or field-editor batch to an object's working JSON.

        Nothing is accepted and no revision is minted. The answer holds the new
        working JSON, the card value that writes it back unchanged, the field
        editor over it and its issues.
        """

        if not isinstance(request, BuilderVisualObjectEditRequest):
            raise TypeError("request must be a BuilderVisualObjectEditRequest")
        source_ref = str(request.target_ref)
        change = request.change
        working = request.working
        card_issues: tuple[BuilderIssue, ...] = ()
        if isinstance(change, BuilderVisualObjectControlEdit):
            working = self._apply_object_controls(request, change)
        elif isinstance(change, BuilderVisualLinkRuleWidget):
            working, card_issues = _link_rule_document(change.rule)
        elif isinstance(change, BuilderVisualRoutingDomainWidget):
            working, card_issues = _routing_domain_document(change.domain)
        elif isinstance(change, BuilderVisualRoutingBoundariesWidget):
            working, card_issues = _carded_boundaries(cast(list[JsonValue], working), change)
        widget, validation_issues = _classified_object(
            request.address,
            working,
            source_ref=source_ref,
        )
        return BuilderVisualObjectEditResult(
            address=request.address,
            working=working,
            widget=widget,
            control_tree=_object_control_build(
                request.address,
                working,
                applied_revision=request.applied_revision,
            ).tree,
            issues=(
                *(issue.model_copy(update={"source_ref": source_ref}) for issue in card_issues),
                *validation_issues,
            ),
        )

    @staticmethod
    def _apply_object_controls(
        request: BuilderVisualObjectEditRequest,
        change: BuilderVisualObjectControlEdit,
    ) -> JsonDocument | list[JsonValue]:
        editor = _object_editor(request.address, request.working)
        control_build = _object_control_build(
            request.address,
            request.working,
            applied_revision=request.applied_revision,
        )
        for command in change.commands:
            binding = control_build.bindings.get(command.control_id)
            if (
                binding is not None
                and binding.json_pointer in editor.protected_pointers
                and not isinstance(command, _LIST_ITEM_COMMANDS)
            ):
                raise BuilderVisualDraftCommandError(
                    editor.protected_reason,
                    code="catalog_authoring.invalid_patch",
                    ref=request.target_ref,
                )
        try:
            edited = apply_builder_control_mutations(
                _editor_document(request.address, request.working),
                control_build.bindings,
                change.commands,
            )
        except BuilderControlMutationError as error:
            raise BuilderVisualDraftCommandError(
                str(error),
                code="catalog_authoring.invalid_patch",
                ref=request.target_ref,
            ) from error
        if isinstance(request.address, BuilderWholeListAddress):
            return cast(list[JsonValue], edited["boundaries"])
        return edited

    def retarget(
        self,
        request: BuilderVisualDraftRetargetRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftAssemblyResult:
        """Prepare, but do not persist, one draft under a different session identity."""

        if not isinstance(request, BuilderVisualDraftRetargetRequest):
            raise TypeError("request must be a BuilderVisualDraftRetargetRequest")
        draft = request.draft
        _assert_visual_draft_ownership(draft)
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before the session was retargeted",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )
        snapshot = self._context.repository.snapshot(self._context.scope)
        try:
            current_target_revision = str(snapshot.get(request.target_ref).revision)
        except CatalogNotFoundError:
            pass
        else:
            raise BuilderVisualDraftCommandError(
                f"Session retarget target already exists: {request.target_ref}",
                code="catalog_authoring.conflict",
                ref=request.target_ref,
                current_revision=current_target_revision,
            )

        new_name = request.target_ref.relative_path.stem
        if _identifier(new_name) != new_name:
            raise self._command_error(
                draft,
                f"Session name {new_name!r} must be written as {_identifier(new_name)!r}",
            )
        if draft.applied_session is None:
            raise self._command_error(
                draft,
                "Save under a new name copies the accepted session; the draft holds none yet",
                code="catalog_authoring.invalid_graph",
            )

        # The components the old name generated move under the new name, each
        # named the way the assembler names it: a segment's constellation or
        # site set after the session and the segment, every other component by
        # its own id. Customized forks keep their refs.
        session = SegmentSessionConfig.model_validate(draft.applied_session)
        new_owner = _proposal_owner(request.target_ref)
        named_for_segment = {
            (
                segment.source
                if isinstance(segment, SpaceSegment)
                else segment.placement.from_site_set
            ): segment.id
            for segment in session.segments
            if isinstance(segment, SpaceSegment | GroundSegment)
        }
        generated_refs = _builder_generated_proposal_refs(
            draft.catalog_documents,
            owner=_proposal_owner(draft.target_ref),
        )
        renames = {
            ref: _owned_component_ref(
                _component_family(ref),
                new_owner,
                _identifier(_segment_component_id(new_name, named_for_segment[ref]))
                if ref in named_for_segment
                else ref.relative_path.stem,
            )
            for ref in generated_refs
        }
        derived_ids = [
            renamed.relative_path.stem
            for ref, renamed in renames.items()
            if _component_family(ref) in _DERIVED_ID_FAMILIES
        ]
        if len(set(derived_ids)) != len(derived_ids):
            raise self._command_error(
                draft,
                f"Session name {new_name!r} gives two generated components the same id; "
                "choose a shorter or different name",
            )
        retained = {proposal.ref for proposal in draft.catalog_documents} - generated_refs
        for renamed in renames.values():
            if _ref_taken(renamed, snapshot, retained):
                raise BuilderVisualDraftCommandError(
                    f"Session retarget component already exists: {renamed}",
                    code="catalog_authoring.conflict",
                    ref=renamed,
                )
        view = OverlayCatalogReadView(
            snapshot,
            {
                proposal.ref: (index, proposal)
                for index, proposal in enumerate(draft.catalog_documents)
            },
        )
        try:
            proposals = tuple(
                BuilderProposedCatalogDocument(
                    ref=renames[proposal.ref],
                    origin=proposal.origin,
                    document=forked_document(
                        *_catalog_object(view, proposal.ref),
                        renames[proposal.ref],
                        replacements=renames,
                    ),
                )
                if proposal.ref in renames
                else proposal
                for proposal in draft.catalog_documents
            )
        except (CatalogReadError, TypeError, ValueError, yaml.YAMLError) as error:
            raise self._command_error(
                draft,
                f"Save under a new name needs every generated component to be valid: {error}",
                code="catalog_authoring.invalid_graph",
            ) from error
        session = rewrite_catalog_references(session, renames)
        applied_model = session.model_copy(
            update={
                "session": session.session.model_copy(
                    update={"name": request.target_ref.relative_path.stem}
                )
            }
        )
        applied_session = cast(
            JsonDocument,
            applied_model.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        next_revision = draft.draft_revision + 1
        canonical = canonicalize_persisted_configuration(request.target_ref, applied_session)
        retargeted = draft.model_copy(
            update={
                "target_ref": request.target_ref,
                "expected_session_revision": None,
                "session_name_is_placeholder": False,
                "draft_revision": next_revision,
                "catalog_documents": proposals,
                "session_yaml": canonical.yaml_bytes.decode("utf-8"),
                "applied_revision": next_revision,
                "applied_session": applied_session,
                "control_tree": _applied_control_tree(applied_model, next_revision),
            }
        )
        return _compile_visual_application(
            retargeted,
            snapshot,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )

    @staticmethod
    def _command_error(
        draft: BuilderVisualDraftEnvelope,
        message: str,
        *,
        code: Literal[
            "catalog_authoring.invalid_patch",
            "catalog_authoring.invalid_graph",
            "catalog_authoring.stale_revision",
        ] = "catalog_authoring.invalid_patch",
        expected_revision: int | None = None,
        current_revision: int | None = None,
    ) -> BuilderVisualDraftCommandError:
        return BuilderVisualDraftCommandError(
            message,
            code=code,
            ref=draft.target_ref,
            expected_revision=expected_revision,
            current_revision=current_revision,
        )

    def _node_ref_for_command(
        self,
        draft: BuilderVisualDraftEnvelope,
        snapshot: CatalogReadSnapshot,
        explicit: CatalogRef | None,
        *,
        placement: Literal["space", "ground"],
    ) -> CatalogRef:
        proposed_refs = {proposal.ref for proposal in draft.catalog_documents}
        if explicit is not None:
            candidate = CatalogRef(explicit)
            if candidate in proposed_refs:
                return candidate
            try:
                snapshot.get(candidate)
            except CatalogNotFoundError as error:
                raise self._command_error(
                    draft,
                    f"Node reference {candidate} does not exist in the catalog or draft",
                    code="catalog_authoring.invalid_graph",
                ) from error
            return candidate
        nodes = snapshot.list(family="nodes")
        prefix = f"nodes/{placement}/"
        selected = next(
            (entry.ref for entry in nodes if entry.ref.relative_path.as_posix().startswith(prefix)),
            nodes[0].ref if nodes else None,
        )
        if selected is None:
            raise self._command_error(
                draft,
                "No node models are available to seed this segment",
                code="catalog_authoring.invalid_graph",
            )
        return selected

    def _catalog_model_for_draft(
        self,
        draft: BuilderVisualDraftEnvelope,
        snapshot: CatalogReadSnapshot,
        ref: CatalogRef,
    ) -> BaseModel:
        proposal = next(
            (candidate for candidate in draft.catalog_documents if candidate.ref == ref),
            None,
        )
        if proposal is not None:
            family = cast(CatalogFamily, ref.family)
            return catalog_family_spec(family).validate_document(proposal.document)
        return _validated_catalog_model(snapshot, ref)

    def _ground_installation_facts(
        self,
        draft: BuilderVisualDraftEnvelope,
        snapshot: CatalogReadSnapshot,
        node_ref: CatalogRef,
        *,
        installed_counts: dict[str, int] | None = None,
        authored_boresights: dict[str, BuilderVisualGroundBoresight] | None = None,
    ) -> tuple[dict[str, int], dict[str, BuilderVisualGroundBoresight]]:
        try:
            model = self._catalog_model_for_draft(draft, snapshot, node_ref)
        except (TypeError, ValueError) as error:
            raise self._command_error(
                draft,
                f"Node reference {node_ref} is not a valid node component: {error}",
                code="catalog_authoring.invalid_graph",
            ) from error
        if not isinstance(model, Node):
            raise self._command_error(
                draft,
                f"Node reference {node_ref} does not resolve to a node component",
                code="catalog_authoring.invalid_graph",
            )

        mounts = {mount.id: mount for mount in model.terminals}
        installed = dict(installed_counts or {}) or {
            mount.id: mount.count for mount in model.terminals
        }
        unknown_installed = sorted(set(installed).difference(mounts))
        if unknown_installed:
            raise self._command_error(
                draft,
                "Ground installation names unknown terminal mount(s): "
                + ", ".join(unknown_installed),
                code="catalog_authoring.invalid_graph",
            )

        boresights = dict(authored_boresights or {})
        unknown_boresights = sorted(set(boresights).difference(installed))
        if unknown_boresights:
            raise self._command_error(
                draft,
                "Ground boresight names uninstalled terminal mount(s): "
                + ", ".join(unknown_boresights),
                code="catalog_authoring.invalid_graph",
            )
        for mount_id in installed:
            mount = mounts[mount_id]
            if mount.role == "access":
                boresights.setdefault(
                    mount_id,
                    BuilderVisualGroundBoresight(mode="local_vertical"),
                )
            elif mount_id in boresights:
                raise self._command_error(
                    draft,
                    f"Ground non-access terminal mount {mount_id!r} must not declare a boresight",
                    code="catalog_authoring.invalid_graph",
                )
        return installed, boresights

    def _preview_for_command(
        self,
        draft: BuilderVisualDraftEnvelope,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None,
    ) -> BuilderWorld:
        """The draft's resolved world; a draft that does not resolve is refused
        with its compile issues, since its terminals are then unknown."""
        compiled = self.compile(
            BuilderVisualDraftCompileRequest(draft=draft),
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )
        preview = compiled.compile_result.resolved_preview
        if preview is None:
            issues = "; ".join(issue.message for issue in compiled.compile_result.issues)
            raise self._command_error(
                draft,
                f"The draft does not resolve, so its terminals are unknown: {issues}",
                code="catalog_authoring.invalid_graph",
            )
        return preview

    def _session_segment_pair(
        self,
        draft: BuilderVisualDraftEnvelope,
        session: SegmentSessionConfig,
        first_id: str,
        second_id: str,
    ) -> tuple[_PlacedSegment, _PlacedSegment]:
        """Two segments of the accepted session; a segment it does not declare is refused."""
        segments = _session_segments(session)
        unknown = [segment_id for segment_id in (first_id, second_id) if segment_id not in segments]
        if unknown:
            raise self._command_error(
                draft,
                "The session declares no segment "
                + " or ".join(repr(segment_id) for segment_id in dict.fromkeys(unknown)),
                code="catalog_authoring.invalid_graph",
            )
        return segments[first_id], segments[second_id]

    def _apply_session_command(
        self,
        request: BuilderVisualDraftCommandRequest,
        command: (
            BuilderVisualAddRoutingDomainCommand
            | BuilderVisualAddBoundaryCommand
            | BuilderVisualConnectSegmentsCommand
            | BuilderVisualRederiveLinkCommand
        ),
        reserved: set[str],
        snapshot: CatalogReadSnapshot,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None,
    ) -> BuilderVisualDraftCommandResult:
        """Run one link rule or routing command on the accepted session.

        The card functions write the object's grammar JSON. A complete object is
        accepted as the next revision. An incomplete one is returned as a pending
        edit. The draft then keeps its revision.
        """

        draft = request.draft
        if draft.applied_session is None:
            raise self._command_error(
                draft,
                "Link rule and routing commands require an accepted session",
                code="catalog_authoring.invalid_graph",
            )
        session = SegmentSessionConfig.model_validate(draft.applied_session)
        address: BuilderMemberAddress | BuilderWholeListAddress
        base: JsonDocument | list[JsonValue] | None = None
        working: JsonDocument | list[JsonValue]
        issues: tuple[BuilderIssue, ...]
        affected_kind: Literal["routing_domain", "boundary", "link"]
        affected_id: str
        notice: str | None = None
        if isinstance(command, BuilderVisualAddRoutingDomainCommand):
            domain_id = f"domain-{_next_number('domain', reserved)}"
            # A segment a domain selector names outside every `not` is covered.
            covered = {
                segment_id
                for domain in (session.routing.domains if session.routing is not None else ())
                for segment_id in _named_segments(domain.selectors)
            }
            working, issues = _routing_domain_document(
                BuilderVisualRoutingDomain(
                    domain_id=domain_id,
                    protocol="isis",
                    member_segment_ids=tuple(
                        segment.id for segment in session.segments if segment.id not in covered
                    ),
                )
            )
            address = BuilderMemberAddress(
                kind="member",
                list_pointer="/routing/domains",
                id=domain_id,
            )
            affected_kind = "routing_domain"
            affected_id = domain_id
        elif isinstance(command, BuilderVisualAddBoundaryCommand):
            over = next(
                (rule.id for rule in session.link_rules or () if _boundary_may_run_over(rule)),
                None,
            )
            domains: tuple[RoutingDomain, ...] = (
                session.routing.domains if session.routing is not None else ()
            )
            if over is None or len(domains) < 2:
                conditions = (
                    (
                        over is None,
                        "A routing boundary needs an enabled link rule that is not an "
                        "access rule. The session has none.",
                    ),
                    (
                        len(domains) < 2,
                        "A routing boundary needs two routing domains. "
                        f"The session declares {len(domains)}.",
                    ),
                )
                raise self._command_error(
                    draft,
                    " ".join(message for absent, message in conditions if absent),
                    code="catalog_authoring.invalid_graph",
                )
            # The whole boundary list is the edited object. Its base is absent when
            # the session declares no list.
            current = (
                cast(
                    list[JsonValue],
                    cast(JsonDocument, draft.applied_session["routing"])["boundaries"],
                )
                if session.routing is not None and session.routing.boundaries is not None
                else None
            )
            # The list's own pending edit is folded into this one, as rederive folds
            # its rule's: the new boundary joins the list as the card holds it.
            own_list = next(
                (
                    edit
                    for edit in request.pending_edits
                    if isinstance(edit, BuilderContentEdit)
                    and isinstance(edit.address, BuilderWholeListAddress)
                    and edit.address.pointer == "/routing/boundaries"
                ),
                None,
            )
            if own_list is not None:
                (own_outcome,) = apply_pending_edits(
                    draft.applied_session,
                    (own_list,),
                    proposals=draft.catalog_documents,
                    source_ref=str(draft.target_ref),
                ).outcomes
                if own_outcome.status != "applied":
                    raise self._command_error(
                        draft,
                        "The boundary list has a pending edit that cannot be applied: "
                        + "; ".join(issue.message for issue in own_outcome.issues),
                        code="catalog_authoring.invalid_graph",
                    )
            base = deepcopy(current) if own_list is None else deepcopy(own_list.base)
            boundary_issues: list[BuilderIssue] = []
            listed: list[JsonValue] = (
                deepcopy(cast(list[JsonValue], own_list.working))
                if own_list is not None
                else deepcopy(current)
                if current is not None
                else []
            )
            listed.append(
                _routing_boundary_document(
                    BuilderVisualRoutingBoundary(
                        over_rule_id=over,
                        adapter="static_ip",
                        from_domain_id=domains[0].id,
                        to_domain_id=domains[1].id,
                        export_node_loopbacks=True,
                    ),
                    boundary_issues,
                    at=(len(listed),),
                )
            )
            working = listed
            issues = tuple(boundary_issues)
            address = BuilderWholeListAddress(kind="whole_list", pointer="/routing/boundaries")
            affected_kind = "boundary"
            affected_id = address.pointer
        elif isinstance(command, BuilderVisualConnectSegmentsCommand):
            source, target = self._session_segment_pair(
                draft,
                session,
                command.from_segment_id,
                command.to_segment_id,
            )
            first, second = (
                (source, target)
                if source.kind == "ground" or target.kind != "ground"
                else (target, source)
            )
            physics = _derive_link_physics(
                self._preview_for_command(
                    draft,
                    available_node_count=available_node_count,
                    preview_factory=preview_factory,
                ),
                first,
                second,
            )
            rule_id = f"link-{_next_number('link', reserved)}"

            def endpoint_for_segment(segment: _PlacedSegment) -> BuilderVisualLinkEndpoint:
                # Connect writes no elevation floor. Each ground station keeps the
                # floor its own terminals declare until the user authors one here.
                return BuilderVisualLinkEndpoint(
                    segment_id=segment.segment_id,
                    role=physics.role,
                    medium=physics.medium,
                )

            working, issues = _link_rule_document(
                BuilderVisualLinkRule(
                    rule_id=rule_id,
                    a=endpoint_for_segment(first),
                    b=endpoint_for_segment(second),
                    topology_mode=physics.topology_mode,
                    topology_n=physics.topology_n,
                )
            )
            address = BuilderMemberAddress(kind="member", list_pointer="/link_rules", id=rule_id)
            affected_kind = "link"
            affected_id = rule_id
        else:
            own = next(
                (
                    edit
                    for edit in request.pending_edits
                    if isinstance(edit, BuilderContentEdit)
                    and isinstance(edit.address, BuilderMemberAddress)
                    and edit.address.list_pointer == "/link_rules"
                    and edit.address.id == command.rule_id
                ),
                None,
            )
            # The rule as its card shows it: accepted, with its own pending edit applied.
            own_applied = apply_pending_edits(
                draft.applied_session,
                () if own is None else (own,),
                proposals=draft.catalog_documents,
                source_ref=str(draft.target_ref),
            )
            edited, outcomes = own_applied.session, own_applied.outcomes
            refusals = [
                issue.message
                for outcome in outcomes
                if outcome.status != "applied"
                for issue in outcome.issues
            ]
            if refusals:
                raise self._command_error(
                    draft,
                    f"Link rule {command.rule_id!r} has a pending edit that cannot be applied: "
                    + "; ".join(refusals),
                    code="catalog_authoring.invalid_graph",
                )
            matches = [
                rule
                for rule in _json_items(edited.get("link_rules"))
                if isinstance(rule, dict) and rule.get("id") == command.rule_id
            ]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Link rule {command.rule_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            card = _link_projection(LinkRule.model_validate(matches[0]))
            if card is None:
                raise self._command_error(
                    draft,
                    f"Link rule {command.rule_id!r} has a form the link card does not write "
                    "back, so its endpoints are edited in its field editor",
                    code="catalog_authoring.invalid_graph",
                )
            first, second = self._session_segment_pair(
                draft,
                session,
                command.segment_id if command.side == "a" else card.a.segment_id,
                command.segment_id if command.side == "b" else card.b.segment_id,
            )
            # Terminal facts come from the accepted session's world. A rule edit
            # does not change them.
            physics = _derive_link_physics(
                self._preview_for_command(
                    draft,
                    available_node_count=available_node_count,
                    preview_factory=preview_factory,
                ),
                first,
                second,
            )

            def updated_endpoint_for(
                existing: BuilderVisualLinkEndpoint,
                segment: _PlacedSegment,
            ) -> BuilderVisualLinkEndpoint:
                # An authored floor stays only while this endpoint is the ground
                # side of an access rule. The resolver refuses it anywhere else.
                return existing.model_copy(
                    update={
                        "segment_id": segment.segment_id,
                        "role": physics.role,
                        "medium": physics.medium,
                        "min_elevation_deg": (
                            existing.min_elevation_deg
                            if physics.role == "access" and segment.kind == "ground"
                            else None
                        ),
                    }
                )

            working, issues = _link_rule_document(
                card.model_copy(
                    update={
                        "a": updated_endpoint_for(card.a, first),
                        "b": updated_endpoint_for(card.b, second),
                        "topology_mode": physics.topology_mode,
                        "topology_n": physics.topology_n,
                    }
                )
            )
            address = BuilderMemberAddress(
                kind="member",
                list_pointer="/link_rules",
                id=command.rule_id,
            )
            # The rule's own pending edit is folded into this one.
            base = own.base if own is not None else cast(JsonDocument, matches[0])
            affected_kind = "link"
            affected_id = command.rule_id
            notice = _physics_notice(physics)

        edit = BuilderContentEdit(kind="content", address=address, base=base, working=working)
        return self._accepted_command_result(
            draft,
            command,
            (edit,),
            affected_kind=affected_kind,
            affected_id=affected_id,
            notice=notice,
            issues=tuple(issues),
            snapshot=snapshot,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )

    def _is_complete(
        self,
        draft: BuilderVisualDraftEnvelope,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None,
    ) -> bool:
        """Whether the draft resolves and can be saved."""
        compiled = self.compile(
            BuilderVisualDraftCompileRequest(draft=draft),
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        ).compile_result
        return compiled.resolved_preview is not None and compiled.save_verdict.allowed

    def apply_command(
        self,
        request: BuilderVisualDraftCommandRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftCommandResult:
        """Apply one backend-owned gesture to an exact structured draft revision."""

        draft = request.draft
        _assert_visual_draft_ownership(draft)
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before the command was applied",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )
        snapshot = self._context.repository.snapshot(self._context.scope)
        # Every id the draft and the pending edits hold, before the gesture changes them.
        reserved = _reserved_authoring_ids(draft, request.pending_edits)
        command = request.command
        if isinstance(
            command,
            BuilderVisualAddRoutingDomainCommand
            | BuilderVisualAddBoundaryCommand
            | BuilderVisualConnectSegmentsCommand
            | BuilderVisualRederiveLinkCommand,
        ):
            return self._apply_session_command(
                request,
                command,
                reserved,
                snapshot,
                available_node_count=available_node_count,
                preview_factory=preview_factory,
            )
        edits, affected_kind, affected_id, notice, scheduling_preset = self._segment_command_edits(
            draft, command, reserved, snapshot
        )
        return self._accepted_command_result(
            draft,
            command,
            edits,
            affected_kind=affected_kind,
            affected_id=affected_id,
            notice=notice,
            scheduling_preset=scheduling_preset,
            snapshot=snapshot,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )

    def _segment_command_edits(
        self,
        draft: BuilderVisualDraftEnvelope,
        command: BuilderVisualDraftCommand,
        reserved: set[str],
        snapshot: CatalogReadSnapshot,
    ) -> tuple[
        tuple[BuilderContentEdit, ...],
        Literal["space", "ground", "ground_member"],
        str,
        str | None,
        BuilderVisualSchedulingPreset | None,
    ]:
        """The pending edits one segment command makes of the accepted session and
        its proposed documents: new or changed segments, constellations, orbits,
        site sets and sites."""
        session = _command_base_session(draft)
        session_name = draft.target_ref.relative_path.stem
        owner = _proposal_owner(draft.target_ref)
        proposals = {proposal.ref: proposal for proposal in draft.catalog_documents}
        occupied: set[CatalogRef] = set(proposals)

        def mint(family: str, stem: str) -> CatalogRef:
            ref = _mint_component_ref(
                family, owner, stem, taken=lambda ref: _ref_taken(ref, snapshot, occupied)
            )
            occupied.add(ref)
            return ref

        def proposal_edit(ref: CatalogRef, document: JsonDocument) -> BuilderContentEdit:
            current = proposals.get(ref)
            return BuilderContentEdit(
                kind="content",
                address=BuilderProposalAddress(kind="proposal", ref=ref),
                base=current.document if current is not None else None,
                working=document,
            )

        segments = cast(list[JsonDocument], session.get("segments", []))

        def segment_at(segment_id: str) -> JsonDocument:
            matches = [segment for segment in segments if segment.get("id") == segment_id]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Segment {segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            return matches[0]

        def segment_edit(segment: JsonDocument, *, new: bool) -> BuilderContentEdit:
            segment_id = cast(str, segment["id"])
            return BuilderContentEdit(
                kind="content",
                address=BuilderMemberAddress(
                    kind="member", list_pointer="/segments", id=segment_id
                ),
                base=None if new else segment_at(segment_id),
                working=segment,
            )

        def proposed_document(ref: CatalogRef, family_noun: str) -> JsonDocument:
            current = proposals.get(ref)
            if current is None:
                raise self._command_error(
                    draft,
                    f"{ref} is not this session's own {family_noun}; customize it first",
                    code="catalog_authoring.invalid_graph",
                )
            return deepcopy(current.document)

        def new_ground(site_refs: list[CatalogRef]) -> tuple[BuilderContentEdit, ...]:
            number = _next_number("ground", reserved)
            segment_id = f"ground-{number}"
            site_set_ref = mint("site-sets", _segment_component_id(session_name, segment_id))
            display_name = f"Ground segment {number}"
            return (
                proposal_edit(
                    site_set_ref,
                    {
                        "site_set": {
                            "id": site_set_ref.relative_path.stem,
                            "display_name": display_name,
                            "sites": [str(ref) for ref in site_refs],
                        }
                    },
                ),
                segment_edit(
                    {
                        "id": segment_id,
                        "display_name": display_name,
                        "placement": {"from_site_set": str(site_set_ref)},
                        "apply": {"scheduling": scheduling_preset_block(DEFAULT_SCHEDULING_PRESET)},
                    },
                    new=True,
                ),
            )

        def site_set_of(segment_id: str) -> tuple[CatalogRef, JsonDocument]:
            segment = segment_at(segment_id)
            placement = segment.get("placement")
            if not isinstance(placement, dict):
                raise self._command_error(
                    draft,
                    f"Segment {segment_id!r} is not a ground segment",
                    code="catalog_authoring.invalid_graph",
                )
            ref = CatalogRef(cast(str, placement["from_site_set"]))
            return ref, proposed_document(ref, "site set")

        if isinstance(command, BuilderVisualPlaceSpaceReferenceCommand):
            try:
                model = self._catalog_model_for_draft(draft, snapshot, command.source_ref)
            except (CatalogNotFoundError, TypeError, ValueError) as error:
                raise self._command_error(
                    draft,
                    f"Space source reference {command.source_ref} is invalid: {error}",
                    code="catalog_authoring.invalid_graph",
                ) from error
            if not isinstance(model, (Constellation, SpaceNodeSet)):
                raise self._command_error(
                    draft,
                    f"Space source reference {command.source_ref} does not resolve to a "
                    "constellation or space-node-set component",
                    code="catalog_authoring.invalid_graph",
                )
            segment_id = f"space-{_next_number('space', reserved)}"
            edit = segment_edit({"id": segment_id, "source": str(command.source_ref)}, new=True)
            return (edit,), "space", segment_id, None, None
        if isinstance(command, BuilderVisualPlaceGroundReferenceCommand):
            try:
                model = self._catalog_model_for_draft(draft, snapshot, command.site_set_ref)
            except (CatalogNotFoundError, TypeError, ValueError) as error:
                raise self._command_error(
                    draft,
                    f"Site-set reference {command.site_set_ref} is invalid: {error}",
                    code="catalog_authoring.invalid_graph",
                ) from error
            if not isinstance(model, SiteSet):
                raise self._command_error(
                    draft,
                    f"Site-set reference {command.site_set_ref} does not resolve to a "
                    "site-set component",
                    code="catalog_authoring.invalid_graph",
                )
            segment_id = f"ground-{_next_number('ground', reserved)}"
            edit = segment_edit(
                {
                    "id": segment_id,
                    "placement": {"from_site_set": str(command.site_set_ref)},
                    "apply": {"scheduling": scheduling_preset_block(DEFAULT_SCHEDULING_PRESET)},
                },
                new=True,
            )
            return (edit,), "ground", segment_id, None, DEFAULT_SCHEDULING_PRESET
        if isinstance(command, BuilderVisualAddGeneratedSpaceCommand):
            number = _next_number("space", reserved)
            segment_id = f"space-{number}"
            node_ref = self._node_ref_for_command(
                draft, snapshot, command.node_ref, placement="space"
            )
            single_plane = command.phasing_mode == SINGLE_PLANE_PHASING_MODE
            layout = (
                None
                if single_plane
                else derive_walker_layout(
                    BuilderVisualWalkerLayoutRequest(
                        pattern=cast(Literal["walker_delta", "walker_star"], command.phasing_mode),
                        planes=3,
                        slots_per_plane=8,
                    )
                )
            )
            orbit_ref = mint("orbits", f"{segment_id}-orbit")
            constellation_ref = mint(
                "constellations", _segment_component_id(session_name, segment_id)
            )
            time = cast(JsonDocument, session.get("time", {}))
            orbit: JsonDocument = {
                "orbit": {
                    "id": orbit_ref.relative_path.stem,
                    "central_body": str(DEFAULT_BODY_REF),
                    "epoch": cast(JsonValue, time.get("start_time")),
                    "shape": {"altitude_km": 550},
                    "orientation": {
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "argument_of_perigee_deg": 0,
                    },
                    "phase": {"mean_anomaly_deg": 0},
                    "propagator": "j2_mean_elements",
                    "reference": "urn:nodalarc:session-builder-draft",
                }
            }
            constellation: JsonDocument = {
                "constellation": {
                    "id": constellation_ref.relative_path.stem,
                    "display_name": f"Constellation {number}",
                    "node": str(node_ref),
                    "orbit": str(orbit_ref),
                    "planes": {
                        "count": 1 if single_plane else 3,
                        "raan_spacing_deg": 360 if layout is None else layout.raan_spacing_deg,
                    },
                    "slots_per_plane": 8,
                    "phasing": {
                        "mode": command.phasing_mode,
                        "phase_offset_deg": 0 if layout is None else layout.phase_offset_deg,
                    },
                    "node_tags": [{"tag": "all"}],
                }
            }
            return (
                (
                    proposal_edit(orbit_ref, orbit),
                    proposal_edit(constellation_ref, constellation),
                    segment_edit({"id": segment_id, "source": str(constellation_ref)}, new=True),
                ),
                "space",
                segment_id,
                None,
                None,
            )
        if isinstance(command, BuilderVisualSetSpacePopulationCommand):
            segment = segment_at(command.segment_id)
            source_ref = CatalogRef(cast(str, segment.get("source")))
            document = proposed_document(source_ref, "constellation")
            body = cast(JsonDocument, document["constellation"])
            planes_block = cast(JsonDocument, body.get("planes", {}))
            phasing_block = cast(JsonDocument, body.get("phasing", {}))
            phasing_mode = command.phasing_mode or cast(
                BuilderVisualPhasingMode, phasing_block.get("mode")
            )
            planes = command.planes if command.planes is not None else planes_block.get("count")
            slots_per_plane = (
                command.slots_per_plane
                if command.slots_per_plane is not None
                else body.get("slots_per_plane")
            )
            if command.phasing_mode is not None:
                if phasing_mode == SINGLE_PLANE_PHASING_MODE:
                    planes = 1
                elif isinstance(planes, int):
                    planes = max(2, planes)
            elif command.planes is not None:
                if planes == 1:
                    phasing_mode = SINGLE_PLANE_PHASING_MODE
                elif phasing_mode == SINGLE_PLANE_PHASING_MODE:
                    phasing_mode = DEFAULT_PHASING_MODE
            if not isinstance(planes, int) or not isinstance(slots_per_plane, int):
                raise self._command_error(
                    draft,
                    f"Constellation {source_ref} states no plane or slot count to derive from",
                    code="catalog_authoring.invalid_graph",
                )
            if phasing_mode == SINGLE_PLANE_PHASING_MODE:
                raan_spacing_deg: float = 360
                phase_offset_deg: float = 0
            else:
                layout = derive_walker_layout(
                    BuilderVisualWalkerLayoutRequest(
                        pattern=cast(Literal["walker_delta", "walker_star"], phasing_mode),
                        planes=planes,
                        slots_per_plane=slots_per_plane,
                    )
                )
                raan_spacing_deg = layout.raan_spacing_deg
                phase_offset_deg = layout.phase_offset_deg
            body["planes"] = {**planes_block, "count": planes, "raan_spacing_deg": raan_spacing_deg}
            body["slots_per_plane"] = slots_per_plane
            body["phasing"] = {
                **phasing_block,
                "mode": phasing_mode,
                "phase_offset_deg": phase_offset_deg,
            }
            return (proposal_edit(source_ref, document),), "space", command.segment_id, None, None
        if isinstance(command, BuilderVisualAddGroundSiteReferenceCommand):
            try:
                model = self._catalog_model_for_draft(draft, snapshot, command.site_ref)
            except (CatalogNotFoundError, TypeError, ValueError) as error:
                raise self._command_error(
                    draft,
                    f"Site reference {command.site_ref} is invalid: {error}",
                    code="catalog_authoring.invalid_graph",
                ) from error
            if not isinstance(model, Site):
                raise self._command_error(
                    draft,
                    f"Site reference {command.site_ref} does not resolve to a site component",
                    code="catalog_authoring.invalid_graph",
                )
            if command.segment_id is None:
                ground_edits = new_ground([CatalogRef(command.site_ref)])
                segment_id = cast(BuilderMemberAddress, ground_edits[1].address).id
                return ground_edits, "ground", segment_id, None, DEFAULT_SCHEDULING_PRESET
            site_set_ref, document = site_set_of(command.segment_id)
            sites = cast(list[str], document["site_set"]["sites"])
            if str(command.site_ref) in sites:
                raise self._command_error(
                    draft,
                    f"Site {model.id!r} is already placed in {command.segment_id!r}",
                    code="catalog_authoring.invalid_graph",
                )
            sites.append(str(command.site_ref))
            return (
                (proposal_edit(site_set_ref, document),),
                "ground",
                command.segment_id,
                None,
                None,
            )
        if isinstance(command, BuilderVisualMintGroundMembersCommand):
            installed, boresights = self._ground_installation_facts(
                draft,
                snapshot,
                command.node_ref,
                installed_counts=command.installed or None,
                authored_boresights=command.boresights or None,
            )
            site_edits: list[BuilderContentEdit] = []
            site_refs: list[CatalogRef] = []
            for site_intent in command.sites:
                site_ref = mint(
                    "sites",
                    f"site-{_next_number('site', {ref.relative_path.stem for ref in occupied})}",
                )
                site_refs.append(site_ref)
                site_edits.append(
                    proposal_edit(
                        site_ref,
                        _minted_site(
                            site_ref.relative_path.stem,
                            display_name=site_intent.name,
                            node_ref=command.node_ref,
                            installed=installed,
                            boresights=boresights,
                            body_ref=command.body_ref,
                            lat_deg=site_intent.lat_deg,
                            lon_deg=site_intent.lon_deg,
                            alt_m=site_intent.alt_m,
                        ),
                    )
                )
            notice = f"minted {len(site_refs)} site{'s' if len(site_refs) != 1 else ''}"
            if command.segment_id is None:
                ground_edits = new_ground(site_refs)
                segment_id = cast(BuilderMemberAddress, ground_edits[1].address).id
                return (
                    (*site_edits, *ground_edits),
                    "ground",
                    segment_id,
                    notice,
                    DEFAULT_SCHEDULING_PRESET,
                )
            site_set_ref, document = site_set_of(command.segment_id)
            cast(list[str], document["site_set"]["sites"]).extend(str(ref) for ref in site_refs)
            return (
                (*site_edits, proposal_edit(site_set_ref, document)),
                "ground",
                command.segment_id,
                notice,
                None,
            )
        if isinstance(command, BuilderVisualSetSchedulingPresetCommand):
            segment = deepcopy(segment_at(command.segment_id))
            if not isinstance(segment.get("placement"), dict):
                raise self._command_error(
                    draft,
                    f"Segment {command.segment_id!r} is not a ground segment",
                    code="catalog_authoring.invalid_graph",
                )
            if command.site_id is None:
                assert command.preset is not None
                segment["apply"] = {
                    **cast(JsonDocument, segment.get("apply") or {}),
                    "scheduling": scheduling_preset_block(command.preset),
                }
                return (
                    (segment_edit(segment, new=False),),
                    "ground",
                    command.segment_id,
                    None,
                    command.preset,
                )
            overrides = [
                override
                for override in cast(list[JsonDocument], segment.get("overrides") or [])
                if cast(JsonDocument, override.get("match", {})).get("site") != command.site_id
            ]
            if command.preset is not None:
                overrides.append(
                    {
                        "match": {"site": command.site_id},
                        "scheduling": scheduling_preset_block(command.preset),
                    }
                )
            if overrides:
                segment["overrides"] = cast(JsonValue, overrides)
            else:
                segment.pop("overrides", None)
            return (
                (segment_edit(segment, new=False),),
                "ground_member",
                command.site_id,
                None,
                command.preset,
            )
        raise AssertionError(f"unhandled visual draft command: {type(command).__name__}")

    def _accepted_command_result(
        self,
        draft: BuilderVisualDraftEnvelope,
        command: BuilderVisualDraftCommand,
        edits: tuple[BuilderContentEdit, ...],
        *,
        affected_kind: BuilderVisualDraftAffectedKind,
        affected_id: str,
        notice: str | None,
        snapshot: CatalogReadSnapshot,
        available_node_count: int,
        preview_factory: PreviewFactory | None,
        scheduling_preset: BuilderVisualSchedulingPreset | None = None,
        issues: tuple[BuilderIssue, ...] = (),
    ) -> BuilderVisualDraftCommandResult:
        """A command's edits accepted as the next revision, or held as pending edits.

        Edits that do not apply, or that would take a complete draft to an
        incomplete one, are returned for the user to complete; the draft keeps its
        revision. A draft that was already incomplete is accepted, so a repair is
        never refused.
        """
        held = BuilderVisualDraftCommandResult(
            operation=command.operation,
            base_draft_revision=draft.draft_revision,
            draft=draft,
            affected_kind=affected_kind,
            affected_id=affected_id,
            notice=notice,
            scheduling_preset=scheduling_preset,
            created_edits=edits,
        )
        applied = apply_pending_edits(
            _command_base_session(draft),
            edits,
            proposals=draft.catalog_documents,
            source_ref=str(draft.target_ref),
        )
        if issues or any(outcome.status != "applied" for outcome in applied.outcomes):
            return held
        try:
            _wrapper, validated = validate_referenced_configuration_document(
                draft.target_ref,
                applied.session,
            )
        except (ValidationError, TypeError, ValueError) as error:
            raise self._command_error(
                draft,
                f"The {command.operation} command does not produce a valid session: {error}",
                code="catalog_authoring.invalid_graph",
            ) from error
        accepted = _accepted_draft(
            draft,
            cast(SegmentSessionConfig, validated),
            applied.proposals,
            snapshot,
        )
        if self._is_complete(
            draft,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        ) and not self._is_complete(
            accepted,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        ):
            return held
        return BuilderVisualDraftCommandResult(
            operation=command.operation,
            base_draft_revision=draft.draft_revision,
            draft=accepted,
            affected_kind=affected_kind,
            affected_id=affected_id,
            notice=notice,
            scheduling_preset=scheduling_preset,
        )

    def customize_chain(
        self,
        request: BuilderVisualCustomizeChainRequest,
    ) -> BuilderVisualCustomizeChainResult:
        draft = request.draft
        _assert_visual_draft_ownership(draft)
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before the catalog chain was customized",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )
        snapshot = self._context.repository.snapshot(self._context.scope)

        def refuse(code: str, message: str, path: str) -> BuilderVisualCustomizeChainResult:
            return BuilderVisualCustomizeChainResult(
                applied=False,
                draft=draft,
                issues=(
                    _issue(
                        code,
                        message,
                        target_ref=draft.target_ref,
                        draft_path=path,
                    ),
                ),
            )

        if draft.applied_session is None:
            return refuse(
                "builder.draft.no_valid_projection",
                "Catalog customization requires an accepted session",
                "session_yaml",
            )
        session_json = draft.applied_session
        segment_matches = [
            segment
            for segment in cast(list[JsonDocument], session_json.get("segments", []))
            if segment.get("id") == request.segment_id
        ]
        if len(segment_matches) != 1:
            return refuse(
                "builder.draft.customize_segment_not_unique",
                f"Placed segment {request.segment_id!r} was not found exactly once",
                "segment_id",
            )
        segment = segment_matches[0]
        placement = segment.get("placement")
        selected_ref = (
            cast(JsonDocument, placement).get("from_site_set")
            if isinstance(placement, dict)
            else segment.get("source")
        )
        if not isinstance(selected_ref, str):
            return refuse(
                "builder.draft.customize_root_ref_required",
                "The selected segment does not contain a catalog root reference",
                "segment_id",
            )
        proposed_root = next(
            (item for item in draft.catalog_documents if item.ref == CatalogRef(selected_ref)),
            None,
        )
        root_ref = CatalogRef(selected_ref)
        if any(item.ref == request.leaf_ref for item in draft.catalog_documents):
            return refuse(
                "builder.draft.customize_leaf_proposed",
                f"{request.leaf_ref} is already this session's own document; edit it instead",
                "leaf_ref",
            )
        try:
            if proposed_root is None:
                paths = _dependency_paths(snapshot, root_ref, request.leaf_ref)
            else:
                # The session's own document is the root; the forks start below it,
                # at the catalog documents it references. The session's other
                # documents are edited, not forked.
                root_model = catalog_family_spec(
                    cast(CatalogFamily, root_ref.family)
                ).validate_document(proposed_root.document)
                proposed_refs = {item.ref for item in draft.catalog_documents}
                paths = tuple(
                    path
                    for dependency in catalog_document_references(root_model)
                    if dependency not in proposed_refs
                    for path in _dependency_paths(snapshot, dependency, request.leaf_ref)
                )
        except (CatalogNotFoundError, TypeError, ValueError, yaml.YAMLError) as error:
            return refuse(
                "builder.draft.customize_graph_invalid",
                f"The selected catalog graph could not be inspected: {error}",
                "leaf_ref",
            )
        if not paths:
            return refuse(
                "builder.draft.customize_leaf_unreachable",
                f"{request.leaf_ref} is not a dependency of placed root {root_ref}",
                "leaf_ref",
            )
        if len(paths) != 1:
            return refuse(
                "builder.draft.customize_leaf_ambiguous",
                f"{request.leaf_ref} is reachable through multiple ancestor paths",
                "leaf_ref",
            )
        path = paths[0]
        owner = _proposal_owner(draft.target_ref)
        occupied = {proposal.ref for proposal in draft.catalog_documents}

        def exists(ref: CatalogRef) -> bool:
            return _ref_taken(ref, snapshot, occupied)

        targets: list[CatalogRef] = []
        try:
            for index, source_ref in enumerate(path):
                explicit = request.target_leaf_ref if index == len(path) - 1 else None
                target_ref = (
                    explicit
                    if explicit is not None
                    else _mint_component_ref(
                        _component_family(source_ref),
                        owner,
                        source_ref.relative_path.stem,
                        taken=exists,
                    )
                )
                if exists(target_ref):
                    return refuse(
                        "builder.draft.customize_target_exists",
                        f"Customize-chain target {target_ref} already exists",
                        "target_leaf_ref",
                    )
                occupied.add(target_ref)
                targets.append(target_ref)
        except (TypeError, ValueError) as error:
            return refuse(
                "builder.draft.customize_target_invalid",
                str(error),
                "target_leaf_ref",
            )

        proposals: list[BuilderProposedCatalogDocument] = []
        try:
            for index, (source_ref, target_ref) in enumerate(zip(path, targets, strict=True)):
                replacements = (
                    {path[index + 1]: targets[index + 1]} if index + 1 < len(path) else {}
                )
                proposals.append(
                    BuilderProposedCatalogDocument(
                        ref=target_ref,
                        origin="customized",
                        document=forked_document(
                            *_catalog_object(snapshot, source_ref),
                            target_ref,
                            replacements=replacements,
                        ),
                    )
                )
        except (CatalogReadError, TypeError, ValueError, yaml.YAMLError) as error:
            return refuse(
                "builder.draft.customize_fork_invalid",
                f"The catalog chain could not be forked: {error}",
                "leaf_ref",
            )

        root_target = targets[0]
        if proposed_root is None:
            # A catalog root: the segment is re-pointed at its fork.
            key = "placement" if isinstance(placement, dict) else "source"
            repointed: JsonDocument = (
                {
                    **segment,
                    "placement": {
                        **cast(JsonDocument, placement),
                        "from_site_set": str(root_target),
                    },
                }
                if key == "placement"
                else {**segment, "source": str(root_target)}
            )
            edits: tuple[BuilderContentEdit, ...] = (
                BuilderContentEdit(
                    kind="content",
                    address=BuilderMemberAddress(
                        kind="member", list_pointer="/segments", id=request.segment_id
                    ),
                    base=segment,
                    working=repointed,
                ),
            )
        else:
            # The session's own root document references the fork in place of its source.
            edits = (
                BuilderContentEdit(
                    kind="content",
                    address=BuilderProposalAddress(kind="proposal", ref=root_ref),
                    base=proposed_root.document,
                    working={
                        cast(
                            str, catalog_family_spec(cast(CatalogFamily, root_ref.family)).wrapper
                        ): cast(
                            JsonDocument,
                            rewrite_catalog_references(
                                catalog_family_spec(
                                    cast(CatalogFamily, root_ref.family)
                                ).validate_document(proposed_root.document),
                                {path[0]: root_target},
                            ).model_dump(mode="json", by_alias=True, exclude_none=True),
                        )
                    },
                ),
            )
        # The forks join the proposals as the user's customizations; the edits
        # point the segment or the session's own root at them.
        applied = apply_pending_edits(
            session_json,
            edits,
            proposals=(*draft.catalog_documents, *proposals),
            source_ref=str(draft.target_ref),
        )
        if any(outcome.status != "applied" for outcome in applied.outcomes):
            return refuse(
                "builder.draft.customize_projection_invalid",
                "; ".join(
                    issue.message for outcome in applied.outcomes for issue in outcome.issues
                ),
                "segment_id",
            )
        try:
            _wrapper, validated = validate_referenced_configuration_document(
                draft.target_ref, applied.session
            )
        except (ValidationError, TypeError, ValueError) as error:
            return refuse(
                "builder.draft.customize_projection_invalid",
                f"The customized session is not valid: {error}",
                "segment_id",
            )
        updated = _accepted_draft(
            draft, cast(SegmentSessionConfig, validated), applied.proposals, snapshot
        )
        chain = tuple(
            BuilderVisualCustomizeChainEntry(source_ref=source_ref, target_ref=target_ref)
            for source_ref, target_ref in zip(path, targets, strict=True)
        )
        return BuilderVisualCustomizeChainResult(
            applied=True,
            draft=updated,
            # The head of the forked chain: the placed root, or for a root the
            # session proposes, the catalog document below it.
            root_source_ref=path[0],
            root_target_ref=root_target,
            forked_chain=chain,
        )

    def compile(
        self,
        request: BuilderVisualDraftCompileRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftAssemblyResult:
        visual_draft = request.draft
        _assert_visual_draft_ownership(visual_draft)
        snapshot: CatalogReadSnapshot = self._context.repository.snapshot(self._context.scope)
        return _compile_visual_application(
            visual_draft,
            snapshot,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )
