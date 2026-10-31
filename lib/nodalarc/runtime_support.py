# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime-support matrix for structurally-valid session grammar.

Structural schema may accept future grammar; semantic validation checks
cross-object rules; this layer checks that every structurally-valid feature is
actually implemented by the current backend. Unsupported future grammar fails
here with a typed ``UnsupportedFeature`` reason — never silently ignored.

The matrix is data, not code branches: ``resolve_session`` takes a
``RuntimeSupport`` so a future runtime can enable a feature by widening the
supported sets, with no change to call sites.
"""

from collections.abc import Callable, Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from adapters.registry import registered_adapter_support
from nodalarc.model_validation import ADDRESS_FAMILIES, AddressFamily
from nodalarc.models.catalog import BodyFixedFrameWrapper, Node, Orbit, Site, SpaceNodeSet
from nodalarc.models.segment_session import BfdConfig
from nodalarc.workloads.adapter import RoutingProtocolSupport


class FeatureCategory(StrEnum):
    """Which grammar dimension an unsupported feature came from."""

    SEGMENT_KIND = "segment_kind"
    CENTRAL_BODY = "central_body"
    REFERENCE_BODY = "reference_body"
    FRAME_BODY = "frame_body"
    PROTOCOL_ADAPTER = "protocol_adapter"
    EPHEMERIS_PROVIDER = "ephemeris_provider"
    EPHEMERIS_FRAME = "ephemeris_frame"
    TERMINAL_CAPABILITY = "terminal_capability"
    FRAME_REALIZATION = "frame_realization"
    ROUTING_PROTOCOL = "routing_protocol"
    ROUTING_CAPABILITY = "routing_capability"
    ROUTING_TIMER = "routing_timer"
    ROUTING_ADDRESS_FAMILY = "routing_address_family"
    ADDRESSING_POOL = "addressing_pool"
    ADDRESS_ALLOCATION = "address_allocation"
    LINK_TOPOLOGY = "link_topology"
    LINK_CONSTRAINT = "link_constraint"
    GROUND_SCHEDULING = "ground_scheduling"
    PAYLOAD = "payload"
    CLOCK_MODEL = "clock_model"
    PROPAGATOR = "propagator"
    WORKLOAD_ADAPTER = "workload_adapter"
    FORWARDING_CLASS = "forwarding_class"
    ROUTER_DOMAINS = "router_domains"
    ROUTING_LINK_RATE = "routing_link_rate"
    ROUTING_SID_CAPACITY = "routing_sid_capacity"


# Informational notes shown with unsupported features.
FEATURE_SUPPORT_NOTES: dict[tuple[FeatureCategory, str], str] = {
    (FeatureCategory.SEGMENT_KIND, "space_node_set"): "supported by the Earth-Luna runtime",
    (FeatureCategory.SEGMENT_KIND, "lagrange_point"): "future runtime capability",
    (FeatureCategory.CENTRAL_BODY, "luna"): "supported by the Earth-Luna runtime",
    (FeatureCategory.CENTRAL_BODY, "mars"): "future runtime capability",
    (FeatureCategory.CENTRAL_BODY, "sun"): "future runtime capability",
    (FeatureCategory.REFERENCE_BODY, "luna"): "supported by the Earth-Luna runtime",
    (FeatureCategory.REFERENCE_BODY, "mars"): "future runtime capability",
    (FeatureCategory.FRAME_BODY, "luna"): "supported by the Earth-Luna runtime",
    (FeatureCategory.FRAME_BODY, "mars"): "future runtime capability",
    (FeatureCategory.FRAME_BODY, "sun"): "future runtime capability",
    (FeatureCategory.PROPAGATOR, "crtbp"): (
        "future runtime capability - three-body (CR3BP) propagation for NRHO/halo "
        "orbits; Kepler elements cannot represent these trajectories truthfully"
    ),
    (FeatureCategory.PROTOCOL_ADAPTER, "static_ip"): "supported by the Earth-Luna runtime",
    (FeatureCategory.PROTOCOL_ADAPTER, "bgp"): "future runtime capability",
    (FeatureCategory.PROTOCOL_ADAPTER, "dtn_bundle"): "future runtime capability",
    (FeatureCategory.PROTOCOL_ADAPTER, "custom"): "future runtime capability",
    (FeatureCategory.EPHEMERIS_PROVIDER, "skyfield_bsp"): "supported by the Earth-Luna runtime",
    (FeatureCategory.EPHEMERIS_FRAME, "gcrs"): "supported by the Earth-Luna runtime",
    (FeatureCategory.EPHEMERIS_PROVIDER, "spice_kernel_stack"): "future runtime capability",
    (FeatureCategory.EPHEMERIS_PROVIDER, "operator_supplied_spk"): "future runtime capability",
    (FeatureCategory.ROUTING_PROTOCOL, "bgp"): "planned runtime capability (eBGP-first)",
    (FeatureCategory.ADDRESSING_POOL, "loopbacks"): "supported pool class",
    (FeatureCategory.ADDRESSING_POOL, "point_to_point"): (
        "future runtime capability — WAN interfaces are unnumbered (borrow lo0)"
    ),
    (FeatureCategory.ADDRESSING_POOL, "terrestrial_prefixes"): (
        "future runtime capability — segment subnets are resolver-allocated"
    ),
    (FeatureCategory.PAYLOAD, "space_payload_execution"): ("supported by the Earth-Luna runtime"),
    (FeatureCategory.CLOCK_MODEL, "session"): "supported clock model",
    (FeatureCategory.CLOCK_MODEL, "affine"): "future runtime capability",
    (FeatureCategory.FORWARDING_CLASS, "bridge"): "future runtime capability",
    (FeatureCategory.FORWARDING_CLASS, "control_only"): "future runtime capability",
    (FeatureCategory.GROUND_SCHEDULING, "handover_concurrency:all_at_once"): (
        "future runtime capability - the current allocator serializes ground handovers"
    ),
}


def _registered_routing_protocols() -> frozenset[str]:
    """Routing protocols some registered adapter renders."""
    return frozenset(
        protocol
        for support in registered_adapter_support().values()
        for protocol in support.routing
    )


def _registered_routing_capabilities() -> frozenset[str]:
    """``protocol:capability`` pairs some registered adapter renders."""
    return frozenset(
        f"{protocol}:{capability}"
        for support in registered_adapter_support().values()
        for protocol, protocol_support in support.routing.items()
        for capability in protocol_support.capabilities
    )


def adapter_renders_routing(adapter: str | None) -> bool:
    """Whether an adapter renders any routing protocol at all.

    A node is a router exactly when its profile's adapter declares routing
    support.
    """
    if adapter is None:
        return False
    support = registered_adapter_support().get(adapter)
    return support is not None and bool(support.routing)


class UnsupportedFeature(BaseModel):
    """A typed reason that a structurally-valid feature is not runtime-supported."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: FeatureCategory
    value: str
    message: str
    support_note: str | None = None


