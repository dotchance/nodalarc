# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The explicit adapter registry.

Core selects an adapter per node through this registry, keyed by adapter
name: the value a profile's ``adapter:`` field carries. It is deliberately a
plain, imported list — no filesystem discovery, no dynamic import. A new
adapter enters the platform by being imported here and added to
``_ADAPTERS``; its ``support`` declaration travels with it. That single edit
is the whole coupling surface between core and any one technology.

Importing this module loads every adapter's declaration and no rendering
dependency, so every service that resolves sessions can import it.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from nodalarc.workloads.adapter import AdapterSupport, WorkloadAdapter

from adapters.frr import FrrAdapter

if TYPE_CHECKING:
    from collections.abc import Mapping

# Every adapter the platform knows, one instance each. Add a technology by
# importing its adapter and listing it here.
_ADAPTERS: tuple[WorkloadAdapter, ...] = (FrrAdapter(),)

_BY_NAME: dict[str, WorkloadAdapter] = {}
for _adapter in _ADAPTERS:
    if _adapter.name in _BY_NAME:
        raise ValueError(f"two adapters claim the name {_adapter.name!r}")
    if not isinstance(_adapter.support, AdapterSupport):
        raise TypeError(f"adapter {_adapter.name!r} declares no AdapterSupport")
    _BY_NAME[_adapter.name] = _adapter

_SUPPORT_BY_NAME: Mapping[str, AdapterSupport] = MappingProxyType(
    {name: adapter.support for name, adapter in _BY_NAME.items()}
)


def registered_adapter_support() -> Mapping[str, AdapterSupport]:
    """Every registered adapter's support declaration, keyed by adapter name."""
    return _SUPPORT_BY_NAME


def adapter_named(name: str | None) -> WorkloadAdapter | None:
    """The adapter carrying ``name``, or None when the profile names none.

    None is not a fallback: a profile without an adapter is fully
    self-describing (its containers, command, and args come straight from the
    admitted profile). The caller delivers no adapter-rendered configuration
    for such a node and invents nothing. A profile naming an adapter that is
    not registered never reaches this call: the resolver's runtime-support
    gate refused the session first.
    """
    if name is None:
        return None
    adapter = _BY_NAME.get(name)
    if adapter is None:
        raise ValueError(f"adapter {name!r} passed the support gate but is not registered")
    return adapter
