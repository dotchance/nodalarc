# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Stack resolver — derives routing stack configuration from (protocol, extensions).

Replaces the need for per-stack directories when deploying via the wizard.
Session routing uses protocol/extensions only; routing.stack is rejected at parse time.

The resolver is the single source of truth for per-permutation configuration:
sysctls, template variables, derived SID ranges, and constraint validation.
The deployer and templates are pure pass-through — no protocol-aware logic.
"""

from dataclasses import dataclass, field
from typing import Any, NamedTuple


class TemplateFile(NamedTuple):
    """A Jinja2 template to render into a pod."""

    src: str
    dst: str


# Must exceed the maximum number of ground stations in any constellation.
# GS SID indices are allocated from (srgb_size - GS_SID_HEADROOM) upward.
GS_SID_HEADROOM = 100


@dataclass(frozen=True)
class ResolvedStack:
    """Fully resolved routing stack — everything the deployer needs.

    The image field is a logical runtime image name, not a registry/tagged
    deployment reference. Deployment image resolution belongs to the Operator
    environment injected by Helm.

    The deployer merges base platform sysctls (forwarding, rp_filter) with
    the stack-provided sysctls and writes them to the wiring manifest.
    The deployer never interprets stack fields to derive sysctls.
    """

    daemons: list[str]
    template_files: list[TemplateFile]
    template_variables: dict[str, Any]
    image: str
    mi_adapter: str | None
    segment_routing: bool
    sysctls: dict[str, str] = field(default_factory=dict)
    transport: str | None = None
    host_modules: list[str] = field(default_factory=list)
    env: list[dict[str, str]] = field(default_factory=list)
    security_context_capabilities: list[str] = field(default_factory=list)
    reconfigure_command: str | None = None
    max_compression: int = 10

    @property
    def ttl_propagation(self) -> str | None:
        """Derive ttl_propagation from sysctls for backward compatibility."""
        val = self.sysctls.get("net.mpls.ip_ttl_propagate")
        if val == "0":
            return "pipe"
        elif val == "1":
            return "uniform"
        return None


# Daemon-to-template mapping for FRR stacks
_DAEMON_TEMPLATES: dict[str, TemplateFile] = {
    "zebra": TemplateFile("zebra.conf.j2", "/etc/frr/zebra.conf"),
    "ospfd": TemplateFile("ospfd.conf.j2", "/etc/frr/ospfd.conf"),
    "isisd": TemplateFile("isisd.conf.j2", "/etc/frr/isisd.conf"),
    "ldpd": TemplateFile("ldpd.conf.j2", "/etc/frr/ldpd.conf"),
    "pathd": TemplateFile("pathd.conf.j2", "/etc/frr/pathd.conf"),
    "staticd": TemplateFile("staticd.conf.j2", "/etc/frr/staticd.conf"),
}


def _derive_sr_variables(srgb_start: int, srgb_end: int) -> dict[str, Any]:
    """Derive all SR-related template variables from the SRGB range.

    Returns template variables including gs_sid_offset, validated against
    the SRGB size. Raises ValueError if constraints are violated.
    """
    srgb_size = srgb_end - srgb_start + 1
    gs_sid_offset = srgb_size - GS_SID_HEADROOM

    if gs_sid_offset <= 0:
        raise ValueError(f"SRGB too small ({srgb_size}) for GS_SID_HEADROOM ({GS_SID_HEADROOM})")

    return {
        "sr_enabled": True,
        "srgb_start": srgb_start,
        "srgb_end": srgb_end,
        "gs_sid_offset": gs_sid_offset,
    }


def _sr_sysctls(mpls_labels: str = "100000") -> dict[str, str]:
    """Kernel sysctls required for SR-MPLS forwarding.

    ip_ttl_propagate=0 (pipe mode): MPLS TTL starts at 255 regardless of
    IP TTL. Required for tracepath/traceroute to work through MPLS tunnels.
    Uniform mode copies IP TTL into MPLS TTL, causing tracepath probes
    (TTL=1) to expire at the first MPLS transit hop.
    """
    return {
        "net.mpls.platform_labels": mpls_labels,
        "net.mpls.ip_ttl_propagate": "0",
    }


def _mpls_sysctls(mpls_labels: str = "100000") -> dict[str, str]:
    """Kernel sysctls for MPLS forwarding without SR (e.g., LDP)."""
    return {
        "net.mpls.platform_labels": mpls_labels,
    }


def validate_constellation_constraints(
    resolved: ResolvedStack,
    num_planes: int,
    max_slots_per_plane: int,
    num_ground_stations: int,
) -> None:
    """Validate stack × constellation constraints.

    Called by the Operator after constellation expansion, before template
    rendering. Raises ValueError if the constellation is too large for
    the stack's SRGB or SID scheme.
    """
    tv = resolved.template_variables
    if not resolved.segment_routing:
        return

    gs_sid_offset = tv.get("gs_sid_offset", 0)
    srgb_start = tv.get("srgb_start", 0)
    srgb_end = tv.get("srgb_end", 0)
    srgb_size = srgb_end - srgb_start + 1

    # Satellite SID scheme: plane * 100 + slot + 1
    max_sat_sid = num_planes * 100 + max_slots_per_plane
    if max_sat_sid >= gs_sid_offset:
        raise ValueError(
            f"Satellite SID range ({max_sat_sid}) overlaps GS SID offset "
            f"({gs_sid_offset}). Increase SRGB or reduce constellation size."
        )

    max_gs_sid = gs_sid_offset + num_ground_stations
    if max_gs_sid > srgb_size:
        raise ValueError(
            f"GS SID range ({gs_sid_offset}..{max_gs_sid}) exceeds SRGB size "
            f"({srgb_size}). Increase SRGB range or reduce ground stations."
        )


# Canonical routing-extension vocabulary. Aliases normalize to the short form the
# resolver actually consumes; anything else is rejected (never silently ignored).
_EXTENSION_ALIASES: dict[str, str] = {
    "te": "te",
    "traffic-engineering": "te",
    "sr": "sr",
    "segment-routing": "sr",
    "mpls": "mpls",
}


def normalize_extensions(extensions: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Canonicalize routing-extension aliases to {te, sr, mpls}.

    Raises ValueError on unknown or duplicate extensions. This is the owning
    boundary for the extension vocabulary; both RoutingConfig and resolve_stack
    route through it so no caller can pass a value the resolver would drop.
    """
    normalized: list[str] = []
    for ext in extensions:
        canon = _EXTENSION_ALIASES.get(ext)
        if canon is None:
            raise ValueError(
                f"unknown routing extension {ext!r}; valid: "
                "te/traffic-engineering, sr/segment-routing, mpls"
            )
        normalized.append(canon)
    if len(set(normalized)) != len(normalized):
        raise ValueError("routing extensions must not contain duplicates")
    return tuple(normalized)


