# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The explicit adapter registry.

Core selects an adapter per node through this registry, keyed by adapter
name: the value a profile's ``adapter:`` field carries. It is deliberately a
plain, imported list — no filesystem discovery, no dynamic import. A new
adapter enters the platform by being imported here and added to
``_ADAPTERS``. That single edit, plus its runtime-support declaration, is the
whole coupling surface between core and any one technology.
"""

from __future__ import annotations

from nodalarc.workloads.adapter import WorkloadAdapter

from adapters.frr import FrrAdapter

# Every adapter the platform knows, one instance each. Add a technology by
# importing its adapter and listing it here.
_ADAPTERS: tuple[WorkloadAdapter, ...] = (FrrAdapter(),)

_BY_NAME: dict[str, WorkloadAdapter] = {}
for _adapter in _ADAPTERS:
    if _adapter.name in _BY_NAME:
        raise ValueError(f"two adapters claim the name {_adapter.name!r}")
    _BY_NAME[_adapter.name] = _adapter


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
