"""Translate typed Wizard intent into an ordinary ref-composed Builder draft."""

from __future__ import annotations

import re
import secrets
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from nodalarc.catalog_closure import CatalogClosureCollector, CatalogDependencyGraph
from nodalarc.catalog_paths import CatalogRoots, resolve_catalog_reference
from nodalarc.catalog_refs import CatalogRef, SiteSetRef, SpaceSourceRef
from nodalarc.catalog_registry import validate_referenced_configuration_document
from nodalarc.catalog_repository import CatalogReadSnapshot
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.builder_api import (
    BuilderCompileRequest,
    BuilderDraftEnvelope,
    BuilderProposedCatalogDocument,
    JsonDocument,
    WizardBfdMetadata,
    WizardCompileRequest,
    WizardConstellationGeometry,
    WizardCoverageRequest,
    WizardExtensionMetadata,
    WizardExtensionRulesResponse,
    WizardPhysicalIntent,
    WizardProtocolMetadata,
    WizardRoutingTimerFieldMetadata,
    WizardRoutingTimerIntent,
    WizardSessionIntent,
)
from nodalarc.models.segment_session import RoutingTimers
from nodalarc.session_generator import (
    WIZARD_CUSTOM_GEOMETRY_DEFAULT_NODE,
    assemble_session_document,
    constellation_source_runtime_capability,
    custom_geometry_runtime_capability,
)

from .builder_compiler import canonicalize_persisted_configuration

WizardIdentityFactory = Callable[[], str]

