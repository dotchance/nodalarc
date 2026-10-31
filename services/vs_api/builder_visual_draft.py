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
    CatalogClosureCollector,
    CatalogReadError,
    CatalogReadView,
    catalog_document_references,
    load_catalog_object,
)
from nodalarc.catalog_refs import BodyRef, CatalogFamily, CatalogRef, SessionRef, SiteRef
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
from nodalarc.marked_yaml import MarkedYamlError, YamlSourceMap, load_marked_yaml
from nodalarc.models.builder_api import (
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderContentEdit,
    BuilderDraftEnvelope,
    BuilderIssue,
    BuilderListEdit,
    BuilderMemberAddress,
    BuilderPendingEdits,
    BuilderProposedCatalogDocument,
    BuilderSessionSaveRequest,
    BuilderVerdict,
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
    BuilderVisualAddGroundCommand,
    BuilderVisualAddGroundSiteNodeCommand,
    BuilderVisualAddGroundSiteReferenceCommand,
    BuilderVisualAddNodeEthernetPortCommand,
    BuilderVisualAddOrIncrementNodeTerminalCommand,
    BuilderVisualAddRoutingDomainCommand,
    BuilderVisualAuthorInlineSpaceNodeCommand,
    BuilderVisualConnectSegmentsCommand,
    BuilderVisualControlMutationRequest,
    BuilderVisualCustomizeChainEntry,
    BuilderVisualCustomizeChainRequest,
    BuilderVisualCustomizeChainResult,
    BuilderVisualDraftAcceptStateRequest,
    BuilderVisualDraftApplyEditsRequest,
    BuilderVisualDraftApplyEditsResult,
    BuilderVisualDraftApplyWorkspaceRequest,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftApplyYamlResult,
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
    BuilderVisualGroundReference,
    BuilderVisualGroundStamp,
    BuilderVisualLinkEndpoint,
    BuilderVisualLinkRule,
    BuilderVisualLinkRuleListing,
    BuilderVisualLinkRuleWidget,
    BuilderVisualMintGroundMembersCommand,
    BuilderVisualNode,
    BuilderVisualObjectControlEdit,
    BuilderVisualObjectEditRequest,
    BuilderVisualObjectEditResult,
    BuilderVisualObjectWidget,
    BuilderVisualOrbit,
    BuilderVisualPlaceGroundReferenceCommand,
    BuilderVisualPlaceSpaceReferenceCommand,
    BuilderVisualRederiveLinkCommand,
    BuilderVisualRoutingBoundariesWidget,
    BuilderVisualRoutingBoundary,
    BuilderVisualRoutingBoundaryListing,
    BuilderVisualRoutingDomain,
    BuilderVisualRoutingDomainListing,
    BuilderVisualRoutingDomainWidget,
    BuilderVisualSessionListing,
    BuilderVisualSetGroundSiteNodeCommand,
    BuilderVisualSetGroundStampNodeCommand,
    BuilderVisualSetNodeTerminalRoleCommand,
    BuilderVisualSetSchedulingPresetCommand,
    BuilderVisualSetSpacePopulationCommand,
    BuilderVisualSite,
    BuilderVisualSiteNode,
    BuilderVisualSpaceBoresight,
    BuilderVisualSpaceDraft,
    BuilderVisualSpaceReference,
    BuilderVisualTerminalMount,
    BuilderVisualWalkerLayoutRequest,
    BuilderVisualWorkspace,
    derive_walker_layout,
)
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.catalog import (
    Constellation,
    Node,
    Site,
    SiteSet,
    SpaceNodeSet,
    Terminal,
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
    AggregateOf,
    AreaAssignment,
    CandidateLimits,
    RoutingBoundary,
    RoutingDomain,
    RoutingTimers,
    SegmentSessionConfig,
    SessionMeta,
    TimeConfig,
)
from nodalarc.models.segments import GroundPlacement, GroundSegment, SpaceSegment
from nodalarc.resolve_session import SessionResolutionError, endpoint_terminal_role
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

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
    DEFAULT_TERMINAL_MOUNT_COUNT,
    SINGLE_PLANE_PHASING_MODE,
    scheduling_preset_block,
)
from .catalog_context import CatalogContext

Clock = Callable[[], datetime]

_DE440S_EPHEMERIS: JsonDocument = {
    "provider": "skyfield_bsp",
    "quality_tier": "de440s",
    "kernels": [
        {
            "id": "de440s",
            "path": "configs/ephemerides/de440s.bsp",
            "sha256": "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2",
            "coverage_start": "1849-12-25T00:00:00Z",
            "coverage_end": "2150-01-21T00:00:00Z",
            "targets": ["nodalarc:bodies/earth.yaml", "nodalarc:bodies/luna.yaml"],
            "frame": "gcrs",
        }
    ],
}
_BUILDER_GENERATED_COMPONENT_FAMILIES = frozenset(
    {"constellations", "orbits", "nodes", "site-sets", "sites"}
)
BUILDER_VISUAL_SPECIALIZED_FIELDS = frozenset(
    {
        (SessionMeta, "name"),
        (SessionMeta, "display_name"),
        (SessionMeta, "description"),
        (TimeConfig, "start_time"),
        (TimeConfig, "step_seconds"),
        (TimeConfig, "compression"),
        (CandidateLimits, "max_pairs_per_rule"),
        (CandidateLimits, "max_pairs_per_tick"),
        (SpaceSegment, "id"),
        (SpaceSegment, "source"),
        (GroundSegment, "id"),
        (GroundPlacement, "from_site_set"),
    }
)


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
class _CatalogProjectionFact:
    label: str
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class _DerivedLinkPhysics:
    role: Literal["access", "isl", "crosslink", "backbone"]
    medium: Literal["rf", "optical"]
    topology_mode: Literal["visible_candidates", "nearest_n"]
    topology_n: int
    formable: bool