class UnsupportedFeatureError(ValueError):
    """Raised by the resolver when a session uses runtime-unsupported grammar."""

    def __init__(self, features: list[UnsupportedFeature]) -> None:
        self.features = tuple(features)
        joined = "; ".join(f"{f.category}={f.value!r} ({f.message})" for f in features)
        super().__init__(f"session uses runtime-unsupported features: {joined}")


# BFD timer fields, named identically in the grammar and in BfdSupport.
_BFD_TIMER_FIELDS = ("detect_multiplier", "rx_interval_ms", "tx_interval_ms")
_NAMED_MEMBER_LIMIT = 5


def _named_members(node_ids: tuple[str, ...]) -> str:
    ordered = sorted(node_ids)
    if len(ordered) <= _NAMED_MEMBER_LIMIT:
        return f"node(s) {', '.join(ordered)}"
    shown = ", ".join(ordered[:_NAMED_MEMBER_LIMIT])
    return f"{len(ordered)} nodes ({shown} and {len(ordered) - _NAMED_MEMBER_LIMIT} more)"


def bfd_spans_mixed_family_peers(adapter: str, protocol: str) -> bool:
    """Whether ``adapter``'s BFD for ``protocol`` runs on an interface whose
    possible peers mix IPv6 and IPv4-only nodes."""
    support = registered_adapter_support().get(adapter)
    if support is None:
        raise ValueError(f"workload adapter {adapter!r} is not registered")
    protocol_support = support.routing.get(protocol)
    if protocol_support is None or protocol_support.bfd is None:
        raise ValueError(f"workload adapter {adapter!r} renders no BFD for {protocol!r}")
    return protocol_support.bfd.mixed_family_peers