_EARTH_BODY = "nodalarc:bodies/earth.yaml"
_CUSTOM_EPOCH = "2026-06-08T00:00:00Z"
_IDENTIFIER_PARTS = re.compile(r"[^a-z0-9_-]+")
_WIZARD_EXTENSION_METADATA = (
    WizardExtensionMetadata(
        id="te",
        label="Traffic Engineering",
        description="MPLS-TE extensions that advertise bandwidth and delay.",
    ),
    WizardExtensionMetadata(
        id="mpls",
        label="MPLS / LDP",
        description="Label Distribution Protocol for the MPLS forwarding plane.",
    ),
    WizardExtensionMetadata(
        id="sr",
        label="Segment Routing",
        description="Source-routed MPLS using SRGB label blocks.",
    ),
)
_WIZARD_BFD_METADATA = WizardBfdMetadata(
    heading="BFD (Bidirectional Forwarding Detection)",
    enabled_field="bfd",
    enable_label="Enable BFD",
    enable_description=(
        "Sub-second link failure detection independent of routing protocol hellos."
    ),
    timer_fields=(
        WizardRoutingTimerFieldMetadata(
            id="bfd_detect_multiplier",
            label="Detect Multiplier",
            description=(
                "Missed BFD packets before declaring failure. Detection time equals "
                "the multiplier times the interval."
            ),
            guidance="Typical: 3 (900ms detection at 300ms interval).",
            minimum=1,
        ),
        WizardRoutingTimerFieldMetadata(
            id="bfd_rx_interval",
            label="RX Interval",
            unit="ms",
            description="Minimum interval for receiving BFD control packets.",
            guidance="Aggressive: 100ms. Typical: 300ms.",
            minimum=1,
        ),
        WizardRoutingTimerFieldMetadata(
            id="bfd_tx_interval",
            label="TX Interval",
            unit="ms",
            description="Minimum interval for transmitting BFD control packets.",
            guidance="Aggressive: 100ms. Typical: 300ms.",
            minimum=1,
        ),
    ),
)
_WIZARD_PROTOCOL_METADATA = (
    WizardProtocolMetadata(
        id="ospf",
        label="OSPF",
        description="Open Shortest Path First distributed link-state routing.",
        extensions=("sr", "te", "mpls"),
        extension_constraints={},
        timer_label="OSPF Timers",
        timer_fields=(
            WizardRoutingTimerFieldMetadata(
                id="ospf_hello_interval",
                label="Hello Interval",
                unit="s",
                description="Time between OSPF hello packets on each interface.",
                guidance="LEO: 1s. Terrestrial: 10s (default).",
                minimum=1,
            ),
            WizardRoutingTimerFieldMetadata(
                id="ospf_dead_interval",
                label="Dead Interval",
                unit="s",
                description="Time without hellos before declaring a neighbor dead.",
                guidance="Must exceed the hello interval.",
                minimum=1,
            ),
            WizardRoutingTimerFieldMetadata(
                id="ospf_spf_delay",
                label="SPF Delay",
                unit="ms",
                description="Initial delay before SPF computation after a topology change.",
                guidance="Aggressive: 50ms. Conservative: 1000ms.",
                minimum=0,
            ),
            WizardRoutingTimerFieldMetadata(
                id="ospf_spf_initial_hold",
                label="SPF Initial Hold",
                unit="ms",
                description="Minimum time between consecutive SPF runs; doubles on each run.",
                guidance="LEO: 200ms. Stable networks: 1000-5000ms.",
                minimum=0,
            ),
            WizardRoutingTimerFieldMetadata(
                id="ospf_spf_max_hold",
                label="SPF Max Hold",
                unit="ms",
                description="Maximum delay between SPF runs during sustained churn.",
                guidance="LEO: 1000ms. Must not exceed the handover interval.",
                minimum=0,
            ),
        ),
        non_flat_area_warning=(
            "OSPF multi-area with dynamic constellation topologies may lose backbone "
            "contiguity when cross-plane ISLs drop at polar latitudes. Use the flat area "
            "strategy when contiguous area 0 cannot be guaranteed."
        ),
    ),
    WizardProtocolMetadata(
        id="isis",
        label="IS-IS",
        description="Intermediate System to Intermediate System native CLNS routing.",
        extensions=("sr", "te", "mpls"),
        extension_constraints={},
        timer_label="IS-IS Timers",
        timer_fields=(
            WizardRoutingTimerFieldMetadata(
                id="isis_hello_interval",
                label="Hello Interval",
                unit="s",
                description="Time between IS-IS hello packets.",
                guidance="LEO: 1s. Terrestrial: 3-10s.",
                minimum=1,
            ),
            WizardRoutingTimerFieldMetadata(
                id="isis_hello_multiplier",
                label="Hello Multiplier",
                description="Missed hellos before declaring a neighbor down.",
                guidance="Typical: 3, producing 3s dead time with a 1s hello interval.",
                minimum=1,
            ),
            WizardRoutingTimerFieldMetadata(
                id="spf_init_delay",
                label="SPF Init Delay",
                unit="ms",
                description="Delay before the first SPF computation after a topology change.",
                guidance="Aggressive: 50ms. Conservative: 1000ms.",
                minimum=0,
            ),
            WizardRoutingTimerFieldMetadata(
                id="spf_short_delay",
                label="SPF Short Delay",
                unit="ms",
                description="SPF delay for subsequent events within the learning window.",
                guidance="LEO: 200ms. Stable networks: 1000-5000ms.",
                minimum=0,
            ),
            WizardRoutingTimerFieldMetadata(
                id="spf_long_delay",
                label="SPF Long Delay",
                unit="ms",
                description="Maximum SPF delay during sustained topology churn.",
                guidance="LEO: 1000ms. Must not exceed the handover interval.",
                minimum=0,
            ),
            WizardRoutingTimerFieldMetadata(
                id="spf_holddown",
                label="SPF Holddown",
                unit="ms",
                description="Quiet time before returning the SPF backoff to its initial delay.",
                guidance="LEO: 2000ms. Terrestrial: 10000-30000ms.",
                minimum=0,
            ),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class WizardPreviewInputs:
    """Ref-composed physical inputs resolved only for one coverage request."""

    constellation_ref: SpaceSourceRef
    ground_site_set_ref: SiteSetRef
    catalog_roots: CatalogRoots


def _default_identity() -> str:
    return secrets.token_hex(8)


def _identifier(value: str, *, fallback: str = "wizard") -> str:
    normalized = _IDENTIFIER_PARTS.sub("-", value.lower()).strip("-")
    return normalized or fallback


def _number_token(value: float) -> str:
    return _identifier(f"{value:g}".replace(".", "-"), fallback="0")


def _source_name(intent: WizardSessionIntent) -> str:
    if intent.custom_constellation is not None:
        geometry = intent.custom_constellation
        return (
            f"custom-{geometry.planes}x{geometry.slots_per_plane}-"
            f"{_number_token(geometry.altitude_km)}km"
        )
    assert intent.constellation_ref is not None
    return intent.constellation_ref.relative_path.stem


def _session_name(intent: WizardSessionIntent, identity: str) -> str:
    extension = "-".join(intent.extensions) or "plain"
    return _identifier(f"{_source_name(intent)}-{intent.protocol}-{extension}-{identity}")


def _custom_constellation_document(
    geometry: WizardConstellationGeometry,
    *,
    constellation_id: str,
    orbit_ref: CatalogRef,
    node_ref: str,
) -> dict[str, Any]:
    return {
        "constellation": {
            "id": constellation_id,
            "display_name": geometry.display_name,
            "node": node_ref,
            "orbit": str(orbit_ref),
            "planes": {
                "count": geometry.planes,
                "raan_spacing_deg": geometry.raan_spacing_deg,
            },
            "slots_per_plane": geometry.slots_per_plane,
            "phasing": {
                "mode": geometry.pattern,
                "phase_offset_deg": geometry.phase_offset_deg,
            },
            "node_tags": [{"tag": "all"}],
            "reference": "urn:nodalarc:user-authored",
            "notes": geometry.description,
        }
    }


def _custom_orbit_document(
    geometry: WizardConstellationGeometry,
    *,
    orbit_id: str,
    propagator: str,
) -> dict[str, Any]:
    return {
        "orbit": {
            "id": orbit_id,
            "central_body": _EARTH_BODY,
            "epoch": _CUSTOM_EPOCH,
            "shape": {"altitude_km": geometry.altitude_km},
            "orientation": {
                "inclination_deg": geometry.inclination_deg,
                "raan_deg": 0,
                "argument_of_perigee_deg": 0,
            },
            "phase": {"mean_anomaly_deg": 0},
            "propagator": propagator,
            "reference": "urn:nodalarc:user-authored",
            "notes": (
                f"Wizard-authored {geometry.altitude_km:g} km, "
                f"{geometry.inclination_deg:g} degree orbit."
            ),
        }
    }


def _validate_orbit_capability(intent: WizardPhysicalIntent, roots: CatalogRoots) -> None:
    capability = (
        custom_geometry_runtime_capability()
        if intent.custom_constellation is not None
        else constellation_source_runtime_capability(str(intent.constellation_ref), roots)
    )
    if intent.orbit_propagator in capability.runtime_supported_propagators:
        return
    reason = capability.unavailable_reason or (
        f"orbit propagator {intent.orbit_propagator!r} is not supported for the selected "
        f"{capability.source_kind} source"
    )
    raise ValueError(reason)


def _catalog_document(ref: str, roots: CatalogRoots) -> tuple[str, dict[str, Any]]:
    parsed = CatalogRef(ref)
    path = resolve_catalog_reference(parsed, roots)
    raw = load_configuration_yaml(path.read_text(encoding="utf-8"))
    wrapper, model = validate_referenced_configuration_document(parsed, raw)
    if wrapper is None:
        raise ValueError(f"expected wrapped catalog object, got session {parsed!r}")
    return wrapper, model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _customize_selected_constellation(
    intent: WizardPhysicalIntent,
    roots: CatalogRoots,
    *,
    constellation_id: str,
    orbit_id: str,
) -> tuple[SpaceSourceRef, tuple[BuilderProposedCatalogDocument, ...]]:
    if intent.constellation_ref is None:
        raise ValueError("selected constellation customization requires a catalog reference")
    if intent.constellation_ref.family != "constellations":
        if intent.satellite_node_ref is not None:
            raise ValueError(
                "Wizard satellite-node replacement applies only to generated constellations"
            )
        return intent.constellation_ref, ()

    wrapper, constellation = _catalog_document(str(intent.constellation_ref), roots)
    if wrapper != "constellation":
        raise ValueError("selected constellation reference must resolve to a constellation")

    selected_node = str(intent.satellite_node_ref) if intent.satellite_node_ref else None
    node_changes = selected_node is not None and constellation.get("node") != selected_node
    orbit_ref = constellation.get("orbit")
    if not isinstance(orbit_ref, str):
        raise ValueError("selected persisted constellation must reference one orbit document")
    orbit_wrapper, orbit = _catalog_document(orbit_ref, roots)
    if orbit_wrapper != "orbit":
        raise ValueError("selected constellation orbit reference must resolve to an orbit")
    propagator_changes = orbit.get("propagator") != intent.orbit_propagator
    if not node_changes and not propagator_changes:
        return intent.constellation_ref, ()

    proposals: list[BuilderProposedCatalogDocument] = []
    constellation["id"] = constellation_id
    if node_changes:
        constellation["node"] = selected_node
    if propagator_changes:
        orbit["id"] = orbit_id
        orbit["propagator"] = intent.orbit_propagator
        proposed_orbit_ref = CatalogRef(f"user:orbits/wizard/{orbit_id}.yaml")
        constellation["orbit"] = str(proposed_orbit_ref)
        proposals.append(
            BuilderProposedCatalogDocument(
                ref=proposed_orbit_ref,
                origin="generated",
                document=cast(JsonDocument, {"orbit": orbit}),
            )
        )
    proposed_constellation_ref = SpaceSourceRef(
        f"user:constellations/wizard/{constellation_id}.yaml"
    )
    proposals.append(
        BuilderProposedCatalogDocument(
            ref=proposed_constellation_ref,
            origin="generated",
            document=cast(JsonDocument, {"constellation": constellation}),
        )
    )
    return proposed_constellation_ref, tuple(proposals)


def _custom_site_set_document(
    intent: WizardPhysicalIntent,
    *,
    site_set_id: str,
) -> dict[str, Any]:
    return {
        "site_set": {
            "id": site_set_id,
            "display_name": "Wizard custom ground sites",
            "sites": [str(ref) for ref in intent.custom_site_refs],
            "reference": "urn:nodalarc:user-authored",
            "notes": "Ground-site selection authored by the NodalArc session Wizard.",
        }
    }


def _routing_timers(intent: WizardSessionIntent) -> dict[str, Any] | None:
    timers = intent.routing_timers
    is_isis = intent.protocol == "isis"
    hello = timers.isis_hello_interval if is_isis else timers.ospf_hello_interval
    requested_hold = (
        timers.isis_hello_interval * timers.isis_hello_multiplier
        if is_isis
        else timers.ospf_dead_interval
    )
    spf = (
        {
            "init_delay_ms": timers.spf_init_delay,
            "short_delay_ms": timers.spf_short_delay,
            "long_delay_ms": timers.spf_long_delay,
            "holddown_ms": timers.spf_holddown,
            "time_to_learn_ms": timers.spf_time_to_learn,
        }
        if is_isis
        else {
            "init_delay_ms": timers.ospf_spf_delay,
            "short_delay_ms": timers.ospf_spf_initial_hold,
            "long_delay_ms": timers.ospf_spf_max_hold,
        }
    )
    return {
        "hello_interval_s": hello,
        "hold_interval_s": requested_hold,
        "spf": spf,
        "bfd": {
            "enabled": timers.bfd,
            "detect_multiplier": timers.bfd_detect_multiplier,
            "rx_interval_ms": timers.bfd_rx_interval,
            "tx_interval_ms": timers.bfd_tx_interval,
        },
    }


def wizard_routing_timer_defaults() -> WizardRoutingTimerIntent:
    """Return Wizard controls seeded from canonical routing timer defaults."""

    defaults = RoutingTimers()
    return WizardRoutingTimerIntent(
        bfd=defaults.bfd.enabled,
        bfd_detect_multiplier=defaults.bfd.detect_multiplier,
        bfd_rx_interval=defaults.bfd.rx_interval_ms,
        bfd_tx_interval=defaults.bfd.tx_interval_ms,
        isis_hello_interval=defaults.hello_interval_s,
        isis_hello_multiplier=defaults.hold_interval_s // defaults.hello_interval_s,
        spf_init_delay=defaults.spf.init_delay_ms,
        spf_short_delay=defaults.spf.short_delay_ms,
        spf_long_delay=defaults.spf.long_delay_ms,
        spf_holddown=2000,
        spf_time_to_learn=500,
        ospf_hello_interval=defaults.hello_interval_s,
        ospf_dead_interval=defaults.hold_interval_s,
        ospf_spf_delay=defaults.spf.init_delay_ms,
        ospf_spf_initial_hold=defaults.spf.short_delay_ms,
        ospf_spf_max_hold=defaults.spf.long_delay_ms,
    )


def wizard_extension_rules_response() -> WizardExtensionRulesResponse:
    """Return the complete backend-owned Wizard routing inventory and presentation facts."""

    return WizardExtensionRulesResponse(
        protocols=_WIZARD_PROTOCOL_METADATA,
        extensions=_WIZARD_EXTENSION_METADATA,
        area_strategies=("flat", "stripe", "per_plane"),
        default_area_strategy="flat",
        bfd=_WIZARD_BFD_METADATA,
        routing_timer_defaults=wizard_routing_timer_defaults(),
    )


def _validate_routing_choices(intent: WizardSessionIntent) -> None:
    facts = wizard_extension_rules_response()
    protocol = next((item for item in facts.protocols if item.id == intent.protocol), None)
    if protocol is None:
        raise ValueError(f"Wizard protocol {intent.protocol!r} is not available")
    unavailable = sorted(set(intent.extensions).difference(protocol.extensions))
    if unavailable:
        raise ValueError(
            f"Wizard protocol {intent.protocol!r} does not support extensions: {unavailable}"
        )
    selected = set(intent.extensions)
    missing = {
        extension: tuple(
            dependency
            for dependency in protocol.extension_constraints.get(extension, ())
            if dependency not in selected
        )
        for extension in intent.extensions
    }
    missing = {
        extension: dependencies for extension, dependencies in missing.items() if dependencies
    }
    if missing:
        raise ValueError(f"Wizard extension dependencies are not satisfied: {missing}")
    if intent.area_strategy not in facts.area_strategies:
        raise ValueError(f"Wizard area strategy {intent.area_strategy!r} is not available")


def _materialize_graph(root: Path, graph: CatalogDependencyGraph) -> CatalogRoots:
    shipped_root = root / "catalog" / "nodalarc"
    user_root = root / "catalog" / "user"
    shipped_root.mkdir(parents=True)
    user_root.mkdir(parents=True)
    for entry in graph.entries:
        relative = PurePosixPath(entry.preserved_path)
        destination = root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(entry.yaml_bytes)
    return CatalogRoots.from_catalog_root(shipped_root, user_root=user_root)


def _materialize_proposals(
    roots: CatalogRoots,
    proposals: tuple[BuilderProposedCatalogDocument, ...],
) -> None:
    if roots.user_root is None:
        raise ValueError("Wizard proposal materialization requires a user catalog root")
    for proposal in proposals:
        destination = roots.user_root.joinpath(*proposal.ref.relative_path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        canonical = canonicalize_persisted_configuration(proposal.ref, proposal.document)
        destination.write_bytes(canonical.yaml_bytes)


def _selected_graph(intent: WizardPhysicalIntent, snapshot: CatalogReadSnapshot):
    refs: list[CatalogRef] = []
    if intent.constellation_ref is not None:
        refs.append(intent.constellation_ref)
    else:
        refs.extend((CatalogRef(_EARTH_BODY), CatalogRef(WIZARD_CUSTOM_GEOMETRY_DEFAULT_NODE)))
    if intent.satellite_node_ref is not None:
        refs.append(intent.satellite_node_ref)
    if intent.ground_site_set_ref is not None:
        refs.append(intent.ground_site_set_ref)
    else:
        refs.extend(intent.custom_site_refs)
    return CatalogClosureCollector.collect_references(refs, snapshot)


def _custom_geometry_sources(
    intent: WizardPhysicalIntent,
    *,
    constellation_id: str,
    orbit_id: str,
) -> tuple[SpaceSourceRef, tuple[BuilderProposedCatalogDocument, ...]]:
    geometry = intent.custom_constellation
    if geometry is None:
        raise ValueError("custom geometry source requires Wizard geometry")
    orbit_ref = CatalogRef(f"user:orbits/wizard/{orbit_id}.yaml")
    constellation_ref = SpaceSourceRef(f"user:constellations/wizard/{constellation_id}.yaml")
    proposals = (
        BuilderProposedCatalogDocument(
            ref=orbit_ref,
            origin="generated",
            document=cast(
                JsonDocument,
                _custom_orbit_document(
                    geometry,
                    orbit_id=orbit_id,
                    propagator=intent.orbit_propagator,
                ),
            ),
        ),
        BuilderProposedCatalogDocument(
            ref=constellation_ref,
            origin="generated",
            document=cast(
                JsonDocument,
                _custom_constellation_document(
                    geometry,
                    constellation_id=constellation_id,
                    orbit_ref=orbit_ref,
                    node_ref=str(intent.satellite_node_ref or WIZARD_CUSTOM_GEOMETRY_DEFAULT_NODE),
                ),
            ),
        ),
    )
    return constellation_ref, proposals


def _ground_source(
    intent: WizardPhysicalIntent,
    *,
    site_set_id: str,
) -> tuple[SiteSetRef, tuple[BuilderProposedCatalogDocument, ...]]:
    if intent.ground_site_set_ref is not None:
        return intent.ground_site_set_ref, ()
    site_set_ref = SiteSetRef(f"user:site-sets/wizard/{site_set_id}.yaml")
    proposal = BuilderProposedCatalogDocument(
        ref=site_set_ref,
        origin="generated",
        document=cast(
            JsonDocument,
            _custom_site_set_document(intent, site_set_id=site_set_id),
        ),
    )
    return site_set_ref, (proposal,)


def _wizard_sources(
    intent: WizardPhysicalIntent,
    roots: CatalogRoots,
    *,
    name: str,
) -> tuple[SpaceSourceRef, SiteSetRef, tuple[BuilderProposedCatalogDocument, ...]]:
    _validate_orbit_capability(intent, roots)
    constellation_id = f"{name}-constellation"
    orbit_id = f"{name}-orbit"
    site_set_id = f"{name}-sites"
    if intent.custom_constellation is not None:
        constellation_ref, space_proposals = _custom_geometry_sources(
            intent,
            constellation_id=constellation_id,
            orbit_id=orbit_id,
        )
    else:
        constellation_ref, space_proposals = _customize_selected_constellation(
            intent,
            roots,
            constellation_id=constellation_id,
            orbit_id=orbit_id,
        )
    ground_ref, ground_proposals = _ground_source(intent, site_set_id=site_set_id)
    proposals = (*space_proposals, *ground_proposals)
    _materialize_proposals(roots, proposals)
    return constellation_ref, ground_ref, proposals


@contextmanager
def wizard_preview_inputs(
    request: WizardCoverageRequest,
    snapshot: CatalogReadSnapshot,
):
    """Materialize selected scope facts for one non-persistent OME preview."""

    if not isinstance(request, WizardCoverageRequest):
        raise TypeError("request must be a WizardCoverageRequest")
    if not isinstance(snapshot, CatalogReadSnapshot):
        raise TypeError("snapshot must be a CatalogReadSnapshot")

    intent = request.intent
    graph = _selected_graph(intent, snapshot)

    with tempfile.TemporaryDirectory(prefix="nodalarc-wizard-preview-") as temporary:
        roots = _materialize_graph(Path(temporary), graph)
        constellation_ref, ground_site_set_ref, _proposals = _wizard_sources(
            intent,
            roots,
            name=f"wizard-preview-{_default_identity()}",
        )
        yield WizardPreviewInputs(
            constellation_ref=constellation_ref,
            ground_site_set_ref=ground_site_set_ref,
            catalog_roots=roots,
        )


def build_wizard_compile_request(
    request: WizardCompileRequest,
    snapshot: CatalogReadSnapshot,
    *,
    identity_factory: WizardIdentityFactory = _default_identity,
) -> BuilderCompileRequest:
    """Build a complete Builder compile request without persisting anything."""

    if not isinstance(request, WizardCompileRequest):
        raise TypeError("request must be a WizardCompileRequest")
    if not isinstance(snapshot, CatalogReadSnapshot):
        raise TypeError("snapshot must be a CatalogReadSnapshot")

    _validate_routing_choices(request.intent)
    identity = _identifier(identity_factory(), fallback="draft")
    name = _session_name(request.intent, identity)
    intent = request.intent
    graph = _selected_graph(intent, snapshot)

    with tempfile.TemporaryDirectory(prefix="nodalarc-wizard-draft-") as temporary:
        roots = _materialize_graph(Path(temporary), graph)
        constellation_ref, ground_site_set_ref, proposals = _wizard_sources(
            intent,
            roots,
            name=name,
        )
        raw, _warnings = assemble_session_document(
            constellation=str(constellation_ref),
            protocol=intent.protocol,
            extensions=list(intent.extensions),
            orbit_propagator=intent.orbit_propagator,
            area_strategy=intent.area_strategy,
            ground_stations=str(ground_site_set_ref),
            timers=_routing_timers(intent),
            session_name=name,
            catalog_roots=roots,
        )

    if not isinstance(raw, dict):
        raise ValueError("Session preset assembly did not produce a session mapping")
    target_ref = f"user:sessions/wizard/{name}.yaml"
    return BuilderCompileRequest(
        draft=BuilderDraftEnvelope(
            draft_revision=request.draft_revision,
            state={
                "session": cast(JsonDocument, raw),
                "catalog_documents": proposals,
            },
        ),
        target_ref=target_ref,
    )