def _placed_segments(workspace: BuilderVisualWorkspace) -> tuple[_PlacedSegment, ...]:
    return (
        *(_PlacedSegment(item.segment_id, "space") for item in workspace.space_refs),
        *(_PlacedSegment(item.segment_id, "space") for item in workspace.space),
        *(_PlacedSegment(item.segment_id, "ground") for item in workspace.ground_refs),
        *(_PlacedSegment(item.segment_id, "ground") for item in workspace.ground),
    )


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

    These are the allocation history and every id the accepted session, the
    pending edits and the twin define or reference.
    """
    return {
        *draft.reserved_authoring_ids,
        *_current_and_referenced_authoring_ids(
            draft.applied_session,
            pending_edits=pending_edits,
            workspace=draft.authoring_workspace,
        ),
    }


def _current_and_referenced_authoring_ids(
    session: JsonDocument | None,
    *,
    pending_edits: BuilderPendingEdits = (),
    workspace: BuilderVisualWorkspace | None = None,
) -> tuple[str, ...]:
    """Every segment, link rule and routing domain id defined or referenced, in order.

    The walk covers a session document, the working JSON of pending edits and
    the twin. An object the session cannot hold yet is in the twin only.
    Incomplete JSON contributes the ids it holds. A routing boundary has no id
    of its own.
    """
    found = [
        *_session_authoring_ids(session),
        *(_twin_authoring_ids(workspace) if workspace is not None else ()),
        *(
            authoring_id
            for edit in pending_edits
            for authoring_id in _pending_edit_authoring_ids(edit)
        ),
    ]
    return tuple(dict.fromkeys(item for item in found if item))


def _twin_authoring_ids(workspace: BuilderVisualWorkspace) -> Iterator[str]:
    """Every segment id the twin defines."""
    yield from (item.segment_id for item in _placed_segments(workspace))


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
    elif edit.address.list_pointer == "/link_rules":
        yield from _link_rule_authoring_ids(edit.working)
    else:
        yield from _routing_domain_authoring_ids(edit.working)


def _next_number(prefix: str, values: set[str]) -> int:
    number = 1
    while f"{prefix}-{number}" in values:
        number += 1
    return number


def _catalog_label(model: BaseModel) -> str:
    display_name = getattr(model, "display_name", None)
    if isinstance(display_name, str) and display_name.strip():
        return display_name.strip()
    identifier = getattr(model, "id", None)
    if not isinstance(identifier, str) or not identifier:
        raise TypeError(f"{type(model).__name__} has no catalog identity")
    return identifier


def _site_summary(site: Site) -> str | None:
    summary = site.verified.notes if site.verified is not None else None
    if summary is None:
        return None
    normalized = " ".join(summary.split())
    if len(normalized) > 512:
        normalized = normalized[:509].rstrip() + "..."
    return normalized or None


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


def _yaml_issue(
    code: str,
    message: str,
    *,
    target_ref: SessionRef,
    json_pointer: str | None = None,
    source_line: int | None = None,
    source_column: int | None = None,
) -> BuilderIssue:
    return BuilderIssue(
        code=code,
        stage="structural",
        severity="error",
        message=message,
        blocks=("save", "deploy"),
        source_ref=str(target_ref),
        json_pointer=json_pointer,
        draft_path="session_yaml",
        source_line=source_line,
        source_column=source_column,
    )


def _issue_with_yaml_source(
    issue: BuilderIssue,
    source_map: YamlSourceMap,
    *,
    prefer_key: bool = False,
) -> BuilderIssue:
    pointer = issue.json_pointer or ""
    span = source_map.span_for(pointer, prefer_key=prefer_key)
    if span is None:
        return issue
    return issue.model_copy(
        update={
            "source_line": span.start.line,
            "source_column": span.start.column,
        }
    )


def _parse_session_yaml_application(
    target_ref: SessionRef,
    yaml_text: str,
) -> tuple[
    SegmentSessionConfig | None,
    JsonDocument | None,
    str | None,
    YamlSourceMap | None,
    tuple[BuilderIssue, ...],
]:
    try:
        marked = load_marked_yaml(yaml_text)
    except (UnicodeError, MarkedYamlError, RecursionError) as error:
        mark = error.problem_mark if isinstance(error, MarkedYamlError) else None
        return (
            None,
            None,
            None,
            None,
            (
                _yaml_issue(
                    "builder.draft.yaml.invalid_syntax",
                    f"Session YAML is invalid: {error}",
                    target_ref=target_ref,
                    source_line=mark.line if mark is not None else None,
                    source_column=mark.column if mark is not None else None,
                ),
            ),
        )

    try:
        model = SegmentSessionConfig.model_validate(marked.data)
    except ValidationError as error:
        issues = tuple(
            _issue_with_yaml_source(
                _yaml_issue(
                    f"builder.structural.{item.type}",
                    item.message,
                    target_ref=target_ref,
                    json_pointer=item.pointer or None,
                ),
                marked.source_map,
                prefer_key=item.type == "extra_forbidden",
            )
            for item in document_errors(error, marked.data)
        )
        return None, None, None, marked.source_map, issues
    except (TypeError, ValueError) as error:
        return (
            None,
            None,
            None,
            marked.source_map,
            (
                _issue_with_yaml_source(
                    _yaml_issue(
                        "builder.structural.invalid_document",
                        str(error),
                        target_ref=target_ref,
                    ),
                    marked.source_map,
                ),
            ),
        )

    if model.session.name != target_ref.relative_path.stem:
        issue = _issue_with_yaml_source(
            _yaml_issue(
                "builder.draft.yaml.fixed_identity",
                "Session identity is fixed by its user: reference",
                target_ref=target_ref,
                json_pointer="/session/name",
            ),
            marked.source_map,
            prefer_key=True,
        )
        return None, None, None, marked.source_map, (issue,)

    try:
        canonical = canonicalize_persisted_configuration(
            target_ref,
            cast(
                JsonDocument,
                model.model_dump(mode="json", by_alias=True, exclude_none=True),
            ),
        )
    except (ValidationError, TypeError, ValueError) as error:
        return (
            None,
            None,
            None,
            marked.source_map,
            (
                _issue_with_yaml_source(
                    _yaml_issue(
                        "builder.structural.invalid_document",
                        str(error),
                        target_ref=target_ref,
                    ),
                    marked.source_map,
                ),
            ),
        )
    return (
        model,
        canonical.canonical_json,
        canonical.yaml_bytes.decode("utf-8"),
        marked.source_map,
        (),
    )


class _Assembly:
    def __init__(self, draft: BuilderVisualDraftEnvelope) -> None:
        self.draft = draft
        self.issues: list[BuilderIssue] = []
        self.proposals: dict[CatalogRef, BuilderProposedCatalogDocument] = {}
        self.derived_ids: set[str] = set()
        self.owner = _proposal_owner(draft.target_ref)
        for proposal in draft.catalog_documents:
            self.proposals[proposal.ref] = proposal

    def issue(self, code: str, message: str, path: str) -> None:
        self.issues.append(_issue(code, message, target_ref=self.draft.target_ref, draft_path=path))

    def required_identifier(self, value: str, *, path: str, fallback: str) -> str:
        normalized = _identifier(value)
        if not normalized:
            self.issue("builder.draft.identifier_required", "A valid identifier is required", path)
            return fallback
        if normalized != value:
            self.issue(
                "builder.draft.identifier_not_canonical",
                f"Identifier {value!r} must be authored as {normalized!r}",
                path,
            )
        return normalized

    def unique_derived_id(self, value: str, *, path: str, fallback: str) -> str:
        base = self.required_identifier(value, path=path, fallback=fallback)
        if base not in self.derived_ids:
            self.derived_ids.add(base)
            return base
        for suffix in range(2, 1_000):
            candidate = _identifier(f"{base[:40]}-{suffix}")
            if candidate not in self.derived_ids:
                self.derived_ids.add(candidate)
                self.issue(
                    "builder.draft.derived_identity_collision",
                    f"Derived identifier {base!r} collides with another authored object",
                    path,
                )
                return candidate
        self.issue(
            "builder.draft.derived_identity_exhausted",
            f"Unable to allocate a unique identifier for {base!r}",
            path,
        )
        return fallback

    def component_ref(self, family: str, object_id: str) -> CatalogRef:
        return _owned_component_ref(family, self.owner, object_id)

    def propose(self, ref: CatalogRef, document: JsonDocument, *, path: str) -> None:
        candidate = BuilderProposedCatalogDocument(
            ref=ref,
            document=document,
            origin="generated",
        )
        existing = self.proposals.get(ref)
        if existing is None or existing.origin == "generated":
            self.proposals[ref] = candidate
        elif existing.document != candidate.document:
            self.issue(
                "builder.draft.component_identity_collision",
                f"Multiple authored components target {ref} with different content",
                path,
            )


def _node_document(
    assembly: _Assembly,
    node: BuilderVisualNode,
    *,
    path: str,
    fallback_id: str,
) -> tuple[str, JsonDocument]:
    node_id = assembly.required_identifier(node.id, path=f"{path}.id", fallback=fallback_id)
    if not node.display_name:
        assembly.issue(
            "builder.draft.node_display_name_required",
            "Node display name is required",
            f"{path}.display_name",
        )
    if node.forwarding is None:
        assembly.issue(
            "builder.draft.node_forwarding_required",
            "Node forwarding mode is required",
            f"{path}.forwarding",
        )
    terminals: list[JsonValue] = []
    for index, mount in enumerate(node.terminals):
        mount_path = f"{path}.terminals.{index}"
        mount_id = assembly.required_identifier(
            mount.mount_id,
            path=f"{mount_path}.mount_id",
            fallback=f"mount-{index + 1}",
        )
        for field_name, value in (
            ("role", mount.role),
            ("terminal_ref", mount.terminal_ref),
            ("count", mount.count),
        ):
            if value is None:
                assembly.issue(
                    f"builder.draft.terminal_mount_{field_name}_required",
                    f"Terminal mount {field_name.replace('_', ' ')} is required",
                    f"{mount_path}.{field_name}",
                )
        terminals.append(
            {
                "id": mount_id,
                "role": cast(JsonValue, mount.role),
                "terminal": cast(
                    JsonValue, str(mount.terminal_ref) if mount.terminal_ref else None
                ),
                "count": cast(JsonValue, mount.count),
                **(
                    {"boresight": mount.boresight.model_dump(mode="json")}
                    if mount.boresight is not None
                    else {}
                ),
            }
        )
    profile = node.profile
    return node_id, {
        "node": {
            "id": node_id,
            "display_name": node.display_name,
            "forwarding": cast(JsonValue, node.forwarding),
            **({"profile": cast(JsonValue, str(profile))} if profile is not None else {}),
            "ethernet": [
                {"id": assembly.required_identifier(port, path=path, fallback="terr0")}
                for port in node.ethernet
            ],
            "terminals": terminals,
            "payloads": [],
        }
    }


def _orbit_document(
    assembly: _Assembly,
    orbit: BuilderVisualOrbit,
    *,
    orbit_id: str,
    epoch: str,
    path: str,
) -> JsonDocument:
    required = {
        "central_body": orbit.central_body,
        "shape_kind": orbit.shape_kind,
        "inclination_deg": orbit.inclination_deg,
        "raan_deg": orbit.raan_deg,
        "argument_of_perigee_deg": orbit.argument_of_perigee_deg,
        "mean_anomaly_deg": orbit.mean_anomaly_deg,
        "propagator": orbit.propagator,
    }
    for field_name, value in required.items():
        if value is None:
            assembly.issue(
                f"builder.draft.orbit_{field_name}_required",
                f"Orbit {field_name.replace('_', ' ')} is required",
                f"{path}.{field_name}",
            )
    if orbit.shape_kind == "elliptical":
        shape: JsonDocument = {
            "perigee_altitude_km": cast(JsonValue, orbit.perigee_altitude_km),
            "apogee_altitude_km": cast(JsonValue, orbit.apogee_altitude_km),
        }
        for field_name in ("perigee_altitude_km", "apogee_altitude_km"):
            if getattr(orbit, field_name) is None:
                assembly.issue(
                    f"builder.draft.orbit_{field_name}_required",
                    f"Orbit {field_name.replace('_', ' ')} is required",
                    f"{path}.{field_name}",
                )
    else:
        shape = {"altitude_km": cast(JsonValue, orbit.altitude_km)}
        if orbit.altitude_km is None:
            assembly.issue(
                "builder.draft.orbit_altitude_required",
                "Orbit altitude is required",
                f"{path}.altitude_km",
            )
    return {
        "orbit": {
            "id": orbit_id,
            "central_body": cast(
                JsonValue, str(orbit.central_body) if orbit.central_body else None
            ),
            "epoch": epoch,
            "shape": shape,
            "orientation": {
                "inclination_deg": cast(JsonValue, orbit.inclination_deg),
                "raan_deg": cast(JsonValue, orbit.raan_deg),
                "argument_of_perigee_deg": cast(JsonValue, orbit.argument_of_perigee_deg),
            },
            "phase": {"mean_anomaly_deg": cast(JsonValue, orbit.mean_anomaly_deg)},
            "propagator": cast(JsonValue, orbit.propagator),
            "reference": "urn:nodalarc:session-builder-draft",
        }
    }


def _site_document(
    assembly: _Assembly,
    site: BuilderVisualSite | None,
    *,
    member: BuilderVisualGroundMember,
    path: str,
    fallback_id: str,
) -> tuple[str, JsonDocument]:
    if site is None:
        assembly.issue(
            "builder.draft.site_document_required",
            "Draft ground members require a complete site object",
            f"{path}.site",
        )
        site = BuilderVisualSite(site_id=member.site_id, display_name=member.label)
    site_id = assembly.required_identifier(
        site.site_id or member.site_id,
        path=f"{path}.site.site_id",
        fallback=fallback_id,
    )
    for field_name, value in (
        ("display_name", site.display_name),
        ("body", site.body),
        ("lat_deg", site.lat_deg),
        ("lon_deg", site.lon_deg),
        ("alt_m", site.alt_m),
    ):
        if value is None or value == "":
            assembly.issue(
                f"builder.draft.site_{field_name}_required",
                f"Site {field_name.replace('_', ' ')} is required",
                f"{path}.site.{field_name}",
            )
    nodes: list[JsonValue] = []
    for index, node in enumerate(site.nodes):
        node_path = f"{path}.site.nodes.{index}"
        node_id = assembly.required_identifier(
            node.node_id,
            path=f"{node_path}.node_id",
            fallback=f"node-{index + 1}",
        )
        if node.node_ref is None:
            assembly.issue(
                "builder.draft.site_node_ref_required",
                "Installed site nodes require a node model reference",
                f"{node_path}.node_ref",
            )
        unknown_boresights = sorted(set(node.boresights).difference(node.installed))
        for mount in unknown_boresights:
            assembly.issue(
                "builder.draft.site_node_boresight_without_installation",
                f"Ground boresight {mount!r} requires an installed terminal mount",
                f"{node_path}.boresights.{mount}",
            )
        terminals: dict[str, JsonValue] = {}
        for mount, count in node.installed.items():
            boresight = node.boresights.get(mount)
            terminals[mount] = {
                "installed_count": count,
                **(
                    {
                        "capabilities": {
                            "boresight": boresight.model_dump(mode="json"),
                        }
                    }
                    if boresight is not None
                    else {}
                ),
            }
        nodes.append(
            {
                "id": node_id,
                "node": cast(JsonValue, str(node.node_ref) if node.node_ref is not None else None),
                "payloads": {},
                "terminals": terminals,
                "interfaces": {"terr0": "lan0"},
            }
        )
    return site_id, {
        "site": {
            "id": site_id,
            "display_name": site.display_name,
            "ethernet": [{"id": "lan0"}],
            **({"tags": [_identifier(tag) for tag in site.tags]} if site.tags else {}),
            "nodes": nodes,
            "frame": {
                "body_fixed": {"body": cast(JsonValue, str(site.body) if site.body else None)}
            },
            "location": {
                "lat_deg": cast(JsonValue, site.lat_deg),
                "lon_deg": cast(JsonValue, site.lon_deg),
                "alt_m": cast(JsonValue, site.alt_m),
            },
        }
    }


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


def _proposal_documents(
    proposals: tuple[BuilderProposedCatalogDocument, ...],
) -> dict[CatalogRef, JsonDocument]:
    return {proposal.ref: proposal.document for proposal in proposals}


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


def _stored_catalog_facts(
    snapshot: CatalogReadSnapshot,
    root_yaml: bytes,
) -> dict[CatalogRef, _CatalogProjectionFact]:
    closure = CatalogClosureCollector.collect(root_yaml, snapshot)
    catalog_facts: dict[CatalogRef, _CatalogProjectionFact] = {}
    for entry in closure.entries:
        stored_document = snapshot.get(entry.ref)
        parsed = load_configuration_yaml(stored_document.content)
        if not isinstance(parsed, dict):
            continue
        document = cast(JsonDocument, parsed)
        model = catalog_family_spec(cast(CatalogFamily, entry.ref.family)).validate_document(
            document
        )
        catalog_facts[entry.ref] = _CatalogProjectionFact(
            label=_catalog_label(model),
            summary=_site_summary(model) if isinstance(model, Site) else None,
        )
    return catalog_facts


def _authored_space_projection(
    segment: SpaceSegment,
    proposals: dict[CatalogRef, JsonDocument],
    *,
    generated_refs: frozenset[CatalogRef],
) -> BuilderVisualSpaceDraft | None:
    constellation_ref = CatalogRef(segment.source)
    constellation_document = proposals.get(constellation_ref)
    if constellation_document is None or constellation_ref not in generated_refs:
        return None
    constellation = constellation_document.get("constellation")
    if not isinstance(constellation, dict):
        return None
    orbit_ref = constellation.get("orbit")
    node_ref = constellation.get("node")
    if not isinstance(orbit_ref, str) or not isinstance(node_ref, str):
        return None
    orbit_document = proposals.get(CatalogRef(orbit_ref))
    if orbit_document is None or not isinstance(orbit_document.get("orbit"), dict):
        return None
    orbit = cast(JsonDocument, orbit_document["orbit"])
    shape = orbit.get("shape")
    orientation = orbit.get("orientation")
    phase = orbit.get("phase")
    if (
        not isinstance(shape, dict)
        or not isinstance(orientation, dict)
        or not isinstance(phase, dict)
    ):
        return None
    shape_kind: Literal["circular", "elliptical"] = (
        "circular" if "altitude_km" in shape else "elliptical"
    )
    circular_altitude = shape.get("altitude_km") if shape_kind == "circular" else None
    node_draft: BuilderVisualNode | None = None
    parsed_node_ref = CatalogRef(node_ref)
    proposed_node = proposals.get(parsed_node_ref)
    if (
        parsed_node_ref in generated_refs
        and proposed_node is not None
        and isinstance(proposed_node.get("node"), dict)
    ):
        node = cast(JsonDocument, proposed_node["node"])
        terminal_mounts: list[BuilderVisualTerminalMount] = []
        for mount in cast(list[JsonDocument], node.get("terminals", [])):
            boresight = mount.get("boresight")
            terminal_mounts.append(
                BuilderVisualTerminalMount(
                    mount_id=cast(str, mount.get("id", "")),
                    role=cast(Any, mount.get("role")),
                    terminal_ref=cast(Any, mount.get("terminal")),
                    count=cast(Any, mount.get("count")),
                    boresight=(
                        BuilderVisualSpaceBoresight.model_validate(boresight)
                        if isinstance(boresight, dict)
                        else None
                    ),
                )
            )
        node_draft = BuilderVisualNode(
            id=cast(str, node.get("id", "")),
            display_name=cast(str, node.get("display_name", "")),
            forwarding=cast(Any, node.get("forwarding")),
            profile=cast(Any, node.get("profile")),
            ethernet=tuple(
                cast(str, item.get("id", ""))
                for item in cast(list[JsonDocument], node.get("ethernet", []))
            ),
            terminals=tuple(terminal_mounts),
        )
    planes = cast(JsonDocument, constellation.get("planes", {}))
    phasing = cast(JsonDocument, constellation.get("phasing", {}))
    return BuilderVisualSpaceDraft(
        segment_id=segment.id,
        display_name=cast(
            str, constellation.get("display_name", segment.display_name or segment.id)
        ),
        node_ref=None if node_draft is not None else CatalogRef(node_ref),
        node_draft=node_draft,
        orbit=BuilderVisualOrbit(
            central_body=cast(Any, orbit.get("central_body")),
            shape_kind=shape_kind,
            altitude_km=cast(Any, shape.get("altitude_km")),
            perigee_altitude_km=cast(
                Any,
                circular_altitude
                if circular_altitude is not None
                else shape.get("perigee_altitude_km"),
            ),
            apogee_altitude_km=cast(
                Any,
                circular_altitude
                if circular_altitude is not None
                else shape.get("apogee_altitude_km"),
            ),
            inclination_deg=cast(Any, orientation.get("inclination_deg")),
            raan_deg=cast(Any, orientation.get("raan_deg")),
            argument_of_perigee_deg=cast(Any, orientation.get("argument_of_perigee_deg")),
            mean_anomaly_deg=cast(Any, phase.get("mean_anomaly_deg")),
            propagator=cast(Any, orbit.get("propagator")),
        ),
        planes=cast(Any, planes.get("count")),
        raan_spacing_deg=cast(Any, planes.get("raan_spacing_deg")),
        slots_per_plane=cast(Any, constellation.get("slots_per_plane")),
        phasing_mode=cast(Any, phasing.get("mode")),
        phase_offset_deg=cast(Any, phasing.get("phase_offset_deg")),
    )


def _authored_site_projection(document: JsonDocument) -> BuilderVisualSite | None:
    site = document.get("site")
    if not isinstance(site, dict):
        return None
    frame = site.get("frame")
    body_fixed = frame.get("body_fixed") if isinstance(frame, dict) else None
    location = site.get("location")
    if not isinstance(body_fixed, dict) or not isinstance(location, dict):
        return None
    nodes: list[BuilderVisualSiteNode] = []
    for raw_node in cast(list[JsonDocument], site.get("nodes", [])):
        terminals = raw_node.get("terminals")
        interfaces = raw_node.get("interfaces")
        if not isinstance(terminals, dict) or not isinstance(interfaces, dict):
            return None
        installed: dict[str, int] = {}
        boresights: dict[str, BuilderVisualGroundBoresight] = {}
        for mount_id, raw_installation in terminals.items():
            if not isinstance(raw_installation, dict):
                return None
            installed[mount_id] = cast(int, raw_installation.get("installed_count", 0))
            capabilities = raw_installation.get("capabilities")
            boresight = capabilities.get("boresight") if isinstance(capabilities, dict) else None
            if isinstance(boresight, dict):
                boresights[mount_id] = BuilderVisualGroundBoresight.model_validate(boresight)
        nodes.append(
            BuilderVisualSiteNode(
                node_id=cast(str, raw_node.get("id", "")),
                node_ref=cast(Any, raw_node.get("node")),
                installed=installed,
                boresights=boresights,
            )
        )
    return BuilderVisualSite(
        site_id=cast(str, site.get("id", "")),
        display_name=cast(str, site.get("display_name", "")),
        body=cast(Any, body_fixed.get("body")),
        lat_deg=cast(Any, location.get("lat_deg")),
        lon_deg=cast(Any, location.get("lon_deg")),
        alt_m=cast(Any, location.get("alt_m")),
        tags=tuple(cast(list[str], site.get("tags", []))),
        nodes=tuple(nodes),
    )


def _authored_ground_projection(
    segment: GroundSegment,
    proposals: dict[CatalogRef, JsonDocument],
    *,
    generated_refs: frozenset[CatalogRef],
    catalog_facts: Mapping[CatalogRef, _CatalogProjectionFact],
    prior: BuilderVisualGroundDraft | None,
) -> BuilderVisualGroundDraft | None:
    site_set_ref = CatalogRef(segment.placement.from_site_set)
    site_set_document = proposals.get(site_set_ref)
    if site_set_document is None or site_set_ref not in generated_refs:
        return None
    site_set = site_set_document.get("site_set")
    if not isinstance(site_set, dict):
        return None
    overrides = {
        override.match.site: override.scheduling.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        for override in segment.overrides or ()
        if override.scheduling is not None
    }
    prior_members_by_ref = (
        {member.ref: member for member in prior.members if member.ref is not None}
        if prior is not None
        else {}
    )
    prior_members_by_site = (
        {member.site_id: member for member in prior.members if member.site_id}
        if prior is not None
        else {}
    )
    members: list[BuilderVisualGroundMember] = []
    for index, raw_ref in enumerate(cast(list[str], site_set.get("sites", []))):
        ref = SiteRef(raw_ref)
        site = _authored_site_projection(proposals.get(ref, {})) if ref in generated_refs else None
        site_id = site.site_id if site is not None else ref.relative_path.stem
        previous = prior_members_by_ref.get(ref) or prior_members_by_site.get(site_id)
        fact = catalog_facts.get(ref)
        members.append(
            BuilderVisualGroundMember(
                member_id=previous.member_id if previous is not None else f"member-{index + 1}",
                kind="draft" if site is not None else "ref",
                ref=None if site is not None else ref,
                site_id=site_id,
                label=(
                    site.display_name
                    if site is not None
                    else fact.label
                    if fact is not None
                    else previous.label
                    if previous is not None
                    else site_id
                ),
                summary=(
                    fact.summary
                    if site is None and fact is not None
                    else previous.summary
                    if site is None and previous is not None
                    else None
                ),
                site=site,
                scheduling_override=cast(JsonDocument | None, overrides.get(site_id)),
            )
        )
    scheduling = (
        segment.apply.scheduling.model_dump(mode="json", by_alias=True, exclude_none=True)
        if segment.apply is not None and segment.apply.scheduling is not None
        else {}
    )
    originated_ipv4 = (
        segment.apply.originated_prefixes.ipv4
        if segment.apply is not None and segment.apply.originated_prefixes is not None
        else None
    )
    first_site = next((member.site for member in members if member.site is not None), None)
    first_node = first_site.nodes[0] if first_site is not None and first_site.nodes else None
    stamp = (
        prior.stamp
        if prior is not None
        else BuilderVisualGroundStamp(
            node_ref=first_node.node_ref if first_node is not None else None,
            installed=first_node.installed if first_node is not None else {},
            boresights=first_node.boresights if first_node is not None else {},
            body=first_site.body if first_site is not None else DEFAULT_BODY_REF,
        )
    )
    return BuilderVisualGroundDraft(
        segment_id=segment.id,
        display_name=segment.display_name or cast(str, site_set.get("display_name", segment.id)),
        members=tuple(members),
        stamp=stamp,
        scheduling=cast(JsonDocument, scheduling),
        originated_ipv4=tuple(originated_ipv4 or ()),
        tags=tuple(segment.apply.tags or ()) if segment.apply is not None else (),
    )


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


def _object_editor(address: BuilderMemberAddress | BuilderWholeListAddress) -> _ObjectEditor:
    if isinstance(address, BuilderWholeListAddress):
        return _OBJECT_EDITORS[address.pointer]
    return _OBJECT_EDITORS[address.list_pointer]


def _editor_document(
    address: BuilderMemberAddress | BuilderWholeListAddress,
    working: JsonDocument | list[JsonValue],
) -> JsonDocument:
    """The document an object's field editor covers: the object, or the list inside its root."""
    if isinstance(address, BuilderWholeListAddress):
        return {"boundaries": cast(list[JsonValue], working)}
    return cast(JsonDocument, working)


