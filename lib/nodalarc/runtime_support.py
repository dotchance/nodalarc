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


class FeatureCategory(StrEnum):
    """Which grammar dimension an unsupported feature came from."""

    SEGMENT_KIND = "segment_kind"
    CENTRAL_BODY = "central_body"
    REFERENCE_BODY = "reference_body"
    FRAME_BODY = "frame_body"
    PROTOCOL_ADAPTER = "protocol_adapter"
    EPHEMERIS_PROVIDER = "ephemeris_provider"


# Informational notes shown with unsupported features.
FEATURE_SUPPORT_NOTES: dict[tuple[FeatureCategory, str], str] = {
    (FeatureCategory.SEGMENT_KIND, "space_node"): "supported by the Earth-Luna runtime",
    (FeatureCategory.SEGMENT_KIND, "space_node_set"): "future runtime capability",
    (FeatureCategory.SEGMENT_KIND, "lagrange_point"): "future runtime capability",
    (FeatureCategory.CENTRAL_BODY, "luna"): "supported by the Earth-Luna runtime",
    (FeatureCategory.CENTRAL_BODY, "mars"): "future runtime capability",
    (FeatureCategory.CENTRAL_BODY, "sun"): "future runtime capability",
    (FeatureCategory.REFERENCE_BODY, "luna"): "supported by the Earth-Luna runtime",
    (FeatureCategory.REFERENCE_BODY, "mars"): "future runtime capability",
    (FeatureCategory.FRAME_BODY, "luna"): "supported by the Earth-Luna runtime",
    (FeatureCategory.FRAME_BODY, "mars"): "future runtime capability",
    (FeatureCategory.FRAME_BODY, "sun"): "future runtime capability",
    (FeatureCategory.PROTOCOL_ADAPTER, "static_ip"): "supported by the Earth-Luna runtime",
    (FeatureCategory.PROTOCOL_ADAPTER, "bgp"): "future runtime capability",
    (FeatureCategory.PROTOCOL_ADAPTER, "dtn_bundle"): "future runtime capability",
    (FeatureCategory.PROTOCOL_ADAPTER, "custom"): "future runtime capability",
    (FeatureCategory.EPHEMERIS_PROVIDER, "skyfield_bsp"): "supported by the Earth-Luna runtime",
    (FeatureCategory.EPHEMERIS_PROVIDER, "spice_kernel_stack"): "future runtime capability",
    (FeatureCategory.EPHEMERIS_PROVIDER, "operator_supplied_spk"): "future runtime capability",
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
            ephemeris_required_bodies=frozenset({"luna", "mars"}),
        )

    @classmethod
    def earth_luna(cls) -> RuntimeSupport:
        """Earth + Luna runtime with explicit cislunar relay support."""
        return cls(
            supported_segment_kinds=frozenset({"constellation", "ground_set", "space_node"}),
            supported_central_bodies=frozenset({"earth", "luna"}),
            supported_reference_bodies=frozenset({"earth", "luna"}),
            supported_frame_bodies=frozenset({"earth", "luna"}),
            supported_protocol_adapters=frozenset({"static_ip"}),
            supported_ephemeris_providers=frozenset({"skyfield_bsp"}),
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