def check_routing_members(
    *,
    domain_id: str,
    protocol: str,
    capabilities: tuple[str, ...],
    bfd: BfdConfig,
    adapter: str,
    members: Mapping[str, frozenset[AddressFamily]],
) -> list[UnsupportedFeature]:
    """Check routing-domain members against their own adapter's declaration.

    ``members`` maps each member that runs ``adapter`` to the address
    families it carries, so the adapter's declaration decides for each of
    them. Each refusal names the domain, the adapter, the unsupported
    requirement and the members it applies to: every member for a protocol,
    capability or timer, and the members carrying the family for an address
    family.
    """
    support = registered_adapter_support().get(adapter)
    if support is None:
        raise ValueError(f"workload adapter {adapter!r} is not registered")
    every_member = tuple(members)
    gaps: list[tuple[FeatureCategory, str, str, tuple[str, ...]]] = []
    protocol_support = support.routing.get(protocol)
    if protocol_support is None:
        gaps.append(
            (FeatureCategory.ROUTING_PROTOCOL, protocol, f"protocol {protocol!r}", every_member)
        )
    else:
        for capability in capabilities:
            if capability not in protocol_support.capabilities:
                gaps.append(
                    (
                        FeatureCategory.ROUTING_CAPABILITY,
                        f"{protocol}:{capability}",
                        f"capability {capability!r} on protocol {protocol!r}",
                        every_member,
                    )
                )
        if bfd.enabled and protocol_support.bfd is None:
            gaps.append(
                (
                    FeatureCategory.ROUTING_TIMER,
                    f"{protocol}:bfd",
                    f"BFD on protocol {protocol!r}",
                    every_member,
                )
            )
        elif bfd.enabled:
            for name in _BFD_TIMER_FIELDS:
                value = getattr(bfd, name)
                low, high = getattr(protocol_support.bfd, name)
                if not low <= value <= high:
                    gaps.append(
                        (
                            FeatureCategory.ROUTING_TIMER,
                            f"bfd.{name}={value}",
                            f"BFD {name} {value}, outside the rendered range {low}..{high}",
                            every_member,
                        )
                    )
        for family in ADDRESS_FAMILIES:
            carriers = tuple(node_id for node_id, families in members.items() if family in families)
            if carriers and family not in protocol_support.address_families:
                gaps.append(
                    (
                        FeatureCategory.ROUTING_ADDRESS_FAMILY,
                        f"{protocol}:{family}",
                        f"{family} routing on protocol {protocol!r}",
                        carriers,
                    )
                )
    return [
        UnsupportedFeature(
            category=category,
            value=value,
            message=(
                f"routing domain {domain_id!r} requires {requirement}; workload adapter "
                f"{adapter!r} does not render it for {_named_members(node_ids)}"
            ),
        )
        for category, value, requirement, node_ids in gaps
    ]


def check_router_domains(
    *,
    adapter: str,
    routers: Mapping[str, tuple[tuple[str, str], ...]],
) -> list[UnsupportedFeature]:
    """Check how many domains of each protocol a router's adapter renders.

    ``routers`` maps each router that runs ``adapter`` to the
    ``(domain_id, protocol)`` pairs it participates in. Routers that exceed
    the adapter's declared domains per router with the same domains share
    one refusal.
    """
    support = registered_adapter_support().get(adapter)
    if support is None:
        raise ValueError(f"workload adapter {adapter!r} is not registered")
    excess: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for node_id, memberships in routers.items():
        by_protocol: dict[str, list[str]] = {}
        for domain_id, protocol in memberships:
            by_protocol.setdefault(protocol, []).append(domain_id)
        for protocol, domain_ids in by_protocol.items():
            limit = support.routing[protocol].domains_per_router
            if limit is not None and len(domain_ids) > limit:
                excess.setdefault((protocol, tuple(domain_ids)), []).append(node_id)
    return [
        UnsupportedFeature(
            category=FeatureCategory.ROUTER_DOMAINS,
            value=f"{protocol}:{','.join(domain_ids)}",
            message=(
                f"{_named_members(tuple(node_ids))} participate in {protocol} domains "
                f"{list(domain_ids)}; workload adapter {adapter!r} renders "
                f"{support.routing[protocol].domains_per_router} {protocol} domain(s) per router"
            ),
        )
        for (protocol, domain_ids), node_ids in sorted(excess.items())
    ]