def _object_control_build(
    address: BuilderMemberAddress | BuilderWholeListAddress,
    working: JsonDocument | list[JsonValue],
    *,
    applied_revision: int,
) -> BuilderControlTreeBuild:
    editor = _object_editor(address)
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
    address: BuilderMemberAddress | BuilderWholeListAddress,
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
        validated = _object_editor(address).root_model.model_validate(working)
    except ValidationError as error:
        return None, _object_validation_issues(
            error, document=working, at=(), source_ref=source_ref
        )
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


def _workspace_from_applied_session(
    session: SegmentSessionConfig,
    *,
    proposals: tuple[BuilderProposedCatalogDocument, ...] = (),
    catalog_facts: Mapping[CatalogRef, _CatalogProjectionFact] | None = None,
    prior_workspace: BuilderVisualWorkspace | None = None,
) -> BuilderVisualWorkspace:
    space_refs: list[BuilderVisualSpaceReference] = []
    space_drafts: list[BuilderVisualSpaceDraft] = []
    ground_refs: list[BuilderVisualGroundReference] = []
    ground_drafts: list[BuilderVisualGroundDraft] = []
    proposal_documents = _proposal_documents(proposals)
    generated_refs = frozenset(
        proposal.ref for proposal in proposals if proposal.origin == "generated"
    )
    facts = catalog_facts or {}
    prior_space_refs = (
        {item.segment_id: item for item in prior_workspace.space_refs}
        if prior_workspace is not None
        else {}
    )
    prior_ground_refs = (
        {item.segment_id: item for item in prior_workspace.ground_refs}
        if prior_workspace is not None
        else {}
    )
    prior_ground_drafts = (
        {item.segment_id: item for item in prior_workspace.ground}
        if prior_workspace is not None
        else {}
    )
    for segment in session.segments:
        if isinstance(segment, SpaceSegment):
            authored = _authored_space_projection(
                segment,
                proposal_documents,
                generated_refs=generated_refs,
            )
            if authored is None:
                prior = prior_space_refs.get(segment.id)
                fact = facts.get(CatalogRef(segment.source))
                space_refs.append(
                    BuilderVisualSpaceReference(
                        segment_id=segment.id,
                        source_ref=segment.source,
                        label=(
                            fact.label
                            if fact is not None
                            else prior.label
                            if prior is not None
                            else segment.display_name or segment.id
                        ),
                    )
                )
            else:
                space_drafts.append(authored)
        elif isinstance(segment, GroundSegment):
            scheduling: JsonDocument = {}
            if segment.apply is not None and segment.apply.scheduling is not None:
                scheduling = cast(
                    JsonDocument,
                    segment.apply.scheduling.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    ),
                )
            authored = _authored_ground_projection(
                segment,
                proposal_documents,
                generated_refs=generated_refs,
                catalog_facts=facts,
                prior=prior_ground_drafts.get(segment.id),
            )
            if authored is None:
                prior = prior_ground_refs.get(segment.id)
                fact = facts.get(CatalogRef(segment.placement.from_site_set))
                ground_refs.append(
                    BuilderVisualGroundReference(
                        segment_id=segment.id,
                        site_set_ref=segment.placement.from_site_set,
                        label=(
                            fact.label
                            if fact is not None
                            else prior.label
                            if prior is not None
                            else segment.display_name or segment.id
                        ),
                        scheduling=scheduling,
                    )
                )
            else:
                ground_drafts.append(authored)

    max_pairs_per_rule: int | None = None
    max_pairs_per_tick: int | None = None
    if session.simulation is not None and session.simulation.candidate_limits is not None:
        limits = session.simulation.candidate_limits
        max_pairs_per_rule = limits.max_pairs_per_rule
        max_pairs_per_tick = limits.max_pairs_per_tick

    return BuilderVisualWorkspace(
        session_name=session.session.name,
        display_name=session.session.display_name,
        description=session.session.description,
        space=tuple(space_drafts),
        space_refs=tuple(space_refs),
        ground=tuple(ground_drafts),
        ground_refs=tuple(ground_refs),
        max_pairs_per_rule=max_pairs_per_rule,
        max_pairs_per_tick=max_pairs_per_tick,
        start_time=session.time.start_time,
        step_seconds=session.time.step_seconds,
        compression=session.time.compression,
    )


def _applied_control_tree(session: SegmentSessionConfig, revision: int) -> BuilderControlTree:
    """The control tree of an applied session, bound to its revision."""

    return build_session_control_tree(
        session,
        projection_revision=revision,
        specialized_fields=BUILDER_VISUAL_SPECIALIZED_FIELDS,
    ).tree


