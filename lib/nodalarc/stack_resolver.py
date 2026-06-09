# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Stack resolver — derives routing stack configuration from (protocol, extensions).

Replaces the need for per-stack directories when deploying via the wizard.
Session routing uses protocol/extensions only; routing.stack is rejected at parse time.

The resolver is the single source of truth for per-permutation configuration:
sysctls, template variables, and SRGB constraint validation.
The deployer and templates are pure pass-through — no protocol-aware logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from nodalarc.models.resolved_session import ResolvedRoutingDomain

# The routing-domain protocols with an implemented FRR stack. This is the
# single owner of "what renders": RuntimeSupport builds its profile from it,
# so resolve-time gating and the renderer can never disagree.
SUPPORTED_STACK_PROTOCOLS: frozenset[str] = frozenset({"isis", "ospf", "static"})


class TemplateFile(NamedTuple):
    """A Jinja2 template to render into a pod."""

    src: str
    dst: str


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
    """Derive SR template variables from the SRGB range."""
    srgb_size = srgb_end - srgb_start + 1
    if srgb_size <= 0:
        raise ValueError(f"SRGB range is invalid ({srgb_start}..{srgb_end})")

    return {
        "sr_enabled": True,
        "srgb_start": srgb_start,
        "srgb_end": srgb_end,
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


def validate_sid_indices(resolved: ResolvedStack, sid_by_node: Mapping[str, int]) -> None:
    """Validate resolver-owned prefix-SID indices against the stack SRGB.

    The resolver owns SID allocation. The routing stack owns only the SRGB size
    and whether segment routing is enabled. No caller should derive SID indices
    from plane/slot, node kind, or ground-station order.
    """
    if not resolved.segment_routing:
        return

    if not sid_by_node:
        raise ValueError("segment routing requires resolved SID indices")

    tv = resolved.template_variables
    srgb_start = tv.get("srgb_start", 0)
    srgb_end = tv.get("srgb_end", -1)
    srgb_size = srgb_end - srgb_start + 1
    if srgb_size <= 0:
        raise ValueError("segment routing stack has invalid SRGB bounds")

    invalid = {node_id: sid for node_id, sid in sid_by_node.items() if sid <= 0 or sid > srgb_size}
    if invalid:
        examples = ", ".join(f"{node_id}={sid}" for node_id, sid in sorted(invalid.items())[:10])
        raise ValueError(f"resolved SID index exceeds SRGB size {srgb_size}: {examples}")


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


def domain_extensions(domain: ResolvedRoutingDomain) -> list[str]:
    """Return routing-stack extensions implied by a resolved routing domain."""
    if domain.protocol not in SUPPORTED_STACK_PROTOCOLS:
        raise ValueError(
            f"routing domain {domain.domain_id!r} uses protocol {domain.protocol!r}; "
            f"implemented FRR stacks: {', '.join(sorted(SUPPORTED_STACK_PROTOCOLS))}"
        )
    capabilities = set(domain.capabilities or ())
    if domain.protocol == "static" and capabilities:
        raise ValueError(
            f"routing domain {domain.domain_id!r} declares capabilities "
            f"{sorted(capabilities)} on protocol 'static'; static domains carry no "
            "IGP capabilities"
        )
    extensions: list[str] = []
    if "segment_routing" in capabilities:
        extensions.append("sr")
    if "traffic_engineering" in capabilities:
        extensions.append("te")
    if "mpls" in capabilities and "segment_routing" not in capabilities:
        # A bare mpls capability means LDP-distributed MPLS forwarding. When
        # segment_routing is also declared, the MPLS data plane is provided by
        # SR-MPLS (the sr extension carries the label plumbing and sysctls) —
        # the capability is consumed by SR, not dropped.
        extensions.append("mpls")
    return extensions


def resolve_domain_stack(domain: ResolvedRoutingDomain) -> ResolvedStack:
    """Resolve the routing stack for one resolved routing domain."""
    return resolve_stack(domain.protocol, domain_extensions(domain))


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

    if protocol == "ospf":
        return _resolve_ospf(ext_set)
    elif protocol == "isis":
        return _resolve_isis(ext_set)
    elif protocol == "static":
        if ext_set:
            raise ValueError("static protocol takes no extensions")
        return _resolve_static()
    else:
        raise ValueError(f"Unknown protocol: {protocol}")


def _resolve_static() -> ResolvedStack:
    """Static-only routing stack: zebra + staticd, no IGP daemons.

    Valid for stub/edge domains joined to IGP domains through routing
    boundaries. Reachability inside a static domain comes from connected
    routes plus boundary-exported static routes.
    """
    daemons = ["zebra", "staticd"]
    return ResolvedStack(
        daemons=daemons,
        template_files=[_DAEMON_TEMPLATES[d] for d in daemons],
        template_variables={"protocol": "static"},
        image="frr",
        mi_adapter=None,
        segment_routing=False,
        reconfigure_command="vtysh -f {config_path}",
    )


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
