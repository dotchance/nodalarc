"""Protection and garbage collection contracts for catalog upload groups."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nodalarc.catalog_upload import CatalogUploadSelection
from vs_api.catalog_upload_lifecycle import reconcile_catalog_upload_lifecycle
from vs_api.catalog_upload_store import CatalogUploadGarbageCollectionReceipt


def _selection(upload_id: str) -> CatalogUploadSelection:
    return CatalogUploadSelection(
        upload_id=upload_id,
        closure_digest=f"sha256:{'1' * 64}",
        file_count=3,
    )


def _cr(upload_id: str) -> dict:
    return {
        "spec": {
            "sessionYaml": "session:\n  name: test\n",
            "catalogUpload": _selection(upload_id).model_dump(mode="json"),
        }
    }


class RecordingStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def garbage_collect(self, *, active_upload_ids):
        protected = tuple(sorted(active_upload_ids))
        self.calls.append(protected)
        return CatalogUploadGarbageCollectionReceipt(
            active_upload_ids=protected,
            deleted_names=(),
            active_names=(),
            grace_names=(),
            unsafe_names=(),
        )


def _operation(upload_id: str | None, *, terminal: bool = False):
    return SimpleNamespace(
        state=SimpleNamespace(terminal=terminal),
        provenance=SimpleNamespace(upload_id=upload_id),
    )


def test_lifecycle_protects_live_and_nonterminal_upload_ids() -> None:
    store = RecordingStore()

    receipt = reconcile_catalog_upload_lifecycle(
        store,
        constellation_spec=_cr("live-upload"),
        active_operation=_operation("pending-upload"),
    )

    assert receipt.protected_upload_ids == ("live-upload", "pending-upload")
    assert store.calls == [("live-upload", "pending-upload")]


def test_lifecycle_runs_gc_without_live_or_pending_uploads() -> None:
    store = RecordingStore()

    receipt = reconcile_catalog_upload_lifecycle(
        store,
        constellation_spec=None,
        active_operation=None,
    )

    assert receipt.protected_upload_ids == ()
    assert store.calls == [()]


def test_invalid_live_or_terminal_authority_fails_before_gc() -> None:
    store = RecordingStore()
    with pytest.raises(ValueError):
        reconcile_catalog_upload_lifecycle(
            store,
            constellation_spec={"spec": {"sessionYaml": "session: {}\n", "catalogUpload": {}}},
            active_operation=None,
        )
    with pytest.raises(ValueError):
        reconcile_catalog_upload_lifecycle(
            store,
            constellation_spec=None,
            active_operation=_operation("done", terminal=True),
        )
    assert store.calls == []