def _protocol_support(adapter: str, protocol: str) -> RoutingProtocolSupport:
    support = registered_adapter_support().get(adapter)
    if support is None:
        raise ValueError(f"workload adapter {adapter!r} is not registered")
    protocol_support = support.routing.get(protocol)
    if protocol_support is None:
        raise ValueError(f"workload adapter {adapter!r} renders no {protocol!r} routing")
    return protocol_support


def _named_links(links: list[tuple[str, str, float]]) -> str:
    ordered = sorted(links)
    shown = ", ".join(
        f"{node_id} {interface} at {rate:g} Mb/s"
        for node_id, interface, rate in ordered[:_NAMED_MEMBER_LIMIT]
    )
    if len(ordered) <= _NAMED_MEMBER_LIMIT:
        return shown
    return f"{shown} and {len(ordered) - _NAMED_MEMBER_LIMIT} more"


def check_link_rates(
    *,
    domain_id: str,
    protocol: str,
    adapter: str,
    links: Mapping[tuple[str, str], float],
) -> list[UnsupportedFeature]:
    """Check the fixed links a domain's members run ``protocol`` over against
    their adapter's link rate floor.

    ``links`` maps each ``(node_id, interface)`` of a member that runs
    ``adapter`` to the transmit rate of that interface's terminal, in Mb/s.
    A link at or below the floor has no metric in the adapter, and the
    refusal names the links.
    """
    floor = _protocol_support(adapter, protocol).link_rate_floor_mbps
    if floor is None:
        return []
    slow = [
        (node_id, interface, rate) for (node_id, interface), rate in links.items() if rate <= floor
    ]
    if not slow:
        return []
    return [
        UnsupportedFeature(
            category=FeatureCategory.ROUTING_LINK_RATE,
            value=f"{protocol}:{floor:g}",
            message=(
                f"routing domain {domain_id!r} runs {protocol} over fixed links at or below "
                f"{floor:g} Mb/s ({_named_links(slow)}); workload adapter {adapter!r} has no "
                f"{protocol} metric for them"
            ),
        )
    ]


def check_sid_indices(
    *,
    domain_id: str,
    protocol: str,
    adapter: str,
    indices: Mapping[str, int],
) -> list[UnsupportedFeature]:
    """Check a segment-routing domain's prefix-SID indices against the
    capacity of the members' adapter.

    ``indices`` maps each member that runs ``adapter`` to its index.
    """
    capacity = _protocol_support(adapter, protocol).sid_index_capacity
    if capacity is None:
        raise ValueError(f"workload adapter {adapter!r} renders no {protocol!r} segment routing")
    beyond = tuple(node_id for node_id, index in indices.items() if index > capacity)
    if not beyond:
        return []
    return [
        UnsupportedFeature(
            category=FeatureCategory.ROUTING_SID_CAPACITY,
            value=f"{protocol}:{capacity}",
            message=(
                f"routing domain {domain_id!r} gives {_named_members(beyond)} prefix-SID "
                f"indices up to {max(indices[node_id] for node_id in beyond)}; workload adapter "
                f"{adapter!r} renders indices up to {capacity}"
            ),
        )
    ]