def _assemble_authoring_workspace(
    draft: BuilderVisualDraftEnvelope,
) -> tuple[JsonDocument, tuple[BuilderProposedCatalogDocument, ...], tuple[BuilderIssue, ...]]:
    assert draft.authoring_workspace is not None
    workspace = draft.authoring_workspace
    assembly = _Assembly(draft)
    target_name = draft.target_ref.relative_path.stem
    authored_name = _identifier(workspace.session_name)
    if not authored_name:
        assembly.issue(
            "builder.draft.session_name_required",
            "Session name is required",
            "workspace.session_name",
        )
    elif authored_name != target_name:
        assembly.issue(
            "builder.draft.session_identity_mismatch",
            f"Session name {authored_name!r} must match target filename {target_name!r}",
            "workspace.session_name",
        )
    if not workspace.start_time:
        assembly.issue(
            "builder.draft.start_time_required",
            "Session start time is required",
            "workspace.start_time",
        )

    segments: list[JsonValue] = []
    for index, placed in enumerate(workspace.space_refs):
        path = f"workspace.space_refs.{index}"
        segment_id = assembly.required_identifier(
            placed.segment_id,
            path=f"{path}.segment_id",
            fallback=f"space-ref-{index + 1}",
        )
        if placed.source_ref is None:
            assembly.issue(
                "builder.draft.space_source_ref_required",
                "Referenced space segments require a source reference",
                f"{path}.source_ref",
            )
        segments.append(
            {
                "id": segment_id,
                "source": cast(JsonValue, str(placed.source_ref) if placed.source_ref else None),
            }
        )

    for index, space in enumerate(workspace.space):
        path = f"workspace.space.{index}"
        segment_id = assembly.required_identifier(
            space.segment_id,
            path=f"{path}.segment_id",
            fallback=f"space-{index + 1}",
        )
        constellation_id = assembly.unique_derived_id(
            _segment_component_id(target_name, segment_id),
            path=f"{path}.segment_id",
            fallback=f"constellation-{index + 1}",
        )
        orbit_id = assembly.unique_derived_id(
            f"{segment_id}-orbit",
            path=f"{path}.orbit",
            fallback=f"orbit-{index + 1}",
        )
        orbit_ref = assembly.component_ref("orbits", orbit_id)
        assembly.propose(
            orbit_ref,
            _orbit_document(
                assembly,
                space.orbit,
                orbit_id=orbit_id,
                epoch=workspace.start_time,
                path=f"{path}.orbit",
            ),
            path=f"{path}.orbit",
        )
        node_ref: CatalogRef | None
        if space.node_draft is not None:
            node_id, node_document = _node_document(
                assembly,
                space.node_draft,
                path=f"{path}.node_draft",
                fallback_id=f"node-{index + 1}",
            )
            node_ref = assembly.component_ref("nodes", node_id)
            assembly.propose(node_ref, node_document, path=f"{path}.node_draft")
        else:
            node_ref = CatalogRef(space.node_ref) if space.node_ref is not None else None
            if node_ref is None:
                assembly.issue(
                    "builder.draft.space_node_required",
                    "Generated space segments require a node reference or node draft",
                    f"{path}.node_ref",
                )
        for field_name, value in (
            ("display_name", space.display_name),
            ("planes", space.planes),
            ("raan_spacing_deg", space.raan_spacing_deg),
            ("slots_per_plane", space.slots_per_plane),
            ("phase_offset_deg", space.phase_offset_deg),
        ):
            if value is None or value == "":
                assembly.issue(
                    f"builder.draft.constellation_{field_name}_required",
                    f"Constellation {field_name.replace('_', ' ')} is required",
                    f"{path}.{field_name}",
                )
        constellation_ref = assembly.component_ref("constellations", constellation_id)
        constellation_document: JsonDocument = {
            "constellation": {
                "id": constellation_id,
                "display_name": space.display_name,
                "node": cast(JsonValue, str(node_ref) if node_ref is not None else None),
                "orbit": str(orbit_ref),
                "planes": {
                    "count": cast(JsonValue, space.planes),
                    "raan_spacing_deg": cast(JsonValue, space.raan_spacing_deg),
                },
                "slots_per_plane": cast(JsonValue, space.slots_per_plane),
                "phasing": {
                    "mode": space.phasing_mode,
                    "phase_offset_deg": space.phase_offset_deg,
                },
                "node_tags": [{"tag": "all"}],
            }
        }
        assembly.propose(constellation_ref, constellation_document, path=path)
        segments.append({"id": segment_id, "source": str(constellation_ref)})

    for index, placed in enumerate(workspace.ground_refs):
        path = f"workspace.ground_refs.{index}"
        segment_id = assembly.required_identifier(
            placed.segment_id,
            path=f"{path}.segment_id",
            fallback=f"ground-ref-{index + 1}",
        )
        if placed.site_set_ref is None:
            assembly.issue(
                "builder.draft.site_set_ref_required",
                "Referenced ground segments require a site-set reference",
                f"{path}.site_set_ref",
            )
        segments.append(
            {
                "id": segment_id,
                "placement": {
                    "from_site_set": cast(
                        JsonValue,
                        str(placed.site_set_ref) if placed.site_set_ref is not None else None,
                    )
                },
                "apply": {"scheduling": deepcopy(placed.scheduling)},
            }
        )

    for index, ground in enumerate(workspace.ground):
        path = f"workspace.ground.{index}"
        segment_id = assembly.required_identifier(
            ground.segment_id,
            path=f"{path}.segment_id",
            fallback=f"ground-{index + 1}",
        )
        site_set_id = assembly.unique_derived_id(
            _segment_component_id(target_name, segment_id),
            path=f"{path}.segment_id",
            fallback=f"site-set-{index + 1}",
        )
        if not ground.members:
            assembly.issue(
                "builder.draft.ground_members_required",
                "Ground segments require at least one site; the segment was not omitted",
                f"{path}.members",
            )
        site_refs: list[JsonValue] = []
        overrides: list[JsonValue] = []
        for member_index, member in enumerate(ground.members):
            member_path = f"{path}.members.{member_index}"
            if member.kind == "ref":
                if member.ref is None:
                    assembly.issue(
                        "builder.draft.ground_member_ref_required",
                        "Referenced ground members require a site reference",
                        f"{member_path}.ref",
                    )
                    site_refs.append(None)
                else:
                    site_refs.append(str(member.ref))
                site_id = assembly.required_identifier(
                    member.site_id,
                    path=f"{member_path}.site_id",
                    fallback=f"site-{member_index + 1}",
                )
            else:
                site_id, site_document = _site_document(
                    assembly,
                    member.site,
                    member=member,
                    path=member_path,
                    fallback_id=f"site-{member_index + 1}",
                )
                site_ref = assembly.component_ref("sites", site_id)
                assembly.propose(site_ref, site_document, path=member_path)
                site_refs.append(str(site_ref))
            if member.scheduling_override is not None:
                overrides.append(
                    {
                        "match": {"site": site_id},
                        "scheduling": deepcopy(member.scheduling_override),
                    }
                )
        site_set_ref = assembly.component_ref("site-sets", site_set_id)
        assembly.propose(
            site_set_ref,
            {
                "site_set": {
                    "id": site_set_id,
                    "display_name": ground.display_name,
                    "sites": site_refs,
                }
            },
            path=path,
        )
        apply: JsonDocument = {"scheduling": deepcopy(ground.scheduling)}
        if ground.originated_ipv4:
            apply["originated_prefixes"] = {"ipv4": list(ground.originated_ipv4)}
        if ground.tags:
            apply["tags"] = [_identifier(tag) for tag in ground.tags]
        segment: JsonDocument = {
            "id": segment_id,
            "display_name": ground.display_name,
            "placement": {"from_site_set": str(site_set_ref)},
            "apply": apply,
        }
        if overrides:
            segment["overrides"] = overrides
        segments.append(segment)

    session: JsonDocument = {
        "session": {
            "name": target_name,
            **(
                {"display_name": workspace.display_name}
                if workspace.display_name is not None
                else {}
            ),
            **({"description": workspace.description} if workspace.description is not None else {}),
        },
        "segments": segments,
        "time": {
            "start_time": workspace.start_time,
            "step_seconds": cast(JsonValue, workspace.step_seconds),
            "compression": cast(JsonValue, workspace.compression),
        },
    }
    # The limits are written whenever the draft states them, so a session keeps them
    # through the commands that come before its first link rule.
    if workspace.max_pairs_per_rule is not None or workspace.max_pairs_per_tick is not None:
        session["simulation"] = {
            "candidate_limits": {
                "max_pairs_per_rule": cast(JsonValue, workspace.max_pairs_per_rule),
                "max_pairs_per_tick": cast(JsonValue, workspace.max_pairs_per_tick),
            }
        }
    uses_non_earth = any(
        space.orbit.central_body is not None and space.orbit.central_body != DEFAULT_BODY_REF
        for space in workspace.space
    ) or any(
        member.site is not None
        and member.site.body is not None
        and member.site.body != DEFAULT_BODY_REF
        for ground in workspace.ground
        for member in ground.members
    )
    if uses_non_earth:
        session["ephemeris"] = deepcopy(_DE440S_EPHEMERIS)
    return session, tuple(assembly.proposals.values()), tuple(assembly.issues)


def _set_optional_field(document: JsonDocument, key: str, value: JsonValue | None) -> None:
    if value is None:
        document.pop(key, None)
    else:
        document[key] = value


