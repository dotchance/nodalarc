"""Backend authority for visual Builder draft creation, opening, and assembly."""

from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

import yaml
from nodalarc.catalog_closure import catalog_document_references
from nodalarc.catalog_refs import CatalogFamily, CatalogRef, SessionRef
from nodalarc.catalog_registry import catalog_family_spec
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogReadSnapshot,
)
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.builder_api import (
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderDraftEnvelope,
    BuilderIssue,
    BuilderProposedCatalogDocument,
    BuilderSessionSaveRequest,
    BuilderVerdict,
    JsonDocument,
)
from nodalarc.models.builder_visual_api import (
    BuilderVisualAddBoundaryCommand,
    BuilderVisualAddGeneratedSpaceCommand,
    BuilderVisualAddGroundCommand,
    BuilderVisualAddGroundSiteNodeCommand,
    BuilderVisualAddNodeEthernetPortCommand,
    BuilderVisualAddOrIncrementNodeTerminalCommand,
    BuilderVisualAddRoutingDomainCommand,
    BuilderVisualAuthorInlineSpaceNodeCommand,
    BuilderVisualConnectSegmentsCommand,
    BuilderVisualCustomizeChainEntry,
    BuilderVisualCustomizeChainRequest,
    BuilderVisualCustomizeChainResult,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCommandResult,
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
    BuilderVisualMintGroundMembersCommand,
    BuilderVisualNode,
    BuilderVisualOrbit,
    BuilderVisualRederiveLinkCommand,
    BuilderVisualRoutingBoundary,
    BuilderVisualRoutingDomain,
    BuilderVisualSetGroundSiteNodeModelCommand,
    BuilderVisualSetGroundStampNodeModelCommand,
    BuilderVisualSetSchedulingPresetCommand,
    BuilderVisualSetSpacePopulationCommand,
    BuilderVisualSite,
    BuilderVisualSiteNode,
    BuilderVisualSpaceBoresight,
    BuilderVisualSpaceDraft,
    BuilderVisualTerminalMount,
    BuilderVisualWalkerLayoutRequest,
    BuilderVisualWorkspace,
    derive_walker_layout,
)
from nodalarc.models.builder_world import BuilderWorld
from nodalarc.models.catalog import Node, Terminal
from pydantic import BaseModel, JsonValue

from .builder_compiler import (
    PreviewFactory,
    canonicalize_persisted_configuration,
    compile_builder_draft,
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
_DEFAULT_GROUND_MASK_DEG = 25.0


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
            "catalog_authoring.invalid_patch",
            "catalog_authoring.invalid_graph",
            "catalog_authoring.stale_revision",
        ],
        ref: SessionRef,
        expected_revision: int | None = None,
        current_revision: int | None = None,
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


@dataclass(frozen=True, slots=True)
class _PlacedSegment:
    segment_id: str
    label: str
    kind: Literal["space", "ground"]


@dataclass(slots=True)
class _SegmentCapability:
    pairs: set[tuple[str, str]]
    access_min_elevation_deg: float | None = None


@dataclass(frozen=True, slots=True)
class _DerivedLinkPhysics:
    role: Literal["access", "isl", "crosslink", "backbone"]
    medium: Literal["rf", "optical"]
    ground_mask_deg: float | None
    topology_mode: Literal["visible_candidates", "nearest_n"]
    topology_n: int
    formable: bool


def _placed_segments(workspace: BuilderVisualWorkspace) -> tuple[_PlacedSegment, ...]:
    return (
        *(_PlacedSegment(item.segment_id, item.label, "space") for item in workspace.space_refs),
        *(_PlacedSegment(item.segment_id, item.display_name, "space") for item in workspace.space),
        *(_PlacedSegment(item.segment_id, item.label, "ground") for item in workspace.ground_refs),
        *(
            _PlacedSegment(item.segment_id, item.display_name, "ground")
            for item in workspace.ground
        ),
    )


def _next_number(prefix: str, values: set[str]) -> int:
    number = 1
    while f"{prefix}-{number}" in values:
        number += 1
    return number


def _stamp_base(value: str, *, label: str) -> tuple[int, int]:
    parts = value.split(".")
    if len(parts) != 2:
        raise ValueError(f"{label} must contain exactly two IPv4 octets")
    octets: list[int] = []
    for part in parts:
        if not part.isdecimal() or str(int(part)) != part:
            raise ValueError(f"{label} must contain canonical decimal IPv4 octets")
        octet = int(part)
        if not 0 <= octet <= 255:
            raise ValueError(f"{label} octets must be between 0 and 255")
        octets.append(octet)
    return octets[0], octets[1]


def _matching_stamp_index(
    address: str,
    *,
    lan_base: str,
    loopback_base: str,
) -> int | None:
    patterns = (
        (rf"^{re.escape(lan_base)}\.(\d+)\.0/24$", 0),
        (rf"^{re.escape(lan_base)}\.(\d+)\.1/24$", 0),
        (rf"^{re.escape(loopback_base)}\.0\.(\d+)/32$", -1),
    )
    for pattern, adjustment in patterns:
        match = re.fullmatch(pattern, address)
        if match is not None:
            return int(match.group(1)) + adjustment
    return None


