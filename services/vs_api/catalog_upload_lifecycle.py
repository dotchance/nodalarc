"""Protection and garbage collection for ordinary catalog YAML uploads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from nodalarc.cr_runtime_config import ConstellationSpecSpec

from .catalog_upload_store import (
    CatalogUploadGarbageCollectionReceipt,
    KubernetesCatalogUploadStore,
)


class _OperationState(Protocol):
    terminal: bool


class _OperationProvenance(Protocol):
    upload_id: str | None


class ActiveTransitionOperation(Protocol):
    state: _OperationState
    provenance: _OperationProvenance


@dataclass(frozen=True, slots=True)
class CatalogUploadLifecycleReceipt:
    protected_upload_ids: tuple[str, ...]
    garbage_collection: CatalogUploadGarbageCollectionReceipt


def _live_upload_id(constellation_spec: Mapping[str, Any] | None) -> str | None:
    if constellation_spec is None:
        return None
    spec = constellation_spec.get("spec")
    if not isinstance(spec, Mapping):
        raise ValueError("live ConstellationSpec spec must be a mapping")
    # A spec without a selection names no live upload; a spec with one must be whole.
    if spec.get("catalogUpload") is None:
        return None
    return ConstellationSpecSpec.from_cr(spec).catalog_upload.upload_id


def reconcile_catalog_upload_lifecycle(
    store: KubernetesCatalogUploadStore,
    *,
    constellation_spec: Mapping[str, Any] | None,
    active_operation: ActiveTransitionOperation | None,
) -> CatalogUploadLifecycleReceipt:
    """Protect live and in-flight upload IDs, then collect old groups."""

    if active_operation is not None and active_operation.state.terminal:
        raise ValueError("active_operation must be nonterminal")
    protected: set[str] = set()
    live_upload_id = _live_upload_id(constellation_spec)
    if live_upload_id is not None:
        protected.add(live_upload_id)
    if active_operation is not None and active_operation.provenance.upload_id is not None:
        protected.add(active_operation.provenance.upload_id)
    garbage_collection = store.garbage_collect(active_upload_ids=protected)
    return CatalogUploadLifecycleReceipt(
        protected_upload_ids=tuple(sorted(protected)),
        garbage_collection=garbage_collection,
    )
