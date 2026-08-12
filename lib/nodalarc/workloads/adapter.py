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
    field carries, matching the runtime-support renderability declaration.
    The explicit registry keys on it. ``render_node`` is pure: resolved facts
    in, native config out, no I/O and no Kubernetes calls.
    """

    name: str

    def render_node(
        self,
        resolved_node: ResolvedNode,
        session_context: SessionContext,
    ) -> AdapterNodeConfig: ...
