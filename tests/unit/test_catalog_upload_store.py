"""Kubernetes storage contracts for one ordinary YAML file per ConfigMap."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_upload import CatalogUpload, encode_catalog_upload
from nodalarc.content_identity import sha256_digest
from nodalarc.kubernetes_runtime_config import (
    CATALOG_DOCUMENT_KEY,
    CATALOG_REF_ANNOTATION,
    CATALOG_UPLOAD_LABEL,
)
from nodalarc.prepared_session import (
    PreparedSessionSource,
    prepare_session_files,
)
from vs_api.catalog_upload_store import (
    CatalogUploadStoreError,
    CatalogUploadStoreErrorCode,
    KubernetesCatalogUploadStore,
)

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions/earth-leo-simple.yaml"
NAMESPACE = "nodalarc-test"
OLD = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 1, 2, tzinfo=UTC)


class FakeApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"fake Kubernetes status {status}")
        self.status = status


def _object_from_body(
    body: dict[str, Any],
    *,
    uid: str,
    creation_timestamp: datetime | None,
) -> SimpleNamespace:
    metadata = body["metadata"]
    return SimpleNamespace(
        api_version=body["apiVersion"],
        kind=body["kind"],
        metadata=SimpleNamespace(
            name=metadata["name"],
            namespace=metadata["namespace"],
            uid=uid,
            labels=copy.deepcopy(metadata.get("labels", {})),
            annotations=copy.deepcopy(metadata.get("annotations", {})),
            creation_timestamp=creation_timestamp,
            owner_references=copy.deepcopy(metadata.get("ownerReferences", [])),
        ),
        immutable=body.get("immutable"),
        data=copy.deepcopy(body.get("data")),
        binary_data=copy.deepcopy(body.get("binaryData")),
    )


class FakeCoreV1Api:
    def __init__(self) -> None:
        self.config_maps: dict[str, SimpleNamespace] = {}
        self.calls: list[tuple[str, str]] = []
        self.create_failures: dict[str, int] = {}
        self.delete_failures: dict[str, int] = {}
        self.before_list = None
        self.after_create = None
        self._uid = 0

    def create_namespaced_config_map(self, namespace: str, body: dict[str, Any]):
        name = body["metadata"]["name"]
        self.calls.append(("create", name))
        if name in self.create_failures:
            raise FakeApiError(self.create_failures[name])
        if name in self.config_maps:
            raise FakeApiError(409)
        self._uid += 1
        observed = _object_from_body(
            body,
            uid=f"uid-{self._uid}",
            creation_timestamp=NOW,
        )
        self.config_maps[name] = observed
        if self.after_create is not None:
            self.after_create(observed)
        return observed

    def list_namespaced_config_map(self, namespace: str, *, label_selector: str):
        self.calls.append(("list", label_selector))
        callback, self.before_list = self.before_list, None
        if callback is not None:
            callback(self)
        if "=" in label_selector:
            key, value = label_selector.split("=", 1)
            items = [
                item for item in self.config_maps.values() if item.metadata.labels.get(key) == value
            ]
        else:
            items = [
                item for item in self.config_maps.values() if label_selector in item.metadata.labels
            ]
        return SimpleNamespace(items=items)

    def delete_namespaced_config_map(self, name: str, namespace: str, body: dict[str, Any]):
        self.calls.append(("delete", name))
        if name in self.delete_failures:
            raise FakeApiError(self.delete_failures[name])
        if name not in self.config_maps:
            raise FakeApiError(404)
        del self.config_maps[name]

    def seed(
        self,
        *,
        name: str,
        upload_id: str,
        ref: str,
        content: str,
        creation_timestamp: datetime | None = OLD,
    ) -> None:
        self._uid += 1
        self.config_maps[name] = _object_from_body(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": name,
                    "namespace": NAMESPACE,
                    "labels": {CATALOG_UPLOAD_LABEL: upload_id},
                    "annotations": {CATALOG_REF_ANNOTATION: ref},
                },
                "data": {CATALOG_DOCUMENT_KEY: content},
            },
            uid=f"uid-{self._uid}",
            creation_timestamp=creation_timestamp,
        )


@pytest.fixture(scope="module")
def upload() -> CatalogUpload:
    root_yaml = SIMPLE_SESSION.read_bytes()
    prepared = prepare_session_files(
        root_yaml,
        FilesystemCatalogReadView(CatalogRoots.from_catalog_root(SHIPPED_ROOT)),
        source=PreparedSessionSource(
            logical_id="nodalarc:sessions/earth-leo-simple.yaml",
            origin="test.catalog_upload_store",
        ),
        source_revision=sha256_digest(root_yaml),
        available_node_count=100,
    )
    return encode_catalog_upload(prepared, upload_id="upload-store-test")


def test_put_creates_one_plain_config_map_per_yaml_and_lists_once(upload: CatalogUpload) -> None:
    api = FakeCoreV1Api()
    receipt = KubernetesCatalogUploadStore(api, NAMESPACE).put(upload)

    assert len(receipt.resources) == upload.file_count
    assert receipt.selection == upload.selection
    assert len([call for call in api.calls if call[0] == "create"]) == upload.file_count
    assert [call for call in api.calls if call[0] == "list"] == [
        ("list", f"{CATALOG_UPLOAD_LABEL}={upload.upload_id}")
    ]
    expected = {str(entry.ref): entry.yaml_bytes.decode("utf-8") for entry in upload.catalog_files}
    observed: dict[str, str] = {}
    for config_map in api.config_maps.values():
        assert config_map.immutable is None
        assert config_map.binary_data is None
        assert config_map.metadata.owner_references == []
        assert config_map.metadata.labels == {CATALOG_UPLOAD_LABEL: upload.upload_id}
        assert set(config_map.data) == {CATALOG_DOCUMENT_KEY}
        ref = config_map.metadata.annotations[CATALOG_REF_ANNOTATION]
        observed[ref] = config_map.data[CATALOG_DOCUMENT_KEY]
    assert observed == expected


def test_put_accepts_typed_config_maps_without_type_meta(upload: CatalogUpload) -> None:
    api = FakeCoreV1Api()

    def omit_type_meta(selected: FakeCoreV1Api) -> None:
        for config_map in selected.config_maps.values():
            config_map.api_version = None
            config_map.kind = None

    api.before_list = omit_type_meta

    receipt = KubernetesCatalogUploadStore(api, NAMESPACE).put(upload)

    assert receipt.selection == upload.selection


@pytest.mark.parametrize(
    ("field", "value"),
    [("api_version", "v2"), ("kind", "Secret")],
)
def test_put_rejects_contradictory_type_meta(
    upload: CatalogUpload,
    field: str,
    value: str,
) -> None:
    api = FakeCoreV1Api()

    def replace_type_meta(selected: FakeCoreV1Api) -> None:
        setattr(next(iter(selected.config_maps.values())), field, value)

    api.before_list = replace_type_meta

    with pytest.raises(CatalogUploadStoreError) as raised:
        KubernetesCatalogUploadStore(api, NAMESPACE).put(upload)

    assert raised.value.code is CatalogUploadStoreErrorCode.READBACK_MISMATCH


def test_put_cleans_created_files_after_create_or_observer_failure(upload: CatalogUpload) -> None:
    api = FakeCoreV1Api()
    second_name = f"{upload.upload_id}-000001"
    api.create_failures[second_name] = 500
    with pytest.raises(CatalogUploadStoreError) as create_failure:
        KubernetesCatalogUploadStore(api, NAMESPACE).put(upload)
    assert create_failure.value.code is CatalogUploadStoreErrorCode.CREATE_FAILED
    assert api.config_maps == {}

    observed_api = FakeCoreV1Api()

    def refuse(_resource) -> None:
        raise RuntimeError("journal unavailable")

    with pytest.raises(CatalogUploadStoreError):
        KubernetesCatalogUploadStore(observed_api, NAMESPACE).put(
            upload,
            resource_observer=refuse,
        )
    assert observed_api.config_maps == {}


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "corrupt", "extra_label"])
def test_readback_strictly_rejects_wrong_file_sets(upload: CatalogUpload, mutation: str) -> None:
    api = FakeCoreV1Api()

    def mutate(selected: FakeCoreV1Api) -> None:
        names = sorted(selected.config_maps)
        if mutation == "missing":
            del selected.config_maps[names[0]]
        elif mutation == "extra":
            selected.seed(
                name=f"{upload.upload_id}-extra",
                upload_id=upload.upload_id,
                ref="nodalarc:bodies/luna.yaml",
                content=(SHIPPED_ROOT / "bodies/luna.yaml").read_text(encoding="utf-8"),
            )
        elif mutation == "extra_label":
            # The runtime reader refuses any label beyond the upload label;
            # the readback applies the same rule.
            selected.config_maps[names[0]].metadata.labels["unexpected"] = "value"
        elif mutation == "duplicate":
            source = selected.config_maps[names[0]]
            duplicate = copy.deepcopy(source)
            duplicate.metadata.name = f"{upload.upload_id}-duplicate"
            duplicate.metadata.uid = "uid-duplicate"
            selected.config_maps[duplicate.metadata.name] = duplicate
        else:
            selected.config_maps[names[0]].data[CATALOG_DOCUMENT_KEY] = "not: [valid"

    api.before_list = mutate
    with pytest.raises(CatalogUploadStoreError) as raised:
        KubernetesCatalogUploadStore(api, NAMESPACE).put(upload)
    assert raised.value.code is CatalogUploadStoreErrorCode.READBACK_MISMATCH
    assert not any(name.startswith(f"{upload.upload_id}-000") for name in api.config_maps)


def test_delete_removes_only_the_selected_upload_group(upload: CatalogUpload) -> None:
    api = FakeCoreV1Api()
    store = KubernetesCatalogUploadStore(api, NAMESPACE)
    receipt = store.put(upload)
    api.seed(
        name="other-upload-000000",
        upload_id="other-upload",
        ref="nodalarc:bodies/earth.yaml",
        content=(SHIPPED_ROOT / "bodies/earth.yaml").read_text(encoding="utf-8"),
    )

    deleted = store.delete(upload.selection)

    assert set(deleted.deleted_names) == set(receipt.created_names)
    assert set(api.config_maps) == {"other-upload-000000"}


def test_gc_protects_active_groups_and_applies_group_grace() -> None:
    api = FakeCoreV1Api()
    api.seed(
        name="active-000000",
        upload_id="active",
        ref="nodalarc:bodies/earth.yaml",
        content="body: {}\n",
    )
    api.seed(
        name="stale-000000",
        upload_id="stale",
        ref="nodalarc:bodies/earth.yaml",
        content="body: {}\n",
    )
    api.seed(
        name="grace-000000",
        upload_id="grace",
        ref="nodalarc:bodies/earth.yaml",
        content="body: {}\n",
        creation_timestamp=NOW - timedelta(minutes=5),
    )
    api.seed(
        name="unsafe-000000",
        upload_id="unsafe",
        ref="nodalarc:bodies/earth.yaml",
        content="body: {}\n",
        creation_timestamp=None,
    )

    receipt = KubernetesCatalogUploadStore(api, NAMESPACE).garbage_collect(
        active_upload_ids={"active"},
        now=NOW,
    )

    assert receipt.active_names == ("active-000000",)
    assert receipt.deleted_names == ("stale-000000",)
    assert receipt.grace_names == ("grace-000000",)
    assert receipt.unsafe_names == ("unsafe-000000",)
    assert set(api.config_maps) == {"active-000000", "grace-000000", "unsafe-000000"}


def test_constructor_and_inputs_are_strict(upload: CatalogUpload) -> None:
    with pytest.raises(TypeError):
        KubernetesCatalogUploadStore(FakeCoreV1Api(), "")
    with pytest.raises(ValueError):
        KubernetesCatalogUploadStore(
            FakeCoreV1Api(),
            NAMESPACE,
            gc_grace=timedelta(days=2),
        )
    with pytest.raises(TypeError):
        KubernetesCatalogUploadStore(FakeCoreV1Api(), NAMESPACE).garbage_collect(
            active_upload_ids="active",
        )


def test_created_resource_is_registered_before_its_content_is_judged(
    upload: CatalogUpload,
) -> None:
    """A create that persisted an unlabelled resource still registers it: the
    observer journals it and the readback failure cleans it up."""
    api = FakeCoreV1Api()

    first = f"{upload.upload_id}-000000"

    def strip_first_label(observed: SimpleNamespace) -> None:
        if observed.metadata.name == first:
            observed.metadata.labels.clear()

    api.after_create = strip_first_label
    journal: list[str] = []

    with pytest.raises(CatalogUploadStoreError) as raised:
        KubernetesCatalogUploadStore(api, NAMESPACE).put(
            upload, resource_observer=lambda evidence: journal.append(evidence.name)
        )

    assert raised.value.code is CatalogUploadStoreErrorCode.READBACK_MISMATCH
    assert journal[0] == first
    assert len(journal) == len(upload.catalog_files)
    assert first in raised.value.evidence.created_names
    assert raised.value.evidence.cleanup_failures == ()
    assert api.config_maps == {}


def test_readback_list_envelope_faults_are_list_failures(upload: CatalogUpload) -> None:
    api = FakeCoreV1Api()
    real_list = api.list_namespaced_config_map

    def list_without_items(namespace: str, *, label_selector: str):
        response = real_list(namespace, label_selector=label_selector)
        if len(api.calls) > len(upload.catalog_files):
            return SimpleNamespace(items=None)
        return response

    api.list_namespaced_config_map = list_without_items  # type: ignore[method-assign]

    with pytest.raises(CatalogUploadStoreError) as raised:
        KubernetesCatalogUploadStore(api, NAMESPACE).put(upload)

    assert raised.value.code is CatalogUploadStoreErrorCode.LIST_FAILED
    assert api.config_maps == {}


def _group_statuses(error: CatalogUploadStoreError) -> tuple[int, ...]:
    cause = error.__cause__
    assert isinstance(cause, ExceptionGroup)
    return tuple(member.status for member in cause.exceptions)


def test_delete_retains_every_failed_delete_as_the_chained_cause(upload: CatalogUpload) -> None:
    api = FakeCoreV1Api()
    store = KubernetesCatalogUploadStore(api, NAMESPACE)
    receipt = store.put(upload)
    first, second = receipt.created_names[0], receipt.created_names[1]
    api.delete_failures[first] = 500
    api.delete_failures[second] = 503

    with pytest.raises(CatalogUploadStoreError) as raised:
        store.delete(upload.selection)

    assert raised.value.code is CatalogUploadStoreErrorCode.DELETE_FAILED
    assert raised.value.evidence.cleanup_failures == (first, second)
    assert raised.value.evidence.cause_type == "ExceptionGroup"
    assert _group_statuses(raised.value) == (500, 503)
    assert set(api.config_maps) == {first, second}


def test_gc_retains_every_failed_delete_as_the_chained_cause() -> None:
    api = FakeCoreV1Api()
    for order in range(2):
        api.seed(
            name=f"stale-00000{order}",
            upload_id="stale",
            ref="nodalarc:bodies/earth.yaml",
            content="body: {}\n",
        )
    api.delete_failures["stale-000000"] = 500
    api.delete_failures["stale-000001"] = 409

    with pytest.raises(CatalogUploadStoreError) as raised:
        KubernetesCatalogUploadStore(api, NAMESPACE).garbage_collect(
            active_upload_ids=set(),
            now=NOW,
        )

    assert raised.value.code is CatalogUploadStoreErrorCode.DELETE_FAILED
    assert raised.value.evidence.upload_id == "stale"
    assert raised.value.evidence.cleanup_failures == ("stale-000000", "stale-000001")
    assert _group_statuses(raised.value) == (500, 409)


def test_put_cleanup_failures_stay_names_behind_the_create_failure(upload: CatalogUpload) -> None:
    api = FakeCoreV1Api()
    first = f"{upload.upload_id}-000000"
    second = f"{upload.upload_id}-000001"
    api.create_failures[second] = 500
    api.delete_failures[first] = 503

    with pytest.raises(CatalogUploadStoreError) as raised:
        KubernetesCatalogUploadStore(api, NAMESPACE).put(upload)

    assert raised.value.code is CatalogUploadStoreErrorCode.CREATE_FAILED
    assert raised.value.evidence.created_names == (first,)
    assert raised.value.evidence.cleanup_failures == (first,)
    create_failure = raised.value.__cause__
    assert isinstance(create_failure, CatalogUploadStoreError)
    assert create_failure.code is CatalogUploadStoreErrorCode.CREATE_FAILED
    assert isinstance(create_failure.__cause__, FakeApiError)
    assert create_failure.__cause__.status == 500
