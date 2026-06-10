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

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from nodalarc.stack_resolver import SUPPORTED_STACK_PROTOCOLS


class FeatureCategory(StrEnum):
    """Which grammar dimension an unsupported feature came from."""

    SEGMENT_KIND = "segment_kind"
    CENTRAL_BODY = "central_body"
    REFERENCE_BODY = "reference_body"
    FRAME_BODY = "frame_body"
    PROTOCOL_ADAPTER = "protocol_adapter"
    EPHEMERIS_PROVIDER = "ephemeris_provider"
    ROUTING_PROTOCOL = "routing_protocol"
    ADDRESSING_POOL = "addressing_pool"
    PAYLOAD = "payload"
    CLOCK_MODEL = "clock_model"
    PROPAGATOR = "propagator"


# Informational notes shown with unsupported features.
FEATURE_SUPPORT_NOTES: dict[tuple[FeatureCategory, str], str] = {
    (FeatureCategory.SEGMENT_KIND, "space_node"): "supported by the Earth-Luna runtime",
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
    (FeatureCategory.EPHEMERIS_PROVIDER, "spice_kernel_stack"): "future runtime capability",
    (FeatureCategory.EPHEMERIS_PROVIDER, "operator_supplied_spk"): "future runtime capability",
    (FeatureCategory.ROUTING_PROTOCOL, "isis"): "supported FRR routing stack",
    (FeatureCategory.ROUTING_PROTOCOL, "ospf"): "supported FRR routing stack",
    (FeatureCategory.ROUTING_PROTOCOL, "static"): "supported FRR routing stack",
    (FeatureCategory.ROUTING_PROTOCOL, "bgp"): "planned runtime capability (eBGP-first)",
    (FeatureCategory.ADDRESSING_POOL, "loopbacks"): "supported pool class",
    (FeatureCategory.ADDRESSING_POOL, "point_to_point"): (
        "future runtime capability — WAN interfaces are unnumbered (borrow lo0)"
    ),
    (FeatureCategory.ADDRESSING_POOL, "terrestrial_prefixes"): (
        "future runtime capability — sites author terr0 addresses directly"
    ),
    (FeatureCategory.PAYLOAD, "payloads"): "future runtime capability",
    (FeatureCategory.CLOCK_MODEL, "session"): "supported clock model",
    (FeatureCategory.CLOCK_MODEL, "affine"): "future runtime capability",
}


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


class RuntimeSupport(BaseModel):
    """The set of grammar features the current backend actually implements."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supported_segment_kinds: frozenset[str]
    supported_central_bodies: frozenset[str]
    supported_reference_bodies: frozenset[str]
    supported_frame_bodies: frozenset[str]
    supported_protocol_adapters: frozenset[str]
    supported_ephemeris_providers: frozenset[str]
    supported_routing_protocols: frozenset[str]
    supported_addressing_pools: frozenset[str]
    supported_clock_models: frozenset[str]
    supported_propagators: frozenset[str]
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
            supported_routing_protocols=SUPPORTED_STACK_PROTOCOLS,
            supported_addressing_pools=frozenset({"loopbacks"}),
            supported_clock_models=frozenset({"session"}),
            supported_propagators=frozenset({"two_body", "j2_mean_elements", "sgp4_tle"}),
            supports_payloads=False,
            ephemeris_required_bodies=frozenset({"luna", "mars"}),
        )

    @classmethod
    def earth_luna(cls) -> RuntimeSupport:
        """Earth + Luna runtime with explicit cislunar relay support."""
        return cls(
            supported_segment_kinds=frozenset(
                {"constellation", "ground_set", "space_node", "space_node_set"}
            ),
            supported_central_bodies=frozenset({"earth", "luna"}),
            supported_reference_bodies=frozenset({"earth", "luna"}),
            supported_frame_bodies=frozenset({"earth", "luna"}),
            supported_protocol_adapters=frozenset({"static_ip"}),
            supported_ephemeris_providers=frozenset({"skyfield_bsp"}),
            supported_routing_protocols=SUPPORTED_STACK_PROTOCOLS,
            supported_addressing_pools=frozenset({"loopbacks"}),
            supported_clock_models=frozenset({"session"}),
            supported_propagators=frozenset({"two_body", "j2_mean_elements", "sgp4_tle"}),
            supports_payloads=False,
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

    def check_addressing_pool(self, pool_class: str) -> UnsupportedFeature | None:
        if pool_class in self.supported_addressing_pools:
            return None
        return self._unsupported(FeatureCategory.ADDRESSING_POOL, pool_class, "addressing pool")

    def check_clock_model(self, model: str) -> UnsupportedFeature | None:
        if model in self.supported_clock_models:
            return None
        return self._unsupported(FeatureCategory.CLOCK_MODEL, model, "segment clock model")

    def check_payloads(self, has_payloads: bool) -> UnsupportedFeature | None:
        if not has_payloads or self.supports_payloads:
            return None
        return self._unsupported(FeatureCategory.PAYLOAD, "payloads", "node payloads")