def _overlay_applied_workspace(
    draft: BuilderVisualDraftEnvelope,
    baseline: BuilderVisualWorkspace,
) -> tuple[JsonDocument, tuple[BuilderProposedCatalogDocument, ...], tuple[BuilderIssue, ...]]:
    assert draft.applied_session is not None
    assert draft.authoring_workspace is not None
    workspace = draft.authoring_workspace
    candidate, proposals, issues = _assemble_authoring_workspace(draft)
    session = deepcopy(draft.applied_session)

    session_meta = cast(JsonDocument, session["session"])
    candidate_meta = cast(JsonDocument, candidate["session"])
    for workspace_field, canonical_field in (
        ("session_name", "name"),
        ("display_name", "display_name"),
        ("description", "description"),
    ):
        if getattr(workspace, workspace_field) != getattr(baseline, workspace_field):
            _set_optional_field(session_meta, canonical_field, candidate_meta.get(canonical_field))

    session_time = cast(JsonDocument, session["time"])
    candidate_time = cast(JsonDocument, candidate["time"])
    for field_name in ("start_time", "step_seconds", "compression"):
        if getattr(workspace, field_name) != getattr(baseline, field_name):
            _set_optional_field(session_time, field_name, candidate_time.get(field_name))

    limits_changed = any(
        getattr(workspace, field_name) != getattr(baseline, field_name)
        for field_name in ("max_pairs_per_rule", "max_pairs_per_tick")
    )
    if limits_changed:
        simulation = session.get("simulation")
        if not isinstance(simulation, dict):
            simulation = {}
            session["simulation"] = simulation
        if workspace.max_pairs_per_rule is None and workspace.max_pairs_per_tick is None:
            simulation.pop("candidate_limits", None)
            if not simulation:
                session.pop("simulation", None)
        else:
            simulation["candidate_limits"] = {
                "max_pairs_per_rule": cast(JsonValue, workspace.max_pairs_per_rule),
                "max_pairs_per_tick": cast(JsonValue, workspace.max_pairs_per_tick),
            }

    canonical_segments = cast(list[JsonValue], session["segments"])
    candidate_segments = {
        cast(str, segment["id"]): segment
        for segment in cast(list[JsonDocument], candidate["segments"])
    }
    baseline_direct_ids = {
        *(item.segment_id for item in baseline.space_refs),
        *(item.segment_id for item in baseline.ground_refs),
    }
    current_direct_ids = {
        *(item.segment_id for item in workspace.space_refs),
        *(item.segment_id for item in workspace.ground_refs),
    }
    removed_direct_ids = baseline_direct_ids - current_direct_ids
    if removed_direct_ids:
        issues = (
            *issues,
            _issue(
                "builder.draft.placed_segment_identity_read_only",
                "Placed segment identity cannot be removed or renamed through the flat editor: "
                + ", ".join(sorted(removed_direct_ids)),
                target_ref=draft.target_ref,
                draft_path="authoring_workspace",
            ),
        )
    canonical_by_id = {
        cast(str, segment["id"]): segment
        for segment in canonical_segments
        if isinstance(segment, dict) and isinstance(segment.get("id"), str)
    }
    baseline_space = {item.segment_id: item for item in baseline.space_refs}
    baseline_ground = {item.segment_id: item for item in baseline.ground_refs}
    for placed in workspace.space_refs:
        existing = canonical_by_id.get(placed.segment_id)
        before = baseline_space.get(placed.segment_id)
        if existing is None:
            canonical_segments.append(deepcopy(candidate_segments[placed.segment_id]))
        elif before is not None and placed.source_ref != before.source_ref:
            existing["source"] = cast(
                JsonValue,
                str(placed.source_ref) if placed.source_ref is not None else None,
            )
    for placed in workspace.ground_refs:
        existing = canonical_by_id.get(placed.segment_id)
        before = baseline_ground.get(placed.segment_id)
        if existing is None:
            canonical_segments.append(deepcopy(candidate_segments[placed.segment_id]))
            continue
        if before is not None and placed.site_set_ref != before.site_set_ref:
            placement = cast(JsonDocument, existing["placement"])
            placement["from_site_set"] = cast(
                JsonValue,
                str(placed.site_set_ref) if placed.site_set_ref is not None else None,
            )
        if before is not None and placed.scheduling != before.scheduling:
            apply = existing.get("apply")
            if not isinstance(apply, dict):
                apply = {}
                existing["apply"] = apply
            if placed.scheduling:
                apply["scheduling"] = deepcopy(placed.scheduling)
            else:
                apply.pop("scheduling", None)
                if not apply:
                    existing.pop("apply", None)

    for authored in workspace.space:
        if authored.segment_id not in canonical_by_id:
            canonical_segments.append(deepcopy(candidate_segments[authored.segment_id]))
    baseline_ground_drafts = {ground.segment_id: ground for ground in baseline.ground}
    for authored in workspace.ground:
        existing = canonical_by_id.get(authored.segment_id)
        before = baseline_ground_drafts.get(authored.segment_id)
        candidate_ground = candidate_segments[authored.segment_id]
        if existing is None:
            canonical_segments.append(deepcopy(candidate_ground))
            continue
        if before is None:
            continue
        if authored.display_name != before.display_name:
            _set_optional_field(existing, "display_name", candidate_ground.get("display_name"))
        apply = existing.get("apply")
        if not isinstance(apply, dict):
            apply = {}
            existing["apply"] = apply
        candidate_apply = candidate_ground.get("apply")
        if not isinstance(candidate_apply, dict):
            candidate_apply = {}
        if authored.scheduling != before.scheduling:
            _set_optional_field(apply, "scheduling", candidate_apply.get("scheduling"))
        if authored.tags != before.tags:
            _set_optional_field(apply, "tags", candidate_apply.get("tags"))
        if authored.originated_ipv4 != before.originated_ipv4:
            existing_prefixes = apply.get("originated_prefixes")
            if not isinstance(existing_prefixes, dict):
                existing_prefixes = {}
                apply["originated_prefixes"] = existing_prefixes
            candidate_prefixes = candidate_apply.get("originated_prefixes")
            candidate_ipv4 = (
                candidate_prefixes.get("ipv4") if isinstance(candidate_prefixes, dict) else None
            )
            _set_optional_field(existing_prefixes, "ipv4", candidate_ipv4)
            if not existing_prefixes:
                apply.pop("originated_prefixes", None)
        if not apply:
            existing.pop("apply", None)

        before_overrides = {member.site_id: member.scheduling_override for member in before.members}
        current_overrides = {
            member.site_id: member.scheduling_override for member in authored.members
        }
        if current_overrides != before_overrides:
            candidate_overrides = {
                cast(str, cast(JsonDocument, item.get("match", {})).get("site")): item
                for item in cast(list[JsonDocument], candidate_ground.get("overrides", []))
            }
            existing_overrides = cast(list[JsonDocument], existing.get("overrides", []))
            merged_overrides: list[JsonDocument] = []
            seen_sites: set[str] = set()
            for override in existing_overrides:
                match = override.get("match")
                site_id = cast(str, match.get("site")) if isinstance(match, dict) else ""
                if site_id not in current_overrides:
                    merged_overrides.append(override)
                    continue
                seen_sites.add(site_id)
                scheduling = current_overrides[site_id]
                updated_override = deepcopy(override)
                _set_optional_field(updated_override, "scheduling", deepcopy(scheduling))
                if set(updated_override) != {"match"}:
                    merged_overrides.append(updated_override)
            for site_id, scheduling in current_overrides.items():
                if site_id not in seen_sites and scheduling is not None:
                    merged_overrides.append(deepcopy(candidate_overrides[site_id]))
            if merged_overrides:
                existing["overrides"] = merged_overrides
            else:
                existing.pop("overrides", None)

    return session, proposals, issues


def _assemble_structured(
    draft: BuilderVisualDraftEnvelope,
    *,
    allow_workspace_overlay: bool = False,
) -> tuple[JsonDocument, tuple[BuilderProposedCatalogDocument, ...], tuple[BuilderIssue, ...]]:
    if draft.applied_session is None:
        return _assemble_authoring_workspace(draft)
    assert draft.applied_workspace is not None
    assert draft.applied_revision is not None
    assert draft.authoring_workspace is not None
    applied_model = SegmentSessionConfig.model_validate(draft.applied_session)
    baseline = _workspace_from_applied_session(
        applied_model,
        proposals=draft.catalog_documents,
        prior_workspace=draft.applied_workspace,
    )
    if draft.applied_workspace != baseline:
        issue = _issue(
            "builder.draft.applied_projection_mismatch",
            "Applied workspace does not match the authoritative canonical session",
            target_ref=draft.target_ref,
            draft_path="applied_workspace",
        )
        return deepcopy(draft.applied_session), draft.catalog_documents, (issue,)
    if draft.authoring_workspace == baseline:
        return deepcopy(draft.applied_session), draft.catalog_documents, ()
    if not allow_workspace_overlay:
        issue = _issue(
            "builder.draft.unapplied_workspace_changes",
            "Workspace changes must be applied through a typed graphical command",
            target_ref=draft.target_ref,
            draft_path="authoring_workspace",
        )
        return deepcopy(draft.applied_session), draft.catalog_documents, (issue,)
    return _overlay_applied_workspace(draft, baseline)


def _validated_catalog_model(snapshot: CatalogReadSnapshot, ref: CatalogRef) -> BaseModel:
    document = snapshot.get(ref)
    data = load_configuration_yaml(document.content)
    family = cast(CatalogFamily, ref.family)
    return catalog_family_spec(family).validate_document(data)


def _reachable_projection_inputs(
    session: SegmentSessionConfig,
    proposals: tuple[BuilderProposedCatalogDocument, ...],
    snapshot: CatalogReadSnapshot,
) -> tuple[
    tuple[BuilderProposedCatalogDocument, ...],
    dict[CatalogRef, _CatalogProjectionFact],
]:
    proposed_by_ref = {proposal.ref: proposal for proposal in proposals}
    reached_proposals: set[CatalogRef] = set()
    visited: set[CatalogRef] = set()
    catalog_facts: dict[CatalogRef, _CatalogProjectionFact] = {}

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
        catalog_facts[ref] = _CatalogProjectionFact(
            label=_catalog_label(model),
            summary=_site_summary(model) if isinstance(model, Site) else None,
        )
        for dependency in catalog_document_references(model):
            visit(dependency)

    for ref in catalog_document_references(session):
        visit(ref)
    return (
        tuple(proposal for proposal in proposals if proposal.ref in reached_proposals),
        catalog_facts,
    )


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


def _rewrite_catalog_references(
    value: Any,
    replacements: dict[CatalogRef, CatalogRef],
) -> Any:
    if isinstance(value, CatalogRef):
        return replacements.get(value, value)
    if isinstance(value, BaseModel):
        return value.model_copy(
            update={
                field_name: _rewrite_catalog_references(
                    getattr(value, field_name),
                    replacements,
                )
                for field_name in type(value).model_fields
            }
        )
    if isinstance(value, dict):
        return {
            _rewrite_catalog_references(key, replacements): _rewrite_catalog_references(
                nested,
                replacements,
            )
            for key, nested in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_rewrite_catalog_references(item, replacements) for item in value)
    if isinstance(value, list):
        return [_rewrite_catalog_references(item, replacements) for item in value]
    return value


