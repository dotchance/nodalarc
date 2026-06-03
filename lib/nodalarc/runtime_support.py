# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Runtime-support matrix — the third validation layer (CDR-6).

Structural schema may accept future grammar; semantic validation checks
cross-object rules; this layer checks that every structurally-valid feature is
actually implemented by the current backend. Unsupported future grammar fails
here with a typed ``UnsupportedFeature`` reason — never silently ignored.

The matrix is data, not code branches: ``resolve_session`` takes a
``RuntimeSupport`` so a later milestone flips a feature on by widening the
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


# Informational: which milestone a structurally-valid future feature is planned for.
PLANNED_MILESTONE: dict[tuple[FeatureCategory, str], str] = {
    (FeatureCategory.SEGMENT_KIND, "space_node"): "M3 (Luna)",
    (FeatureCategory.SEGMENT_KIND, "space_node_set"): "post-MVP",
    (FeatureCategory.SEGMENT_KIND, "lagrange_point"): "post-MVP",
    (FeatureCategory.CENTRAL_BODY, "luna"): "M3 (Luna)",
    (FeatureCategory.CENTRAL_BODY, "mars"): "post-MVP",
    (FeatureCategory.CENTRAL_BODY, "sun"): "post-MVP",
    (FeatureCategory.REFERENCE_BODY, "luna"): "M3 (Luna)",
    (FeatureCategory.REFERENCE_BODY, "mars"): "post-MVP",
    (FeatureCategory.FRAME_BODY, "luna"): "M3 (Luna)",
    (FeatureCategory.FRAME_BODY, "mars"): "post-MVP",
    (FeatureCategory.FRAME_BODY, "sun"): "post-MVP",
    (FeatureCategory.PROTOCOL_ADAPTER, "static_ip"): "M3 (Luna)",
    (FeatureCategory.PROTOCOL_ADAPTER, "bgp"): "post-MVP",
    (FeatureCategory.PROTOCOL_ADAPTER, "dtn_bundle"): "post-MVP",
    (FeatureCategory.PROTOCOL_ADAPTER, "custom"): "post-MVP",
    (FeatureCategory.EPHEMERIS_PROVIDER, "skyfield_bsp"): "M3 (Luna)",
    (FeatureCategory.EPHEMERIS_PROVIDER, "spice_kernel_stack"): "post-MVP",
    (FeatureCategory.EPHEMERIS_PROVIDER, "operator_supplied_spk"): "post-MVP",
}


class UnsupportedFeature(BaseModel):
    """A typed reason that a structurally-valid feature is not runtime-supported."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: FeatureCategory
    value: str
    message: str
    planned_milestone: str | None = None


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
    def mvp_m1(cls) -> RuntimeSupport:
        """M1: Earth-only LEO/MEO/GEO. constellation + ground_set, earth only.

        space_node, static_ip protocol boundaries, ephemeris, and the luna body
        are the M3 (Luna) milestone; mars/sun and the future segment kinds are
        post-MVP. They parse structurally and are rejected here with a typed
        reason until their milestone implements them.
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
    def mvp_m3(cls) -> RuntimeSupport:
        """M3: Earth + Luna demonstrator with explicit cislunar relay support."""
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
        milestone = PLANNED_MILESTONE.get((category, value))
        suffix = f" Planned for {milestone}." if milestone else ""
        return UnsupportedFeature(
            category=category,
            value=value,
            message=f"{what} {value!r} is not supported by the current runtime.{suffix}",
            planned_milestone=milestone,
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