def resolve_stack(protocol: str, extensions: list[str]) -> ResolvedStack:
    """Resolve a (protocol, extensions) pair into a full stack configuration.

    Raises ValueError for invalid combinations.
    """
    ext_set = set(normalize_extensions(extensions))

    # --- Validate constraints ---
    if protocol == "nodalpath":
        raise ValueError("NodalPath is distributed separately from NodalArc")

    if "sr" in ext_set and protocol not in ("ospf", "isis"):
        raise ValueError("SR extension requires ospf or isis protocol")
    if "te" in ext_set and protocol not in ("ospf", "isis"):
        raise ValueError("TE extension requires ospf or isis protocol")
    if "mpls" in ext_set and protocol not in ("ospf", "isis"):
        raise ValueError("MPLS extension requires ospf or isis protocol")
    if "mpls" in ext_set and "te" not in ext_set:
        raise ValueError("MPLS extension requires TE extension")

    if protocol == "ospf":
        return _resolve_ospf(ext_set)
    elif protocol == "isis":
        return _resolve_isis(ext_set)
    else:
        raise ValueError(f"Unknown protocol: {protocol}")


def _resolve_ospf(ext_set: set[str]) -> ResolvedStack:
    daemons = ["zebra", "ospfd", "staticd"]
    template_vars: dict[str, Any] = {
        "protocol": "ospf",
        "reference_bandwidth": 10000,
    }
    segment_routing = False
    sysctls: dict[str, str] = {}

    if "sr" in ext_set:
        daemons.append("pathd")
        template_vars.update(_derive_sr_variables(16000, 23999))
        segment_routing = True
        sysctls = _sr_sysctls()
    if "te" in ext_set:
        template_vars["te_enabled"] = True
    if "mpls" in ext_set:
        daemons.append("ldpd")
        template_vars["mpls_enabled"] = True
        if not sysctls:
            sysctls = _mpls_sysctls()

    templates = [_DAEMON_TEMPLATES[d] for d in daemons]

    return ResolvedStack(
        daemons=daemons,
        template_files=templates,
        template_variables=template_vars,
        image="frr",
        mi_adapter="frr_ospf_adapter",
        segment_routing=segment_routing,
        sysctls=sysctls,
        reconfigure_command="vtysh -f {config_path}",
    )


def _resolve_isis(ext_set: set[str]) -> ResolvedStack:
    daemons = ["zebra", "isisd", "staticd"]
    template_vars: dict[str, Any] = {
        "protocol": "isis",
        "reference_bandwidth": 10000,
    }
    segment_routing = False
    sysctls: dict[str, str] = {}

    if "sr" in ext_set:
        daemons.append("pathd")
        template_vars.update(_derive_sr_variables(16000, 23999))
        segment_routing = True
        sysctls = _sr_sysctls()
    if "te" in ext_set:
        template_vars["te_enabled"] = True
    if "mpls" in ext_set:
        daemons.append("ldpd")
        template_vars["mpls_enabled"] = True
        if not sysctls:
            sysctls = _mpls_sysctls()

    templates = [_DAEMON_TEMPLATES[d] for d in daemons]

    return ResolvedStack(
        daemons=daemons,
        template_files=templates,
        template_variables=template_vars,
        image="frr",
        mi_adapter="frr_isis_adapter",
        segment_routing=segment_routing,
        sysctls=sysctls,
        reconfigure_command="vtysh -f {config_path}",
    )
