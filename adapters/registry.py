# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The explicit adapter registry.

Core selects an adapter per node through this registry. It is deliberately a
plain, imported list — no filesystem discovery, no dynamic import. A new adapter
enters the platform by being imported here and added to ``_ADAPTERS``. That
single edit is the whole coupling surface between core and any one technology.
"""

from __future__ import annotations

from nodalarc.workloads.adapter import WorkloadAdapter

from adapters.frr import FrrAdapter

# Every adapter the platform knows, one instance each. Add a technology by
# importing its adapter and listing it here.
_ADAPTERS: tuple[WorkloadAdapter, ...] = (FrrAdapter(),)

_BY_PROFILE_REF: dict[str, WorkloadAdapter] = {}
for _adapter in _ADAPTERS:
    if _adapter.profile_ref in _BY_PROFILE_REF:
        raise ValueError(f"two adapters claim profile {_adapter.profile_ref!r}")
    _BY_PROFILE_REF[_adapter.profile_ref] = _adapter


def adapter_for(profile_ref: str) -> WorkloadAdapter | None:
    """The adapter serving ``profile_ref``, or None when no adapter renders it.

    None is not a fallback: a profile without an adapter is fully self-describing
    (its containers, command, and args come straight from the admitted profile).
    The caller delivers no adapter-rendered configuration for such a node and
    invents nothing.
    """
    return _BY_PROFILE_REF.get(profile_ref)
