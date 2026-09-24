# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The workload adapter contract: neutral per-node facts in, native config out.

Core owns this contract and the selection of an adapter per node; it never
learns what any technology *is*. An adapter translates one resolved node into
the exact files, environment, and arguments its image consumes, and the image's
own ENTRYPOINT is always preserved: ``files`` mount, ``env`` sets, ``args``
append. That is what lets a vendor NOS, a DTN daemon, or a plain application run
unmodified — they boot themselves and read the config we place, rather than a
startup script we run in their place.

An adapter reads the whole resolved session through ``SessionContext`` so it can
resolve a *peer's* substrate address (a client needs its server; a DTN node
needs its contact). The relationship is authored config; the address is resolved
from substrate truth. The author says who; the platform says where.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from nodalarc.model_validation import ADDRESS_FAMILIES, AddressFamily
from nodalarc.models.segment_session import (
    ROUTING_CAPABILITIES,
    ROUTING_PROTOCOLS,
    RoutingCapability,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nodalarc.models.resolved_session import ResolvedNode, ResolvedSession


@dataclass(frozen=True, slots=True)
class AdapterNodeConfig:
    """One node's native configuration, in the three shapes an image accepts.

    ``files`` are native config documents, delivered to the adapter-declared
    mount path (reusing the profile's plan-artifact slot); mount-relative file
    name maps to exact bytes. ``env`` are per-node environment variables.
    ``args`` are appended to the image's ENTRYPOINT, or ``None`` to leave the
    image's own argument vector untouched. The ENTRYPOINT is never replaced.
    """

    files: Mapping[str, bytes] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    args: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        files = MappingProxyType(dict(self.files))
        for name, content in files.items():
            if not isinstance(name, str) or not name:
                raise ValueError("adapter file names must be non-empty strings")
            if not isinstance(content, bytes):
                raise TypeError(f"adapter file {name!r} content must be bytes")
        object.__setattr__(self, "files", files)

        env = MappingProxyType(dict(self.env))
        for key, value in env.items():
            if not isinstance(key, str) or not key:
                raise ValueError("adapter env names must be non-empty strings")
            if not isinstance(value, str):
                raise TypeError(f"adapter env {key!r} value must be a string")
        object.__setattr__(self, "env", env)

        if self.args is not None:
            args = tuple(self.args)
            if any(not isinstance(element, str) or not element for element in args):
                raise ValueError("adapter args must be non-empty strings")
            object.__setattr__(self, "args", args)


def _check_bounds(name: str, bounds: tuple[int, int]) -> None:
    low, high = bounds
    if not 1 <= low <= high:
        raise ValueError(f"{name} bounds must satisfy 1 <= low <= high; got {bounds}")


@dataclass(frozen=True, slots=True)
class BfdSupport:
    """The BFD timer values an adapter renders, as inclusive bounds.

    Field names match the session grammar's ``timers.bfd`` fields, so the
    common support check compares each authored value with the bound of the
    same name.
    """

    detect_multiplier: tuple[int, int]
    rx_interval_ms: tuple[int, int]
    tx_interval_ms: tuple[int, int]

    def __post_init__(self) -> None:
        _check_bounds("detect_multiplier", self.detect_multiplier)
        _check_bounds("rx_interval_ms", self.rx_interval_ms)
        _check_bounds("tx_interval_ms", self.tx_interval_ms)


@dataclass(frozen=True, slots=True)
class RoutingProtocolSupport:
    """What an adapter renders for one routing-domain protocol.

    ``capabilities`` are the domain capability names the adapter renders for
    the protocol. ``bfd`` is None when the adapter renders no BFD for it.
    ``address_families`` are the IP address families the adapter routes
    with the protocol; every declaration names them. ``domains_per_router``
    is the most domains of the protocol the adapter renders on one router,
    or None when it renders any number.
    """

    capabilities: frozenset[RoutingCapability] = frozenset()
    bfd: BfdSupport | None = None
    address_families: frozenset[AddressFamily] = field(kw_only=True)
    domains_per_router: int | None = field(kw_only=True)

    def __post_init__(self) -> None:
        if self.domains_per_router is not None and self.domains_per_router < 1:
            raise ValueError(
                "routing support must render at least one domain per router; "
                f"got {self.domains_per_router}"
            )
        unknown_capabilities = sorted(set(self.capabilities) - set(ROUTING_CAPABILITIES))
        if unknown_capabilities:
            raise ValueError(
                f"routing support declares capabilities outside the grammar {unknown_capabilities}"
            )
        families = frozenset(self.address_families)
        if not families:
            raise ValueError("routing support must declare the address families it routes")
        unknown = sorted(families - frozenset(ADDRESS_FAMILIES))
        if unknown:
            raise ValueError(f"routing support declares unknown address families {unknown}")
        object.__setattr__(self, "address_families", families)


@dataclass(frozen=True, slots=True)
class AdapterSupport:
    """An adapter's declaration of the session features it renders.

    ``routing`` maps each routing-domain protocol the adapter renders to what
    it renders for that protocol; an empty mapping declares a non-routing
    adapter. The declaration is plain data: reading it loads no template and
    imports no rendering dependency, so every service that resolves sessions
    can read it.
    """

    routing: Mapping[str, RoutingProtocolSupport] = field(default_factory=dict)

    def __post_init__(self) -> None:
        routing = MappingProxyType(dict(self.routing))
        for protocol, support in routing.items():
            if protocol not in ROUTING_PROTOCOLS:
                raise ValueError(
                    f"adapter declares routing protocol {protocol!r} outside the grammar"
                )
            if not isinstance(support, RoutingProtocolSupport):
                raise TypeError(f"adapter routing support for {protocol!r} has the wrong type")
        object.__setattr__(self, "routing", routing)


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Read access to the whole resolved session for one adapter invocation.

    Technology-blind: it carries the resolved runtime view and nothing an
    adapter could not read from it. Peer resolution (a client's server, a DTN
    contact) reads other nodes' resolved addresses from here.
    """

    resolved: ResolvedSession

    def node(self, node_id: str) -> ResolvedNode:
        """The resolved node for ``node_id``, or a loud failure if absent."""
        found = self.resolved.node_by_id(node_id)
        if found is None:
            raise ValueError(f"session context has no resolved node {node_id!r}")
        return found


@runtime_checkable
class WorkloadAdapter(Protocol):
    """Translate one resolved node into its image's native configuration.

    ``name`` is the adapter's identity: the value a profile's ``adapter:``
    field carries. The explicit registry keys on it. ``support`` declares the
    session features the adapter renders; runtime support reads it to gate
    sessions and to derive router populations. ``render_node`` is pure:
    resolved facts in, native config out, no I/O and no Kubernetes calls.
    """

    name: str
    support: AdapterSupport

    def render_node(
        self,
        resolved_node: ResolvedNode,
        session_context: SessionContext,
    ) -> AdapterNodeConfig: ...