class RuntimeSupport(BaseModel):
    """The set of grammar features the current backend actually implements."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supported_segment_kinds: frozenset[str]
    supported_central_bodies: frozenset[str]
    supported_reference_bodies: frozenset[str]
    supported_frame_bodies: frozenset[str]
    supported_protocol_adapters: frozenset[str]
    supported_ephemeris_providers: frozenset[str]
    supported_ephemeris_frames: frozenset[str]
    supported_routing_protocols: frozenset[str]
    supported_routing_capabilities: frozenset[str]
    supported_addressing_pools: frozenset[str]
    supported_address_allocation_modes: frozenset[str]
    supported_link_topologies: frozenset[str]
    supported_link_constraints: frozenset[str]
    supported_ground_handover_concurrency: frozenset[str]
    max_ground_mbb_reserve: int = Field(ge=0)
    supported_ground_bbm_acquire_timeout_ticks: frozenset[int]
    supported_clock_models: frozenset[str]
    supported_propagators: frozenset[str]
    supported_workload_adapters: frozenset[str]
    supported_forwarding_classes: frozenset[str]
    supports_payloads: bool
    # Surface bodies whose presence requires an ephemeris manifest.
    ephemeris_required_bodies: frozenset[str]

    @classmethod
    def earth_multi_regime(cls) -> RuntimeSupport:
        """Earth-only LEO/MEO/GEO support.

        Space relays, static protocol boundaries, ephemeris providers, and
        non-Earth bodies parse structurally and are rejected here with a typed
        reason until the selected runtime supports them.
        """
        return cls(
            supported_segment_kinds=frozenset({"constellation", "ground_set"}),
            supported_central_bodies=frozenset({"earth"}),
            supported_reference_bodies=frozenset({"earth"}),
            supported_frame_bodies=frozenset({"earth"}),
            supported_protocol_adapters=frozenset(),
            supported_ephemeris_providers=frozenset(),
            supported_ephemeris_frames=frozenset(),
            supported_routing_protocols=_registered_routing_protocols(),
            supported_routing_capabilities=_registered_routing_capabilities(),
            supported_addressing_pools=frozenset({"loopbacks"}),
            supported_address_allocation_modes=frozenset({"by_node_order"}),
            supported_link_topologies=frozenset(
                {"visible_candidates", "nearest_n", "explicit_pairs"}
            ),
            supported_link_constraints=frozenset({"max_links_per_node"}),
            supported_ground_handover_concurrency=frozenset({"one_at_a_time"}),
            max_ground_mbb_reserve=1,
            supported_ground_bbm_acquire_timeout_ticks=frozenset({1}),
            supported_clock_models=frozenset({"session"}),
            supported_propagators=frozenset({"two_body", "j2_mean_elements", "sgp4_tle"}),
            supported_workload_adapters=frozenset(registered_adapter_support()),
            supported_forwarding_classes=frozenset({"routed", "host"}),
            supports_payloads=True,
            ephemeris_required_bodies=frozenset({"luna", "mars"}),
        )

    @classmethod
    def earth_luna(cls) -> RuntimeSupport:
        """Earth + Luna runtime with explicit cislunar relay support."""
        return cls(
            supported_segment_kinds=frozenset({"constellation", "ground_set", "space_node_set"}),
            supported_central_bodies=frozenset({"earth", "luna"}),
            supported_reference_bodies=frozenset({"earth", "luna"}),
            supported_frame_bodies=frozenset({"earth", "luna"}),
            supported_protocol_adapters=frozenset({"static_ip"}),
            supported_ephemeris_providers=frozenset({"skyfield_bsp"}),
            supported_ephemeris_frames=frozenset({"gcrs"}),
            supported_routing_protocols=_registered_routing_protocols(),
            supported_routing_capabilities=_registered_routing_capabilities(),
            supported_addressing_pools=frozenset({"loopbacks"}),
            supported_address_allocation_modes=frozenset({"by_node_order"}),
            supported_link_topologies=frozenset(
                {"visible_candidates", "nearest_n", "explicit_pairs"}
            ),
            supported_link_constraints=frozenset({"max_links_per_node"}),
            supported_ground_handover_concurrency=frozenset({"one_at_a_time"}),
            max_ground_mbb_reserve=1,
            supported_ground_bbm_acquire_timeout_ticks=frozenset({1}),
            supported_clock_models=frozenset({"session"}),
            supported_propagators=frozenset({"two_body", "j2_mean_elements", "sgp4_tle"}),
            supported_workload_adapters=frozenset(registered_adapter_support()),
            supported_forwarding_classes=frozenset({"routed", "host"}),
            supports_payloads=True,
            ephemeris_required_bodies=frozenset({"luna"}),
        )

    def _unsupported(self, category: FeatureCategory, value: str, what: str) -> UnsupportedFeature:
        support_note = FEATURE_SUPPORT_NOTES.get((category, value))
        suffix = f" Support note: {support_note}." if support_note else ""
        return UnsupportedFeature(
            category=category,
            value=value,
            message=f"{what} {value!r} is not supported by the current runtime.{suffix}",
            support_note=support_note,
        )

    def check_segment_kind(self, kind: str) -> UnsupportedFeature | None:
        if kind in self.supported_segment_kinds:
            return None
        return self._unsupported(FeatureCategory.SEGMENT_KIND, kind, "segment kind")

    def check_workload_adapter(self, adapter: str) -> UnsupportedFeature | None:
        if adapter in self.supported_workload_adapters:
            return None
        return self._unsupported(FeatureCategory.WORKLOAD_ADAPTER, adapter, "workload adapter")

    def check_forwarding_class(self, forwarding: str) -> UnsupportedFeature | None:
        if forwarding in self.supported_forwarding_classes:
            return None
        return self._unsupported(FeatureCategory.FORWARDING_CLASS, forwarding, "forwarding class")

    def check_propagator(self, propagator: str) -> UnsupportedFeature | None:
        if propagator in self.supported_propagators:
            return None
        return self._unsupported(FeatureCategory.PROPAGATOR, propagator, "orbit propagator")

    def check_central_body(self, body: str) -> UnsupportedFeature | None:
        if body in self.supported_central_bodies:
            return None
        return self._unsupported(FeatureCategory.CENTRAL_BODY, body, "central_body")

    def check_reference_body(self, body: str) -> UnsupportedFeature | None:
        if body in self.supported_reference_bodies:
            return None
        return self._unsupported(FeatureCategory.REFERENCE_BODY, body, "reference_body")

    def check_frame_body(self, body: str) -> UnsupportedFeature | None:
        if body in self.supported_frame_bodies:
            return None
        return self._unsupported(FeatureCategory.FRAME_BODY, body, "frame body")

    def check_protocol_adapter(self, adapter: str) -> UnsupportedFeature | None:
        if adapter in self.supported_protocol_adapters:
            return None
        return self._unsupported(
            FeatureCategory.PROTOCOL_ADAPTER, adapter, "protocol_boundary adapter"
        )

    def check_ephemeris_frame(self, frame: str) -> UnsupportedFeature | None:
        """The frame the installed providers actually compute.

        skyfield_bsp always produces Earth-relative GCRS vectors; an
        authored kernel frame outside this set would be copied onto GCRS
        values as a false label.
        """
        if frame in self.supported_ephemeris_frames:
            return None
        return self._unsupported(FeatureCategory.EPHEMERIS_FRAME, frame, "ephemeris kernel frame")

    def check_ephemeris_provider(self, provider: str) -> UnsupportedFeature | None:
        if provider in self.supported_ephemeris_providers:
            return None
        return self._unsupported(FeatureCategory.EPHEMERIS_PROVIDER, provider, "ephemeris provider")

    def check_routing_protocol(self, protocol: str) -> UnsupportedFeature | None:
        if protocol in self.supported_routing_protocols:
            return None
        return self._unsupported(
            FeatureCategory.ROUTING_PROTOCOL, protocol, "routing domain protocol"
        )

    def check_routing_capability(self, protocol: str, capability: str) -> UnsupportedFeature | None:
        value = f"{protocol}:{capability}"
        if value in self.supported_routing_capabilities:
            return None
        return self._unsupported(
            FeatureCategory.ROUTING_CAPABILITY,
            value,
            "routing protocol capability",
        )

    def check_addressing_pool(self, pool_class: str) -> UnsupportedFeature | None:
        if pool_class in self.supported_addressing_pools:
            return None
        return self._unsupported(FeatureCategory.ADDRESSING_POOL, pool_class, "addressing pool")

    def check_address_allocation(self, allocation: str) -> UnsupportedFeature | None:
        if allocation in self.supported_address_allocation_modes:
            return None
        return self._unsupported(
            FeatureCategory.ADDRESS_ALLOCATION,
            allocation,
            "address allocation mode",
        )

    def check_link_topology(self, topology: str) -> UnsupportedFeature | None:
        if topology in self.supported_link_topologies:
            return None
        return self._unsupported(
            FeatureCategory.LINK_TOPOLOGY,
            topology,
            "link topology mode",
        )

    def check_link_constraint(self, constraint: str) -> UnsupportedFeature | None:
        if constraint in self.supported_link_constraints:
            return None
        return self._unsupported(
            FeatureCategory.LINK_CONSTRAINT,
            constraint,
            "link rule constraint",
        )

    def check_ground_handover_concurrency(self, mode: str) -> UnsupportedFeature | None:
        if mode in self.supported_ground_handover_concurrency:
            return None
        return self._unsupported(
            FeatureCategory.GROUND_SCHEDULING,
            f"handover_concurrency:{mode}",
            "ground handover concurrency",
        )

    def check_ground_mbb_reserve(self, reserve: int) -> UnsupportedFeature | None:
        if reserve <= self.max_ground_mbb_reserve:
            return None
        return self._unsupported(
            FeatureCategory.GROUND_SCHEDULING,
            f"mbb_reserve:{reserve}",
            "ground MBB reserve",
        )

    def check_ground_bbm_acquire_timeout_ticks(
        self, timeout_ticks: int
    ) -> UnsupportedFeature | None:
        if timeout_ticks in self.supported_ground_bbm_acquire_timeout_ticks:
            return None
        return self._unsupported(
            FeatureCategory.GROUND_SCHEDULING,
            f"bbm_acquire_timeout_ticks:{timeout_ticks}",
            "ground BBM acquire timeout",
        )

    def check_clock_model(self, model: str) -> UnsupportedFeature | None:
        if model in self.supported_clock_models:
            return None
        return self._unsupported(FeatureCategory.CLOCK_MODEL, model, "clock model")

    def check_catalog_object(
        self,
        model: BaseModel,
        *,
        body_id: Callable[[str], str],
    ) -> tuple[tuple[UnsupportedFeature, str], ...]:
        """What the runtime cannot run in one catalog object, each at its JSON pointer.

        These are the gates the object decides alone, with the bodies its
        references reach (``body_id`` reads a body reference's id): a node's
        forwarding class, an orbit's propagator and central body, a TLE
        placement's central body, and a site's frame body. Gates that depend on
        placement or profile precedence belong to the session's resolution.
        """
        findings: list[tuple[UnsupportedFeature, str]] = []

        def add(feature: UnsupportedFeature | None, pointer: str) -> None:
            if feature is not None:
                findings.append((feature, pointer))

        if isinstance(model, Node):
            add(self.check_forwarding_class(model.forwarding), "/node/forwarding")
        elif isinstance(model, Orbit):
            add(self.check_propagator(model.propagator), "/orbit/propagator")
            add(
                self.check_central_body(body_id(str(model.central_body))),
                "/orbit/central_body",
            )
        elif isinstance(model, SpaceNodeSet):
            for index, entry in enumerate(model.nodes):
                if entry.sgp4_tle is not None:
                    pointer = f"/space_node_set/nodes/{index}/sgp4_tle"
                    add(self.check_propagator("sgp4_tle"), pointer)
                    add(
                        self.check_central_body(body_id(str(entry.sgp4_tle.central_body))),
                        f"{pointer}/central_body",
                    )
        elif isinstance(model, Site) and isinstance(model.frame, BodyFixedFrameWrapper):
            add(
                self.check_reference_body(body_id(str(model.frame.body_fixed.body))),
                "/site/frame/body_fixed/body",
            )
        return tuple(findings)

    def check_payloads(self, has_payloads: bool) -> UnsupportedFeature | None:
        """Gate space-placed onboard execution: bus ports and payload mounts.

        Ground-installed payload mounts execute through the site path;
        space-placed nodes need the bus wiring chain and motion sharing, so
        a space node definition declaring Ethernet ports or payload mounts
        refuses here until the runtime carries them.
        """
        if not has_payloads or self.supports_payloads:
            return None
        return self._unsupported(
            FeatureCategory.PAYLOAD,
            "space_payload_execution",
            "onboard bus and payload execution on space placements",
        )