def _next_ground_mint_index(ground: BuilderVisualGroundDraft) -> int:
    highest = -1
    for member in ground.members:
        if member.site is None:
            continue
        addresses = [
            member.site.lan_ipv4,
            *(
                address
                for node in member.site.nodes
                for address in (node.lo0_ipv4, node.terr0_ipv4)
            ),
        ]
        for address in addresses:
            index = _matching_stamp_index(
                address,
                lan_base=ground.stamp.lan_base,
                loopback_base=ground.stamp.loopback_base,
            )
            if index is not None:
                highest = max(highest, index)
    return highest + 1


def _capabilities_by_segment(world: BuilderWorld | None) -> dict[str, _SegmentCapability]:
    capabilities: dict[str, _SegmentCapability] = {}
    if world is None:
        return capabilities
    for node in world.nodes:
        capability = capabilities.setdefault(node.segment_id, _SegmentCapability(pairs=set()))
        for block in node.terminal_inventory:
            capability.pairs.add((block.endpoint_role, block.medium))
            if block.endpoint_role == "access" and block.min_elevation_deg is not None:
                capability.access_min_elevation_deg = max(
                    capability.access_min_elevation_deg or 0,
                    block.min_elevation_deg,
                )
    return capabilities


def _derive_link_physics(
    world: BuilderWorld | None,
    first: _PlacedSegment,
    second: _PlacedSegment,
) -> _DerivedLinkPhysics:
    capabilities = _capabilities_by_segment(world)
    first_capability = capabilities.get(first.segment_id)
    second_capability = capabilities.get(second.segment_id)
    ground_capability = (
        first_capability
        if first.kind == "ground"
        else second_capability
        if second.kind == "ground"
        else None
    )
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
                first_capability is not None
                and second_capability is not None
                and token in first_capability.pairs
                and token in second_capability.pairs
            ):
                return _DerivedLinkPhysics(
                    role=role,
                    medium=medium,
                    ground_mask_deg=(
                        ground_capability.access_min_elevation_deg
                        if ground_capability is not None
                        else None
                    ),
                    topology_mode=topology_mode,
                    topology_n=topology_n,
                    formable=True,
                )
    role, topology_mode, topology_n = preferences[0]
    return _DerivedLinkPhysics(
        role=role,
        medium="rf" if role == "access" else "optical",
        ground_mask_deg=(
            ground_capability.access_min_elevation_deg if ground_capability is not None else None
        ),
        topology_mode=topology_mode,
        topology_n=topology_n,
        formable=False,
    )


def _physics_notice(physics: _DerivedLinkPhysics, *, has_ground: bool) -> str:
    mask = ""
    if has_ground and physics.role == "access":
        if physics.ground_mask_deg is None:
            mask = (
                " · no terminal declares an elevation floor — seeded default "
                f"{int(_DEFAULT_GROUND_MASK_DEG)}°"
            )
        else:
            mask = f" · {physics.ground_mask_deg:g}° mask"
    topology = (
        f" · nearest-{physics.topology_n}"
        if physics.topology_mode == "nearest_n"
        else " · all visible pairs"
    )
    warning = "" if physics.formable else " — WARNING: neither side has matching terminals"
    return f"re-derived: {physics.role} · {physics.medium}{mask}{topology}{warning}"


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


def _boundary_exchange(
    source: str,
    target: str,
    *,
    export_node_loopbacks: bool,
) -> JsonDocument:
    return {
        "from": source,
        "to": target,
        "prefixes": {"aggregate_of": "originated"},
        "export_node_loopbacks": export_node_loopbacks,
        "install_via": "peer_loopback",
    }