def _forked_document(
    view: CatalogReadView,
    source_ref: CatalogRef,
    target_ref: CatalogRef,
    *,
    replacements: dict[CatalogRef, CatalogRef],
) -> JsonDocument:
    """Copy one catalog object to ``target_ref``, its references replaced."""
    wrapper, source = load_catalog_object(source_ref, view)
    if wrapper is None:
        raise ValueError("a session document cannot be forked as a component")
    model = _rewrite_catalog_references(source, replacements)
    model = model.model_copy(update={"id": target_ref.relative_path.stem})
    normalized = cast(
        JsonDocument,
        model.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    return {wrapper: normalized}


def _merge_assembly_issues(
    result: BuilderCompileResult,
    assembly_issues: tuple[BuilderIssue, ...],
) -> BuilderCompileResult:
    if not assembly_issues:
        return result
    issues = tuple(dict.fromkeys((*assembly_issues, *result.issues)))
    save_blockers = tuple(issue for issue in issues if "save" in issue.blocks)
    deploy_blockers = tuple(issue for issue in issues if "deploy" in issue.blocks)
    return BuilderCompileResult(
        draft=result.draft,
        target_ref=result.target_ref,
        canonical_session_yaml=result.canonical_session_yaml,
        canonical_session_json=result.canonical_session_json,
        dependency_closure=result.dependency_closure,
        resolved_preview=result.resolved_preview,
        digests=result.digests,
        issues=issues,
        save_verdict=BuilderVerdict(
            operation="save",
            allowed=not save_blockers,
            blockers=save_blockers,
        ),
        deploy_eligibility_after_save=BuilderVerdict(
            operation="deploy",
            allowed=not deploy_blockers,
            blockers=deploy_blockers,
        ),
    )


@dataclass(frozen=True, slots=True)
class _WorkspaceApplication:
    draft: BuilderVisualDraftEnvelope
    session: JsonDocument
    proposals: tuple[BuilderProposedCatalogDocument, ...]
    assembly_issues: tuple[BuilderIssue, ...]


def _apply_workspace_revision(
    draft: BuilderVisualDraftEnvelope,
    workspace: BuilderVisualWorkspace,
) -> _WorkspaceApplication:
    candidate = draft.model_copy(update={"authoring_workspace": workspace})
    session, proposals, assembly_issues = _assemble_structured(
        candidate,
        allow_workspace_overlay=True,
    )
    reserved_authoring_ids = tuple(
        dict.fromkeys(
            (
                *draft.reserved_authoring_ids,
                *_current_and_referenced_authoring_ids(session, workspace=workspace),
            )
        )
    )
    try:
        applied_model = SegmentSessionConfig.model_validate(session)
    except TypeError, ValueError:
        applied_model = None
    next_revision = draft.draft_revision + 1
    if assembly_issues or applied_model is None:
        projection_status = (
            "pending_authoring" if draft.applied_session is not None else "incomplete_authoring"
        )
        updated = draft.model_copy(
            update={
                "draft_revision": next_revision,
                "projection_status": projection_status,
                "reserved_authoring_ids": reserved_authoring_ids,
                "session_yaml": yaml.safe_dump(session, sort_keys=False),
                "authoring_workspace": workspace,
                "catalog_documents": proposals,
                "control_tree": None,
            }
        )
    else:
        applied_session = cast(
            JsonDocument,
            applied_model.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        applied_workspace = _workspace_from_applied_session(
            applied_model,
            proposals=proposals,
            prior_workspace=workspace,
        )
        canonical = canonicalize_persisted_configuration(draft.target_ref, applied_session)
        updated = draft.model_copy(
            update={
                "draft_revision": next_revision,
                "projection_status": "applied",
                "reserved_authoring_ids": reserved_authoring_ids,
                "session_yaml": canonical.yaml_bytes.decode("utf-8"),
                "authoring_workspace": applied_workspace,
                "applied_workspace": applied_workspace,
                "applied_revision": next_revision,
                "applied_session": applied_session,
                "control_tree": _applied_control_tree(applied_model, next_revision),
                "catalog_documents": proposals,
            }
        )
        session = applied_session
    return _WorkspaceApplication(
        draft=updated,
        session=session,
        proposals=proposals,
        assembly_issues=assembly_issues,
    )


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
    reachable_proposals, catalog_facts = _reachable_projection_inputs(
        session,
        proposals,
        snapshot,
    )
    accepted_session = cast(
        JsonDocument,
        session.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    next_revision = draft.draft_revision + 1
    workspace = _workspace_from_applied_session(
        session,
        proposals=reachable_proposals,
        catalog_facts=catalog_facts,
        prior_workspace=draft.applied_workspace,
    )
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
            "projection_status": "applied",
            "catalog_documents": reachable_proposals,
            "reserved_authoring_ids": reserved_authoring_ids,
            "session_yaml": canonical.yaml_bytes.decode("utf-8"),
            "authoring_workspace": workspace,
            "applied_workspace": workspace,
            "applied_revision": next_revision,
            "applied_session": accepted_session,
            "control_tree": _applied_control_tree(session, next_revision),
        }
    )


def _compile_visual_application(
    visual_draft: BuilderVisualDraftEnvelope,
    session: JsonDocument,
    proposals: tuple[BuilderProposedCatalogDocument, ...],
    assembly_issues: tuple[BuilderIssue, ...],
    snapshot: CatalogReadSnapshot,
    *,
    available_node_count: int,
    preview_factory: PreviewFactory | None,
) -> BuilderVisualDraftAssemblyResult:
    assembled = BuilderDraftEnvelope(
        draft_revision=visual_draft.draft_revision,
        state={"session": session, "catalog_documents": proposals},
    )
    compile_request = BuilderCompileRequest(
        draft=assembled,
        target_ref=visual_draft.target_ref,
    )
    compile_result = compile_builder_draft(
        compile_request,
        snapshot,
        available_node_count=available_node_count,
        preview_factory=preview_factory,
    )
    compile_result = _merge_assembly_issues(compile_result, assembly_issues)
    assembled = compile_result.draft
    save_request = BuilderSessionSaveRequest(
        draft=assembled,
        target_ref=visual_draft.target_ref,
        expected_session_revision=visual_draft.expected_session_revision,
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
        assembled_draft=assembled,
        save_request=save_request,
        compile_result=compile_result,
        listing=listing,
        assembly_issues=assembly_issues,
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
        workspace = BuilderVisualWorkspace(
            session_name=name,
            display_name=request.display_name,
            description=request.description,
            start_time=now.isoformat().replace("+00:00", "Z"),
        )
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
                    "start_time": workspace.start_time,
                    "step_seconds": workspace.step_seconds,
                    "compression": workspace.compression,
                },
            },
            sort_keys=False,
        )
        return BuilderVisualDraftEnvelope(
            draft_revision=0,
            projection_status="incomplete_authoring",
            target_ref=target_ref,
            session_name_is_placeholder=session_name_is_placeholder,
            reserved_authoring_ids=(),
            session_yaml=session_yaml,
            authoring_workspace=workspace,
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
        catalog_facts = _stored_catalog_facts(snapshot, source.content)
        workspace = _workspace_from_applied_session(
            applied_model,
            catalog_facts=catalog_facts,
        )
        return BuilderVisualDraftEnvelope(
            draft_revision=0,
            projection_status="applied",
            target_ref=target_ref,
            source_ref=request.source_ref,
            expected_session_revision=expected_revision,
            catalog_documents=(),
            session_name_is_placeholder=False,
            reserved_authoring_ids=_current_and_referenced_authoring_ids(applied_session),
            session_yaml=session_yaml,
            authoring_workspace=workspace,
            applied_workspace=workspace,
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

        (
            applied_model,
            applied_session,
            canonical_yaml,
            _source_map,
            issues,
        ) = _parse_session_yaml_application(draft.target_ref, request.yaml_text)
        if applied_model is None or applied_session is None or canonical_yaml is None:
            updates: dict[str, Any] = {"session_yaml": request.yaml_text}
            if draft.projection_status == "applied":
                assert draft.authoring_workspace is not None
                updates.update(
                    {
                        "projection_status": "pending_authoring",
                        "control_tree": None,
                    }
                )
            refused = draft.model_copy(update=updates)
            return BuilderVisualDraftApplyYamlResult(
                draft=refused,
                buffer_generation=request.buffer_generation,
                yaml_text=request.yaml_text,
                applied=False,
                canonicalization_required=False,
                issues=issues,
            )

        snapshot = self._context.repository.snapshot(self._context.scope)
        reachable_proposals, catalog_facts = _reachable_projection_inputs(
            applied_model,
            draft.catalog_documents,
            snapshot,
        )
        next_revision = draft.draft_revision + 1
        prior_workspace = draft.applied_workspace or draft.authoring_workspace
        workspace = _workspace_from_applied_session(
            applied_model,
            proposals=reachable_proposals,
            catalog_facts=catalog_facts,
            prior_workspace=prior_workspace,
        )
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
                "projection_status": "applied",
                "catalog_documents": reachable_proposals,
                "reserved_authoring_ids": reserved_authoring_ids,
                "session_yaml": request.yaml_text,
                "authoring_workspace": workspace,
                "applied_workspace": workspace,
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
        if (
            draft.projection_status != "applied"
            or draft.applied_session is None
            or draft.applied_workspace is None
            or draft.applied_revision is None
            or draft.authoring_workspace != draft.applied_workspace
        ):
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
        if draft.projection_status != "applied" or draft.applied_session is None:
            raise self._command_error(
                draft,
                "Pending edits require a fully applied draft revision",
                code="catalog_authoring.invalid_graph",
            )

        candidate, outcomes = apply_pending_edits(
            draft.applied_session,
            request.edits,
            source_ref=str(draft.target_ref),
        )
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
                draft.catalog_documents,
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
        if draft.projection_status != "applied" or draft.applied_session is None:
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
            cast(JsonDocument, updated.applied_session),
            updated.catalog_documents,
            (),
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
        editor = _object_editor(request.address)
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

    def apply_workspace(
        self,
        request: BuilderVisualDraftApplyWorkspaceRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftAssemblyResult:
        """Apply and compile one graphical workspace as a single fenced revision."""

        if not isinstance(request, BuilderVisualDraftApplyWorkspaceRequest):
            raise TypeError("request must be a BuilderVisualDraftApplyWorkspaceRequest")
        draft = request.draft
        _assert_visual_draft_ownership(draft)
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before the workspace was applied",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )
        application = _apply_workspace_revision(draft, request.workspace)
        snapshot = self._context.repository.snapshot(self._context.scope)
        return _compile_visual_application(
            application.draft,
            application.session,
            application.proposals,
            application.assembly_issues,
            snapshot,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )

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
        if draft.applied_session is None or draft.projection_status != "applied":
            raise self._command_error(
                draft,
                "Save under a new name copies the accepted session; apply or discard the "
                "pending edits first",
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
                    document=_forked_document(
                        view,
                        proposal.ref,
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
        session = _rewrite_catalog_references(session, renames)
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
        applied_workspace = _workspace_from_applied_session(
            applied_model,
            proposals=proposals,
            prior_workspace=draft.authoring_workspace,
        )
        canonical = canonicalize_persisted_configuration(request.target_ref, applied_session)
        retargeted = draft.model_copy(
            update={
                "target_ref": request.target_ref,
                "expected_session_revision": None,
                "session_name_is_placeholder": False,
                "draft_revision": next_revision,
                "catalog_documents": proposals,
                "session_yaml": canonical.yaml_bytes.decode("utf-8"),
                "authoring_workspace": applied_workspace,
                "applied_workspace": applied_workspace,
                "applied_revision": next_revision,
                "applied_session": applied_session,
                "control_tree": _applied_control_tree(applied_model, next_revision),
            }
        )
        return _compile_visual_application(
            retargeted,
            applied_session,
            proposals,
            (),
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

    def _new_ground_draft(
        self,
        draft: BuilderVisualDraftEnvelope,
        snapshot: CatalogReadSnapshot,
        reserved: set[str],
        *,
        node_ref: CatalogRef | None = None,
        installed_counts: dict[str, int] | None = None,
        authored_boresights: dict[str, BuilderVisualGroundBoresight] | None = None,
        body_ref: BodyRef | None = None,
    ) -> BuilderVisualGroundDraft:
        number = _next_number("ground", reserved)
        segment_id = f"ground-{number}"
        selected_node_ref = self._node_ref_for_command(
            draft,
            snapshot,
            node_ref,
            placement="ground",
        )
        installed, boresights = self._ground_installation_facts(
            draft,
            snapshot,
            selected_node_ref,
            installed_counts=installed_counts,
            authored_boresights=authored_boresights,
        )
        return BuilderVisualGroundDraft(
            segment_id=segment_id,
            display_name=f"Ground segment {number}",
            stamp=BuilderVisualGroundStamp(
                node_ref=selected_node_ref,
                installed=installed,
                boresights=boresights,
                body=body_ref or DEFAULT_BODY_REF,
            ),
            scheduling=scheduling_preset_block(DEFAULT_SCHEDULING_PRESET),
        )

    def _assert_terminal_ref(
        self,
        draft: BuilderVisualDraftEnvelope,
        snapshot: CatalogReadSnapshot,
        terminal_ref: CatalogRef,
    ) -> None:
        try:
            model = self._catalog_model_for_draft(draft, snapshot, terminal_ref)
        except (CatalogNotFoundError, TypeError, ValueError) as error:
            raise self._command_error(
                draft,
                f"Terminal reference {terminal_ref} is invalid: {error}",
                code="catalog_authoring.invalid_graph",
            ) from error
        if not isinstance(model, Terminal):
            raise self._command_error(
                draft,
                f"Terminal reference {terminal_ref} does not resolve to a terminal component",
                code="catalog_authoring.invalid_graph",
            )

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
        if (
            draft.projection_status != "applied"
            or draft.applied_session is None
            or draft.authoring_workspace != draft.applied_workspace
        ):
            raise self._command_error(
                draft,
                "Link rule and routing commands require a fully applied draft revision",
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
                _, (own_outcome,) = apply_pending_edits(
                    draft.applied_session,
                    (own_list,),
                    source_ref=str(draft.target_ref),
                )
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
            edited, outcomes = apply_pending_edits(
                draft.applied_session,
                () if own is None else (own,),
                source_ref=str(draft.target_ref),
            )
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
        candidate, (outcome,) = apply_pending_edits(
            draft.applied_session,
            (edit,),
            source_ref=str(draft.target_ref),
        )
        if issues or outcome.status != "applied":
            return BuilderVisualDraftCommandResult(
                operation=command.operation,
                base_draft_revision=draft.draft_revision,
                draft=draft,
                affected_kind=affected_kind,
                affected_id=affected_id,
                notice=notice,
                created_edits=(edit,),
            )
        try:
            _wrapper, validated = validate_referenced_configuration_document(
                draft.target_ref,
                candidate,
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
            draft.catalog_documents,
            snapshot,
        )
        # A command never takes a complete draft to an incomplete one: when the
        # draft resolved and could be saved, and would not with this object (a
        # ground station with no stated floor, a boundary inside one domain), the
        # object is held as a pending edit for the user to complete. A draft that
        # was already incomplete is accepted, so a repair is never refused.
        if self._is_complete(
            draft,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        ) and not self._is_complete(
            accepted,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        ):
            return BuilderVisualDraftCommandResult(
                operation=command.operation,
                base_draft_revision=draft.draft_revision,
                draft=draft,
                affected_kind=affected_kind,
                affected_id=affected_id,
                notice=notice,
                created_edits=(edit,),
            )
        return BuilderVisualDraftCommandResult(
            operation=command.operation,
            base_draft_revision=draft.draft_revision,
            draft=accepted,
            affected_kind=affected_kind,
            affected_id=affected_id,
            notice=notice,
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
        if draft.authoring_workspace is None:
            raise self._command_error(
                draft,
                "Typed visual commands require an authoring workspace",
            )

        snapshot = self._context.repository.snapshot(self._context.scope)
        workspace = draft.authoring_workspace
        # Every id the draft and the pending edits hold, before the gesture changes the twin.
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
        affected_kind: Literal["space", "ground", "ground_member"]
        affected_id: str
        notice: str | None = None
        scheduling_preset = None
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
            number = _next_number("space", reserved)
            segment_id = f"space-{number}"
            space_reference = BuilderVisualSpaceReference(
                segment_id=segment_id,
                source_ref=command.source_ref,
                label=_catalog_label(model),
            )
            workspace = workspace.model_copy(
                update={"space_refs": (*workspace.space_refs, space_reference)}
            )
            affected_kind = "space"
            affected_id = segment_id
        elif isinstance(command, BuilderVisualPlaceGroundReferenceCommand):
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
            number = _next_number("ground", reserved)
            segment_id = f"ground-{number}"
            ground_reference = BuilderVisualGroundReference(
                segment_id=segment_id,
                site_set_ref=command.site_set_ref,
                label=_catalog_label(model),
                scheduling=scheduling_preset_block(DEFAULT_SCHEDULING_PRESET),
            )
            workspace = workspace.model_copy(
                update={"ground_refs": (*workspace.ground_refs, ground_reference)}
            )
            affected_kind = "ground"
            affected_id = segment_id
            scheduling_preset = DEFAULT_SCHEDULING_PRESET
        elif isinstance(command, BuilderVisualAddGeneratedSpaceCommand):
            number = _next_number("space", reserved)
            segment_id = f"space-{number}"
            node_ref = self._node_ref_for_command(
                draft,
                snapshot,
                command.node_ref,
                placement="space",
            )
            single_plane = command.phasing_mode == SINGLE_PLANE_PHASING_MODE
            walker_layout = (
                None
                if single_plane
                else derive_walker_layout(
                    BuilderVisualWalkerLayoutRequest(
                        pattern=cast(
                            Literal["walker_delta", "walker_star"],
                            command.phasing_mode,
                        ),
                        planes=3,
                        slots_per_plane=8,
                    )
                )
            )
            space = BuilderVisualSpaceDraft(
                segment_id=segment_id,
                display_name=f"Constellation {number}",
                node_ref=node_ref,
                orbit=BuilderVisualOrbit(
                    central_body=DEFAULT_BODY_REF,
                    shape_kind="circular",
                    altitude_km=550,
                    perigee_altitude_km=550,
                    apogee_altitude_km=550,
                    inclination_deg=53,
                    raan_deg=0,
                    argument_of_perigee_deg=0,
                    mean_anomaly_deg=0,
                    propagator="j2_mean_elements",
                ),
                planes=1 if single_plane else 3,
                raan_spacing_deg=(360 if walker_layout is None else walker_layout.raan_spacing_deg),
                slots_per_plane=8,
                phasing_mode=command.phasing_mode,
                phase_offset_deg=(0 if walker_layout is None else walker_layout.phase_offset_deg),
            )
            workspace = workspace.model_copy(update={"space": (*workspace.space, space)})
            affected_kind = "space"
            affected_id = segment_id
        elif isinstance(command, BuilderVisualSetSpacePopulationCommand):
            matches = [
                (index, space)
                for index, space in enumerate(workspace.space)
                if space.segment_id == command.segment_id
            ]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Authored space segment {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            space_index, space = matches[0]
            phasing_mode = command.phasing_mode or space.phasing_mode
            planes = command.planes if command.planes is not None else space.planes
            slots_per_plane = (
                command.slots_per_plane
                if command.slots_per_plane is not None
                else space.slots_per_plane
            )
            if command.phasing_mode is not None:
                if phasing_mode == SINGLE_PLANE_PHASING_MODE:
                    planes = 1
                elif planes is not None:
                    planes = max(2, planes)
            elif command.planes is not None:
                if planes == 1:
                    phasing_mode = SINGLE_PLANE_PHASING_MODE
                elif phasing_mode == SINGLE_PLANE_PHASING_MODE:
                    phasing_mode = DEFAULT_PHASING_MODE

            single_plane = phasing_mode == SINGLE_PLANE_PHASING_MODE
            raan_spacing_deg: float | None
            phase_offset_deg: float | None
            if planes is None or slots_per_plane is None:
                raan_spacing_deg = None
                phase_offset_deg = None
            elif single_plane:
                raan_spacing_deg = 360
                phase_offset_deg = 0
            else:
                walker_layout = derive_walker_layout(
                    BuilderVisualWalkerLayoutRequest(
                        pattern=cast(
                            Literal["walker_delta", "walker_star"],
                            phasing_mode,
                        ),
                        planes=planes,
                        slots_per_plane=slots_per_plane,
                    )
                )
                raan_spacing_deg = walker_layout.raan_spacing_deg
                phase_offset_deg = walker_layout.phase_offset_deg
            spaces = list(workspace.space)
            spaces[space_index] = space.model_copy(
                update={
                    "phasing_mode": phasing_mode,
                    "planes": planes,
                    "slots_per_plane": slots_per_plane,
                    "raan_spacing_deg": raan_spacing_deg,
                    "phase_offset_deg": phase_offset_deg,
                }
            )
            workspace = workspace.model_copy(update={"space": tuple(spaces)})
            affected_kind = "space"
            affected_id = command.segment_id
        elif isinstance(command, BuilderVisualAuthorInlineSpaceNodeCommand):
            matches = [
                (index, space)
                for index, space in enumerate(workspace.space)
                if space.segment_id == command.segment_id
            ]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Authored space segment {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            space_index, space = matches[0]
            if space.node_draft is not None:
                raise self._command_error(
                    draft,
                    f"Authored space segment {command.segment_id!r} already has an inline node",
                    code="catalog_authoring.invalid_patch",
                )
            node_ids = {
                candidate.node_draft.id
                for candidate in workspace.space
                if candidate.node_draft is not None
            }
            node_id = f"{command.segment_id}-node"
            suffix = 2
            while node_id in node_ids:
                node_id = f"{command.segment_id}-node-{suffix}"
                suffix += 1
            spaces = list(workspace.space)
            spaces[space_index] = space.model_copy(
                update={
                    "node_draft": BuilderVisualNode(
                        id=node_id,
                        display_name=f"{space.display_name or command.segment_id} node",
                        forwarding=None,
                    )
                }
            )
            workspace = workspace.model_copy(update={"space": tuple(spaces)})
            affected_kind = "space"
            affected_id = command.segment_id
        elif isinstance(command, BuilderVisualAddOrIncrementNodeTerminalCommand):
            matches = [
                (index, space)
                for index, space in enumerate(workspace.space)
                if space.segment_id == command.segment_id
            ]
            if len(matches) != 1 or matches[0][1].node_draft is None:
                raise self._command_error(
                    draft,
                    f"Authored inline node for {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            self._assert_terminal_ref(draft, snapshot, command.terminal_ref)
            space_index, space = matches[0]
            assert space.node_draft is not None
            terminals = list(space.node_draft.terminals)
            existing_index = next(
                (
                    index
                    for index, mount in enumerate(terminals)
                    if mount.terminal_ref == command.terminal_ref and mount.role == command.role
                ),
                None,
            )
            if existing_index is not None:
                existing = terminals[existing_index]
                terminals[existing_index] = existing.model_copy(
                    update={
                        "count": (existing.count or DEFAULT_TERMINAL_MOUNT_COUNT) + 1,
                    }
                )
            else:
                mount_ids = {mount.mount_id for mount in terminals}
                mount_index = 0
                mount_id = f"{command.role}_{mount_index}"
                while mount_id in mount_ids:
                    mount_index += 1
                    mount_id = f"{command.role}_{mount_index}"
                terminals.append(
                    BuilderVisualTerminalMount(
                        mount_id=mount_id,
                        role=command.role,
                        terminal_ref=command.terminal_ref,
                        count=DEFAULT_TERMINAL_MOUNT_COUNT,
                        boresight=(
                            BuilderVisualSpaceBoresight(mode="nadir")
                            if command.role == "access"
                            else None
                        ),
                    )
                )
            spaces = list(workspace.space)
            spaces[space_index] = space.model_copy(
                update={
                    "node_draft": space.node_draft.model_copy(
                        update={"terminals": tuple(terminals)}
                    )
                }
            )
            workspace = workspace.model_copy(update={"space": tuple(spaces)})
            affected_kind = "space"
            affected_id = command.segment_id
        elif isinstance(command, BuilderVisualSetNodeTerminalRoleCommand):
            matches = [
                (index, space)
                for index, space in enumerate(workspace.space)
                if space.segment_id == command.segment_id
            ]
            if len(matches) != 1 or matches[0][1].node_draft is None:
                raise self._command_error(
                    draft,
                    f"Authored inline node for {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            space_index, space = matches[0]
            assert space.node_draft is not None
            mount_matches = [
                (index, mount)
                for index, mount in enumerate(space.node_draft.terminals)
                if mount.mount_id == command.mount_id
            ]
            if len(mount_matches) != 1:
                raise self._command_error(
                    draft,
                    f"Terminal mount {command.mount_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            mount_index, mount = mount_matches[0]
            terminals = list(space.node_draft.terminals)
            terminals[mount_index] = mount.model_copy(
                update={
                    "role": command.role,
                    "boresight": (
                        BuilderVisualSpaceBoresight(mode="nadir")
                        if command.role == "access"
                        else None
                    ),
                }
            )
            spaces = list(workspace.space)
            spaces[space_index] = space.model_copy(
                update={
                    "node_draft": space.node_draft.model_copy(
                        update={"terminals": tuple(terminals)}
                    )
                }
            )
            workspace = workspace.model_copy(update={"space": tuple(spaces)})
            affected_kind = "space"
            affected_id = command.segment_id
        elif isinstance(command, BuilderVisualAddNodeEthernetPortCommand):
            matches = [
                (index, space)
                for index, space in enumerate(workspace.space)
                if space.segment_id == command.segment_id
            ]
            if len(matches) != 1 or matches[0][1].node_draft is None:
                raise self._command_error(
                    draft,
                    f"Authored inline node for {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            space_index, space = matches[0]
            assert space.node_draft is not None
            ethernet = list(space.node_draft.ethernet)
            port_index = 0
            port_id = f"terr{port_index}"
            while port_id in ethernet:
                port_index += 1
                port_id = f"terr{port_index}"
            ethernet.append(port_id)
            spaces = list(workspace.space)
            spaces[space_index] = space.model_copy(
                update={
                    "node_draft": space.node_draft.model_copy(update={"ethernet": tuple(ethernet)})
                }
            )
            workspace = workspace.model_copy(update={"space": tuple(spaces)})
            affected_kind = "space"
            affected_id = command.segment_id
        elif isinstance(command, BuilderVisualAddGroundCommand):
            ground = self._new_ground_draft(
                draft,
                snapshot,
                reserved,
                node_ref=command.node_ref,
                installed_counts=command.installed,
                authored_boresights=command.boresights,
                body_ref=command.body_ref,
            )
            workspace = workspace.model_copy(update={"ground": (*workspace.ground, ground)})
            affected_kind = "ground"
            affected_id = ground.segment_id
            scheduling_preset = DEFAULT_SCHEDULING_PRESET
        elif isinstance(command, BuilderVisualAddGroundSiteReferenceCommand):
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
                ground = self._new_ground_draft(draft, snapshot, reserved)
                workspace = workspace.model_copy(update={"ground": (*workspace.ground, ground)})
                ground_index = len(workspace.ground) - 1
                scheduling_preset = DEFAULT_SCHEDULING_PRESET
            else:
                matches = [
                    (index, ground)
                    for index, ground in enumerate(workspace.ground)
                    if ground.segment_id == command.segment_id
                ]
                if len(matches) != 1:
                    raise self._command_error(
                        draft,
                        f"Authored ground segment {command.segment_id!r} was not found exactly once",
                        code="catalog_authoring.invalid_graph",
                    )
                ground_index, ground = matches[0]
            if any(
                member.ref == command.site_ref or member.site_id == model.id
                for member in ground.members
            ):
                raise self._command_error(
                    draft,
                    f"Site {model.id!r} is already placed in {command.segment_id!r}",
                    code="catalog_authoring.invalid_graph",
                )
            member_ids = {
                member.member_id for candidate in workspace.ground for member in candidate.members
            }
            member_number = _next_number("member", member_ids)
            member_id = f"member-{member_number}"
            member = BuilderVisualGroundMember(
                member_id=member_id,
                kind="ref",
                ref=command.site_ref,
                site_id=model.id,
                label=_catalog_label(model),
                summary=_site_summary(model),
            )
            grounds = list(workspace.ground)
            grounds[ground_index] = ground.model_copy(update={"members": (*ground.members, member)})
            workspace = workspace.model_copy(update={"ground": tuple(grounds)})
            affected_kind = "ground_member"
            affected_id = member_id
        elif isinstance(command, BuilderVisualSetGroundStampNodeCommand):
            matches = [
                (index, ground)
                for index, ground in enumerate(workspace.ground)
                if ground.segment_id == command.segment_id
            ]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Authored ground segment {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            ground_index, ground = matches[0]
            node_ref = self._node_ref_for_command(
                draft,
                snapshot,
                command.node_ref,
                placement="ground",
            )
            installed, boresights = self._ground_installation_facts(
                draft,
                snapshot,
                node_ref,
            )
            grounds = list(workspace.ground)
            grounds[ground_index] = ground.model_copy(
                update={
                    "stamp": ground.stamp.model_copy(
                        update={
                            "node_ref": node_ref,
                            "installed": installed,
                            "boresights": boresights,
                        }
                    )
                }
            )
            workspace = workspace.model_copy(update={"ground": tuple(grounds)})
            affected_kind = "ground"
            affected_id = command.segment_id
        elif isinstance(command, BuilderVisualSetGroundSiteNodeCommand):
            matches = [
                (index, ground)
                for index, ground in enumerate(workspace.ground)
                if ground.segment_id == command.segment_id
            ]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Authored ground segment {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            ground_index, ground = matches[0]
            member_matches = [
                (index, member)
                for index, member in enumerate(ground.members)
                if member.member_id == command.member_id
            ]
            if len(member_matches) != 1 or member_matches[0][1].site is None:
                raise self._command_error(
                    draft,
                    f"Authored ground member {command.member_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            member_index, member = member_matches[0]
            assert member.site is not None
            node_matches = [
                (index, node)
                for index, node in enumerate(member.site.nodes)
                if node.node_id == command.node_id
            ]
            if len(node_matches) != 1:
                raise self._command_error(
                    draft,
                    f"Site node {command.node_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            node_index, node = node_matches[0]
            node_ref = self._node_ref_for_command(
                draft,
                snapshot,
                command.node_ref,
                placement="ground",
            )
            installed, boresights = self._ground_installation_facts(
                draft,
                snapshot,
                node_ref,
            )
            nodes = list(member.site.nodes)
            nodes[node_index] = node.model_copy(
                update={
                    "node_ref": node_ref,
                    "installed": installed,
                    "boresights": boresights,
                }
            )
            members = list(ground.members)
            members[member_index] = member.model_copy(
                update={"site": member.site.model_copy(update={"nodes": tuple(nodes)})}
            )
            grounds = list(workspace.ground)
            grounds[ground_index] = ground.model_copy(update={"members": tuple(members)})
            workspace = workspace.model_copy(update={"ground": tuple(grounds)})
            affected_kind = "ground_member"
            affected_id = command.member_id
        elif isinstance(command, BuilderVisualAddGroundSiteNodeCommand):
            matches = [
                (index, ground)
                for index, ground in enumerate(workspace.ground)
                if ground.segment_id == command.segment_id
            ]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Authored ground segment {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            ground_index, ground = matches[0]
            member_matches = [
                (index, member)
                for index, member in enumerate(ground.members)
                if member.member_id == command.member_id
            ]
            if len(member_matches) != 1 or member_matches[0][1].site is None:
                raise self._command_error(
                    draft,
                    f"Authored ground member {command.member_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            member_index, member = member_matches[0]
            assert member.site is not None
            source_ref = command.node_ref or (
                member.site.nodes[0].node_ref if member.site.nodes else None
            )
            node_ref = self._node_ref_for_command(
                draft,
                snapshot,
                source_ref,
                placement="ground",
            )
            installed, boresights = self._ground_installation_facts(
                draft,
                snapshot,
                node_ref,
            )
            node_ids = {node.node_id for node in member.site.nodes}
            node_number = 1
            while f"gw{node_number}" in node_ids:
                node_number += 1
            nodes_with_added = (
                *member.site.nodes,
                BuilderVisualSiteNode(
                    node_id=f"gw{node_number}",
                    node_ref=node_ref,
                    installed=installed,
                    boresights=boresights,
                ),
            )
            members = list(ground.members)
            members[member_index] = member.model_copy(
                update={"site": member.site.model_copy(update={"nodes": nodes_with_added})}
            )
            grounds = list(workspace.ground)
            grounds[ground_index] = ground.model_copy(update={"members": tuple(members)})
            workspace = workspace.model_copy(update={"ground": tuple(grounds)})
            affected_kind = "ground_member"
            affected_id = command.member_id
        elif isinstance(command, BuilderVisualMintGroundMembersCommand):
            matches = [
                (index, ground)
                for index, ground in enumerate(workspace.ground)
                if ground.segment_id == command.segment_id
            ]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Authored ground segment {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            ground_index, ground = matches[0]
            if ground.stamp.node_ref is None or ground.stamp.body is None:
                raise self._command_error(
                    draft,
                    "Ground stamp requires a node model and body before sites can be minted",
                    code="catalog_authoring.invalid_graph",
                )

            member_ids = {
                member.member_id for candidate in workspace.ground for member in candidate.members
            }
            site_ids = {
                member.site_id for candidate in workspace.ground for member in candidate.members
            }
            minted: list[BuilderVisualGroundMember] = []
            for site_intent in command.sites:
                member_number = _next_number("member", member_ids)
                member_id = f"member-{member_number}"
                member_ids.add(member_id)
                site_number = _next_number("site", site_ids)
                site_id = f"site-{site_number}"
                site_ids.add(site_id)
                site = BuilderVisualSite(
                    site_id=site_id,
                    display_name=site_intent.name,
                    body=ground.stamp.body,
                    lat_deg=site_intent.lat_deg,
                    lon_deg=site_intent.lon_deg,
                    alt_m=site_intent.alt_m,
                    nodes=(
                        BuilderVisualSiteNode(
                            node_id="gw1",
                            node_ref=ground.stamp.node_ref,
                            installed=dict(ground.stamp.installed),
                            boresights=dict(ground.stamp.boresights),
                        ),
                    ),
                )
                minted.append(
                    BuilderVisualGroundMember(
                        member_id=member_id,
                        kind="draft",
                        site_id=site_id,
                        label=site_intent.name,
                        site=site,
                    )
                )
            grounds = list(workspace.ground)
            grounds[ground_index] = ground.model_copy(
                update={"members": (*ground.members, *minted)}
            )
            workspace = workspace.model_copy(update={"ground": tuple(grounds)})
            affected_kind = "ground"
            affected_id = command.segment_id
            notice = f"minted {len(minted)} site{'s' if len(minted) != 1 else ''}"
        elif isinstance(command, BuilderVisualSetSchedulingPresetCommand):
            scheduling_preset = command.preset
            ground_matches = [
                ("draft", index)
                for index, item in enumerate(workspace.ground)
                if item.segment_id == command.segment_id
            ]
            ground_matches.extend(
                ("ref", index)
                for index, item in enumerate(workspace.ground_refs)
                if item.segment_id == command.segment_id
            )
            if len(ground_matches) != 1:
                raise self._command_error(
                    draft,
                    f"Ground segment {command.segment_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            kind, index = ground_matches[0]
            if command.member_id is not None:
                if kind != "draft":
                    raise self._command_error(
                        draft,
                        "Only an authored ground segment exposes editable member scheduling",
                    )
                grounds = list(workspace.ground)
                ground = grounds[index]
                member_matches = [
                    (member_index, member)
                    for member_index, member in enumerate(ground.members)
                    if member.member_id == command.member_id
                ]
                if len(member_matches) != 1:
                    raise self._command_error(
                        draft,
                        f"Ground member {command.member_id!r} was not found exactly once",
                        code="catalog_authoring.invalid_graph",
                    )
                member_index, member = member_matches[0]
                members = list(ground.members)
                members[member_index] = member.model_copy(
                    update={
                        "scheduling_override": (
                            None
                            if command.preset is None
                            else scheduling_preset_block(command.preset)
                        )
                    }
                )
                grounds[index] = ground.model_copy(update={"members": tuple(members)})
                workspace = workspace.model_copy(update={"ground": tuple(grounds)})
                affected_kind = "ground_member"
                affected_id = command.member_id
            elif kind == "draft":
                assert command.preset is not None
                grounds = list(workspace.ground)
                grounds[index] = grounds[index].model_copy(
                    update={"scheduling": scheduling_preset_block(command.preset)}
                )
                workspace = workspace.model_copy(update={"ground": tuple(grounds)})
                affected_kind = "ground"
                affected_id = command.segment_id
            else:
                assert command.preset is not None
                ground_refs = list(workspace.ground_refs)
                ground_refs[index] = ground_refs[index].model_copy(
                    update={"scheduling": scheduling_preset_block(command.preset)}
                )
                workspace = workspace.model_copy(update={"ground_refs": tuple(ground_refs)})
                affected_kind = "ground"
                affected_id = command.segment_id
        else:
            raise AssertionError(f"unhandled visual draft command: {type(command).__name__}")

        updated = _apply_workspace_revision(draft, workspace).draft
        return BuilderVisualDraftCommandResult(
            operation=command.operation,
            base_draft_revision=draft.draft_revision,
            draft=updated,
            affected_kind=affected_kind,
            affected_id=affected_id,
            scheduling_preset=scheduling_preset,
            notice=notice,
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

        if draft.authoring_workspace is None:
            return refuse(
                "builder.draft.no_valid_projection",
                "Catalog customization requires a valid applied graphical projection",
                "session_yaml",
            )
        matches: list[tuple[str, int, CatalogRef | None]] = []
        matches.extend(
            ("space", index, placed.source_ref)
            for index, placed in enumerate(draft.authoring_workspace.space_refs)
            if placed.segment_id == request.segment_id
        )
        matches.extend(
            ("ground", index, placed.site_set_ref)
            for index, placed in enumerate(draft.authoring_workspace.ground_refs)
            if placed.segment_id == request.segment_id
        )
        if len(matches) != 1:
            return refuse(
                "builder.draft.customize_segment_not_unique",
                f"Placed segment {request.segment_id!r} was not found exactly once",
                "segment_id",
            )
        kind, selected_index, selected_ref = matches[0]
        if selected_ref is None:
            return refuse(
                "builder.draft.customize_root_ref_required",
                "The selected segment does not contain a catalog root reference",
                "segment_id",
            )
        root_ref = CatalogRef(selected_ref)
        try:
            paths = _dependency_paths(snapshot, root_ref, request.leaf_ref)
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
                        document=_forked_document(
                            snapshot,
                            source_ref,
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
        if kind == "space":
            space_refs = list(draft.authoring_workspace.space_refs)
            space_refs[selected_index] = space_refs[selected_index].model_copy(
                update={"source_ref": root_target}
            )
            workspace = draft.authoring_workspace.model_copy(
                update={"space_refs": tuple(space_refs)}
            )
        else:
            ground_refs = list(draft.authoring_workspace.ground_refs)
            ground_refs[selected_index] = ground_refs[selected_index].model_copy(
                update={"site_set_ref": root_target}
            )
            workspace = draft.authoring_workspace.model_copy(
                update={"ground_refs": tuple(ground_refs)}
            )
        candidate = draft.model_copy(
            update={
                "authoring_workspace": workspace,
                "catalog_documents": (*draft.catalog_documents, *proposals),
            }
        )
        session, reachable_proposals, assembly_issues = _assemble_structured(
            candidate,
            allow_workspace_overlay=True,
        )
        try:
            applied_model = SegmentSessionConfig.model_validate(session)
        except (TypeError, ValueError) as error:
            return refuse(
                "builder.draft.customize_projection_invalid",
                f"Customized graphical projection is not canonical: {error}",
                "authoring_workspace",
            )
        if assembly_issues:
            return refuse(
                "builder.draft.customize_projection_invalid",
                "; ".join(issue.message for issue in assembly_issues),
                "authoring_workspace",
            )
        next_revision = draft.draft_revision + 1
        applied_session = cast(
            JsonDocument,
            applied_model.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        applied_workspace = _workspace_from_applied_session(
            applied_model,
            proposals=reachable_proposals,
            prior_workspace=workspace,
        )
        canonical = canonicalize_persisted_configuration(draft.target_ref, applied_session)
        updated = candidate.model_copy(
            update={
                "draft_revision": next_revision,
                "projection_status": "applied",
                "session_yaml": canonical.yaml_bytes.decode("utf-8"),
                "authoring_workspace": applied_workspace,
                "applied_workspace": applied_workspace,
                "applied_revision": next_revision,
                "applied_session": applied_session,
                "control_tree": _applied_control_tree(applied_model, next_revision),
                "catalog_documents": reachable_proposals,
            }
        )
        chain = tuple(
            BuilderVisualCustomizeChainEntry(source_ref=source_ref, target_ref=target_ref)
            for source_ref, target_ref in zip(path, targets, strict=True)
        )
        return BuilderVisualCustomizeChainResult(
            applied=True,
            draft=updated,
            root_source_ref=root_ref,
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
        if request.workspace is not None:
            # The page's preview: the workspace is assembled over the draft the
            # way the accepting apply assembles it, and nothing is accepted.
            session, proposals, assembly_issues = _assemble_structured(
                visual_draft.model_copy(update={"authoring_workspace": request.workspace}),
                allow_workspace_overlay=True,
            )
        elif visual_draft.authoring_workspace is not None:
            session, proposals, assembly_issues = _assemble_structured(visual_draft)
        else:
            assert visual_draft.projection_status == "no_valid_projection"
            session = {}
            proposals = ()
            assembly_issues = (
                _issue(
                    "builder.draft.no_valid_projection",
                    "Session YAML has no valid applied graphical projection",
                    target_ref=visual_draft.target_ref,
                    draft_path="session_yaml",
                ),
            )
        return _compile_visual_application(
            visual_draft,
            session,
            proposals,
            assembly_issues,
            snapshot,
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )
