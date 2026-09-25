# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The explicit adapter registry.

Core selects an adapter per node through this registry, keyed by adapter
name: the value a profile's ``adapter:`` field carries. It is deliberately a
plain, imported list with no filesystem discovery. Each entry pairs an
adapter's declaration, imported here, with the function that imports its
renderer. A new adapter enters the platform by adding one entry; that edit is
the whole coupling surface between core and any one technology.

Importing this module loads every adapter's declaration and no renderer, so
every service that resolves sessions can import it. A renderer loads the first
time ``adapter_named`` is asked for it.
"""

from __future__ import annotations

from functools import cache
from types import MappingProxyType
from typing import TYPE_CHECKING

from nodalarc.workloads.adapter import AdapterSupport, WorkloadAdapter

from adapters.frr.support import FRR_ADAPTER_NAME, FRR_SUPPORT

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def _frr_renderer() -> WorkloadAdapter:
    from adapters.frr.adapter import FrrAdapter

    return FrrAdapter()


# Every adapter the platform knows: its name, its declaration, and the
# function that imports its renderer.
_ADAPTERS: tuple[tuple[str, AdapterSupport, Callable[[], WorkloadAdapter]], ...] = (
    (FRR_ADAPTER_NAME, FRR_SUPPORT, _frr_renderer),
)

_LOADERS: dict[str, Callable[[], WorkloadAdapter]] = {}
for _name, _support, _loader in _ADAPTERS:
    if _name in _LOADERS:
        raise ValueError(f"two adapters claim the name {_name!r}")
    if not isinstance(_support, AdapterSupport):
        raise TypeError(f"adapter {_name!r} declares no AdapterSupport")
    _LOADERS[_name] = _loader

_SUPPORT_BY_NAME: Mapping[str, AdapterSupport] = MappingProxyType(
    {name: support for name, support, _ in _ADAPTERS}
)


def registered_adapter_support() -> Mapping[str, AdapterSupport]:
    """Every registered adapter's support declaration, keyed by adapter name."""
    return _SUPPORT_BY_NAME


@cache
def _renderer(name: str) -> WorkloadAdapter:
    adapter = _LOADERS[name]()
    if adapter.name != name or adapter.support is not _SUPPORT_BY_NAME[name]:
        raise TypeError(f"adapter {name!r} renderer does not carry its registered declaration")
    return adapter


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
    if name not in _LOADERS:
        raise ValueError(f"adapter {name!r} passed the support gate but is not registered")
    return _renderer(name)