class _Assembly:
    def __init__(self, draft: BuilderVisualDraftEnvelope) -> None:
        self.draft = draft
        self.issues: list[BuilderIssue] = []
        self.proposals: dict[CatalogRef, BuilderProposedCatalogDocument] = {}
        self.expected_revisions = {
            item.ref: item.expected_revision for item in draft.expected_catalog_revisions
        }
        self.used_expected_revisions: set[CatalogRef] = set()
        self.derived_ids: set[str] = set()
        self.owner = _identifier(draft.target_ref.relative_path.stem) or "untitled-session"
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
        return CatalogRef(f"user:{family}/{self.owner}/{object_id}.yaml")

    def propose(self, ref: CatalogRef, document: JsonDocument, *, path: str) -> None:
        candidate = BuilderProposedCatalogDocument(
            ref=ref,
            document=document,
            expected_revision=self.expected_revisions.get(ref),
        )
        if ref in self.expected_revisions:
            self.used_expected_revisions.add(ref)
        existing = self.proposals.get(ref)
        if existing is None:
            self.proposals[ref] = candidate
        elif existing.document != candidate.document:
            self.issue(
                "builder.draft.component_identity_collision",
                f"Multiple authored components target {ref} with different content",
                path,
            )

    def finish_revision_checks(self) -> None:
        for ref in sorted(
            set(self.expected_revisions).difference(self.used_expected_revisions),
            key=str,
        ):
            self.issue(
                "builder.draft.unused_revision_expectation",
                f"Revision expectation for {ref} does not match an authored component",
                "expected_catalog_revisions",
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
    return node_id, {
        "node": {
            "id": node_id,
            "display_name": node.display_name,
            "forwarding": cast(JsonValue, node.forwarding),
            "ethernet": [
                {"id": assembly.required_identifier(port, path=path, fallback="terr0")}
                for port in node.ethernet
            ],
            "terminals": terminals,
            "payloads": [],
            "reference": "urn:nodalarc:session-builder-draft",
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
        ("lan_ipv4", site.lan_ipv4),
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
        if node.model_ref is None:
            assembly.issue(
                "builder.draft.site_node_model_required",
                "Installed site nodes require a node model reference",
                f"{node_path}.model_ref",
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
                "model": cast(
                    JsonValue, str(node.model_ref) if node.model_ref is not None else None
                ),
                "payloads": {},
                "terminals": terminals,
                "interfaces": {
                    "lo0": {"ipv4": node.lo0_ipv4},
                    "terr0": {"ipv4": node.terr0_ipv4},
                },
            }
        )
    return site_id, {
        "site": {
            "id": site_id,
            "display_name": site.display_name,
            "lan": {"ipv4": site.lan_ipv4},
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


def _endpoint_document(
    assembly: _Assembly,
    endpoint: BuilderVisualLinkEndpoint,
    *,
    path: str,
) -> JsonDocument:
    segment_id = assembly.required_identifier(
        endpoint.segment_id,
        path=f"{path}.segment_id",
        fallback="missing-segment",
    )
    for field_name, value in (("role", endpoint.role), ("medium", endpoint.medium)):
        if value is None:
            assembly.issue(
                f"builder.draft.link_endpoint_{field_name}_required",
                f"Link endpoint {field_name} is required",
                f"{path}.{field_name}",
            )
    selector: JsonDocument = {"segment": segment_id}
    if endpoint.tag:
        selector = {
            "all": [
                {"segment": segment_id},
                {"tag": _identifier(endpoint.tag)},
            ]
        }
    document: JsonDocument = {
        "select": selector,
        "terminal": {
            "all": [
                {"role": cast(JsonValue, endpoint.role)},
                {"medium": cast(JsonValue, endpoint.medium)},
            ]
        },
    }
    if endpoint.min_elevation_deg is not None:
        document["min_elevation_deg"] = endpoint.min_elevation_deg
    return document


def _assemble_structured(
    draft: BuilderVisualDraftEnvelope,
) -> tuple[JsonDocument, tuple[BuilderProposedCatalogDocument, ...], tuple[BuilderIssue, ...]]:
    assert draft.workspace is not None
    workspace = draft.workspace
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
            f"{target_name}-{segment_id}",
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
                "reference": "urn:nodalarc:session-builder-draft",
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
            f"{target_name}-{segment_id}",
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
                    "reference": "urn:nodalarc:session-builder-draft",
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

    link_rules: list[JsonValue] = []
    rule_ids: dict[str, str] = {}
    for index, rule in enumerate(workspace.links):
        path = f"workspace.links.{index}"
        rule_id = assembly.required_identifier(
            rule.rule_id,
            path=f"{path}.rule_id",
            fallback=f"link-{index + 1}",
        )
        rule_ids[rule.rule_id] = rule_id
        if rule.topology_mode is None:
            assembly.issue(
                "builder.draft.link_topology_required",
                "Link topology mode is required",
                f"{path}.topology_mode",
            )
        topology: JsonDocument = {"mode": cast(JsonValue, rule.topology_mode)}
        if rule.topology_mode == "nearest_n":
            topology["n"] = cast(JsonValue, rule.topology_n)
            if rule.topology_n is None:
                assembly.issue(
                    "builder.draft.link_topology_n_required",
                    "nearest_n topology requires n",
                    f"{path}.topology_n",
                )
        document: JsonDocument = {
            "id": rule_id,
            **({} if rule.enabled else {"enabled": False}),
            "topology": topology,
            "endpoints": [
                _endpoint_document(assembly, rule.a, path=f"{path}.a"),
                _endpoint_document(assembly, rule.b, path=f"{path}.b"),
            ],
        }
        if rule.max_range_km is not None:
            document["constraints"] = {"max_range_km": rule.max_range_km}
        link_rules.append(document)

    domains: list[JsonValue] = []
    domain_ids: dict[str, str] = {}
    for index, domain in enumerate(workspace.routing_domains):
        path = f"workspace.routing_domains.{index}"
        domain_id = assembly.required_identifier(
            domain.domain_id,
            path=f"{path}.domain_id",
            fallback=f"domain-{index + 1}",
        )
        domain_ids[domain.domain_id] = domain_id
        if domain.protocol is None:
            assembly.issue(
                "builder.draft.routing_protocol_required",
                "Routing protocol is required",
                f"{path}.protocol",
            )
        if not domain.member_segment_ids:
            assembly.issue(
                "builder.draft.routing_members_required",
                "Routing domains require members; the domain was not omitted",
                f"{path}.member_segment_ids",
            )
        selectors = [
            {"segment": assembly.required_identifier(member, path=path, fallback="missing-segment")}
            for member in domain.member_segment_ids
        ]
        domain_document: JsonDocument = {
            "id": domain_id,
            "protocol": cast(JsonValue, domain.protocol),
            "selectors": selectors if len(selectors) <= 1 else [{"any": selectors}],
        }
        if domain.protocol in {"isis", "ospf"}:
            domain_document["area_assignment"] = {"strategy": "flat"}
        if domain.hello_interval_s is not None or domain.hold_interval_s is not None:
            if domain.hello_interval_s is None or domain.hold_interval_s is None:
                assembly.issue(
                    "builder.draft.routing_timers_incomplete",
                    "Routing timers require both hello and hold intervals",
                    path,
                )
            domain_document["timers"] = {
                "hello_interval_s": cast(JsonValue, domain.hello_interval_s),
                "hold_interval_s": cast(JsonValue, domain.hold_interval_s),
            }
        domains.append(domain_document)

    boundaries: list[JsonValue] = []
    for index, boundary in enumerate(workspace.boundaries):
        path = f"workspace.boundaries.{index}"
        if boundary.adapter is None:
            assembly.issue(
                "builder.draft.routing_adapter_required",
                "Routing boundary adapter is required",
                f"{path}.adapter",
            )
        from_id = domain_ids.get(boundary.from_domain_id, boundary.from_domain_id)
        to_id = domain_ids.get(boundary.to_domain_id, boundary.to_domain_id)
        over_id = rule_ids.get(boundary.over_rule_id, boundary.over_rule_id)
        for field_name, value in (
            ("over_rule_id", over_id),
            ("from_domain_id", from_id),
            ("to_domain_id", to_id),
        ):
            if not value:
                assembly.issue(
                    f"builder.draft.boundary_{field_name}_required",
                    f"Boundary {field_name.replace('_', ' ')} is required",
                    f"{path}.{field_name}",
                )
        boundaries.append(
            {
                "over": over_id,
                "adapter": cast(JsonValue, boundary.adapter),
                "export": [
                    _boundary_exchange(
                        from_id,
                        to_id,
                        export_node_loopbacks=boundary.export_node_loopbacks,
                    ),
                    _boundary_exchange(
                        to_id,
                        from_id,
                        export_node_loopbacks=boundary.export_node_loopbacks,
                    ),
                ],
            }
        )

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
    if domains or boundaries:
        session["routing"] = {
            "domains": domains,
            **({"boundaries": boundaries} if boundaries else {}),
        }
    if link_rules:
        session["link_rules"] = link_rules
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
    assembly.finish_revision_checks()
    return session, tuple(assembly.proposals.values()), tuple(assembly.issues)


def _assemble_opaque(
    draft: BuilderVisualDraftEnvelope,
) -> tuple[JsonDocument, tuple[BuilderIssue, ...]]:
    assert draft.session_yaml is not None
    issues: list[BuilderIssue] = []
    try:
        parsed = load_configuration_yaml(draft.session_yaml)
        if not isinstance(parsed, dict):
            raise TypeError("session YAML must contain one mapping document")
        session = cast(JsonDocument, parsed)
    except (UnicodeError, yaml.YAMLError, TypeError, ValueError) as error:
        issues.append(
            _issue(
                "builder.draft.opaque_yaml_invalid",
                f"Session YAML could not be parsed: {error}",
                target_ref=draft.target_ref,
                draft_path="session_yaml",
            )
        )
        session = {}
    return session, tuple(issues)


def _validated_catalog_model(snapshot: CatalogReadSnapshot, ref: CatalogRef) -> BaseModel:
    document = snapshot.get(ref)
    data = load_configuration_yaml(document.content)
    family = cast(CatalogFamily, ref.family)
    return catalog_family_spec(family).validate_document(data)


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
    snapshot: CatalogReadSnapshot,
    source_ref: CatalogRef,
    target_ref: CatalogRef,
    *,
    replacements: dict[CatalogRef, CatalogRef],
) -> JsonDocument:
    model = _rewrite_catalog_references(
        _validated_catalog_model(snapshot, source_ref),
        replacements,
    )
    model = model.model_copy(update={"id": target_ref.relative_path.stem})
    normalized = cast(
        JsonDocument,
        model.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    wrapper = catalog_family_spec(cast(CatalogFamily, source_ref.family)).wrapper
    if wrapper is None:
        raise ValueError("customize-chain cannot fork session documents")
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
        name = _identifier(request.session_name) or "untitled-session"
        target_ref = SessionRef(f"user:sessions/{name}.yaml")
        snapshot = self._context.repository.snapshot(self._context.scope)
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
        return BuilderVisualDraftEnvelope(
            draft_revision=0,
            mode="structured",
            target_ref=target_ref,
            workspace=BuilderVisualWorkspace(
                session_name=name,
                display_name=request.display_name,
                description=request.description,
                start_time=now.isoformat().replace("+00:00", "Z"),
            ),
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
            if request.source_ref.namespace == "nodalarc":
                try:
                    snapshot.get(target_ref)
                except CatalogNotFoundError:
                    pass
                else:
                    raise BuilderVisualDraftConflictError(
                        f"Default session customization target already exists: {target_ref}; "
                        "choose an explicit new user: session reference",
                        ref=target_ref,
                    )
        expected_revision: str | None = None
        try:
            target = snapshot.get(target_ref)
        except CatalogNotFoundError:
            pass
        else:
            expected_revision = str(target.revision)
        session_yaml = source.content.decode("utf-8")
        if request.source_ref.relative_path.stem != target_ref.relative_path.stem:
            document = load_configuration_yaml(source.content)
            if not isinstance(document, dict) or not isinstance(document.get("session"), dict):
                raise ValueError(f"Stored session {request.source_ref} has no session identity")
            document["session"]["name"] = target_ref.relative_path.stem
            session_yaml = canonicalize_persisted_configuration(
                target_ref,
                cast(JsonDocument, document),
            ).yaml_bytes.decode("utf-8")
        return BuilderVisualDraftEnvelope(
            draft_revision=0,
            mode="opaque_yaml",
            target_ref=target_ref,
            source_ref=request.source_ref,
            expected_session_revision=expected_revision,
            session_yaml=session_yaml,
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
    ) -> BuilderWorld | None:
        compiled = self.compile(
            BuilderVisualDraftCompileRequest(draft=draft),
            available_node_count=available_node_count,
            preview_factory=preview_factory,
        )
        return compiled.compile_result.resolved_preview

    def apply_command(
        self,
        request: BuilderVisualDraftCommandRequest,
        *,
        available_node_count: int,
        preview_factory: PreviewFactory | None = None,
    ) -> BuilderVisualDraftCommandResult:
        """Apply one backend-owned gesture to an exact structured draft revision."""

        draft = request.draft
        if request.expected_draft_revision != draft.draft_revision:
            raise self._command_error(
                draft,
                "Visual draft revision changed before the command was applied",
                code="catalog_authoring.stale_revision",
                expected_revision=request.expected_draft_revision,
                current_revision=draft.draft_revision,
            )
        if draft.mode != "structured" or draft.workspace is None:
            raise self._command_error(
                draft,
                "Typed visual commands require a structured visual draft",
            )

        snapshot = self._context.repository.snapshot(self._context.scope)
        workspace = draft.workspace
        command = request.command
        affected_kind: Literal[
            "space",
            "ground",
            "routing_domain",
            "boundary",
            "link",
            "ground_member",
        ]
        affected_id: str
        notice: str | None = None
        scheduling_preset = None

        if isinstance(command, BuilderVisualAddGeneratedSpaceCommand):
            segment_ids = {item.segment_id for item in _placed_segments(workspace)}
            number = _next_number("space", segment_ids)
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
            if space.planes is None or space.slots_per_plane is None:
                raise self._command_error(
                    draft,
                    "Space population requires planes and slots before phasing can be derived",
                    code="catalog_authoring.invalid_graph",
                )

            phasing_mode = command.phasing_mode or space.phasing_mode
            planes = command.planes or space.planes
            slots_per_plane = command.slots_per_plane or space.slots_per_plane
            if command.phasing_mode is not None:
                planes = 1 if phasing_mode == SINGLE_PLANE_PHASING_MODE else max(2, planes)
            elif command.planes is not None:
                if planes == 1:
                    phasing_mode = SINGLE_PLANE_PHASING_MODE
                elif phasing_mode == SINGLE_PLANE_PHASING_MODE:
                    phasing_mode = DEFAULT_PHASING_MODE

            single_plane = phasing_mode == SINGLE_PLANE_PHASING_MODE
            walker_layout = (
                None
                if single_plane
                else derive_walker_layout(
                    BuilderVisualWalkerLayoutRequest(
                        pattern=cast(
                            Literal["walker_delta", "walker_star"],
                            phasing_mode,
                        ),
                        planes=planes,
                        slots_per_plane=slots_per_plane,
                    )
                )
            )
            spaces = list(workspace.space)
            spaces[space_index] = space.model_copy(
                update={
                    "phasing_mode": phasing_mode,
                    "planes": planes,
                    "slots_per_plane": slots_per_plane,
                    "raan_spacing_deg": (
                        360 if walker_layout is None else walker_layout.raan_spacing_deg
                    ),
                    "phase_offset_deg": (
                        0 if walker_layout is None else walker_layout.phase_offset_deg
                    ),
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
            segment_ids = {item.segment_id for item in _placed_segments(workspace)}
            number = _next_number("ground", segment_ids)
            segment_id = f"ground-{number}"
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
                installed_counts=command.installed,
                authored_boresights=command.boresights,
            )
            ground = BuilderVisualGroundDraft(
                segment_id=segment_id,
                display_name=f"Ground segment {number}",
                stamp=BuilderVisualGroundStamp(
                    node_ref=node_ref,
                    installed=installed,
                    boresights=boresights,
                    body=command.body_ref or DEFAULT_BODY_REF,
                    lan_base=f"172.{20 + ((number - 1) % 12)}",
                    loopback_base=f"10.{200 + ((number - 1) % 55)}",
                ),
                scheduling=scheduling_preset_block(DEFAULT_SCHEDULING_PRESET),
            )
            workspace = workspace.model_copy(update={"ground": (*workspace.ground, ground)})
            affected_kind = "ground"
            affected_id = segment_id
            scheduling_preset = DEFAULT_SCHEDULING_PRESET
        elif isinstance(command, BuilderVisualSetGroundStampNodeModelCommand):
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
        elif isinstance(command, BuilderVisualSetGroundSiteNodeModelCommand):
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
                    "model_ref": node_ref,
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
                member.site.nodes[0].model_ref if member.site.nodes else None
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
                    model_ref=node_ref,
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
            try:
                _stamp_base(ground.stamp.lan_base, label="ground LAN stamp base")
                _stamp_base(ground.stamp.loopback_base, label="ground loopback stamp base")
            except ValueError as error:
                raise self._command_error(draft, str(error)) from error
            if ground.stamp.node_ref is None or ground.stamp.body is None:
                raise self._command_error(
                    draft,
                    "Ground stamp requires a node model and body before sites can be minted",
                    code="catalog_authoring.invalid_graph",
                )

            start_index = _next_ground_mint_index(ground)
            final_index = start_index + len(command.sites) - 1
            if start_index < 0 or final_index > 254:
                raise self._command_error(
                    draft,
                    "Ground stamp has no remaining IPv4 addressing room for these sites",
                    code="catalog_authoring.invalid_graph",
                )

            member_ids = {
                member.member_id for candidate in workspace.ground for member in candidate.members
            }
            site_ids = {
                member.site_id for candidate in workspace.ground for member in candidate.members
            }
            minted: list[BuilderVisualGroundMember] = []
            for offset, site_intent in enumerate(command.sites):
                address_index = start_index + offset
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
                    lan_ipv4=f"{ground.stamp.lan_base}.{address_index}.0/24",
                    nodes=(
                        BuilderVisualSiteNode(
                            node_id="gw1",
                            model_ref=ground.stamp.node_ref,
                            installed=dict(ground.stamp.installed),
                            boresights=dict(ground.stamp.boresights),
                            lo0_ipv4=(f"{ground.stamp.loopback_base}.0.{address_index + 1}/32"),
                            terr0_ipv4=f"{ground.stamp.lan_base}.{address_index}.1/24",
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
        elif isinstance(command, BuilderVisualAddRoutingDomainCommand):
            domain_ids = {item.domain_id for item in workspace.routing_domains}
            number = _next_number("domain", domain_ids)
            domain_id = f"domain-{number}"
            covered = {
                member
                for domain in workspace.routing_domains
                for member in domain.member_segment_ids
            }
            domain = BuilderVisualRoutingDomain(
                domain_id=domain_id,
                label=f"domain {number}",
                protocol="isis",
                member_segment_ids=tuple(
                    item.segment_id
                    for item in _placed_segments(workspace)
                    if item.segment_id not in covered
                ),
            )
            workspace = workspace.model_copy(
                update={"routing_domains": (*workspace.routing_domains, domain)}
            )
            affected_kind = "routing_domain"
            affected_id = domain_id
        elif isinstance(command, BuilderVisualAddBoundaryCommand):
            boundary_ids = {item.boundary_id for item in workspace.boundaries}
            number = _next_number("boundary", boundary_ids)
            boundary_id = f"boundary-{number}"
            first_rule = next(
                (
                    rule
                    for rule in workspace.links
                    if rule.a.role != "access" and rule.b.role != "access"
                ),
                workspace.links[0] if workspace.links else None,
            )
            from_domain = workspace.routing_domains[0] if workspace.routing_domains else None
            to_domain = (
                workspace.routing_domains[1] if len(workspace.routing_domains) > 1 else from_domain
            )
            boundary = BuilderVisualRoutingBoundary(
                boundary_id=boundary_id,
                over_rule_id=first_rule.rule_id if first_rule is not None else "",
                adapter="static_ip",
                from_domain_id=from_domain.domain_id if from_domain is not None else "",
                to_domain_id=to_domain.domain_id if to_domain is not None else "",
                export_node_loopbacks=True,
            )
            workspace = workspace.model_copy(
                update={"boundaries": (*workspace.boundaries, boundary)}
            )
            affected_kind = "boundary"
            affected_id = boundary_id
        elif isinstance(command, BuilderVisualConnectSegmentsCommand):
            placed = _placed_segments(workspace)
            from_matches = [item for item in placed if item.segment_id == command.from_segment_id]
            to_matches = [item for item in placed if item.segment_id == command.to_segment_id]
            if len(from_matches) != 1 or len(to_matches) != 1:
                raise self._command_error(
                    draft,
                    "Connect endpoints must each identify exactly one placed segment",
                    code="catalog_authoring.invalid_graph",
                )
            source = from_matches[0]
            target = to_matches[0]
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
            link_ids = {item.rule_id for item in workspace.links}
            number = _next_number("link", link_ids)
            rule_id = f"link-{number}"
            label = (
                f"{first.label} mesh"
                if first.segment_id == second.segment_id
                else f"{first.label} to {second.label}"
            )
            taken = {_identifier(item.label) or item.rule_id for item in workspace.links}
            if _identifier(label) in taken:
                base = label[:40]
                suffix = 2
                while _identifier(f"{base} {suffix}") in taken:
                    suffix += 1
                    if suffix >= 1_000:
                        raise self._command_error(
                            draft,
                            f"Cannot allocate a unique link label for {label!r}",
                            code="catalog_authoring.invalid_graph",
                        )
                label = f"{base} {suffix}"

            def endpoint_for_segment(segment: _PlacedSegment) -> BuilderVisualLinkEndpoint:
                ground_mask = (
                    physics.ground_mask_deg
                    if physics.ground_mask_deg is not None
                    else _DEFAULT_GROUND_MASK_DEG
                )
                return BuilderVisualLinkEndpoint(
                    segment_id=segment.segment_id,
                    role=physics.role,
                    medium=physics.medium,
                    min_elevation_deg=(
                        ground_mask
                        if physics.role == "access" and segment.kind == "ground"
                        else None
                    ),
                )

            rule = BuilderVisualLinkRule(
                rule_id=rule_id,
                label=label,
                a=endpoint_for_segment(first),
                b=endpoint_for_segment(second),
                topology_mode=physics.topology_mode,
                topology_n=physics.topology_n,
            )
            workspace = workspace.model_copy(update={"links": (*workspace.links, rule)})
            affected_kind = "link"
            affected_id = rule_id
        elif isinstance(command, BuilderVisualRederiveLinkCommand):
            matches = [
                (index, rule)
                for index, rule in enumerate(workspace.links)
                if rule.rule_id == command.rule_id
            ]
            if len(matches) != 1:
                raise self._command_error(
                    draft,
                    f"Link rule {command.rule_id!r} was not found exactly once",
                    code="catalog_authoring.invalid_graph",
                )
            rule_index, rule = matches[0]
            placed = _placed_segments(workspace)
            a_id = command.segment_id if command.side == "a" else rule.a.segment_id
            b_id = command.segment_id if command.side == "b" else rule.b.segment_id
            a_matches = [item for item in placed if item.segment_id == a_id]
            b_matches = [item for item in placed if item.segment_id == b_id]
            if len(a_matches) != 1 or len(b_matches) != 1:
                selected_endpoint = rule.a if command.side == "a" else rule.b
                updated_endpoint = selected_endpoint.model_copy(
                    update={"segment_id": command.segment_id}
                )
                updated_rule = rule.model_copy(update={command.side: updated_endpoint})
                notice = "endpoint changed — pick a placed segment to re-derive physics"
            else:
                first, second = a_matches[0], b_matches[0]
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
                    ground_mask = (
                        physics.ground_mask_deg
                        if physics.ground_mask_deg is not None
                        else _DEFAULT_GROUND_MASK_DEG
                    )
                    return existing.model_copy(
                        update={
                            "segment_id": segment.segment_id,
                            "role": physics.role,
                            "medium": physics.medium,
                            "min_elevation_deg": (
                                ground_mask
                                if physics.role == "access" and segment.kind == "ground"
                                else None
                            ),
                        }
                    )

                updated_rule = rule.model_copy(
                    update={
                        "a": updated_endpoint_for(rule.a, first),
                        "b": updated_endpoint_for(rule.b, second),
                        "topology_mode": physics.topology_mode,
                        "topology_n": physics.topology_n,
                    }
                )
                notice = _physics_notice(
                    physics,
                    has_ground=first.kind == "ground" or second.kind == "ground",
                )
            links = list(workspace.links)
            links[rule_index] = updated_rule
            workspace = workspace.model_copy(update={"links": tuple(links)})
            affected_kind = "link"
            affected_id = command.rule_id
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

        updated = draft.model_copy(
            update={
                "draft_revision": draft.draft_revision + 1,
                "workspace": workspace,
            }
        )
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
        snapshot = self._context.repository.snapshot(self._context.scope)
        draft = request.draft

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

        root_ref: CatalogRef | None = None
        structured_location: tuple[str, int] | None = None
        opaque_document: JsonDocument | None = None
        opaque_segment_index: int | None = None
        if draft.mode == "structured":
            assert draft.workspace is not None
            matches: list[tuple[str, int, CatalogRef | None]] = []
            matches.extend(
                ("space", index, placed.source_ref)
                for index, placed in enumerate(draft.workspace.space_refs)
                if placed.segment_id == request.segment_id
            )
            matches.extend(
                ("ground", index, placed.site_set_ref)
                for index, placed in enumerate(draft.workspace.ground_refs)
                if placed.segment_id == request.segment_id
            )
            if len(matches) != 1:
                return refuse(
                    "builder.draft.customize_segment_not_unique",
                    f"Placed segment {request.segment_id!r} was not found exactly once",
                    "segment_id",
                )
            kind, index, selected_ref = matches[0]
            if selected_ref is None:
                return refuse(
                    "builder.draft.customize_root_ref_required",
                    "The selected segment does not contain a catalog root reference",
                    "segment_id",
                )
            root_ref = CatalogRef(selected_ref)
            structured_location = (kind, index)
        else:
            assert draft.session_yaml is not None
            try:
                parsed = load_configuration_yaml(draft.session_yaml)
                if not isinstance(parsed, dict):
                    raise TypeError("session YAML must contain one mapping")
                opaque_document = cast(JsonDocument, parsed)
                segments = opaque_document.get("segments")
                if not isinstance(segments, list):
                    raise TypeError("session YAML segments must be an array")
            except (UnicodeError, yaml.YAMLError, TypeError, ValueError) as error:
                return refuse(
                    "builder.draft.customize_session_invalid",
                    f"The opaque session could not be inspected: {error}",
                    "session_yaml",
                )
            opaque_matches = [
                (index, segment)
                for index, segment in enumerate(segments)
                if isinstance(segment, dict) and segment.get("id") == request.segment_id
            ]
            if len(opaque_matches) != 1:
                return refuse(
                    "builder.draft.customize_segment_not_unique",
                    f"Placed segment {request.segment_id!r} was not found exactly once",
                    "segment_id",
                )
            opaque_segment_index, selected = opaque_matches[0]
            raw_root = selected.get("source")
            if not isinstance(raw_root, str):
                placement = selected.get("placement")
                raw_root = placement.get("from_site_set") if isinstance(placement, dict) else None
            try:
                root_ref = CatalogRef(raw_root) if isinstance(raw_root, str) else None
            except TypeError, ValueError:
                root_ref = None
            if root_ref is None:
                return refuse(
                    "builder.draft.customize_root_ref_required",
                    "The selected segment does not contain a catalog root reference",
                    "segment_id",
                )

        assert root_ref is not None
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
        owner = _identifier(draft.target_ref.relative_path.stem) or "untitled-session"
        occupied = {proposal.ref for proposal in draft.catalog_documents}

        def exists(ref: CatalogRef) -> bool:
            if ref in occupied:
                return True
            try:
                snapshot.get(ref)
            except CatalogNotFoundError:
                return False
            return True

        def allocate(source_ref: CatalogRef, *, explicit: CatalogRef | None = None) -> CatalogRef:
            if explicit is not None:
                return explicit
            stem = source_ref.relative_path.stem
            family = source_ref.family
            candidate = CatalogRef(f"user:{family}/{owner}/{stem}.yaml")
            if not exists(candidate):
                return candidate
            for suffix in range(2, 1_000):
                candidate = CatalogRef(f"user:{family}/{owner}/{stem}-{suffix}.yaml")
                if not exists(candidate):
                    return candidate
            raise ValueError(f"could not allocate a user ref for {source_ref}")

        targets: list[CatalogRef] = []
        try:
            for index, source_ref in enumerate(path):
                explicit = request.target_leaf_ref if index == len(path) - 1 else None
                target_ref = allocate(source_ref, explicit=explicit)
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
                        document=_forked_document(
                            snapshot,
                            source_ref,
                            target_ref,
                            replacements=replacements,
                        ),
                    )
                )
        except (CatalogNotFoundError, TypeError, ValueError, yaml.YAMLError) as error:
            return refuse(
                "builder.draft.customize_fork_invalid",
                f"The catalog chain could not be forked: {error}",
                "leaf_ref",
            )

        root_target = targets[0]
        if structured_location is not None:
            assert draft.workspace is not None
            kind, selected_index = structured_location
            if kind == "space":
                space_refs = list(draft.workspace.space_refs)
                space_refs[selected_index] = space_refs[selected_index].model_copy(
                    update={"source_ref": root_target}
                )
                workspace = draft.workspace.model_copy(update={"space_refs": tuple(space_refs)})
            else:
                ground_refs = list(draft.workspace.ground_refs)
                ground_refs[selected_index] = ground_refs[selected_index].model_copy(
                    update={"site_set_ref": root_target}
                )
                workspace = draft.workspace.model_copy(update={"ground_refs": tuple(ground_refs)})
            updated = draft.model_copy(
                update={
                    "draft_revision": draft.draft_revision + 1,
                    "workspace": workspace,
                    "catalog_documents": (*draft.catalog_documents, *proposals),
                }
            )
        else:
            assert opaque_document is not None and opaque_segment_index is not None
            segments = cast(list[JsonValue], opaque_document["segments"])
            selected = cast(dict[str, JsonValue], segments[opaque_segment_index])
            if isinstance(selected.get("source"), str):
                selected["source"] = str(root_target)
            else:
                placement = cast(dict[str, JsonValue], selected["placement"])
                placement["from_site_set"] = str(root_target)
            updated = draft.model_copy(
                update={
                    "draft_revision": draft.draft_revision + 1,
                    "session_yaml": yaml.safe_dump(opaque_document, sort_keys=False),
                    "catalog_documents": (*draft.catalog_documents, *proposals),
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
        snapshot: CatalogReadSnapshot = self._context.repository.snapshot(self._context.scope)
        visual_draft = request.draft
        if visual_draft.mode == "structured":
            session, proposals, assembly_issues = _assemble_structured(visual_draft)
        else:
            session, assembly_issues = _assemble_opaque(visual_draft)
            proposals = visual_draft.catalog_documents
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
        save_request = BuilderSessionSaveRequest(
            draft=assembled,
            target_ref=visual_draft.target_ref,
            expected_session_revision=visual_draft.expected_session_revision,
        )
        return BuilderVisualDraftAssemblyResult(
            visual_draft=visual_draft,
            assembled_draft=assembled,
            save_request=save_request,
            compile_result=compile_result,
            assembly_issues=assembly_issues,
        )
