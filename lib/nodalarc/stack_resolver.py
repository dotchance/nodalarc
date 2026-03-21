"""Stack resolver — derives routing stack configuration from (protocol, extensions).

Replaces the need for per-stack directories when deploying via the wizard.
Legacy deploys using routing.stack still load from stack.yaml directly.
"""

from dataclasses import dataclass
from typing import Any, NamedTuple


class TemplateFile(NamedTuple):
    """A Jinja2 template to render into a pod."""

    src: str
    dst: str


@dataclass(frozen=True)
class ResolvedStack:
    """Fully resolved routing stack — everything na_deploy needs."""

    daemons: list[str]
    template_files: list[TemplateFile]
    template_variables: dict[str, Any]
    image: str
    mi_adapter: str | None
    segment_routing: bool
    ttl_propagation: str | None
    transport: str | None
    host_modules: list[str]
    env: list[dict[str, str]]
    security_context_capabilities: list[str]
    reconfigure_command: str | None
    max_compression: int


# Daemon-to-template mapping for FRR stacks
_DAEMON_TEMPLATES: dict[str, TemplateFile] = {
    "zebra": TemplateFile("zebra.conf.j2", "/etc/frr/zebra.conf"),
    "ospfd": TemplateFile("ospfd.conf.j2", "/etc/frr/ospfd.conf"),
    "isisd": TemplateFile("isisd.conf.j2", "/etc/frr/isisd.conf"),
    "ldpd": TemplateFile("ldpd.conf.j2", "/etc/frr/ldpd.conf"),
    "pathd": TemplateFile("pathd.conf.j2", "/etc/frr/pathd.conf"),
    "staticd": TemplateFile("staticd.conf.j2", "/etc/frr/staticd.conf"),
}


def resolve_stack(protocol: str, extensions: list[str]) -> ResolvedStack:
    """Resolve a (protocol, extensions) pair into a full stack configuration.

    Raises ValueError for invalid combinations.
    """
    ext_set = set(extensions)

    # --- Validate constraints ---
    if protocol == "nodalpath":
        if ext_set:
            raise ValueError("nodalpath does not accept extensions")
        return _resolve_nodalpath()

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


def _resolve_nodalpath() -> ResolvedStack:
    return ResolvedStack(
        daemons=["zebra", "staticd"],
        template_files=[_DAEMON_TEMPLATES["zebra"], _DAEMON_TEMPLATES["staticd"]],
        template_variables={"grpc_port": 50051},
        image="nodalpath-fwd:latest",
        mi_adapter=None,
        segment_routing=False,
        ttl_propagation=None,
        transport="grpc",
        host_modules=["mpls_router", "mpls_iptunnel"],
        env=[
            {"name": "NODE_ID", "value": "{{ node_id }}"},
            {"name": "GRPC_PORT", "value": "50051"},
            {"name": "LOOPBACK_IPV4", "value": "{{ ipv4_loopback }}"},
        ],
        security_context_capabilities=["NET_ADMIN", "NET_RAW", "SYS_ADMIN"],
        reconfigure_command=None,
        max_compression=10,
    )


def _resolve_ospf(ext_set: set[str]) -> ResolvedStack:
    daemons = ["zebra", "ospfd"]
    template_vars: dict[str, Any] = {
        "protocol": "ospf",
        "reference_bandwidth": 10000,
    }
    segment_routing = False
    ttl_propagation = None

    if "sr" in ext_set:
        daemons.append("pathd")
        template_vars["sr_enabled"] = True
        template_vars["srgb_start"] = 16000
        template_vars["srgb_end"] = 23999
        segment_routing = True
        ttl_propagation = "uniform"
    if "te" in ext_set:
        template_vars["te_enabled"] = True
    if "mpls" in ext_set:
        daemons.append("ldpd")
        template_vars["mpls_enabled"] = True

    templates = [_DAEMON_TEMPLATES[d] for d in daemons]

    return ResolvedStack(
        daemons=daemons,
        template_files=templates,
        template_variables=template_vars,
        image="nodalarc/frr:10",
        mi_adapter="frr_ospf_adapter",
        segment_routing=segment_routing,
        ttl_propagation=ttl_propagation,
        transport=None,
        host_modules=[],
        env=[],
        security_context_capabilities=[],
        reconfigure_command="vtysh -f {config_path}",
        max_compression=10,
    )


def _resolve_isis(ext_set: set[str]) -> ResolvedStack:
    daemons = ["zebra", "isisd"]
    template_vars: dict[str, Any] = {
        "protocol": "isis",
        "reference_bandwidth": 10000,
    }
    segment_routing = False
    ttl_propagation = None

    if "sr" in ext_set:
        daemons.append("pathd")
        template_vars["sr_enabled"] = True
        template_vars["srgb_start"] = 16000
        template_vars["srgb_end"] = 23999
        segment_routing = True
        ttl_propagation = "uniform"
    if "te" in ext_set:
        template_vars["te_enabled"] = True
    if "mpls" in ext_set:
        daemons.append("ldpd")
        template_vars["mpls_enabled"] = True

    templates = [_DAEMON_TEMPLATES[d] for d in daemons]

    return ResolvedStack(
        daemons=daemons,
        template_files=templates,
        template_variables=template_vars,
        image="nodalarc/frr:10",
        mi_adapter="frr_isis_adapter",
        segment_routing=segment_routing,
        ttl_propagation=ttl_propagation,
        transport=None,
        host_modules=[],
        env=[],
        security_context_capabilities=[],
        reconfigure_command="vtysh -f {config_path}",
        max_compression=10,
    )
