"""Restart protection tests for ordinary catalog upload groups."""

from __future__ import annotations

from types import SimpleNamespace

from nodalarc.catalog_upload import CatalogUploadSelection
from vs_api.catalog_upload_lifecycle import reconcile_catalog_upload_lifecycle
from vs_api.catalog_upload_store import CatalogUploadGarbageCollectionReceipt


class RecordingStore:
    def __init__(self) -> None:
        self.protected: list[tuple[str, ...]] = []

    def garbage_collect(self, *, active_upload_ids):
        selected = tuple(sorted(active_upload_ids))
        self.protected.append(selected)
        return CatalogUploadGarbageCollectionReceipt(
            active_upload_ids=selected,
            deleted_names=(),
            active_names=(),
            grace_names=(),
            unsafe_names=(),
        )


def test_restart_protects_the_persisted_nonterminal_operation() -> None:
    store = RecordingStore()
    operation = SimpleNamespace(
        state=SimpleNamespace(terminal=False),
        provenance=SimpleNamespace(upload_id="pending-upload"),
    )

    reconcile_catalog_upload_lifecycle(
        store,
        constellation_spec=None,
        active_operation=operation,
    )

    assert store.protected == [("pending-upload",)]


def test_restart_protects_the_live_cr_after_operation_completion() -> None:
    store = RecordingStore()
    selection = CatalogUploadSelection(
        upload_id="live-upload",
        closure_digest=f"sha256:{'2' * 64}",
        file_count=7,
    )
    cr = {
        "spec": {
            "sessionYaml": "session:\n  name: recovered\n",
            "catalogUpload": selection.model_dump(mode="json"),
        }
    }

    reconcile_catalog_upload_lifecycle(
        store,
        constellation_spec=cr,
        active_operation=None,
    )

    assert store.protected == [("live-upload",)]
